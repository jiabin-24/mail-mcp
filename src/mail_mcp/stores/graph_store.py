from __future__ import annotations

import os
from typing import Any, Callable

import httpx


class GraphStoreBase:
    """Shared Microsoft Graph client behavior for mailbox-backed stores."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

    @property
    def _mailbox_prefix(self) -> str:
        return "/me"

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(limit, 100))

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
        query = (
            f"/users?$top={safe_limit}"
            "&$select=id,displayName,mail,userPrincipalName"
            "&$filter=mail ne null"
            "&$orderby=displayName"
        )

        search_value = (search or "").strip()
        if search_value:
            escaped = search_value.replace("'", "''")
            query = (
                f"/users?$top={safe_limit}"
                "&$select=id,displayName,mail,userPrincipalName"
                f"&$filter=mail ne null and (startswith(displayName,'{escaped}')"
                f" or startswith(mail,'{escaped}')"
                f" or startswith(userPrincipalName,'{escaped}'))"
                "&$orderby=displayName"
            )

        payload = self._request("GET", query)
        users = payload.get("value", [])
        return [
            {
                "id": str(user.get("id", "") or ""),
                "displayName": str(user.get("displayName", "") or ""),
                "mail": str(user.get("mail", "") or ""),
                "userPrincipalName": str(user.get("userPrincipalName", "") or ""),
            }
            for user in users
        ]


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