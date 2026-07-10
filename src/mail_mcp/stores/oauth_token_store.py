from __future__ import annotations

import hashlib
import json
import os
import time
import base64
import threading
from typing import Any

from .table_storage import AzureTableContext, AzureTableJsonKV, build_table_context_from_env


class AzureTableOAuthTokenStore:
    """Persist OAuth runtime token/state artifacts into Azure Table Storage."""

    _PENDING_AUTH_PARTITION = "pending_auth"
    _AUTH_CODE_PARTITION = "auth_code"
    _ACCESS_TOKEN_PARTITION = "access_token"
    # token -> 实际分区 的二级索引（保持按 token 查找接口不变）
    _ACCESS_TOKEN_INDEX_PARTITION = "access_token_index"
    # refresh token 的二级索引：token(rowkey) -> 实际分区
    _REFRESH_TOKEN_INDEX_PARTITION = "refresh_token_index"
    _REFRESH_TOKEN_PARTITION = "refresh_token"
    _CLEANUP_MIN_INTERVAL_SECONDS = 3600

    def __init__(self, context: AzureTableContext) -> None:
        self._kv = AzureTableJsonKV(context.table_client)
        # 清理任务后台串行执行，避免并发触发造成重复扫描。
        self._cleanup_lock = threading.Lock()
        self._cleanup_running = False
        # 固定窗口节流：默认 1 小时内最多触发一次清理。
        self._cleanup_window_seconds = self._CLEANUP_MIN_INTERVAL_SECONDS
        self._last_cleanup_started_at = 0.0

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
        # access token 主存储按 client + account 分区，避免全分区扫描。
        client_id = str(payload.get("client_id", "") or "").strip()
        client_key = _hash_text(client_id)
        account_key = _derive_account_key(external_payload)
        row_key = _token_row_key(token)
        scoped_partition = _access_token_partition(client_key, account_key)
        stored_payload = dict(payload)
        # 表内不落明文 token，只存 sha 行键。
        stored_payload["token"] = row_key

        # 仅清理同 client/account 下过期数据，避免误删其他账号。
        self.cleanup_expired_access_tokens(client_key=client_key, account_key=account_key)
        self._upsert_entity(
            partition_key=scoped_partition,
            row_key=row_key,
            payload=stored_payload,
            external_payload=external_payload,
            expires_at=float(expires_at) if expires_at is not None else None,
            extra_fields={
                "clientid": client_key,
                "accountkey": account_key,
            },
        )
        # 写入 token 索引：读取/删除时可先按 token 反查真实分区。
        self._kv.set_json(
            partition_key=self._ACCESS_TOKEN_INDEX_PARTITION,
            row_key=row_key,
            payload={"partitionkey": scoped_partition},
            payload_field="payloadjson",
            expires_epoch=expires_at,
        )
        # 每次 access token 生成/更新后，异步触发一次批量过期清理（按需、非阻塞）。
        self._schedule_async_cleanup(limit=100)

    def get_access_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        row_key = _token_row_key(token)
        # 先通过索引定位分区，再走分区+行键精确读取。
        scoped_partition = self._resolve_access_partition_by_token_rowkey(row_key)
        if not scoped_partition:
            return None

        entity = self._kv.get_valid_entity(
            partition_key=scoped_partition,
            row_key=row_key,
        )
        if entity is None:
            return None

        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        payload = dict(payload)
        # 内存态恢复明文 token，兼容上层模型校验逻辑。
        payload["token"] = token
        return payload, external_payload

    def delete_access_token(self, token: str) -> None:
        row_key = _token_row_key(token)
        # 先删主数据，再删索引，保持两者一致。
        scoped_partition = self._resolve_access_partition_by_token_rowkey(row_key)
        if scoped_partition:
            self._kv.delete(partition_key=scoped_partition, row_key=row_key)
        self._kv.delete(partition_key=self._ACCESS_TOKEN_INDEX_PARTITION, row_key=row_key)

    def cleanup_expired_access_tokens(
        self,
        *,
        client_key: str,
        account_key: str,
        limit: int = 200,
    ) -> int:
        if not client_key or not account_key:
            return 0
        return self._kv.delete_expired_entities(
            partition_key=_access_token_partition(client_key, account_key),
            now_epoch=int(time.time()),
            limit=limit,
        )

    def _resolve_access_partition_by_token_rowkey(self, row_key: str) -> str | None:
        index_entity = self._kv.get_valid_entity(
            partition_key=self._ACCESS_TOKEN_INDEX_PARTITION,
            row_key=row_key,
        )
        if index_entity is None:
            return None

        index_payload = _entity_payload(index_entity)
        if not index_payload:
            return None

        value = str(index_payload.get("partitionkey", "") or "").strip()
        return value or None

    def upsert_refresh_token(
        self,
        token: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: int | None,
    ) -> None:
        # refresh token 与 access token 一样按 client/account 分区。
        client_id = str(payload.get("client_id", "") or "").strip()
        client_key = _hash_text(client_id)
        account_key = _derive_account_key(external_payload)
        row_key = _token_row_key(token)
        scoped_partition = _refresh_token_partition(client_key, account_key)
        stored_payload = dict(payload)
        # 表内不落明文 token，只存 sha 行键。
        stored_payload["token"] = row_key

        # 仅清理同 client/account 下过期 refresh token。
        self._kv.delete_expired_entities(
            partition_key=scoped_partition,
            now_epoch=int(time.time()),
            limit=200,
        )
        self._upsert_entity(
            partition_key=scoped_partition,
            row_key=row_key,
            payload=stored_payload,
            external_payload=external_payload,
            expires_at=float(expires_at) if expires_at is not None else None,
            extra_fields={
                "clientid": client_key,
                "accountkey": account_key,
            },
        )
        # 写入 refresh token 索引：读取/删除时按 token 反查分区。
        self._kv.set_json(
            partition_key=self._REFRESH_TOKEN_INDEX_PARTITION,
            row_key=row_key,
            payload={"partitionkey": scoped_partition},
            payload_field="payloadjson",
            expires_epoch=expires_at,
        )

    def get_refresh_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        row_key = _token_row_key(token)
        scoped_partition = self._resolve_refresh_partition_by_token_rowkey(row_key)
        if not scoped_partition:
            return None

        entity = self._kv.get_valid_entity(
            partition_key=scoped_partition,
            row_key=row_key,
        )
        if entity is None:
            return None

        payload = _entity_payload(entity)
        external_payload = _entity_external_payload(entity)
        if payload is None:
            return None
        payload = dict(payload)
        # 内存态恢复明文 token，兼容上层模型校验逻辑。
        payload["token"] = token
        return payload, external_payload

    def delete_refresh_token(self, token: str) -> None:
        row_key = _token_row_key(token)
        scoped_partition = self._resolve_refresh_partition_by_token_rowkey(row_key)
        if scoped_partition:
            self._kv.delete(partition_key=scoped_partition, row_key=row_key)
        self._kv.delete(partition_key=self._REFRESH_TOKEN_INDEX_PARTITION, row_key=row_key)

    def _resolve_refresh_partition_by_token_rowkey(self, row_key: str) -> str | None:
        index_entity = self._kv.get_valid_entity(
            partition_key=self._REFRESH_TOKEN_INDEX_PARTITION,
            row_key=row_key,
        )
        if index_entity is None:
            return None

        index_payload = _entity_payload(index_entity)
        if not index_payload:
            return None

        value = str(index_payload.get("partitionkey", "") or "").strip()
        return value or None

    def cleanup_expired_access_and_refresh_tokens(self, *, limit: int = 100) -> dict[str, int]:
        """On-demand cleanup for expired access/refresh tokens.

        Each token type is cleaned in a bounded batch (max 100 per run by default).
        """
        safe_limit = max(1, min(int(limit), 100))
        now_epoch = int(time.time())

        access_deleted = self._cleanup_expired_from_index_partition(
            index_partition=self._ACCESS_TOKEN_INDEX_PARTITION,
            now_epoch=now_epoch,
            limit=safe_limit,
        )
        refresh_deleted = self._cleanup_expired_from_index_partition(
            index_partition=self._REFRESH_TOKEN_INDEX_PARTITION,
            now_epoch=now_epoch,
            limit=safe_limit,
        )

        return {
            "access_token_deleted": access_deleted,
            "refresh_token_deleted": refresh_deleted,
            "limit_per_type": safe_limit,
        }

    def _schedule_async_cleanup(self, *, limit: int = 100) -> None:
        with self._cleanup_lock:
            now = time.time()
            if self._cleanup_running:
                return
            if (
                self._cleanup_window_seconds > 0
                and self._last_cleanup_started_at > 0
                and (now - self._last_cleanup_started_at) < self._cleanup_window_seconds
            ):
                return
            self._cleanup_running = True
            self._last_cleanup_started_at = now

        worker = threading.Thread(
            target=self._run_cleanup_worker,
            kwargs={"limit": limit},
            daemon=True,
            name="oauth-token-cleanup",
        )
        worker.start()

    def _run_cleanup_worker(self, *, limit: int) -> None:
        try:
            self.cleanup_expired_access_and_refresh_tokens(limit=limit)
        finally:
            with self._cleanup_lock:
                self._cleanup_running = False

    def _cleanup_expired_from_index_partition(
        self,
        *,
        index_partition: str,
        now_epoch: int,
        limit: int,
    ) -> int:
        safe_partition = index_partition.replace("'", "''")
        filter_expr = (
            f"PartitionKey eq '{safe_partition}' "
            f"and expiresepoch ge 0 "
            f"and expiresepoch le {int(now_epoch)}"
        )
        expired_index_rows = self._kv.query_entities(query_filter=filter_expr, limit=limit)

        deleted = 0
        for index_entity in expired_index_rows:
            row_key = str(index_entity.get("RowKey", "") or "").strip()
            if not row_key:
                continue

            index_payload = _entity_payload(index_entity) or {}
            target_partition = str(index_payload.get("partitionkey", "") or "").strip()
            if target_partition:
                self._kv.delete(partition_key=target_partition, row_key=row_key)

            self._kv.delete(partition_key=index_partition, row_key=row_key)
            deleted += 1

        return deleted

    def _upsert_entity(
        self,
        *,
        partition_key: str,
        row_key: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: float | None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        merged_extra: dict[str, Any] = {
            "externaljson": _dumps_json(external_payload)
            if external_payload is not None
            else "",
        }
        if extra_fields:
            merged_extra.update(extra_fields)

        self._kv.set_json(
            partition_key=partition_key,
            row_key=row_key,
            payload=payload,
            payload_field="payloadjson",
            expires_epoch=int(expires_at) if expires_at is not None else None,
            extra_fields=merged_extra,
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


def _token_row_key(token: str) -> str:
    normalized = str(token or "")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _access_token_partition(client_key: str, account_key: str) -> str:
    # 分区键设计：access_token|clientkey|accountkey（均为哈希值）
    normalized_client_id = str(client_key or "").strip() or "unknown_client"
    normalized_account_key = str(account_key or "").strip() or "unknown_account"
    return f"access_token|{normalized_client_id}|{normalized_account_key}"


def _refresh_token_partition(client_key: str, account_key: str) -> str:
    normalized_client_id = str(client_key or "").strip() or "unknown_client"
    normalized_account_key = str(account_key or "").strip() or "unknown_account"
    return f"refresh_token|{normalized_client_id}|{normalized_account_key}"


def _hash_text(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _derive_account_key(external_payload: dict[str, Any] | None) -> str:
    if not external_payload:
        return ""

    token = str(external_payload.get("graph_access_token", "") or "").strip()
    if not token:
        return ""

    claims = _decode_jwt_claims_without_verify(token)
    if not claims:
        return ""

    # 账号唯一性优先级：oid > preferred_username/upn > sub
    account_raw = (
        str(claims.get("oid", "") or "").strip()
        or str(claims.get("preferred_username", "") or "").strip().lower()
        or str(claims.get("upn", "") or "").strip().lower()
        or str(claims.get("sub", "") or "").strip()
    )
    if not account_raw:
        return ""
    return hashlib.sha256(account_raw.encode("utf-8")).hexdigest()


def _decode_jwt_claims_without_verify(token: str) -> dict[str, Any] | None:
    # 仅用于提取分区维度，不做签名校验。
    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload_part = parts[1]
    padding = "=" * (-len(payload_part) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_part + padding)
        obj = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None

    if isinstance(obj, dict):
        return obj
    return None
