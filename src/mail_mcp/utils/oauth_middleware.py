from __future__ import annotations

import contextvars
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware

AUTH_LOGGER = logging.getLogger("mail_mcp.auth")

class OAuthTokenLogMiddleware(BaseHTTPMiddleware):
    """Capture delegated bearer token preview and store token in request context."""

    def __init__(
        self,
        app: Any,
        token_context: contextvars.ContextVar[str | None],
    ) -> None:
        super().__init__(app)
        self._token_context = token_context

    async def dispatch(self, request, call_next):
        authorization = request.headers.get("authorization", "")
        token_value: str | None = None
        if authorization:
            token = authorization
            if authorization.lower().startswith("bearer "):
                token = authorization[7:]
            token_preview = token[:12] + "..." if len(token) > 12 else token
            AUTH_LOGGER.info("delegated_token_preview=%s", token_preview)
            token_value = token

        token_ctx = self._token_context.set(token_value)
        try:
            return await call_next(request)
        finally:
            self._token_context.reset(token_ctx)
