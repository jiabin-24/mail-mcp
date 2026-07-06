from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

import httpx
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import ClientSecretCredential

from ..schemas.request_models import MailboxCreateSendJobInput, MailboxUpdateSendJobScheduleInput
from .graph_store import GraphStoreBase


class EmailSendQueueStore(GraphStoreBase):
    """Persist scheduled email send jobs into Azure Table Storage."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        super().__init__(token_provider=token_provider)
        self._account_name = (os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
        self._table_name = (os.getenv("AZURE_STORAGE_TABLE_NAME") or "EmailSendQueue").strip()

        tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
        client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("AZURE_CLIENT_SECRET") or "").strip()
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

        if not self._account_name:
            raise ValueError("Missing AZURE_STORAGE_ACCOUNT_NAME for Azure Table Storage")
        if not tenant_id or not client_id or not client_secret:
            raise ValueError(
                "Missing Service Principal credentials. Required: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET"
            )

        account_url = f"https://{self._account_name}.table.core.windows.net"
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self._sp_credential = credential
        service_client = TableServiceClient(endpoint=account_url, credential=credential)
        self._table_client: TableClient = service_client.get_table_client(table_name=self._table_name)
        self._ensure_table_exists()

    def enqueue_send_job(self, req: MailboxCreateSendJobInput) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        row_key = uuid4().hex

        entity: dict[str, Any] = {
            "PartitionKey": user_upn,
            "RowKey": row_key,
            "draftemailid": req.draft_email_id,
            "schedulesendtime": _to_utc_iso(req.schedule_send_time, require_tz=True),
            "status": req.status,
            "senttime": _to_utc_iso(req.sent_time) if req.sent_time else "",
            "subject": req.subject or "",
            "userupn": user_upn,
            "createdtime": _to_utc_iso(datetime.now(tz=UTC)),
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

    def _ensure_table_exists(self) -> None:
        try:
            self._table_client.create_table()
        except ResourceExistsError:
            return

    def list_pending_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        user_upn = self.resolve_current_user_upn()
        safe_limit = max(1, min(limit, 100))
        escaped_upn = user_upn.replace("'", "''")
        query_filter = (
            f"PartitionKey eq '{escaped_upn}' and "
            "(status eq 'scheduled' or status eq 'pending' or status eq 'failed')"
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

    def delete_job(self, job_id: str) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        job = self.get_job(job_id)
        self._table_client.delete_entity(partition_key=user_upn, row_key=job_id)
        return job

    def update_job_schedule(self, req: MailboxUpdateSendJobScheduleInput) -> dict[str, Any]:
        user_upn = self.resolve_current_user_upn()
        job = self.get_job(req.job_id)

        status = str(job.get("status", "") or "").strip().lower()
        if status not in {"scheduled", "pending"}:
            raise ValueError(f"send job is not pending: {req.job_id}")

        self._table_client.update_entity(
            entity={
                "PartitionKey": user_upn,
                "RowKey": req.job_id,
                "schedulesendtime": _to_utc_iso(req.schedule_send_time, require_tz=True),
                "status": "scheduled",
                "senttime": "",
                "lasterror": "",
                "updatedtime": _to_utc_iso(datetime.now(tz=UTC)),
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

    def dispatch_pending_jobs(self) -> dict[str, Any]:
        query_filter = "(status eq 'scheduled' or status eq 'pending' or status eq 'failed')"
        entities = list(self._table_client.query_entities(query_filter=query_filter))

        now_utc = datetime.now(tz=UTC)
        sent_count = 0
        failed_count = 0
        skipped_not_due_count = 0
        sent: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        skipped_not_due: list[dict[str, str]] = []
        sent_time_utc = _to_utc_iso(datetime.now(tz=UTC))

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
            "updatedtime": _to_utc_iso(datetime.now(tz=UTC)),
        }
        self._table_client.update_entity(entity=entity, mode="merge")


def _to_utc_iso(value: datetime, require_tz: bool = False) -> str:
    if value.tzinfo is None:
        if require_tz:
            raise ValueError("schedule_send_time must include timezone offset (e.g. Z or +08:00)")
        else:
            value = value.replace(tzinfo=UTC)
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


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
