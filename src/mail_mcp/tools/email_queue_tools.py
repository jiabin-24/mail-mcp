from __future__ import annotations

from ..schemas.request_models import (
    MailboxCreateSendJobInput,
    MailboxDraftIdInput,
    MailboxListSendJobsInput,
    MailboxSendJobIdInput,
    validate_input,
)
from ..stores.email_store import EmailStore
from ..stores.email_send_queue_store import EmailSendQueueStore


QUEUE_STORE_CONFIG_ERROR = (
    "Azure Table queue store is not configured. Set AZURE_STORAGE_ACCOUNT_NAME, "
    "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET."
)


def register_email_queue_tools(
    app,
    queue_store: EmailSendQueueStore | None,
    email_store: EmailStore,
) -> None:
    @app.tool()
    def mailbox_create_email_draft_send_job(
        draft_email_id: str,
        schedule_send_time: str,
        subject: str | None = None,
        status: str = "scheduled",
        sent_time: str | None = None,
    ) -> dict:
        """Create a scheduled send job in Azure Table Storage (EmailSendQueue)."""
        if queue_store is None:
            raise ValueError(QUEUE_STORE_CONFIG_ERROR)

        req = validate_input(
            MailboxCreateSendJobInput,
            {
                "draft_email_id": draft_email_id,
                "schedule_send_time": schedule_send_time,
                "subject": subject,
                "status": status,
                "sent_time": sent_time,
            },
        )
        return queue_store.enqueue_send_job(req)

    @app.tool()
    def mailbox_list_pending_email_draft_send_jobs(limit: int = 20) -> list[dict]:
        """List pending scheduled-send jobs for the current signed-in user."""
        if queue_store is None:
            raise ValueError(QUEUE_STORE_CONFIG_ERROR)

        req = validate_input(MailboxListSendJobsInput, {"limit": limit})
        return queue_store.list_pending_jobs(limit=req.limit)

    @app.tool()
    def mailbox_revoke_email_draft_send_job(job_id: str) -> dict:
        """Revoke a scheduled-send job and also delete its related draft email."""
        if queue_store is None:
            raise ValueError(QUEUE_STORE_CONFIG_ERROR)

        req = validate_input(MailboxSendJobIdInput, {"job_id": job_id})
        job = queue_store.get_job(req.job_id)

        status = str(job.get("status", "") or "").strip().lower()
        if status not in {"scheduled", "pending"}:
            raise ValueError(f"send job is not pending: {req.job_id}")

        draft_id = str(job.get("draftemailid", "") or "").strip()
        if not draft_id:
            raise ValueError(f"send job missing draft id: {req.job_id}")

        draft_req = validate_input(MailboxDraftIdInput, {"draft_id": draft_id})
        try:
            email_store.revoke_draft(draft_req)
        except ValueError:
            pass

        queue_store.delete_job(req.job_id)
        return {
            "status": "revoked",
            "job_id": req.job_id,
            "message": "scheduled send job revoked successfully",
        }
