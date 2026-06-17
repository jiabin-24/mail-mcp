from __future__ import annotations

import base64
import contextvars
import logging
import os
from pathlib import PurePosixPath

from azure.storage.blob import BlobServiceClient, ContentSettings
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .store import MailStore


load_dotenv(override=False)


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
UPLOAD_LOGGER = logging.getLogger("mail_mcp.upload")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _blob_service_client() -> BlobServiceClient:
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
    account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY", "").strip()
    if not account_name:
        raise ValueError("AZURE_STORAGE_ACCOUNT_NAME is required")
    if not account_key:
        raise ValueError("AZURE_STORAGE_ACCOUNT_KEY is required")

    account_url = f"https://{account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=account_key)


def _normalize_mail_id(mail_id: str) -> str:
    normalized = mail_id.strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("mail_id cannot be empty")
    if ".." in normalized.split("/"):
        raise ValueError("mail_id contains invalid path segment")
    return normalized


def _normalize_filename(filename: str | None) -> str:
    value = (filename or "").strip()
    if not value:
        raise ValueError("file name cannot be empty")
    name = PurePosixPath(value.replace("\\", "/")).name.strip()
    if not name:
        raise ValueError("file name cannot be empty")
    return name


def _decode_content_base64(content_base64: str) -> bytes:
    value = (content_base64 or "").strip()
    if not value:
        raise ValueError("content_base64 cannot be empty")

    # Allow both raw base64 and data URL forms like data:image/png;base64,xxxx
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1].strip()

    try:
        return base64.b64decode(value, validate=True)
    except ValueError as ex:
        raise ValueError("content_base64 is not valid base64") from ex


async def upload_attachment_to_blob(request: Request) -> JSONResponse:
    container = os.getenv("AZURE_STORAGE_CONTAINER", "mail-attachments").strip() or "mail-attachments"

    UPLOAD_LOGGER.info("upload_attachment request started")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")

        mail_id = _normalize_mail_id(str(payload.get("mail_id", "")))
        filename = _normalize_filename(str(payload.get("filename", "")))
        UPLOAD_LOGGER.debug("upload_attachment parsing | mail_id=%s filename=%s", mail_id, filename)
        content_bytes = _decode_content_base64(str(payload.get("content_base64", "")))
        UPLOAD_LOGGER.debug("upload_attachment decoded | size_bytes=%d", len(content_bytes))
        if len(content_bytes) > MAX_UPLOAD_BYTES:
            UPLOAD_LOGGER.warning("upload_attachment size_limit_exceeded | size_bytes=%d max=%d", len(content_bytes), MAX_UPLOAD_BYTES)
            raise ValueError(f"file size exceeds {MAX_UPLOAD_BYTES} bytes limit")
        content_type = str(payload.get("content_type", "") or "").strip() or "application/octet-stream"
        blob_name = f"{mail_id}/{filename}"

        service = _blob_service_client()
        container_client = service.get_container_client(container)
        container_client.upload_blob(
            name=blob_name,
            data=content_bytes,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        blob_client = container_client.get_blob_client(blob_name)
        UPLOAD_LOGGER.info("upload_attachment success | mail_id=%s blob_name=%s container=%s size_bytes=%d", mail_id, blob_name, container, len(content_bytes))
    except ValueError as ex:
        UPLOAD_LOGGER.warning("upload_attachment validation_error | error=%s", str(ex))
        return JSONResponse({"error": str(ex)}, status_code=400)
    except Exception as ex:
        UPLOAD_LOGGER.exception("upload_attachment failed")
        return JSONResponse({"error": f"blob upload failed: {ex}"}, status_code=500)

    return JSONResponse(
        {
            "ok": True,
            "mail_id": mail_id,
            "container": container,
            "blob_name": blob_name,
            "blob_url": blob_client.url,
            "size_bytes": len(content_bytes),
        }
    )


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


def main() -> None:
    import uvicorn

    starlette_app = APP.streamable_http_app()
    starlette_app.add_route("/mail/upload-attachment", upload_attachment_to_blob, methods=["POST"])
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
