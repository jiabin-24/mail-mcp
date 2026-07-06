from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import time
from typing import Any

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

AUTH_LOGGER = logging.getLogger("mail_mcp.auth")
TOKEN_LOG_MODE_ENV = "DELEGATED_TOKEN_LOG_MODE"
TOKEN_LOG_MODE_MASKED = "masked"
TOKEN_LOG_MODE_FULL = "full"
TOKEN_LOG_MODE_NONE = "none"
TOKEN_PREVIEW_LENGTH = 12
TOKEN_VALIDATION_ENV = "DELEGATED_TOKEN_VALIDATE"
TOKEN_VALIDATION_CACHE_TTL_ENV = "DELEGATED_TOKEN_CACHE_TTL_SECONDS"
DEFAULT_VALIDATION_CACHE_TTL_SECONDS = 300
GRAPH_BASE_URL_ENV = "GRAPH_BASE_URL"
GRAPH_DEFAULT_BASE_URL = "https://graph.microsoft.com/v1.0"

def _resolve_token_log_mode() -> str:
    mode = os.getenv(TOKEN_LOG_MODE_ENV, TOKEN_LOG_MODE_NONE).strip().lower()
    if mode in {TOKEN_LOG_MODE_MASKED, TOKEN_LOG_MODE_FULL, TOKEN_LOG_MODE_NONE}:
        return mode

    return TOKEN_LOG_MODE_NONE

def _extract_token(authorization: str) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return authorization


def _has_bearer_prefix(authorization: str) -> bool:
    return authorization.lower().startswith("bearer ")

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


def _should_validate_token() -> bool:
    value = os.getenv(TOKEN_VALIDATION_ENV, "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _token_cache_ttl_seconds() -> int:
    raw = os.getenv(TOKEN_VALIDATION_CACHE_TTL_ENV, str(DEFAULT_VALIDATION_CACHE_TTL_SECONDS)).strip()
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_VALIDATION_CACHE_TTL_SECONDS
    return max(0, parsed)

class OAuthTokenLogMiddleware(BaseHTTPMiddleware):
    """Capture delegated bearer token preview and store token in request context."""

    def __init__(
        self,
        app: Any,
        token_context: contextvars.ContextVar[str | None],
    ) -> None:
        super().__init__(app)
        self._token_context = token_context
        self._token_cache: dict[str, float] = {}
        self._graph_base = os.getenv(GRAPH_BASE_URL_ENV, GRAPH_DEFAULT_BASE_URL).rstrip("/")

    async def dispatch(self, request, call_next):
        path = request.url.path
        # 健康检查与首页放行，便于探活与基础可用性检查。
        if path in {"/", "/healthz", "/jobs/dispatch"}:
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        # 受保护接口必须携带 Bearer token。
        if not authorization or not _has_bearer_prefix(authorization):
            return JSONResponse({"error": "missing or invalid Authorization header"}, status_code=401)

        token_value: str | None = None
        if authorization:
            token_value = _extract_token(authorization)
            if not token_value.strip():
                return JSONResponse({"error": "empty bearer token"}, status_code=401)
            _log_token(token_value)

        if token_value and _should_validate_token():
            # 对传入 token 做有效性校验（缓存命中时不访问 Graph）。
            is_valid = await self._validate_token(token_value)
            if not is_valid:
                return JSONResponse({"error": "invalid or expired token"}, status_code=401)

        token_ctx = self._token_context.set(token_value)
        try:
            return await call_next(request)
        finally:
            self._token_context.reset(token_ctx)

    async def _validate_token(self, token: str) -> bool:
        ttl = _token_cache_ttl_seconds()
        now = time.time()
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # 进程内缓存：同一 token 在 TTL 内直接复用校验结果。
        if ttl > 0:
            expires_at = self._token_cache.get(key)
            if expires_at and expires_at > now:
                return True

        validate_url = f"{self._graph_base}/me?$select=id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        try:
            # 通过调用 Graph /me 验证 token 是否可用。
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(validate_url, headers=headers)
        except Exception:
            return False

        if response.status_code >= 400:
            return False

        if ttl > 0:
            # 仅缓存校验通过的 token，失败请求不进入缓存。
            self._token_cache[key] = now + ttl

        return True
