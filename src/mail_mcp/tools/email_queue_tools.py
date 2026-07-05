from __future__ import annotations

from ..schemas.request_models import MailboxCreateSendJobInput, validate_input
from ..stores.email_send_queue_store import EmailSendQueueStore


def register_email_queue_tools(app, queue_store: EmailSendQueueStore | None) -> None:
    @app.tool()
    def mailbox_create_email_draft_send_job(
        draft_email_id: str,
        schedule_send_time: str,
        user_upn: str,
        subject: str | None = None,
        status: str = "scheduled",
        sent_time: str | None = None,
    ) -> dict:
        """Create a scheduled send job in Azure Table Storage (EmailSendQueue)."""
        if queue_store is None:
            raise ValueError(
                "Azure Table queue store is not configured. Set AZURE_STORAGE_ACCOUNT_NAME, "
                "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET."
            )

        req = validate_input(
            MailboxCreateSendJobInput,
            {
                "draft_email_id": draft_email_id,
                "schedule_send_time": schedule_send_time,
                "user_upn": user_upn,
                "subject": subject,
                "status": status,
                "sent_time": sent_time,
            },
        )
        return queue_store.enqueue_send_job(req)
