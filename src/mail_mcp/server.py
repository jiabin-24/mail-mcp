from __future__ import annotations

import contextvars
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from .stores.calendar_store import CalendarStore
from .stores.email_store import EmailStore
from .stores.email_send_queue_store import EmailSendQueueStore
from .stores.graph_store import GraphStoreBase
from .tools.calendar_tools import register_calendar_tools
from .tools.email_tools import register_email_tools
from .tools.email_queue_tools import register_email_queue_tools
from .utils.biz_logger import configure_default_loggers
from .utils.oauth_middleware import OAuthTokenLogMiddleware

load_dotenv(override=False)
configure_default_loggers()

CURRENT_ACCESS_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_access_token", default=None
)
TOKEN_PROVIDER = CURRENT_ACCESS_TOKEN.get
EMAIL_STORE, CALENDAR_STORE, GRAPH_STORE = (EmailStore(token_provider=TOKEN_PROVIDER), CalendarStore(token_provider=TOKEN_PROVIDER), GraphStoreBase(token_provider=TOKEN_PROVIDER))
EMAIL_SEND_QUEUE_STORE = EmailSendQueueStore()
APP = FastMCP(
    "mail-assistant",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", os.getenv("PORT", "80"))),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)
register_calendar_tools(APP, CALENDAR_STORE)
register_email_tools(APP, EMAIL_STORE)
register_email_queue_tools(APP, EMAIL_SEND_QUEUE_STORE)


@APP.tool()
def mailbox_list_tenant_users(search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    """List tenant users and their mailbox addresses via Microsoft Graph /users."""
    return GRAPH_STORE.list_tenant_users(search=search, limit=limit)


@APP.tool()
def mailbox_get_user_time_zone() -> dict[str, str]:
    """Get current user's mailbox time zone."""
    return GRAPH_STORE.get_user_time_zone()

@APP.tool()
def ping() -> dict[str, str]:
    """Health check tool."""
    return {"status": "ok", "service": "mail-assistant"}

def _build_asgi_app():
    starlette_app = APP.streamable_http_app()

    def healthz(_request):
        return JSONResponse({"status": "ok", "service": "mail-assistant"})

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
    starlette_app.add_middleware(
        OAuthTokenLogMiddleware,
        token_context=CURRENT_ACCESS_TOKEN,
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
