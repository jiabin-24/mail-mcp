from __future__ import annotations

from typing import Any, Callable

from ...utils.graph_request_client import GraphRequestClient
from ..gateway_base import GatewayBase


class GraphGateway(GatewayBase):
    """Microsoft Graph implementation for Exchange Online."""

    def __init__(
        self,
        token_provider: Callable[[], str | None],
        request_client: GraphRequestClient | None = None,
    ) -> None:
        super().__init__(token_provider=token_provider)
        self._request_client = request_client or GraphRequestClient(token_provider)

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        return self._request_client.request(
            method,
            path,
            json=json,
            headers=headers,
            expect_json=expect_json,
        )


ExchangeOnlineGraphStore = GraphGateway

__all__ = ["GraphGateway", "ExchangeOnlineGraphStore"]
