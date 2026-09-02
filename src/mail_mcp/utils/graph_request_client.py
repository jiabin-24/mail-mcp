from __future__ import annotations

import logging
import os
from typing import Any, Callable

import httpx

from .token_log_utils import log_token_value

LOGGER = logging.getLogger("mail_mcp")


class GraphRequestClient:
    """负责 Microsoft Graph 请求认证、发送与错误处理。"""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

    def request(
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

        log_token_value(
            LOGGER,
            token,
            full_key="graph_request_token",
            preview_key="graph_request_token_preview",
        )
        LOGGER.info("Graph request: %s %s", method, path)

        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        with httpx.Client(base_url=self._graph_base, timeout=30.0) as client:
            response = client.request(method, path, headers=request_headers, json=json)

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(f"Graph API request failed ({response.status_code}): {body}")

        if not expect_json or not response.content:
            return {}
        return response.json()


__all__ = ["GraphRequestClient"]