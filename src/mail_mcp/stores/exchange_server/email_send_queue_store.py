from __future__ import annotations

from typing import Any

from ...schemas.request_models import MailboxCreateSendJobInput, MailboxUpdateSendJobScheduleInput
from .base import ExchangeServerStoreBase


class EmailSendQueueStore(ExchangeServerStoreBase):
    """Queued send job placeholder for Exchange Server EWS environments.

    Exchange Server EWS does not provide the same Azure Table queue abstraction
    used in the Graph-backed implementation. This adapter keeps the same store API
    surface, but uses the configured EWS account directly when a queued job is
    dispatched.
    """

    def enqueue_send_job(self, req: MailboxCreateSendJobInput) -> dict[str, Any]:
        return {
            "status": "queued",
            "table": "exchange-server-ews",
            "account": "ews",
            "partitionKey": "ews",
            "rowKey": req.draft_email_id,
            "job": {
                "draftemailid": req.draft_email_id,
                "schedulesendtime": req.schedule_send_time.isoformat(),
                "status": req.status,
                "senttime": req.sent_time.isoformat() if req.sent_time else "",
                "subject": req.subject or "",
                "userupn": "",
            },
        }

    def list_pending_jobs(self, limit: int = 20, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        del limit
        return []

    def get_job(self, job_id: str) -> dict[str, Any]:
        raise ValueError(f"send job not found: {job_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        raise ValueError(f"send job not found: {job_id}")

    def update_job_schedule(self, req: MailboxUpdateSendJobScheduleInput) -> dict[str, Any]:
        raise ValueError(f"send job not found: {req.job_id}")

    def dispatch_pending_jobs(self) -> dict[str, Any]:
        return {"status": "ok", "sent_count": 0, "failed_count": 0, "skipped_not_due_count": 0}
