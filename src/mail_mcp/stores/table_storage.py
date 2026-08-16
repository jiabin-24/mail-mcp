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
    """基于 Azure Table Storage 的通用 JSON 键值存储封装。"""

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
        """将 JSON 负载写入 Azure Table 中的指定实体。"""
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
        """按分区键和行键读取一个表实体；若不存在则返回 None。"""
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
        """读取并反序列化 JSON 字段内容。"""
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
        """返回未过期的实体；过期后会自动清理并返回 None。"""
        entity = self.get_entity(partition_key=partition_key, row_key=row_key)
        if entity is None:
            return None

        expires_epoch = _to_int(entity.get(expires_field))
        if expires_epoch is not None and expires_epoch >= 0 and expires_epoch <= _now_epoch():
            self.delete(partition_key=partition_key, row_key=row_key)
            return None

        return entity

    def delete(self, *, partition_key: str, row_key: str) -> None:
        """删除指定实体；若实体不存在则忽略。"""
        try:
            self._table_client.delete_entity(partition_key=partition_key, row_key=row_key)
        except ResourceNotFoundError:
            return

    def query_entities(self, *, query_filter: str, limit: int = 100) -> list[dict[str, Any]]:
        """按过滤条件查询实体，并限制返回数量。"""
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
        """清理某个分区内已过期的实体。

        返回已删除的记录数；这是尽力清理逻辑，会忽略并发删除竞争导致的异常。
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

    def delete_expired_entities_global(
        self,
        *,
        now_epoch: int,
        expires_field: str = "expiresepoch",
        limit: int = 200,
    ) -> int:
        """清理所有分区中已过期的实体。

        返回已删除的记录数；这是尽力清理逻辑，会忽略并发删除竞争导致的异常。
        """
        safe_limit = max(1, min(int(limit), 1000))
        filter_expr = f"{expires_field} ge 0 and {expires_field} le {int(now_epoch)}"

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
        """为过期清理构造 Azure Table 查询过滤条件。"""
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
        """从实体中提取分区键和行键。"""
        pk = str(entity.get("PartitionKey", "") or "")
        rk = str(entity.get("RowKey", "") or "")
        if not pk or not rk:
            return None
        return pk, rk

    def _delete_entity_if_exists(self, *, partition_key: str, row_key: str) -> bool:
        """删除实体；如果实体已存在被其他实例清理，则忽略错误。"""
        try:
            self._table_client.delete_entity(partition_key=partition_key, row_key=row_key)
            return True
        except ResourceNotFoundError:
            # 该实体可能已被其他实例先删除，忽略此并发冲突。
            return False


def build_table_context_from_env(table_name: str, *, optional: bool = False) -> AzureTableContext | None:
    """基于 AZURE_* 环境变量构建 Azure Table 客户端上下文。"""

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
        raise ValueError(f"缺少 Azure Table 环境变量: {', '.join(missing)}")

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
    """确保目标表存在；如果当前身份仅有读写权限而不能创建表，则忽略该异常。"""
    try:
        table_client.create_table()
    except ResourceExistsError:
        return
    except Exception:
        # 某些 Azure 身份仅允许读写已存在的表，而不能创建表。
        # 此时不应阻止启动；客户端仍可继续操作已有表，真正的失败会在
        # 实际访问时再暴露出来。
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
