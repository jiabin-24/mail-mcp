from __future__ import annotations

import contextvars
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .store import MailStore


load_dotenv(override=False)


CURRENT_ACCESS_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_access_token", default=None
)
STORE = MailStore(token_provider=lambda: CURRENT_ACCESS_TOKEN.get())
APP = FastMCP(
    "mail-assistant",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", os.getenv("PORT", "80"))),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)
AUTH_LOGGER = logging.getLogger("mail_mcp.auth")


class OAuthTokenLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        authorization = request.headers.get("authorization", "")
        token_value: str | None = None
        if authorization:
            token = authorization
            if authorization.lower().startswith("bearer "):
                token = authorization[7:]
            token_preview = token[:12] + "..." if len(token) > 12 else token
            AUTH_LOGGER.info("delegated_token_preview=%s", token_preview)
            token_value = token

        token_ctx = CURRENT_ACCESS_TOKEN.set(token_value)
        try:
            return await call_next(request)
        finally:
            CURRENT_ACCESS_TOKEN.reset(token_ctx)


@APP.tool()
def ping() -> dict[str, str]:
    """Health check tool."""
    return {"status": "ok", "service": "mail-assistant"}


@APP.tool()
def mailbox_list_folders() -> list[str]:
    """List available mail folders."""
    return STORE.list_folders()


@APP.tool()
def mailbox_list_messages(folder: str = "inbox", limit: int = 20) -> list[dict]:
    """List messages from a folder."""
    return STORE.list_messages(folder=folder, limit=limit)


@APP.tool()
def mailbox_get_message(message_id: str) -> dict:
    """Get one message by ID."""
    message = STORE.get_message(message_id)
    if not message:
        raise ValueError(f"message not found: {message_id}")
    return message


@APP.tool()
def mailbox_search(
    search: str | None = None,
    filter: str | None = None,
    folder: str = "inbox",
    limit: int = 20,
) -> list[dict]:
    """Search messages with direct Graph $search/$filter passthrough."""
    return STORE.search_messages(search=search, filter=filter, folder=folder, limit=limit)


@APP.tool()
def mailbox_compose(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    """Create a draft message in Outlook mailbox."""
    if not to:
        raise ValueError("to cannot be empty")
    if not subject.strip():
        raise ValueError("subject cannot be empty")
    if not body.strip():
        raise ValueError("body cannot be empty")

    return STORE.create_draft(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
    )


@APP.tool()
def mailbox_reply_compose(message_id: str, body: str) -> dict:
    """Create a reply draft for an existing message while preserving thread context."""
    if not message_id.strip():
        raise ValueError("message_id cannot be empty")
    if not body.strip():
        raise ValueError("body cannot be empty")

    return STORE.create_reply_draft(message_id=message_id, body=body)


@APP.tool()
def mailbox_update_draft(
    draft_id: str,
    to: list[str] | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    """Update an existing draft message in Outlook mailbox."""
    if not draft_id.strip():
        raise ValueError("draft_id cannot be empty")

    updated = STORE.update_draft(
        draft_id=draft_id,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
    )
    if not updated:
        raise ValueError(f"draft not found: {draft_id}")
    return updated


@APP.tool()
def mailbox_send_draft(draft_id: str) -> dict:
    """Send an existing draft in Outlook mailbox."""
    sent = STORE.send_draft(draft_id=draft_id)
    if not sent:
        raise ValueError(f"draft not found: {draft_id}")
    return sent


@APP.tool()
def mailbox_revoke_draft(draft_id: str) -> dict:
    """Revoke (delete) an existing draft in Outlook mailbox."""
    revoked = STORE.revoke_draft(draft_id=draft_id)
    if not revoked:
        raise ValueError(f"draft not found: {draft_id}")
    return revoked


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
    starlette_app.add_middleware(OAuthTokenLogMiddleware)
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
