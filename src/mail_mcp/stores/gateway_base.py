from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote

from cachetools import TTLCache
from mail_mcp.utils.search_token_tools import expand_search_tokens

from ..models import map_graph_calendar_event, map_graph_message
from ..utils.datetime_utils import parse_iso_datetime, resolve_zone_info
from ..utils.token_log_utils import log_token_value

GRAPH_QUERY_SAFE = "()':,=-"
LOGGER = logging.getLogger("mail_mcp")


class GatewayBase:
    """Shared Microsoft Graph gateway helpers for mailbox and calendar operations."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        """初始化网关基础能力（令牌提供器、Graph 地址与缓存配置）。"""
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
        self._cache_ttl = max(0, int(os.getenv("GRAPH_CACHE_TTL_SECONDS") or 300))
        self._cache: TTLCache[Any, Any] = TTLCache(maxsize=128, ttl=max(1, self._cache_ttl))

    @property
    def _mailbox_prefix(self) -> str:
        return "/me"

    def _normalize_limit(self, limit: int) -> int:
        """将查询条数限制在 Graph 允许范围内（1-100）。"""
        return max(1, min(limit, 100))

    def _folder_segment(self, folder: str | None) -> str:
        """将外部文件夹名称规范化为 Graph 可识别的路径片段。"""
        normalized = (folder or "").strip()
        if not normalized:
            return "inbox"

        mapping = {
            "inbox": "inbox",
            "sent": "sentitems",
            "sentitems": "sentitems",
            "drafts": "drafts",
            "archive": "archive",
            "deleteditems": "deleteditems",
            "deleted": "deleteditems",
            "junk": "junkemail",
            "junkemail": "junkemail",
        }
        lowered = normalized.lower()
        if lowered in mapping:
            return mapping[lowered]
        return quote(normalized, safe="")

    def _body_content_type(self, body: str | None) -> str:
        """根据正文内容判断邮件体类型（Text 或 HTML）。"""
        text = (body or "").strip()
        if not text:
            return "Text"
        if "<" in text and ">" in text:
            return "HTML"
        return "Text"

    def _emails_to_recipients(self, emails: list[str] | None) -> list[dict[str, Any]]:
        """将邮箱字符串列表转换为 Graph recipients 结构。"""
        recipients: list[dict[str, Any]] = []
        for email in emails or []:
            cleaned = str(email or "").strip()
            if cleaned:
                recipients.append({"emailAddress": {"address": cleaned}})
        return recipients

    def _emails_to_attendees(self, emails: list[str] | None) -> list[dict[str, Any]]:
        """将邮箱字符串列表转换为会议 attendees 结构。"""
        attendees: list[dict[str, Any]] = []
        for email in emails or []:
            cleaned = str(email or "").strip()
            if cleaned:
                attendees.append({
                    "type": "required",
                    "emailAddress": {"address": cleaned},
                })
        return attendees

    def _plain_text_to_html(self, text: str | None) -> str:
        """将纯文本安全转义为简单 HTML，并保留换行。"""
        content = str(text or "")
        escaped = (
            content.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return escaped.replace("\r\n", "\n").replace("\n", "<br/>")

    def _compose_online_meeting_body(self, description: str | None, existing_body_html: str | None) -> str:
        """生成会议正文：优先使用新描述，并在存在旧内容时进行拼接。"""
        new_html = self._plain_text_to_html(description)
        if not new_html.strip():
            return existing_body_html or "<div></div>"

        combined = f"<div>{new_html}</div>"
        if (existing_body_html or "").strip():
            return f"{combined}<br/>{existing_body_html}"
        return combined

    def _event_path(self, event_id: str, calendar_id: str | None = None) -> str:
        """构建会议事件资源路径，支持主日历与指定日历。"""
        encoded_event_id = quote(str(event_id or "").strip(), safe="")
        if calendar_id:
            return f"{self._mailbox_prefix}/calendars/{quote(str(calendar_id), safe='')}/events/{encoded_event_id}"
        return f"{self._mailbox_prefix}/events/{encoded_event_id}"

    def _map_messages(
        self,
        messages: list[dict[str, Any]] | None,
        *,
        folder: str | None = None,
        prefer_preview: bool = False,
        mailbox_time_zone: str | None = None,
    ) -> list[dict[str, Any]]:
        """将 Graph 原始邮件列表映射为统一输出结构。"""
        return [
            map_graph_message(
                message,
                folder=folder,
                prefer_preview=prefer_preview,
                mailbox_time_zone=mailbox_time_zone,
            )
            for message in (messages or [])
            if isinstance(message, dict)
        ]

    def _map_calendar_events(
        self,
        events: list[dict[str, Any]] | None,
        mailbox_time_zone: str | None = None,
    ) -> list[dict[str, Any]]:
        """将 Graph 原始日程列表映射为统一输出结构。"""
        return [
            map_graph_calendar_event(event, mailbox_time_zone=mailbox_time_zone)
            for event in (events or [])
            if isinstance(event, dict)
        ]

    def _cache_scope_key(self) -> str:
        """基于访问令牌生成缓存作用域键，避免跨用户缓存污染。"""
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            return "anonymous"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        """发送 Graph 请求的抽象方法，需由具体网关实现。"""
        raise NotImplementedError("A concrete gateway implementation must override _request().")

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        """解析 ISO 时间并统一转换为 UTC。"""
        dt = parse_iso_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def _parse_iso_datetime_with_time_zone(self, value: str, request_time_zone: str | None = None) -> datetime:
        """解析 ISO 时间并返回带时区的 datetime（不强制转 UTC）。

        - 输入已带时区：保持原始时区信息。
        - 输入无时区：按 request_time_zone -> mailbox time zone -> UTC 推断并附加 tzinfo。
        """
        dt = parse_iso_datetime(value)
        if dt.tzinfo is not None:
            return dt

        effective_time_zone = (
            (request_time_zone or "").strip()
            or (self.get_mailbox_time_zone_if_available() or "").strip()
            or "UTC"
        )
        zone = resolve_zone_info(effective_time_zone)
        if zone is None:
            raise ValueError(f"invalid time zone: {effective_time_zone}")
        return dt.replace(tzinfo=zone)

    def list_tenant_users(self, search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        """按关键词列出租户用户（仅返回含邮箱的用户）。"""
        safe_limit = self._normalize_limit(limit)
        headers = {"ConsistencyLevel": "eventual"}
        base_query_prefix = (
            f"/users?$top={safe_limit}"
            "&$count=true"
            "&$select=id,displayName,mail,userPrincipalName"
        )

        def fetch_users(filter_expr: str) -> list[dict[str, Any]]:
            query = (
                f"{base_query_prefix}"
                f"&$filter={quote(filter_expr, safe=GRAPH_QUERY_SAFE)}"
                "&$orderby=displayName"
            )
            payload = self._request("GET", query, headers=headers)
            return payload.get("value", [])

        search_value = (search or "").strip()
        if search_value:
            tokens = [token for token in search_value.split() if token]
            if tokens:
                expanded_tokens = expand_search_tokens(tokens)
                token_clauses: list[str] = []
                for token in expanded_tokens:
                    escaped = token.replace("'", "''")
                    token_clauses.append(
                        "("
                        f"startswith(displayName,'{escaped}') "
                        f"or startswith(mail,'{escaped}') "
                        f"or startswith(userPrincipalName,'{escaped}')"
                        ")"
                    )
                filter_expr = "mail ne null and (" + " or ".join(token_clauses) + ")"
                users = fetch_users(filter_expr)
            else:
                users = fetch_users("mail ne null")
        else:
            users = fetch_users("mail ne null")

        return [
            {
                "id": str(user.get("id", "") or ""),
                "displayName": str(user.get("displayName", "") or ""),
                "mail": str(user.get("mail", "") or ""),
                "userPrincipalName": str(user.get("userPrincipalName", "") or ""),
            }
            for user in users
        ]

    def get_user_time_zone(self, fallback: str = "UTC") -> dict[str, str]:
        """读取并缓存当前用户邮箱时区，失败时返回回退值。"""
        cache_key = f"{self._cache_scope_key()}:mailbox_time_zone"
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict):
            return cached

        try:
            payload = self._request(
                "GET",
                f"{self._mailbox_prefix}/mailboxSettings?$select=timeZone",
            )
        except ValueError:
            return {"time_zone": fallback, "source": "fallback"}

        resolved = str(payload.get("timeZone", "") or "").strip()
        if resolved:
            result = {"time_zone": resolved, "source": "mailboxSettings"}
            (self._cache.__setitem__(cache_key, result) if self._cache_ttl > 0 else self._cache.pop(cache_key, None))
            return result
        return {"time_zone": fallback, "source": "fallback"}

    def get_mailbox_time_zone_if_available(self) -> str | None:
        """获取邮箱时区；若不可用则返回 None。"""
        time_zone_info = self.get_user_time_zone(fallback="")
        resolved = str(time_zone_info.get("time_zone", "") or "").strip()
        return resolved or None

    def resolve_current_user_upn(self) -> str:
        """解析并缓存当前用户主邮箱地址（mail 或 UPN）。"""
        cache_key = f"{self._cache_scope_key()}:current_user_upn"
        cached = self._cache.get(cache_key)
        if isinstance(cached, str) and cached:
            return cached

        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}?$select=mail,userPrincipalName",
        )
        mail = str(payload.get("mail", "") or "").strip().lower()
        upn = str(payload.get("userPrincipalName", "") or "").strip().lower()
        resolved = mail or upn
        if not resolved:
            raise ValueError("Cannot resolve current user mailbox from token")
        (self._cache.__setitem__(cache_key, resolved) if self._cache_ttl > 0 else self._cache.pop(cache_key, None))
        return resolved
GraphStoreBase = GatewayBase

__all__ = ["GatewayBase", "GraphStoreBase"]
