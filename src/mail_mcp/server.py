from __future__ import annotations

import contextvars
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from .stores.calendar_store import CalendarStore
from .stores.email_store import EmailStore
from .tools.calendar_tools import register_calendar_tools
from .tools.email_tools import register_email_tools
from .utils.biz_logger import configure_default_loggers
from .utils.oauth_middleware import OAuthTokenLogMiddleware

load_dotenv(override=False)
configure_default_loggers()

CURRENT_ACCESS_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_access_token", default=None
)
EMAIL_STORE = EmailStore(token_provider=lambda: CURRENT_ACCESS_TOKEN.get())
CALENDAR_STORE = CalendarStore(token_provider=lambda: CURRENT_ACCESS_TOKEN.get())
APP = FastMCP(
    "mail-assistant",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", os.getenv("PORT", "80"))),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)
register_calendar_tools(APP, CALENDAR_STORE)
register_email_tools(APP, EMAIL_STORE)

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
