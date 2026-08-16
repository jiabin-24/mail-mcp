import logging
import os
import threading
import time
from pathlib import Path
from typing import cast

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse

from .environment_bootstrap import EnvironmentBootstrapper
from .stores.exchange_online.calendar_store import CalendarStore
from .stores.exchange_online.email_store import EmailStore
from .stores.exchange_online.email_send_queue_store import EmailSendQueueStore
from .stores.graph_store import GraphStoreBase
from .stores.oauth_client_store import build_oauth_client_store_from_env
from .stores.oauth_token_store import build_oauth_token_store_from_env
from .tools.calendar_tools import register_calendar_tools
from .tools.common_tools import register_common_tools
from .tools.email_tools import register_email_tools
from .tools.email_queue_tools import register_email_queue_tools
from .utils.oauth_dynamic_provider import DynamicOAuthProvider, get_dynamic_oauth_config_from_env
from .utils.request_token_provider import RequestTokenProvider
from .utils.biz_logger import configure_default_loggers
from .utils.oauth_middleware import OAuthTokenLogMiddleware

_ROOT_DIR = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("mail_mcp")


def _build_store_backend(token_provider):
    # 选择邮件后端：默认使用 Microsoft Graph，亦支持 Exchange Server EWS。
    # MAIL_MCP_BACKEND 仅允许取值 "graph" 或 "ews"，用于在启动时切换实现。
    backend = (os.getenv("MAIL_MCP_BACKEND") or "graph").strip().lower()
    LOGGER.info("mail backend selected: %s", backend)

    if backend == "ews":
        from .stores.exchange_server.calendar_store import CalendarStore as EwsCalendarStore
        from .stores.exchange_server.email_send_queue_store import EmailSendQueueStore as EwsEmailSendQueueStore
        from .stores.exchange_server.email_store import EmailStore as EwsEmailStore
        return (
            cast(EmailStore, EwsEmailStore(token_provider=token_provider)),
            cast(CalendarStore, EwsCalendarStore(token_provider=token_provider)),
            cast(EmailSendQueueStore, EwsEmailSendQueueStore(token_provider=token_provider)),
        )

    from .stores.exchange_online.calendar_store import CalendarStore as GraphCalendarStore
    from .stores.exchange_online.email_send_queue_store import EmailSendQueueStore as GraphEmailSendQueueStore
    from .stores.exchange_online.email_store import EmailStore as GraphEmailStore
    return (
        cast(EmailStore, GraphEmailStore(token_provider=token_provider)),
        cast(CalendarStore, GraphCalendarStore(token_provider=token_provider)),
        cast(EmailSendQueueStore, GraphEmailSendQueueStore(token_provider=token_provider)),
    )


EnvironmentBootstrapper(_ROOT_DIR).bootstrap()
configure_default_loggers()

# 统一构造 token provider 与后端存储实例，供邮件/日历/队列工具共用。
TOKEN_PROVIDER = RequestTokenProvider.as_callable()
EMAIL_STORE, CALENDAR_STORE, EMAIL_SEND_QUEUE_STORE = _build_store_backend(TOKEN_PROVIDER)
GRAPH_STORE = GraphStoreBase(token_provider=TOKEN_PROVIDER)

_oauth_provider: DynamicOAuthProvider | None = None
_auth_settings: AuthSettings | None = None
_oauth_client_store = None
_oauth_token_store = None
_oauth_config = get_dynamic_oauth_config_from_env()
# 仅当启用了动态 OAuth 配置时，才注册 OAuth 客户端/令牌存储与鉴权服务。
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
            valid_scopes=None,
            default_scopes=None,
            client_secret_expiry_seconds=365 * 24 * 3600,
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=None,
        service_documentation_url=cast(AnyHttpUrl, os.getenv("MCP_OAUTH_SERVICE_DOCUMENTATION_URL") or issuer_url),
    )


def _run_startup_token_cleanup_once() -> None:
    if _oauth_token_store is None:
        return
    try:
        now_epoch = int(time.time())
        token_cutoff_epoch = now_epoch - _oauth_token_store._STARTUP_CLEANUP_TOKEN_EXPIRED_AGE_SECONDS
        pending_and_code_cutoff_epoch = (
            now_epoch - _oauth_token_store._STARTUP_CLEANUP_PENDING_AND_CODE_EXPIRED_AGE_SECONDS
        )
        _oauth_token_store.cleanup_expired_scopes_before_until_clean(
            scopes=[
                "access_token",
                "refresh_token",
            ],
            cutoff_epoch=token_cutoff_epoch,
            limit=100,
        )
        _oauth_token_store.cleanup_expired_scopes_before_until_clean(
            scopes=[
                "pending_auth",
                "auth_code",
            ],
            cutoff_epoch=pending_and_code_cutoff_epoch,
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

register_calendar_tools(APP, CALENDAR_STORE)
register_common_tools(APP, GRAPH_STORE)
register_email_tools(APP, EMAIL_STORE)
register_email_queue_tools(APP, EMAIL_SEND_QUEUE_STORE, EMAIL_STORE)


@APP.tool()
def mailbox_list_tenant_users(search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    """List tenant users and their mailbox addresses via Microsoft Graph /users."""
    return GRAPH_STORE.list_tenant_users(search=search, limit=limit)


@APP.tool()
def mailbox_get_user_time_zone() -> dict[str, str]:
    """Get current user's mailbox time zone."""
    return GRAPH_STORE.get_user_time_zone()

# 运行时对外暴露的 MCP 工具入口，后续按具体存储实现注册邮件、日历和队列能力。
_AGENTS_MD_PATH = _ROOT_DIR / "AGENTS.md"
if (os.getenv("MCP_EXPOSE_AGENTS_MD", "false").strip().lower() == "true") and _AGENTS_MD_PATH.exists():

    @APP.tool()
    def mailbox_get_agents_md() -> dict[str, str | bool]:
        """Read repository AGENTS.md for external MCP clients."""
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
    oauth_provider = _oauth_provider

    @APP.custom_route("/oauth/callback", methods=["GET"])
    async def oauth_callback(request):
        params = dict(request.query_params.items())
        return await oauth_provider.build_callback_redirect(params)

def _build_asgi_app():
    # 通过 FastMCP 生成 ASGI app，并挂接额外的健康检查和任务调度入口。
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
