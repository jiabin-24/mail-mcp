from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from ..stores.email_store import EmailStore
from ..schemas.request_models import (
    MailboxComposeInput,
    MailboxDraftIdInput,
    MailboxGetMessageInput,
    MailboxListMessagesInput,
    MailboxReplyComposeInput,
    MailboxSearchInput,
    MailboxUpdateDraftInput,
    validate_input,
)


P = ParamSpec("P")
R = TypeVar("R")


def log_tool_result(func: Callable[P, R]) -> Callable[P, R]:
    """Print tool return payload to console for debugging."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        result = func(*args, **kwargs)
        try:
            payload = json.dumps(result, ensure_ascii=False, default=str, indent=2)
        except Exception:
            payload = repr(result)
        print(f"[tool:{func.__name__}] return=\n{payload}")
        return result

    return wrapper


def register_email_tools(app, email_store: EmailStore) -> None:
    @app.tool()
    def mailbox_list_folders() -> list[str]:
        """List available mail folders."""
        return email_store.list_folders()

    @app.tool()
    def mailbox_list_messages(folder: str = "inbox", limit: int = 20) -> list[dict]:
        """List messages from a folder."""
        req = validate_input(
            MailboxListMessagesInput,
            {"folder": folder, "limit": limit},
        )
        return email_store.list_messages(req)

    @app.tool()
    def mailbox_get_message(message_id: str) -> dict:
        """Get one message by ID."""
        req = validate_input(MailboxGetMessageInput, {"message_id": message_id})
        message = email_store.get_message(req)
        if not message:
            raise ValueError(f"message not found: {req.message_id}")
        return message

    @app.tool()
    @log_tool_result
    def mailbox_search(
        search: str | None = None,
        filter: str | None = None,
        orderby: str | None = None,
        folder: str = "inbox",
        limit: int = 20,
    ) -> list[dict]:
        """Search messages with direct Graph $search/$filter passthrough."""
        req = validate_input(
            MailboxSearchInput,
            {
                "search": search,
                "filter": filter,
                "orderby": orderby,
                "folder": folder,
                "limit": limit,
            },
        )
        return email_store.search_messages(req)

    @app.tool()
    def mailbox_compose(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict:
        """Create a draft message in Outlook mailbox."""
        req = validate_input(
            MailboxComposeInput,
            {
                "to": to,
                "subject": subject,
                "body": body,
                "cc": cc,
                "bcc": bcc,
            },
        )
        return email_store.create_draft(req)

    @app.tool()
    def mailbox_reply_compose(message_id: str, body: str) -> dict:
        """Create a reply draft for an existing message while preserving thread context."""
        req = validate_input(
            MailboxReplyComposeInput,
            {"message_id": message_id, "body": body},
        )
        return email_store.create_reply_draft(req)

    @app.tool()
    def mailbox_update_draft(
        draft_id: str,
        to: list[str] | None = None,
        subject: str | None = None,
        body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict:
        """Update an existing draft message in Outlook mailbox."""
        req = validate_input(
            MailboxUpdateDraftInput,
            {
                "draft_id": draft_id,
                "to": to,
                "subject": subject,
                "body": body,
                "cc": cc,
                "bcc": bcc,
            },
        )
        updated = email_store.update_draft(req)
        if not updated:
            raise ValueError(f"draft not found: {req.draft_id}")
        return updated

    @app.tool()
    def mailbox_send_draft(draft_id: str) -> dict:
        """Send an existing draft in Outlook mailbox."""
        req = validate_input(MailboxDraftIdInput, {"draft_id": draft_id})
        sent = email_store.send_draft(req)
        if not sent:
            raise ValueError(f"draft not found: {req.draft_id}")
        return sent

    @app.tool()
    def mailbox_revoke_draft(draft_id: str) -> dict:
        """Revoke (delete) an existing draft in Outlook mailbox."""
        req = validate_input(MailboxDraftIdInput, {"draft_id": draft_id})
        revoked = email_store.revoke_draft(req)
        if not revoked:
            raise ValueError(f"draft not found: {req.draft_id}")
        return revoked
