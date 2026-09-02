from ..stores.exchange_online.email_store import EmailStore
from ..stores.attachment_store import AttachmentStore, build_attachment_upload_url
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


def _add_attachment_upload_url(result: dict) -> dict:
    draft_id = result.get("draft_id")
    if not draft_id:
        raise ValueError("created draft did not return an id")
    result["attachment_upload_url"] = build_attachment_upload_url(draft_id)
    return result


def register_email_tools(
    app,
    email_store: EmailStore,
    attachment_store: AttachmentStore,
) -> None:
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
    def mailbox_list_draft_attachments(message_id: str) -> list[dict] | dict:
        """List uploaded attachment names and links for the current draft."""
        req = validate_input(MailboxGetMessageInput, {"message_id": message_id})
        return attachment_store.list_message_attachments(req.message_id)

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
        return _add_attachment_upload_url(email_store.create_draft(req))

    @app.tool()
    @tool_exception_logging
    def mailbox_reply_compose(message_id: str, body: str, subject: str) -> dict:
        """Create a reply draft based on an existing message while preserving thread context."""
        req = validate_input(
            MailboxReplyComposeInput,
            {"message_id": message_id, "subject": subject, "body": body},
        )
        return _add_attachment_upload_url(email_store.create_reply_draft(req))

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
