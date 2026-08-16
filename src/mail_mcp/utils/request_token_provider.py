from __future__ import annotations

from typing import Callable

RESOLVED_GRAPH_TOKEN_STATE_KEY = "resolved_graph_access_token"


class RequestTokenProvider:
    """从当前 MCP 请求上下文解析 Bearer 令牌的辅助工具。"""

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
        """在当前请求上下文中解析令牌，若上下文不可用则返回 None。"""
        try:
            from mcp.server.lowlevel.server import request_ctx as mcp_request_ctx

            request_context = mcp_request_ctx.get()
        except Exception:
            return None

        request_obj = getattr(request_context, "request", None)
        if request_obj is None:
            return None

        resolved_token = RequestTokenProvider._resolve_token_from_request_state(request_obj)
        if resolved_token:
            return resolved_token

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
    def _resolve_token_from_request_state(request_obj) -> str | None:
        state = getattr(request_obj, "state", None)
        if state is None:
            return None

        token_value = getattr(state, RESOLVED_GRAPH_TOKEN_STATE_KEY, None)
        if isinstance(token_value, str):
            token_value = token_value.strip()
            return token_value or None
        return None

    @staticmethod
    def token_provider() -> str | None:
        # 仅使用当前 MCP 请求头中的 token，避免复用过期或错误的历史令牌。
        return RequestTokenProvider.current_request_token()

    @staticmethod
    def as_callable() -> Callable[[], str | None]:
        return RequestTokenProvider.token_provider
