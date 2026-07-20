from __future__ import annotations

from typing import Callable


class RequestTokenProvider:
    """Helpers for resolving bearer token from current MCP request context."""

    @staticmethod
    def extract_bearer_token(authorization: str) -> str | None:
        raw = authorization.strip()
        if not raw:
            return None
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        return raw or None

    @staticmethod
    def current_request_token() -> str | None:
        """Resolve token from the current MCP request context when available."""
        try:
            from mcp.server.lowlevel.server import request_ctx as mcp_request_ctx

            request_context = mcp_request_ctx.get()
        except Exception:
            return None

        request_obj = getattr(request_context, "request", None)
        if request_obj is None:
            return None

        headers = getattr(request_obj, "headers", None)
        if headers is None:
            return None

        try:
            authorization = headers.get("authorization", "")
        except Exception:
            return None

        if not authorization:
            return None

        return RequestTokenProvider.extract_bearer_token(str(authorization))

    @staticmethod
    def token_provider() -> str | None:
        # Only use token from current MCP request header to avoid stale token reuse.
        return RequestTokenProvider.current_request_token()

    @staticmethod
    def as_callable() -> Callable[[], str | None]:
        return RequestTokenProvider.token_provider
