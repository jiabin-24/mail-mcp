from __future__ import annotations

import json
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull

from .table_storage import AzureTableContext, AzureTableJsonKV, build_table_context_from_env


class AzureTableOAuthClientStore:
    """Persist OAuth dynamic clients into Azure Table Storage."""

    _PARTITION_KEY = "oauth_clients"

    def __init__(self, context: AzureTableContext) -> None:
        self._kv = AzureTableJsonKV(context.table_client)

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        payload = self._kv.get_json(
            partition_key=self._PARTITION_KEY,
            row_key=client_id,
            payload_field="clientjson",
        )
        if payload is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(payload)
        except Exception:
            return None

    def upsert_client(self, client: OAuthClientInformationFull) -> None:
        payload: dict[str, Any] = client.model_dump(mode="json")
        self._kv.set_json(
            partition_key=self._PARTITION_KEY,
            row_key=client.client_id,
            payload=payload,
            payload_field="clientjson",
            expires_epoch=None,
        )

def build_oauth_client_store_from_env() -> AzureTableOAuthClientStore | None:
    """Build Azure Table-backed OAuth client store using existing AZURE_* settings."""

    context = build_table_context_from_env("OAuthClientRegistry", optional=True)
    if context is None:
        return None

    return AzureTableOAuthClientStore(context)
