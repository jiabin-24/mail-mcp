from __future__ import annotations

import os
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import ClientSecretCredential


@dataclass
class AzureTableContext:
    account_name: str
    table_name: str
    credential: ClientSecretCredential
    table_client: TableClient


class AzureTableJsonKV:
    """Reusable JSON key-value helper on top of Azure Table Storage."""

    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client

    def set_json(
        self,
        *,
        partition_key: str,
        row_key: str,
        payload: dict[str, Any],
        payload_field: str = "payloadjson",
        expires_epoch: int | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        entity: dict[str, Any] = {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            payload_field: _dumps_json(payload),
            "expiresepoch": expires_epoch if expires_epoch is not None else -1,
            "updatedtime": _to_utc_iso(datetime.now(tz=UTC)),
        }

        if extra_fields:
            entity.update(extra_fields)

        self._table_client.upsert_entity(entity=entity)

    def get_entity(self, *, partition_key: str, row_key: str) -> dict[str, Any] | None:
        try:
            return self._table_client.get_entity(partition_key=partition_key, row_key=row_key)
        except ResourceNotFoundError:
            return None

    def get_json(
        self,
        *,
        partition_key: str,
        row_key: str,
        payload_field: str = "payloadjson",
    ) -> dict[str, Any] | None:
        entity = self.get_entity(partition_key=partition_key, row_key=row_key)
        if entity is None:
            return None
        return _loads_json(entity.get(payload_field))

    def get_valid_entity(
        self,
        *,
        partition_key: str,
        row_key: str,
        expires_field: str = "expiresepoch",
    ) -> dict[str, Any] | None:
        entity = self.get_entity(partition_key=partition_key, row_key=row_key)
        if entity is None:
            return None

        expires_epoch = _to_int(entity.get(expires_field))
        if expires_epoch is not None and expires_epoch >= 0 and expires_epoch <= _now_epoch():
            self.delete(partition_key=partition_key, row_key=row_key)
            return None

        return entity

    def delete(self, *, partition_key: str, row_key: str) -> None:
        try:
            self._table_client.delete_entity(partition_key=partition_key, row_key=row_key)
        except ResourceNotFoundError:
            return

    def query_entities(self, *, query_filter: str, limit: int = 100) -> list[dict[str, Any]]:
        """Query entities with a bounded result size."""
        safe_limit = max(1, min(int(limit), 1000))
        items: list[dict[str, Any]] = []
        entities = self._table_client.query_entities(query_filter=query_filter, results_per_page=safe_limit)
        for entity in entities:
            items.append(dict(entity))
            if len(items) >= safe_limit:
                break
        return items

    def delete_expired_entities(
        self,
        *,
        partition_key: str,
        now_epoch: int,
        expires_field: str = "expiresepoch",
        equals_filters: dict[str, str] | None = None,
        limit: int = 200,
    ) -> int:
        """Delete expired entities in one partition.

        Returns number of deleted rows. This is best-effort cleanup and ignores
        concurrent delete races.
        """
        safe_limit = max(1, min(int(limit), 1000))
        filter_expr = self._build_expired_filter(
            partition_key=partition_key,
            now_epoch=int(now_epoch),
            expires_field=expires_field,
            equals_filters=equals_filters,
        )

        deleted = 0
        entities = self._table_client.query_entities(query_filter=filter_expr, results_per_page=safe_limit)
        for entity in entities:
            if deleted >= safe_limit:
                break
            keys = self._entity_keys(entity)
            if keys is None:
                continue
            pk, rk = keys
            if self._delete_entity_if_exists(partition_key=pk, row_key=rk):
                deleted += 1
        return deleted

    def _build_expired_filter(
        self,
        *,
        partition_key: str,
        now_epoch: int,
        expires_field: str,
        equals_filters: dict[str, str] | None,
    ) -> str:
        safe_partition = partition_key.replace("'", "''")
        filter_expr = (
            f"PartitionKey eq '{safe_partition}' "
            f"and {expires_field} ge 0 "
            f"and {expires_field} le {now_epoch}"
        )
        if not equals_filters:
            return filter_expr

        extra_parts: list[str] = []
        for field, value in equals_filters.items():
            field_name = str(field or "").strip()
            if not field_name:
                continue
            escaped = str(value or "").replace("'", "''")
            extra_parts.append(f"{field_name} eq '{escaped}'")

        if extra_parts:
            filter_expr += " and " + " and ".join(extra_parts)
        return filter_expr

    def _entity_keys(self, entity: dict[str, Any]) -> tuple[str, str] | None:
        pk = str(entity.get("PartitionKey", "") or "")
        rk = str(entity.get("RowKey", "") or "")
        if not pk or not rk:
            return None
        return pk, rk

    def _delete_entity_if_exists(self, *, partition_key: str, row_key: str) -> bool:
        try:
            self._table_client.delete_entity(partition_key=partition_key, row_key=row_key)
            return True
        except ResourceNotFoundError:
            # Entity may already be removed by another instance.
            return False


def build_table_context_from_env(table_name: str, *, optional: bool = False) -> AzureTableContext | None:
    """Build Azure Table client context from AZURE_* environment variables."""

    account_name = (os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
    tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
    client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("AZURE_CLIENT_SECRET") or "").strip()

    missing = [
        key
        for key, value in (
            ("AZURE_STORAGE_ACCOUNT_NAME", account_name),
            ("AZURE_TENANT_ID", tenant_id),
            ("AZURE_CLIENT_ID", client_id),
            ("AZURE_CLIENT_SECRET", client_secret),
        )
        if not value
    ]

    if missing:
        if optional:
            return None
        raise ValueError(f"Missing Azure Table env vars: {', '.join(missing)}")

    account_url = f"https://{account_name}.table.core.windows.net"
    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    service_client = TableServiceClient(endpoint=account_url, credential=credential)
    table_client = service_client.get_table_client(table_name=table_name)
    _ensure_table_exists(table_client)

    return AzureTableContext(
        account_name=account_name,
        table_name=table_name,
        credential=credential,
        table_client=table_client,
    )


def _ensure_table_exists(table_client: TableClient) -> None:
    try:
        table_client.create_table()
    except ResourceExistsError:
        return


def _dumps_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)


def _loads_json(value: Any) -> dict[str, Any] | None:
    import json

    text = str(value or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
    except Exception:
        return None

    if isinstance(obj, dict):
        return obj
    return None


def _to_utc_iso(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _now_epoch() -> int:
    import time

    return int(time.time())
