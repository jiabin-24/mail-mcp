from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import ClientSecretCredential

from ..schemas.request_models import MailboxCreateSendJobInput


class EmailSendQueueStore:
    """Persist scheduled email send jobs into Azure Table Storage."""

    def __init__(self) -> None:
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
        user_upn = req.user_upn.strip().lower()
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


def _to_utc_iso(value: datetime, require_tz: bool = False) -> str:
    if value.tzinfo is None:
        if require_tz:
            raise ValueError("schedule_send_time must include timezone offset (e.g. Z or +08:00)")
        else:
            value = value.replace(tzinfo=UTC)
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")
