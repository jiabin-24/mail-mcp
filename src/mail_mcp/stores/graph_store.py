from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from mail_mcp.utils.search_token_tools import expand_search_tokens

from ..models import map_graph_calendar_event, map_graph_message
from ..utils.token_log_utils import log_token_value

GRAPH_QUERY_SAFE = "()':,=-"
LOGGER = logging.getLogger("mail_mcp")

class GraphStoreBase:
    """封装 Microsoft Graph 通用访问逻辑，供邮件和日历等存储层复用。"""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        """初始化 Graph 客户端，并准备缓存与鉴权所需的上下文。"""
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
        # 统一缓存 TTL（秒），<=0 表示禁用写入缓存。
        self._cache_ttl = max(0, int(os.getenv("GRAPH_CACHE_TTL_SECONDS") or 300))
        # 进程内小缓存：减少重复查询当前用户时区与邮箱标识。
        self._cache: TTLCache[Any, Any] = TTLCache(maxsize=128, ttl=max(1, self._cache_ttl))

    @property
    def _mailbox_prefix(self) -> str:
        return "/me"

    def _normalize_limit(self, limit: int) -> int:
        """将查询上限规范化到合理范围，避免超出 Graph 限制。"""
        return max(1, min(limit, 100))

    def _folder_segment(self, folder: str | None) -> str:
        """Normalize mailbox folder names to Graph folder paths."""
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
        """Detect whether a message body should be sent as HTML or plain text."""
        text = (body or "").strip()
        if not text:
            return "Text"
        if "<" in text and ">" in text:
            return "HTML"
        return "Text"

    def _emails_to_recipients(self, emails: list[str] | None) -> list[dict[str, Any]]:
        """Convert plain email addresses into Graph recipient payloads."""
        recipients: list[dict[str, Any]] = []
        for email in emails or []:
            cleaned = str(email or "").strip()
            if cleaned:
                recipients.append({"emailAddress": {"address": cleaned}})
        return recipients

    def _emails_to_attendees(self, emails: list[str] | None) -> list[dict[str, Any]]:
        """Convert plain email addresses into Graph attendee payloads."""
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
        """Escape plain text and convert line breaks to HTML breaks."""
        content = str(text or "")
        escaped = (
            content.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return escaped.replace("\r\n", "\n").replace("\n", "<br/>")

    def _compose_online_meeting_body(self, description: str | None, existing_body_html: str | None) -> str:
        """Compose HTML meeting body while preserving any existing meeting text."""
        new_html = self._plain_text_to_html(description)
        if not new_html.strip():
            return existing_body_html or "<div></div>"

        combined = f"<div>{new_html}</div>"
        if (existing_body_html or "").strip():
            return f"{combined}<br/>{existing_body_html}"
        return combined

    def _event_path(self, event_id: str, calendar_id: str | None = None) -> str:
        """Build a Graph event path for a calendar or default mailbox calendar."""
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
        """Map Graph message payloads into the canonical response format."""
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
        """Map Graph calendar payloads into the canonical response format."""
        return [
            map_graph_calendar_event(event, mailbox_time_zone=mailbox_time_zone)
            for event in (events or [])
            if isinstance(event, dict)
        ]

    def _cache_scope_key(self) -> str:
        """基于当前访问令牌生成缓存作用域，避免用户之间的数据串用。"""
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            return "anonymous"
        # 仅使用 token 指纹作为作用域，避免不同调用者串缓存值。
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        """向 Microsoft Graph 发送统一的 HTTP 请求，并附带访问令牌与标准头信息。"""
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "No Outlook token available. Provide bearer token in Authorization header or set OUTLOOK_ACCESS_TOKEN."
            )

        log_token_value(
            LOGGER,
            token,
            full_key="graph_request_token",
            preview_key="graph_request_token_preview",
        )
        LOGGER.info("Graph request: %s %s", method, path)

        req_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        with httpx.Client(base_url=self._graph_base, timeout=30.0) as client:
            response = client.request(method, path, headers=req_headers, json=json)

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(f"Graph API request failed ({response.status_code}): {body}")

        if not expect_json:
            return {}
        if not response.content:
            return {}
        return response.json()

    def list_tenant_users(self, search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        """查询租户中的用户列表，可按关键字过滤并返回邮箱和 UPN 等字段。"""
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
        """读取当前用户邮箱的时区配置；若未配置则返回 fallback。"""
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
        """返回当前邮箱时区；如果未配置，则返回 None。"""
        time_zone_info = self.get_user_time_zone(fallback="")
        resolved = str(time_zone_info.get("time_zone", "") or "").strip()
        return resolved or None

    def resolve_current_user_upn(self) -> str:
        """解析当前登录用户的邮箱或 UPN，用于后续邮件、日历等操作中的用户识别。"""
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


def recipient_addresses(recipients: list[dict[str, Any]]) -> list[str]:
    """从 Graph 收件人列表中提取所有邮箱地址。"""
    result: list[str] = []
    for recipient in recipients:
        address = recipient_address(recipient)
        if address:
            result.append(address)
    return result


def recipient_address(recipient: dict[str, Any]) -> str:
    """从单个收件人对象中提取邮箱地址字符串。"""
    email_address = recipient.get("emailAddress", {}) if isinstance(recipient, dict) else {}
    return str(email_address.get("address", "") or "")