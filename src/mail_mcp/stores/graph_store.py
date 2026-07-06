from __future__ import annotations

import hashlib
import os
from typing import Any, Callable
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from mail_mcp.tools.search_token_tools import expand_search_tokens

GRAPH_QUERY_SAFE = "()':,=-"

class GraphStoreBase:
    """Shared Microsoft Graph client behavior for mailbox-backed stores."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
        # 统一缓存 TTL（秒），<=0 表示禁用写入缓存。
        self._cache_ttl = max(0, int(os.getenv("GRAPH_CACHE_TTL_SECONDS") or 300))
        # 进程内小缓存：减少重复查询当前用户时区与邮箱标识。
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=128, ttl=max(1, self._cache_ttl))

    @property
    def _mailbox_prefix(self) -> str:
        return "/me"

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(limit, 100))

    def _cache_scope_key(self) -> str:
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
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "No Outlook token available. Provide bearer token in Authorization header or set OUTLOOK_ACCESS_TOKEN."
            )

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
                        f"contains(displayName,'{escaped}') "
                        f"or contains(mail,'{escaped}') "
                        f"or contains(userPrincipalName,'{escaped}')"
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

    def resolve_current_user_upn(self) -> str:
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
    result: list[str] = []
    for recipient in recipients:
        address = recipient_address(recipient)
        if address:
            result.append(address)
    return result


def recipient_address(recipient: dict[str, Any]) -> str:
    email_address = recipient.get("emailAddress", {}) if isinstance(recipient, dict) else {}
    return str(email_address.get("address", "") or "")