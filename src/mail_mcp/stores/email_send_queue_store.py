from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

import httpx
from azure.core.exceptions import ResourceNotFoundError

from ..schemas.request_models import MailboxCreateSendJobInput, MailboxUpdateSendJobScheduleInput
from ..utils.datetime_utils import to_utc_iso_from_datetime
from .graph_store import GraphStoreBase
from .table_storage import build_table_context_from_env


class EmailSendQueueStore(GraphStoreBase):
    """Persist scheduled email send jobs into Azure Table Storage."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        super().__init__(token_provider=token_provider)
        self._table_name = (os.getenv("AZURE_STORAGE_TABLE_NAME") or "EmailSendQueue").strip()
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

        table_context = build_table_context_from_env(self._table_name)
        if table_context is None:
            raise ValueError("Azure Table context is unavailable")
        self._account_name = table_context.account_name
        self._sp_credential = table_context.credential
        self._table_client = table_context.table_client

    def enqueue_send_job(self, req: MailboxCreateSendJobInput) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        existing_scheduled_job = self._find_scheduled_job_for_draft(
            user_upn=user_upn,
            draft_email_id=req.draft_email_id,
        )
        if existing_scheduled_job is not None:
            return {
                "status": "no_change",
                "message": "scheduled send job already exists for this draft"
            }

        row_key = uuid4().hex
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()

        entity: dict[str, Any] = {
            "PartitionKey": user_upn,
            "RowKey": row_key,
            "draftemailid": req.draft_email_id,
            "schedulesendtime": to_utc_iso_from_datetime(
                req.schedule_send_time,
                mailbox_time_zone=mailbox_time_zone,
            ),
            "status": req.status,
            "senttime": (
                to_utc_iso_from_datetime(req.sent_time, mailbox_time_zone=mailbox_time_zone)
                if req.sent_time
                else ""
            ),
            "subject": req.subject or "",
            "userupn": user_upn,
            "createdtime": to_utc_iso_from_datetime(datetime.now(tz=UTC)),
        }

        self._table_client.create_entity(entity=entity)

        return {
            "status": "queued",
            "table": self._table_name,
            "account": self._account_name,
            "partitionKey": entity["PartitionKey"],
            "rowKey": entity["RowKey"],
            "job": {
                "draftemailid": entity["draftemailid"],
                "schedulesendtime": entity["schedulesendtime"],
                "status": entity["status"],
                "senttime": entity["senttime"],
                "subject": entity["subject"],
                "userupn": entity["userupn"],
            },
        }

    def list_pending_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        user_upn = self.resolve_current_user_upn()
        safe_limit = max(1, min(limit, 100))
        escaped_upn = user_upn.replace("'", "''")
        query_filter = (
            f"PartitionKey eq '{escaped_upn}' and "
            "(status eq 'scheduled' or status eq 'failed')"
        )

        results: list[dict[str, Any]] = []
        entities = self._table_client.query_entities(query_filter=query_filter)
        for entity in entities:
            results.append(self._map_entity(entity))
            if len(results) >= safe_limit:
                break
        return results

    def get_job(self, job_id: str) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        try:
            entity = self._table_client.get_entity(partition_key=user_upn, row_key=job_id)
        except ResourceNotFoundError as exc:
            raise ValueError(f"send job not found: {job_id}") from exc
        return self._map_entity(entity)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        self.get_job(job_id)

        self._table_client.update_entity(
            entity={
                "PartitionKey": user_upn,
                "RowKey": job_id,
                "status": "cancel",
                "updatedtime": to_utc_iso_from_datetime(datetime.now(tz=UTC)),
            },
            mode="merge",
        )

        return self.get_job(job_id)

    def update_job_schedule(self, req: MailboxUpdateSendJobScheduleInput) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        job = self.get_job(req.job_id)
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()

        status = str(job.get("status", "") or "").strip().lower()
        if status != "scheduled":
            raise ValueError(f"send job is not scheduled: {req.job_id}")

        self._table_client.update_entity(
            entity={
                "PartitionKey": user_upn,
                "RowKey": req.job_id,
                "schedulesendtime": to_utc_iso_from_datetime(
                    req.schedule_send_time,
                    mailbox_time_zone=mailbox_time_zone,
                ),
                "status": "scheduled",
                "senttime": "",
                "lasterror": "",
                "updatedtime": to_utc_iso_from_datetime(datetime.now(tz=UTC)),
            },
            mode="merge",
        )

        return self.get_job(req.job_id)

    def _map_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": str(entity.get("RowKey", "") or ""),
            "userupn": str(entity.get("userupn", "") or ""),
            "draftemailid": str(entity.get("draftemailid", "") or ""),
            "schedulesendtime": str(entity.get("schedulesendtime", "") or ""),
            "status": str(entity.get("status", "") or ""),
            "senttime": str(entity.get("senttime", "") or ""),
            "subject": str(entity.get("subject", "") or ""),
            "createdtime": str(entity.get("createdtime", "") or ""),
        }

    def _find_scheduled_job_for_draft(
        self,
        *,
        user_upn: str,
        draft_email_id: str,
    ) -> dict[str, Any] | None:
        escaped_upn = user_upn.replace("'", "''")
        escaped_draft_email_id = draft_email_id.replace("'", "''")
        query_filter = (
            f"PartitionKey eq '{escaped_upn}' and "
            f"draftemailid eq '{escaped_draft_email_id}' and "
            "status eq 'scheduled'"
        )

        for entity in self._table_client.query_entities(query_filter=query_filter):
            return self._map_entity(entity)
        return None

    def dispatch_pending_jobs(self) -> dict[str, Any]:
        query_filter = "(status eq 'scheduled' or status eq 'failed')"
        entities = list(self._table_client.query_entities(query_filter=query_filter))

        now_utc = datetime.now(tz=UTC)
        sent_count = 0
        failed_count = 0
        skipped_not_due_count = 0
        sent: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        skipped_not_due: list[dict[str, str]] = []
        sent_time_utc = to_utc_iso_from_datetime(datetime.now(tz=UTC))

        for entity in entities:
            partition_key = str(entity.get("PartitionKey", "") or "")
            row_key = str(entity.get("RowKey", "") or "")
            draft_id = str(entity.get("draftemailid", "") or "").strip()
            user_upn = str(entity.get("userupn", "") or partition_key).strip().lower()
            schedule_send_time = str(entity.get("schedulesendtime", "") or "").strip()

            due_time = _parse_utc_time(schedule_send_time)
            if due_time is None:
                continue

            if due_time > now_utc:
                skipped_not_due_count += 1
                skipped_not_due.append({"job_id": row_key, "schedulesendtime": schedule_send_time})
                continue

            if not draft_id or not user_upn:
                continue

            try:
                self._send_draft_as_service_principal(user_upn=user_upn, draft_email_id=draft_id)
                self._update_job_status(
                    partition_key=partition_key,
                    row_key=row_key,
                    status="sent",
                    sent_time=sent_time_utc,
                    last_error="",
                )
                sent_count += 1
                sent.append({"job_id": row_key, "draft_id": draft_id, "userupn": user_upn})
            except Exception as exc:
                failed_count += 1
                error = str(exc)
                failed.append({"job_id": row_key, "error": error})
                self._update_job_status(
                    partition_key=partition_key,
                    row_key=row_key,
                    status="failed",
                    sent_time="",
                    last_error=error,
                )

        return {
            "status": "ok",
            "processed": len(entities),
            "sent": sent_count,
            "failed": failed_count,
            "skipped_not_due": skipped_not_due_count,
            "sent_jobs": sent,
            "failed_jobs": failed,
            "skipped_not_due_jobs": skipped_not_due,
        }

    def _send_draft_as_service_principal(self, user_upn: str, draft_email_id: str) -> None:
        token = self._sp_credential.get_token("https://graph.microsoft.com/.default").token
        path = (
            f"/users/{quote(user_upn, safe='')}/messages/"
            f"{quote(draft_email_id, safe='')}/send"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        with httpx.Client(base_url=self._graph_base, timeout=30.0) as client:
            response = client.post(path, headers=headers)

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(f"Graph send failed ({response.status_code}): {body}")

    def _update_job_status(
        self,
        *,
        partition_key: str,
        row_key: str,
        status: str,
        sent_time: str,
        last_error: str,
    ) -> None:
        entity = {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            "status": status,
            "senttime": sent_time,
            "lasterror": last_error,
            "updatedtime": to_utc_iso_from_datetime(datetime.now(tz=UTC)),
        }
        self._table_client.update_entity(entity=entity, mode="merge")


def _parse_utc_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)
