from __future__ import annotations

from ..schemas.request_models import (
    MailboxCreateSendJobInput,
    MailboxListSendJobsInput,
    MailboxSendJobIdInput,
    MailboxUpdateSendJobScheduleInput,
    validate_input,
)
from ..stores.email_store import EmailStore
from ..stores.email_send_queue_store import EmailSendQueueStore
from ..utils.datetime_utils import normalize_query_datetime_with_mailbox_timezone


QUEUE_STORE_CONFIG_ERROR = (
    "Azure Table queue store is not configured. Set AZURE_STORAGE_ACCOUNT_NAME, "
    "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET."
)


def _require_queue_store(queue_store: EmailSendQueueStore | None) -> EmailSendQueueStore:
    if queue_store is None:
        raise ValueError(QUEUE_STORE_CONFIG_ERROR)
    return queue_store


def _normalize_datetime_input_with_mailbox_time_zone(
    store: EmailSendQueueStore,
    value: str,
) -> str:
    mailbox_time_zone = store.get_mailbox_time_zone_if_available()
    return normalize_query_datetime_with_mailbox_timezone(value, mailbox_time_zone)


def register_email_queue_tools(
    app,
    queue_store: EmailSendQueueStore | None,
    _email_store: EmailStore,
) -> None:
    @app.tool()
    def mailbox_create_email_draft_send_job(
        draft_email_id: str,
        schedule_send_time: str,
        subject: str | None = None,
        status: str = "scheduled",
    ) -> dict:
        """Create a scheduled send job in Azure Table Storage (EmailSendQueue)."""
        store = _require_queue_store(queue_store)
        normalized_schedule_send_time = _normalize_datetime_input_with_mailbox_time_zone(
            store,
            schedule_send_time,
        )

        req = validate_input(
            MailboxCreateSendJobInput,
            {
                "draft_email_id": draft_email_id,
                "schedule_send_time": normalized_schedule_send_time,
                "subject": subject,
                "status": status,
            },
        )
        return store.enqueue_send_job(req)

    @app.tool()
    def mailbox_list_pending_email_draft_send_jobs(limit: int = 20) -> list[dict]:
        """List scheduled/failed send jobs for the current signed-in user."""
        store = _require_queue_store(queue_store)

        req = validate_input(MailboxListSendJobsInput, {"limit": limit})
        return store.list_pending_jobs(limit=req.limit)

    @app.tool()
    def mailbox_revoke_email_draft_send_job(job_id: str) -> dict:
        """Revoke a scheduled-send job by marking it as cancel in Azure Table Storage."""
        store = _require_queue_store(queue_store)

        req = validate_input(MailboxSendJobIdInput, {"job_id": job_id})
        job = store.get_job(req.job_id)

        status = str(job.get("status", "") or "").strip().lower()
        if status != "scheduled":
            raise ValueError(f"send job is not scheduled: {req.job_id}")

        updated_job = store.cancel_job(req.job_id)
        return {
            "status": "revoked",
            "job_id": req.job_id,
            "job": updated_job,
            "message": "scheduled send job marked as cancel successfully",
        }

    @app.tool()
    def mailbox_update_email_draft_send_job_schedule(
        job_id: str,
        schedule_send_time: str,
    ) -> dict:
        """Update schedule_send_time for one scheduled send job."""
        store = _require_queue_store(queue_store)
        normalized_schedule_send_time = _normalize_datetime_input_with_mailbox_time_zone(
            store,
            schedule_send_time,
        )

        req = validate_input(
            MailboxUpdateSendJobScheduleInput,
            {
                "job_id": job_id,
                "schedule_send_time": normalized_schedule_send_time,
            },
        )
        updated = store.update_job_schedule(req)
        return {
            "status": "updated",
            "job": updated,
        }
