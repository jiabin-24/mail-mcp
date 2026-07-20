from __future__ import annotations

import hashlib
import inspect
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .token_log_utils import log_token_value

AUTH_LOGGER = logging.getLogger("mail_mcp.auth")
TOKEN_VALIDATION_CACHE_TTL_ENV = "DELEGATED_TOKEN_CACHE_TTL_SECONDS"
DEFAULT_VALIDATION_CACHE_TTL_SECONDS = 300
GRAPH_BASE_URL_ENV = "GRAPH_BASE_URL"
GRAPH_DEFAULT_BASE_URL = "https://graph.microsoft.com/v1.0"
INVALID_OR_EXPIRED_TOKEN_ERROR = "invalid or expired token"
RESOLVED_GRAPH_TOKEN_STATE_KEY = "resolved_graph_access_token"

def _extract_token(authorization: str) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return authorization

def _has_bearer_prefix(authorization: str) -> bool:
    return authorization.lower().startswith("bearer ")

def _log_token(token: str) -> None:
    log_token_value(
        AUTH_LOGGER,
        token,
        full_key="delegated_token",
        preview_key="delegated_token_preview",
    )

def _token_cache_ttl_seconds() -> int:
    raw = os.getenv(TOKEN_VALIDATION_CACHE_TTL_ENV, str(DEFAULT_VALIDATION_CACHE_TTL_SECONDS)).strip()
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_VALIDATION_CACHE_TTL_SECONDS
    return max(0, parsed)

class OAuthTokenLogMiddleware(BaseHTTPMiddleware):
    """Capture delegated bearer token and store resolved token in request context."""

    def __init__(
        self,
        app: Any,
        token_resolver: Callable[..., str | None | Awaitable[str | None]] | None = None,
        require_bearer_token: bool = True,
    ) -> None:
        super().__init__(app)
        self._token_resolver = token_resolver
        self._require_bearer_token = require_bearer_token
        self._token_cache: dict[str, float] = {}
        self._graph_base = os.getenv(GRAPH_BASE_URL_ENV, GRAPH_DEFAULT_BASE_URL).rstrip("/")
        self._resolver_supports_force_refresh = self._detect_force_refresh_support(token_resolver)

    async def dispatch(self, request, call_next):
        # 健康检查与首页放行，便于探活与基础可用性检查。
        if self._is_public_path(request.url.path):
            return await call_next(request)

        token_value, error = self._extract_request_token(request.headers.get("authorization", ""))
        if error is not None:
            return error

        resolved_token, resolve_error = await self._resolve_and_validate_delegated_token(token_value)
        if resolve_error is not None:
            return resolve_error

        self._store_resolved_token_in_request_state(request, resolved_token)

        if self._should_validate_graph_token(resolved_token, token_value):
            # 对传入 token 做有效性校验（缓存命中时不访问 Graph）。
            is_valid = await self._validate_token(token_value or "")
            if not is_valid:
                return self._invalid_or_expired_response()

        return await call_next(request)

    def _store_resolved_token_in_request_state(self, request, resolved_token: str | None) -> None:
        if not resolved_token:
            return
        try:
            setattr(request.state, RESOLVED_GRAPH_TOKEN_STATE_KEY, resolved_token)
        except Exception:
            # 请求上下文不可写时跳过，不影响后续 header 回退逻辑。
            return

    async def _resolve_and_validate_delegated_token(
        self,
        token_value: str | None,
    ) -> tuple[str | None, JSONResponse | None]:
        resolved_token = await self._resolve_token(token_value)

        # 启用 resolver 时，解析失败应立即拒绝，避免回退使用已过期原始 token。
        if token_value and self._token_resolver is not None and not resolved_token:
            return resolved_token, self._invalid_or_expired_response()

        if token_value and resolved_token and self._token_resolver is not None:
            # 解析出的 Graph token 若失效，强制触发一次 refresh token 兑换并重试校验。
            if not await self._validate_token(resolved_token):
                AUTH_LOGGER.warning("resolved delegated token is invalid; attempting forced refresh")
                refreshed_token = await self._resolve_token(token_value, force_refresh=True)
                if not refreshed_token or not await self._validate_token(refreshed_token):
                    return resolved_token, self._invalid_or_expired_response()
                resolved_token = refreshed_token

        return resolved_token, None

    def _invalid_or_expired_response(self) -> JSONResponse:
        return JSONResponse({"error": INVALID_OR_EXPIRED_TOKEN_ERROR}, status_code=401)

    def _is_public_path(self, path: str) -> bool:
        return path in {"/", "/healthz", "/jobs/dispatch"}

    def _extract_request_token(self, authorization: str) -> tuple[str | None, JSONResponse | None]:
        # 在仅资源服务器模式下，保持历史行为：要求 Bearer token。
        if self._require_bearer_token and (not authorization or not _has_bearer_prefix(authorization)):
            return None, JSONResponse({"error": "missing or invalid Authorization header"}, status_code=401)

        if not authorization:
            return None, None

        token_value = _extract_token(authorization)
        if not token_value.strip():
            if self._require_bearer_token:
                return None, JSONResponse({"error": "empty bearer token"}, status_code=401)
            return None, None

        _log_token(token_value)
        return token_value, None

    async def _resolve_token(self, token_value: str | None, force_refresh: bool = False) -> str | None:
        if not token_value or self._token_resolver is None:
            return token_value

        if force_refresh and self._resolver_supports_force_refresh:
            maybe_resolved = self._token_resolver(token_value, force_refresh=True)
        else:
            maybe_resolved = self._token_resolver(token_value)
        return await maybe_resolved if inspect.isawaitable(maybe_resolved) else maybe_resolved

    def _detect_force_refresh_support(
        self,
        token_resolver: Callable[..., str | None | Awaitable[str | None]] | None,
    ) -> bool:
        if token_resolver is None:
            return False

        try:
            signature = inspect.signature(token_resolver)
        except (TypeError, ValueError):
            return False

        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True
            if parameter.name == "force_refresh":
                return True

        return False

    def _should_validate_graph_token(self, resolved_token: str | None, token_value: str | None) -> bool:
        return bool((resolved_token or token_value) and self._token_resolver is None)

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
