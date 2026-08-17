from __future__ import annotations

import logging
import os
from typing import Any, Callable

import httpx

from ..gateway_base import GatewayBase
from ...utils.token_log_utils import log_token_value

LOGGER = logging.getLogger("mail_mcp")


class GraphGateway(GatewayBase):
    """Microsoft Graph implementation for Exchange Online."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        super().__init__(token_provider=token_provider)

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


ExchangeOnlineGraphStore = GraphGateway

__all__ = ["GraphGateway", "ExchangeOnlineGraphStore"]
