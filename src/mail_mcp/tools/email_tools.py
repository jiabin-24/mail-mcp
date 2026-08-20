from ..stores.exchange_online.email_store import EmailStore
from ..tools.tool_error_handler import tool_exception_logging
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

def register_email_tools(app, email_store: EmailStore) -> None:
    @app.tool()
    @tool_exception_logging
    def mailbox_list_folders() -> list[str]:
        """List all available folders in the current mailbox."""
        return email_store.list_folders()

    @app.tool()
    @tool_exception_logging
    def mailbox_list_messages(folder: str = "inbox", limit: int = 20) -> list[dict]:
        """List messages from a specific mailbox folder."""
        req = validate_input(
            MailboxListMessagesInput,
            {"folder": folder, "limit": limit},
        )
        return email_store.list_messages(req)

    @app.tool()
    @tool_exception_logging
    def mailbox_get_message(message_id: str) -> dict:
        """Retrieve a message by message ID."""
        req = validate_input(MailboxGetMessageInput, {"message_id": message_id})
        message = email_store.get_message(req)
        if not message:
            raise ValueError(f"message not found: {req.message_id}")
        return message

    @app.tool()
    @tool_exception_logging
    def mailbox_search(
        search: str | None = None,
        filter: str | None = None,
        orderby: str | None = None,
        folder: str = "inbox",
        limit: int = 20,
    ) -> list[dict]:
        """Search mailbox messages using Graph $search and/or $filter parameters."""
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
    @tool_exception_logging
    def mailbox_compose(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict:
        """Create a draft email in the Outlook mailbox."""
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
    @tool_exception_logging
    def mailbox_reply_compose(message_id: str, body: str, subject: str) -> dict:
        """Create a reply draft based on an existing message while preserving thread context."""
        req = validate_input(
            MailboxReplyComposeInput,
            {"message_id": message_id, "subject": subject, "body": body},
        )
        return email_store.create_reply_draft(req)

    @app.tool()
    @tool_exception_logging
    def mailbox_update_draft(
        draft_id: str,
        to: list[str] | None = None,
        subject: str | None = None,
        body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict:
        """Update an existing draft email in the Outlook mailbox."""
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
    @tool_exception_logging
    def mailbox_send_draft(draft_id: str) -> dict:
        """Send a draft email from the Outlook mailbox."""
        req = validate_input(MailboxDraftIdInput, {"draft_id": draft_id})
        sent = email_store.send_draft(req)
        if not sent:
            raise ValueError(f"draft not found: {req.draft_id}")
        return sent

    @app.tool()
    @tool_exception_logging
    def mailbox_revoke_draft(draft_id: str) -> dict:
        """Discard and delete a draft email from the Outlook mailbox."""
        req = validate_input(MailboxDraftIdInput, {"draft_id": draft_id})
        revoked = email_store.revoke_draft(req)
        if not revoked:
            raise ValueError(f"draft not found: {req.draft_id}")
        return revoked
