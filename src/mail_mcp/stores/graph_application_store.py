from __future__ import annotations

from typing import Any
from urllib.parse import quote

from azure.identity import ClientSecretCredential

from ..utils.graph_request_client import GraphRequestClient
from ..utils.search_token_tools import expand_search_tokens

GRAPH_QUERY_SAFE = "()':,=-"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphApplicationStore:
    """使用应用身份访问 Microsoft Graph API。"""

    def __init__(self, credential: ClientSecretCredential) -> None:
        self._credential = credential
        self._request_client = GraphRequestClient(self._get_access_token)

    def _get_access_token(self) -> str:
        return self._credential.get_token(GRAPH_SCOPE).token

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        return max(1, min(limit, 100))

    def list_tenant_users(self, search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        """按关键词列出租户中拥有邮箱地址的用户。"""
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
            payload = self._request_client.request("GET", query, headers=headers)
            return payload.get("value", [])

        search_value = (search or "").strip()
        tokens = [token for token in search_value.split() if token]
        if tokens:
            token_clauses: list[str] = []
            for token in expand_search_tokens(tokens):
                escaped = token.replace("'", "''")
                token_clauses.append(
                    "("
                    f"startswith(displayName,'{escaped}') "
                    f"or startswith(mail,'{escaped}') "
                    f"or startswith(userPrincipalName,'{escaped}')"
                    ")"
                )
            users = fetch_users("mail ne null and (" + " or ".join(token_clauses) + ")")
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


__all__ = ["GraphApplicationStore"]