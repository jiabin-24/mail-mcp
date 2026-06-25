from __future__ import annotations

import contextvars
import logging
import os
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware

AUTH_LOGGER = logging.getLogger("mail_mcp.auth")
TOKEN_LOG_MODE_ENV = "DELEGATED_TOKEN_LOG_MODE"
TOKEN_LOG_MODE_MASKED = "masked"
TOKEN_LOG_MODE_FULL = "full"
TOKEN_LOG_MODE_NONE = "none"
TOKEN_PREVIEW_LENGTH = 12

def _resolve_token_log_mode() -> str:
    mode = os.getenv(TOKEN_LOG_MODE_ENV, TOKEN_LOG_MODE_MASKED).strip().lower()
    if mode in {TOKEN_LOG_MODE_MASKED, TOKEN_LOG_MODE_FULL, TOKEN_LOG_MODE_NONE}:
        return mode

    return TOKEN_LOG_MODE_MASKED

def _extract_token(authorization: str) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return authorization

def _masked_token(token: str) -> str:
    if len(token) > TOKEN_PREVIEW_LENGTH:
        return token[:TOKEN_PREVIEW_LENGTH] + "..."
    return token

def _log_token(token: str) -> None:
    token_log_mode = _resolve_token_log_mode()
    if token_log_mode == TOKEN_LOG_MODE_FULL:
        AUTH_LOGGER.info("delegated_token=%s", token)
    elif token_log_mode == TOKEN_LOG_MODE_MASKED:
        AUTH_LOGGER.info("delegated_token_preview=%s", _masked_token(token))

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
            token_value = _extract_token(authorization)
            _log_token(token_value)

        token_ctx = self._token_context.set(token_value)
        try:
            return await call_next(request)
        finally:
            self._token_context.reset(token_ctx)
