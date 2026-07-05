from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import ClientSecretCredential

from ..schemas.request_models import MailboxCreateSendJobInput
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
            "(status eq 'scheduled' or status eq 'pending')"
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


def _to_utc_iso(value: datetime, require_tz: bool = False) -> str:
    if value.tzinfo is None:
        if require_tz:
            raise ValueError("schedule_send_time must include timezone offset (e.g. Z or +08:00)")
        else:
            value = value.replace(tzinfo=UTC)
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")
