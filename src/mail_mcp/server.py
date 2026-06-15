from __future__ import annotations

import contextvars
import logging
import os

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware

from .store import MailStore


CURRENT_ACCESS_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_access_token", default=None
)
STORE = MailStore(token_provider=lambda: CURRENT_ACCESS_TOKEN.get())
APP = FastMCP(
    "mail-assistant",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
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
def mailbox_search(query: str, folder: str = "inbox", limit: int = 20) -> list[dict]:
    """Search messages by keyword in sender/recipient/subject/body."""
    return STORE.search_messages(query=query, folder=folder, limit=limit)


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

    return STORE.create_draft(to=to, subject=subject, body=body, cc=cc, bcc=bcc)


@APP.tool()
def mailbox_reply_compose(message_id: str, body: str) -> dict:
    """Create a reply draft for an existing message while preserving thread context."""
    if not message_id.strip():
        raise ValueError("message_id cannot be empty")
    if not body.strip():
        raise ValueError("body cannot be empty")

    return STORE.create_reply_draft(message_id=message_id, body=body)


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


def main() -> None:
    import uvicorn

    starlette_app = APP.streamable_http_app()
    starlette_app.add_middleware(OAuthTokenLogMiddleware)

    config = uvicorn.Config(
        starlette_app,
        host=APP.settings.host,
        port=APP.settings.port,
        log_level=APP.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
