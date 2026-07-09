from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from mcp.shared.auth import OAuthClientInformationFull

from .table_storage import AzureTableContext, build_table_context_from_env


class AzureTableOAuthClientStore:
    """Persist OAuth dynamic clients into Azure Table Storage."""

    _PARTITION_KEY = "oauth_clients"

    def __init__(self, context: AzureTableContext) -> None:
        self._table_client = context.table_client

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        try:
            entity = self._table_client.get_entity(
                partition_key=self._PARTITION_KEY,
                row_key=client_id,
            )
        except ResourceNotFoundError:
            return None

        payload = str(entity.get("clientjson", "") or "").strip()
        if not payload:
            return None

        try:
            data: dict[str, Any] = json.loads(payload)
            return OAuthClientInformationFull.model_validate(data)
        except Exception:
            return None

    def upsert_client(self, client: OAuthClientInformationFull) -> None:
        entity = {
            "PartitionKey": self._PARTITION_KEY,
            "RowKey": client.client_id,
            "clientjson": json.dumps(client.model_dump(mode="json"), ensure_ascii=True),
            "updatedtime": _to_utc_iso(datetime.now(tz=UTC)),
        }
        self._table_client.upsert_entity(entity=entity)

def build_oauth_client_store_from_env() -> AzureTableOAuthClientStore | None:
    """Build Azure Table-backed OAuth client store using existing AZURE_* settings."""

    context = build_table_context_from_env("OAuthClientRegistry", optional=True)
    if context is None:
        return None

    return AzureTableOAuthClientStore(context)


def _to_utc_iso(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")
