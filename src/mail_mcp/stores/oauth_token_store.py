from __future__ import annotations

import os
from typing import Any

from .table_storage import AzureTableContext, AzureTableJsonKV, build_table_context_from_env


class AzureTableOAuthTokenStore:
    """Persist OAuth runtime token/state artifacts into Azure Table Storage."""

    _PENDING_AUTH_PARTITION = "pending_auth"
    _AUTH_CODE_PARTITION = "auth_code"
    _ACCESS_TOKEN_PARTITION = "access_token"
    _REFRESH_TOKEN_PARTITION = "refresh_token"

    def __init__(self, context: AzureTableContext) -> None:
        self._kv = AzureTableJsonKV(context.table_client)

    def upsert_pending_auth(self, state_id: str, payload: dict[str, Any], expires_at: float) -> None:
        self._upsert_entity(
            partition_key=self._PENDING_AUTH_PARTITION,
            row_key=state_id,
            payload=payload,
            external_payload=None,
            expires_at=expires_at,
        )

    def pop_pending_auth(self, state_id: str) -> dict[str, Any] | None:
        entity = self._kv.get_valid_entity(
            partition_key=self._PENDING_AUTH_PARTITION,
            row_key=state_id,
        )
        if entity is None:
            return None

        self._kv.delete(partition_key=self._PENDING_AUTH_PARTITION, row_key=state_id)
        return _entity_payload(entity)

    def upsert_authorization_code(
        self,
        code: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any],
        expires_at: float,
    ) -> None:
        self._upsert_entity(
            partition_key=self._AUTH_CODE_PARTITION,
            row_key=code,
            payload=payload,
            external_payload=external_payload,
            expires_at=expires_at,
        )

    def get_authorization_code(
        self,
        code: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        entity = self._kv.get_valid_entity(
            partition_key=self._AUTH_CODE_PARTITION,
            row_key=code,
        )
        if entity is None:
            return None

        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        return payload, external_payload

    def pop_authorization_code(
        self,
        code: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        entity = self._kv.get_valid_entity(
            partition_key=self._AUTH_CODE_PARTITION,
            row_key=code,
        )
        if entity is None:
            return None

        self._kv.delete(partition_key=self._AUTH_CODE_PARTITION, row_key=code)
        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        return payload, external_payload

    def upsert_access_token(
        self,
        token: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: int | None,
    ) -> None:
        self._upsert_entity(
            partition_key=self._ACCESS_TOKEN_PARTITION,
            row_key=token,
            payload=payload,
            external_payload=external_payload,
            expires_at=float(expires_at) if expires_at is not None else None,
        )

    def get_access_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        entity = self._kv.get_valid_entity(
            partition_key=self._ACCESS_TOKEN_PARTITION,
            row_key=token,
        )
        if entity is None:
            return None

        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        return payload, external_payload

    def delete_access_token(self, token: str) -> None:
        self._kv.delete(partition_key=self._ACCESS_TOKEN_PARTITION, row_key=token)

    def upsert_refresh_token(
        self,
        token: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: int | None,
    ) -> None:
        self._upsert_entity(
            partition_key=self._REFRESH_TOKEN_PARTITION,
            row_key=token,
            payload=payload,
            external_payload=external_payload,
            expires_at=float(expires_at) if expires_at is not None else None,
        )

    def get_refresh_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        entity = self._kv.get_valid_entity(
            partition_key=self._REFRESH_TOKEN_PARTITION,
            row_key=token,
        )
        if entity is None:
            return None

        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        return payload, external_payload

    def delete_refresh_token(self, token: str) -> None:
        self._kv.delete(partition_key=self._REFRESH_TOKEN_PARTITION, row_key=token)

    def _upsert_entity(
        self,
        *,
        partition_key: str,
        row_key: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: float | None,
    ) -> None:
        self._kv.set_json(
            partition_key=partition_key,
            row_key=row_key,
            payload=payload,
            payload_field="payloadjson",
            expires_epoch=int(expires_at) if expires_at is not None else None,
            extra_fields={
                "externaljson": _dumps_json(external_payload)
                if external_payload is not None
                else "",
            },
        )


def build_oauth_token_store_from_env() -> AzureTableOAuthTokenStore | None:
    """Build Azure Table-backed OAuth token store using existing AZURE_* settings."""

    table_name = (os.getenv("MCP_OAUTH_TOKEN_TABLE_NAME") or "OAuthTokenRegistry").strip()
    context = build_table_context_from_env(table_name, optional=True)
    if context is None:
        return None

    return AzureTableOAuthTokenStore(context)


def _entity_payload(entity: dict[str, Any]) -> dict[str, Any] | None:
    return _loads_json(entity.get("payloadjson"))


def _entity_external_payload(entity: dict[str, Any]) -> dict[str, Any] | None:
    return _loads_json(entity.get("externaljson"))


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


def _dumps_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)
