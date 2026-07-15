from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import dotenv_values
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from .stores.calendar_store import CalendarStore
from .stores.email_store import EmailStore
from .stores.email_send_queue_store import EmailSendQueueStore
from .stores.graph_store import GraphStoreBase
from .stores.oauth_client_store import build_oauth_client_store_from_env
from .stores.oauth_token_store import build_oauth_token_store_from_env
from .tools.calendar_tools import register_calendar_tools
from .tools.email_tools import register_email_tools
from .tools.email_queue_tools import register_email_queue_tools
from .utils.oauth_dynamic_provider import DynamicOAuthProvider, get_dynamic_oauth_config_from_env
from .utils.biz_logger import configure_default_loggers
from .utils.oauth_middleware import OAuthTokenLogMiddleware

_ROOT_DIR = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("mail_mcp")

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for key, value in dotenv_values(path).items():
        if value is None:
            continue
        # Keep process-level env vars (for App Service / secret settings) as highest priority.
        os.environ.setdefault(key, value)

def _bootstrap_env() -> None:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    env_files: list[Path] = [_ROOT_DIR / ".env"]

    if app_env:
        env_files.append(_ROOT_DIR / f".env.{app_env}")
    else:
        env_files.append(_ROOT_DIR / ".env.prod")

    for env_file in env_files:
        _load_env_file(env_file)

_bootstrap_env()
configure_default_loggers()

CURRENT_ACCESS_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_access_token", default=None
)
MCP_SCOPE = "mail.mcp"
TOKEN_PROVIDER = CURRENT_ACCESS_TOKEN.get
EMAIL_STORE, CALENDAR_STORE, GRAPH_STORE = (EmailStore(token_provider=TOKEN_PROVIDER), CalendarStore(token_provider=TOKEN_PROVIDER), GraphStoreBase(token_provider=TOKEN_PROVIDER))
EMAIL_SEND_QUEUE_STORE = EmailSendQueueStore(token_provider=TOKEN_PROVIDER)

_oauth_provider: DynamicOAuthProvider | None = None
_auth_settings: AuthSettings | None = None
_oauth_config = get_dynamic_oauth_config_from_env()
if _oauth_config:
    _oauth_client_store = build_oauth_client_store_from_env()
    _oauth_token_store = build_oauth_token_store_from_env()
    _oauth_provider = DynamicOAuthProvider(
        **_oauth_config,
        client_registry=_oauth_client_store,
        token_registry=_oauth_token_store,
    )
    issuer_url = _oauth_config["issuer_url"]
    _auth_settings = AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=issuer_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE],
            default_scopes=[MCP_SCOPE],
            client_secret_expiry_seconds=365 * 24 * 3600,
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[MCP_SCOPE],
        service_documentation_url=(os.getenv("MCP_OAUTH_SERVICE_DOCUMENTATION_URL") or issuer_url),
    )


def _run_startup_token_cleanup_once() -> None:
    if _oauth_token_store is None:
        return
    try:
        cutoff_epoch = int(time.time()) - _oauth_token_store._STARTUP_CLEANUP_EXPIRED_AGE_SECONDS
        _oauth_token_store.cleanup_oauth_artifacts_expired_before_until_clean(
            cutoff_epoch=cutoff_epoch,
            limit=100,
        )
    except Exception:
        # 启动清理失败不影响主服务可用性。
        return


def _schedule_startup_token_cleanup_once() -> None:
    worker = threading.Thread(
        target=_run_startup_token_cleanup_once,
        daemon=True,
        name="oauth-token-startup-cleanup",
    )
    worker.start()


_schedule_startup_token_cleanup_once()

APP = FastMCP(
    "mail-assistant",
    auth_server_provider=_oauth_provider,
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", os.getenv("PORT", "80"))),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
    auth=_auth_settings,
)

_AGENTS_MD_PATH = _ROOT_DIR / "AGENTS.md"
_EXPOSE_AGENTS_MD = os.getenv("MCP_EXPOSE_AGENTS_MD", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

register_calendar_tools(APP, CALENDAR_STORE)
register_email_tools(APP, EMAIL_STORE)
register_email_queue_tools(APP, EMAIL_SEND_QUEUE_STORE, EMAIL_STORE)


@APP.tool()
def mailbox_list_tenant_users(search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    """List tenant users and their mailbox addresses via Microsoft Graph /users."""
    return GRAPH_STORE.list_tenant_users(search=search, limit=limit)


@APP.tool()
def mailbox_get_user_time_zone() -> dict[str, str]:
    """Get current user's mailbox time zone."""
    result = GRAPH_STORE.get_user_time_zone()
    LOGGER.info(
        "mailbox_get_user_time_zone result: time_zone=%s source=%s",
        result.get("time_zone", ""),
        result.get("source", ""),
    )
    return result


if _EXPOSE_AGENTS_MD:

    @APP.tool()
    def mailbox_get_agents_md() -> dict[str, str | bool]:
        """Read repository AGENTS.md for external MCP clients."""
        if not _AGENTS_MD_PATH.exists():
            return {
                "enabled": True,
                "found": False,
                "path": str(_AGENTS_MD_PATH),
                "content": "",
            }

        content = _AGENTS_MD_PATH.read_text(encoding="utf-8")
        return {
            "enabled": True,
            "found": True,
            "path": str(_AGENTS_MD_PATH),
            "content": content,
        }

@APP.tool()
def ping() -> dict[str, str]:
    """Health check tool."""
    return {"status": "ok", "service": "mail-assistant"}


if _oauth_provider is not None:

    @APP.custom_route("/oauth/callback", methods=["GET"])
    async def oauth_callback(request):
        params = dict(request.query_params.items())
        return await _oauth_provider.build_callback_redirect(params)

def _build_asgi_app():
    starlette_app = APP.streamable_http_app()

    def healthz(_request):
        return JSONResponse({"status": "ok", "service": "mail-assistant"})

    def dispatch_send_jobs(_request):
        try:
            result = EMAIL_SEND_QUEUE_STORE.dispatch_pending_jobs()
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "dispatch pending jobs failed",
                    "error": str(exc),
                },
                status_code=500,
            )

    def index(_request):
        return JSONResponse(
            {
                "status": "ok",
                "service": "mail-assistant",
                "mcp_path": APP.settings.streamable_http_path,
                "healthz": "/healthz",
            }
        )

    starlette_app.add_route("/", index, methods=["GET"])
    starlette_app.add_route("/healthz", healthz, methods=["GET"])
    starlette_app.add_route("/jobs/dispatch", dispatch_send_jobs, methods=["GET"])
    starlette_app.add_middleware(
        OAuthTokenLogMiddleware,
        token_context=CURRENT_ACCESS_TOKEN,
        token_resolver=(_oauth_provider.resolve_graph_access_token if _oauth_provider else None),
        require_bearer_token=(_oauth_provider is None),
    )
    return starlette_app

app = _build_asgi_app()

def main() -> None:
    import uvicorn

    config = uvicorn.Config(
        app,
        host=APP.settings.host,
        port=APP.settings.port,
        log_level=APP.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    main()
