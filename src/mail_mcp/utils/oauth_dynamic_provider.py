from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from starlette.responses import RedirectResponse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

LOGGER = logging.getLogger("mail_mcp.oauth")


class OAuthClientRegistry(Protocol):
    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        ...

    def upsert_client(self, client: OAuthClientInformationFull) -> None:
        ...


class OAuthTokenRegistry(Protocol):
    def upsert_pending_auth(self, state_id: str, payload: dict[str, Any], expires_at: float) -> None:
        ...

    def pop_pending_auth(self, state_id: str) -> dict[str, Any] | None:
        ...

    def upsert_authorization_code(
        self,
        code: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any],
        expires_at: float,
    ) -> None:
        ...

    def get_authorization_code(
        self,
        code: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        ...

    def pop_authorization_code(
        self,
        code: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        ...

    def upsert_access_token(
        self,
        token: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: int | None,
    ) -> None:
        ...

    def get_access_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        ...

    def delete_access_token(self, token: str) -> None:
        ...

    def upsert_refresh_token(
        self,
        token: str,
        payload: dict[str, Any],
        external_payload: dict[str, Any] | None,
        expires_at: int | None,
    ) -> None:
        ...

    def get_refresh_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        ...

    def delete_refresh_token(self, token: str) -> None:
        ...


@dataclass
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float


@dataclass
class ExternalTokenBundle:
    graph_access_token: str
    graph_refresh_token: str | None
    graph_expires_at: int | None


class DynamicOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider with Dynamic Client Registration and Entra-based user login."""

    def __init__(
        self,
        *,
        issuer_url: str,
        callback_url: str,
        tenant_id: str,
        entra_client_id: str,
        entra_client_secret: str,
        entra_scopes: list[str],
        auth_code_ttl_seconds: int = 300,
        access_token_ttl_seconds: int = 3600,
        refresh_token_ttl_seconds: int = 30 * 24 * 3600,
        state_ttl_seconds: int = 600,
        client_registry: OAuthClientRegistry | None = None,
        token_registry: OAuthTokenRegistry | None = None,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.callback_url = callback_url
        self.tenant_id = tenant_id
        self.entra_client_id = entra_client_id
        self.entra_client_secret = entra_client_secret
        self.entra_scopes = entra_scopes
        self.auth_code_ttl_seconds = auth_code_ttl_seconds
        self.access_token_ttl_seconds = access_token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds
        self.state_ttl_seconds = state_ttl_seconds
        self._client_registry = client_registry
        self._token_registry = token_registry

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending_auth: dict[str, PendingAuthorization] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._code_external_tokens: dict[str, ExternalTokenBundle] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._access_external_tokens: dict[str, ExternalTokenBundle] = {}
        self._refresh_external_tokens: dict[str, ExternalTokenBundle] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._lock:
            cached = self._clients.get(client_id)
            if cached is not None:
                return cached

            if self._client_registry is not None:
                try:
                    persisted = self._client_registry.get_client(client_id)
                except Exception as exc:
                    LOGGER.warning("load oauth client from registry failed: %s", exc)
                    return None
                if persisted is not None:
                    self._clients[client_id] = persisted
                return persisted
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with self._lock:
            self._clients[client_info.client_id] = client_info
            if self._client_registry is not None:
                try:
                    self._client_registry.upsert_client(client_info)
                except Exception as exc:
                    LOGGER.warning("persist oauth client to registry failed: %s", exc)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state_id = secrets.token_urlsafe(24)
        now = time.time()
        pending = PendingAuthorization(
            client_id=client.client_id,
            params=params,
            expires_at=now + self.state_ttl_seconds,
        )
        async with self._lock:
            self._prune_expired(now)
            self._pending_auth[state_id] = pending

        if self._token_registry is not None:
            try:
                self._token_registry.upsert_pending_auth(
                    state_id=state_id,
                    payload=_pending_auth_to_payload(pending),
                    expires_at=pending.expires_at,
                )
            except Exception as exc:
                LOGGER.warning("persist pending auth to token registry failed: %s", exc)

        aad_state = state_id
        query = urlencode(
            {
                "client_id": self.entra_client_id,
                "response_type": "code",
                "redirect_uri": self.callback_url,
                "response_mode": "query",
                "scope": " ".join(self.entra_scopes),
                "state": aad_state,
            }
        )
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?{query}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        async with self._lock:
            code = self._auth_codes.get(authorization_code)
            if code is not None:
                if code.client_id != client.client_id:
                    return None
                return code

        if self._token_registry is None:
            return None

        try:
            persisted = self._token_registry.get_authorization_code(authorization_code)
        except Exception as exc:
            LOGGER.warning("load authorization code from token registry failed: %s", exc)
            return None

        if persisted is None:
            return None

        code_payload, external_payload = persisted
        code = _authorization_code_from_payload(code_payload)
        if code is None or code.client_id != client.client_id:
            return None

        async with self._lock:
            self._auth_codes[authorization_code] = code
            if external_payload is not None:
                external = _external_bundle_from_payload(external_payload)
                if external is not None:
                    self._code_external_tokens[authorization_code] = external

        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        now = int(time.time())
        async with self._lock:
            external = self._code_external_tokens.pop(authorization_code.code, None)
            self._auth_codes.pop(authorization_code.code, None)

        external = self._load_external_for_authorization_code(authorization_code.code, external)

        if external is None:
            raise TokenError("invalid_grant", "authorization code not found or already consumed")

        self._cleanup_authorization_code_from_registry(authorization_code.code)

        access_token_value = secrets.token_urlsafe(48)
        refresh_token_value = secrets.token_urlsafe(48)

        access_expires_at = now + self.access_token_ttl_seconds
        refresh_expires_at = now + self.refresh_token_ttl_seconds

        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=access_expires_at,
            resource=authorization_code.resource,
        )
        refresh_token = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=refresh_expires_at,
        )

        async with self._lock:
            self._access_tokens[access_token_value] = access_token
            self._refresh_tokens[refresh_token_value] = refresh_token
            self._access_external_tokens[access_token_value] = external
            self._refresh_external_tokens[refresh_token_value] = external

        if self._token_registry is not None:
            access_payload = _access_token_to_payload(access_token)
            refresh_payload = _refresh_token_to_payload(refresh_token)
            external_payload = _external_bundle_to_payload(external)
            try:
                self._token_registry.upsert_access_token(
                    token=access_token_value,
                    payload=access_payload,
                    external_payload=external_payload,
                    expires_at=access_token.expires_at,
                )
                self._token_registry.upsert_refresh_token(
                    token=refresh_token_value,
                    payload=refresh_payload,
                    external_payload=external_payload,
                    expires_at=refresh_token.expires_at,
                )
            except Exception as exc:
                LOGGER.warning("persist exchanged tokens to token registry failed: %s", exc)

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=self.access_token_ttl_seconds,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token_value,
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        async with self._lock:
            token = self._refresh_tokens.get(refresh_token)
            if token is not None:
                if token.client_id != client.client_id:
                    return None
                return token

        if self._token_registry is None:
            return None

        try:
            persisted = self._token_registry.get_refresh_token(refresh_token)
        except Exception as exc:
            LOGGER.warning("load refresh token from token registry failed: %s", exc)
            return None

        if persisted is None:
            return None

        token_payload, external_payload = persisted
        token = _refresh_token_from_payload(token_payload)
        if token is None or token.client_id != client.client_id:
            return None

        async with self._lock:
            self._refresh_tokens[refresh_token] = token
            if external_payload is not None:
                external = _external_bundle_from_payload(external_payload)
                if external is not None:
                    self._refresh_external_tokens[refresh_token] = external

        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with self._lock:
            external = self._refresh_external_tokens.get(refresh_token.token)

        if external is None and self._token_registry is not None:
            try:
                persisted = self._token_registry.get_refresh_token(refresh_token.token)
            except Exception as exc:
                LOGGER.warning("load refresh token external mapping from token registry failed: %s", exc)
                persisted = None

            if persisted is not None:
                _, external_payload = persisted
                if external_payload is not None:
                    external = _external_bundle_from_payload(external_payload)

        if external is None:
            raise TokenError("invalid_grant", "refresh token does not have a delegated token mapping")

        refreshed_external = await self._refresh_external_graph_token(external)

        now = int(time.time())
        access_token_value = secrets.token_urlsafe(48)
        new_refresh_token_value = secrets.token_urlsafe(48)

        access_expires_at = now + self.access_token_ttl_seconds
        refresh_expires_at = now + self.refresh_token_ttl_seconds

        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=access_expires_at,
            resource=None,
        )
        new_refresh_token = RefreshToken(
            token=new_refresh_token_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=refresh_expires_at,
        )

        async with self._lock:
            self._refresh_tokens.pop(refresh_token.token, None)
            self._refresh_external_tokens.pop(refresh_token.token, None)

            self._access_tokens[access_token_value] = access_token
            self._refresh_tokens[new_refresh_token_value] = new_refresh_token
            self._access_external_tokens[access_token_value] = refreshed_external
            self._refresh_external_tokens[new_refresh_token_value] = refreshed_external

        if self._token_registry is not None:
            access_payload = _access_token_to_payload(access_token)
            refresh_payload = _refresh_token_to_payload(new_refresh_token)
            external_payload = _external_bundle_to_payload(refreshed_external)
            try:
                self._token_registry.delete_refresh_token(refresh_token.token)
                self._token_registry.upsert_access_token(
                    token=access_token_value,
                    payload=access_payload,
                    external_payload=external_payload,
                    expires_at=access_token.expires_at,
                )
                self._token_registry.upsert_refresh_token(
                    token=new_refresh_token_value,
                    payload=refresh_payload,
                    external_payload=external_payload,
                    expires_at=new_refresh_token.expires_at,
                )
            except Exception as exc:
                LOGGER.warning("persist refresh exchange result to token registry failed: %s", exc)

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=self.access_token_ttl_seconds,
            scope=" ".join(scopes),
            refresh_token=new_refresh_token_value,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        now = int(time.time())
        async with self._lock:
            access = self._access_tokens.get(token)
            if access is not None:
                if access.expires_at is not None and access.expires_at <= now:
                    self._access_tokens.pop(token, None)
                    self._access_external_tokens.pop(token, None)
                else:
                    return access

        if self._token_registry is None:
            return None

        try:
            persisted = self._token_registry.get_access_token(token)
        except Exception as exc:
            LOGGER.warning("load access token from token registry failed: %s", exc)
            return None

        if persisted is None:
            return None

        access_payload, external_payload = persisted
        access = _access_token_from_payload(access_payload)
        if access is None:
            return None

        if access.expires_at is not None and access.expires_at <= now:
            return None

        async with self._lock:
            self._access_tokens[token] = access
            if external_payload is not None:
                external = _external_bundle_from_payload(external_payload)
                if external is not None:
                    self._access_external_tokens[token] = external

        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with self._lock:
            access = self._access_tokens.pop(token.token, None)
            if access is not None:
                self._access_external_tokens.pop(access.token, None)
                if self._token_registry is not None:
                    try:
                        self._token_registry.delete_access_token(access.token)
                    except Exception as exc:
                        LOGGER.warning("delete access token from token registry failed: %s", exc)
                return

            refresh = self._refresh_tokens.pop(token.token, None)
            if refresh is not None:
                self._refresh_external_tokens.pop(refresh.token, None)
                if self._token_registry is not None:
                    try:
                        self._token_registry.delete_refresh_token(refresh.token)
                    except Exception as exc:
                        LOGGER.warning("delete refresh token from token registry failed: %s", exc)
                return

        if self._token_registry is not None:
            try:
                self._token_registry.delete_access_token(token.token)
                self._token_registry.delete_refresh_token(token.token)
            except Exception as exc:
                LOGGER.warning("delete token from token registry failed: %s", exc)

    async def build_callback_redirect(self, query_params: dict[str, str]) -> RedirectResponse:
        state = query_params.get("state", "")

        async with self._lock:
            pending = self._pending_auth.pop(state, None)

        if pending is None and self._token_registry is not None:
            try:
                pending_payload = self._token_registry.pop_pending_auth(state)
            except Exception as exc:
                LOGGER.warning("pop pending auth from token registry failed: %s", exc)
                pending_payload = None

            if pending_payload is not None:
                pending = _pending_auth_from_payload(pending_payload)

        if pending is None or pending.expires_at < time.time():
            return RedirectResponse(
                url=f"{self.issuer_url}/authorize?error=invalid_request&error_description=state_expired",
                status_code=302,
            )

        original_params = pending.params
        if "error" in query_params:
            redirect_url = construct_redirect_uri(
                str(original_params.redirect_uri),
                error=query_params.get("error"),
                error_description=query_params.get("error_description"),
                state=original_params.state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        code = query_params.get("code")
        if not code:
            redirect_url = construct_redirect_uri(
                str(original_params.redirect_uri),
                error="invalid_request",
                error_description="missing_authorization_code",
                state=original_params.state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        try:
            external = await self._exchange_entra_code_for_graph_tokens(code)
        except Exception:
            redirect_url = construct_redirect_uri(
                str(original_params.redirect_uri),
                error="server_error",
                error_description="entra_code_exchange_failed",
                state=original_params.state,
            )
            return RedirectResponse(url=redirect_url, status_code=302)

        issued_code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=issued_code,
            scopes=original_params.scopes or [],
            expires_at=time.time() + self.auth_code_ttl_seconds,
            client_id=pending.client_id,
            code_challenge=original_params.code_challenge,
            redirect_uri=original_params.redirect_uri,
            redirect_uri_provided_explicitly=original_params.redirect_uri_provided_explicitly,
            resource=original_params.resource,
        )

        async with self._lock:
            self._auth_codes[issued_code] = auth_code
            self._code_external_tokens[issued_code] = external

        if self._token_registry is not None:
            try:
                self._token_registry.upsert_authorization_code(
                    code=issued_code,
                    payload=_authorization_code_to_payload(auth_code),
                    external_payload=_external_bundle_to_payload(external),
                    expires_at=auth_code.expires_at,
                )
            except Exception as exc:
                LOGGER.warning("persist authorization code to token registry failed: %s", exc)

        redirect_url = construct_redirect_uri(
            str(original_params.redirect_uri),
            code=issued_code,
            state=original_params.state,
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    async def resolve_graph_access_token(self, mcp_access_token: str) -> str | None:
        now = int(time.time())
        access, external = await self._load_access_bundle(mcp_access_token)

        if access is None or external is None:
            return None

        if access.expires_at is not None and access.expires_at <= now:
            self._delete_access_token_from_registry(mcp_access_token)
            return None

        if external.graph_expires_at is not None and external.graph_expires_at <= now:
            refreshed = await self._refresh_external_graph_token(external)
            async with self._lock:
                if mcp_access_token in self._access_external_tokens:
                    self._access_external_tokens[mcp_access_token] = refreshed

            self._persist_access_token_bundle(
                token=mcp_access_token,
                access=access,
                external=refreshed,
            )
            return refreshed.graph_access_token

        return external.graph_access_token

    async def _load_access_bundle(
        self,
        mcp_access_token: str,
    ) -> tuple[AccessToken | None, ExternalTokenBundle | None]:
        async with self._lock:
            access = self._access_tokens.get(mcp_access_token)
            external = self._access_external_tokens.get(mcp_access_token)

        if access is not None and external is not None:
            return access, external

        if self._token_registry is None:
            return access, external

        try:
            persisted = self._token_registry.get_access_token(mcp_access_token)
        except Exception as exc:
            LOGGER.warning("load graph token mapping from token registry failed: %s", exc)
            return access, external

        if persisted is None:
            return access, external

        access_payload, external_payload = persisted
        loaded_access = _access_token_from_payload(access_payload)
        loaded_external = (
            _external_bundle_from_payload(external_payload)
            if external_payload is not None
            else None
        )

        if loaded_access is not None:
            access = loaded_access
        if loaded_external is not None:
            external = loaded_external

        if loaded_access is not None or loaded_external is not None:
            async with self._lock:
                if loaded_access is not None:
                    self._access_tokens[mcp_access_token] = loaded_access
                if loaded_external is not None:
                    self._access_external_tokens[mcp_access_token] = loaded_external

        return access, external

    def _load_external_for_authorization_code(
        self,
        authorization_code: str,
        external: ExternalTokenBundle | None,
    ) -> ExternalTokenBundle | None:
        if external is not None or self._token_registry is None:
            return external

        try:
            persisted = self._token_registry.pop_authorization_code(authorization_code)
        except Exception as exc:
            LOGGER.warning("pop authorization code from token registry failed: %s", exc)
            return None

        if persisted is None:
            return None

        _, external_payload = persisted
        if external_payload is None:
            return None

        return _external_bundle_from_payload(external_payload)

    def _cleanup_authorization_code_from_registry(self, authorization_code: str) -> None:
        if self._token_registry is None:
            return

        try:
            self._token_registry.pop_authorization_code(authorization_code)
        except Exception as exc:
            LOGGER.warning("cleanup authorization code from token registry failed: %s", exc)

    def _delete_access_token_from_registry(self, token: str) -> None:
        if self._token_registry is None:
            return

        try:
            self._token_registry.delete_access_token(token)
        except Exception as exc:
            LOGGER.warning("delete expired access token from token registry failed: %s", exc)

    def _persist_access_token_bundle(
        self,
        *,
        token: str,
        access: AccessToken,
        external: ExternalTokenBundle,
    ) -> None:
        if self._token_registry is None:
            return

        try:
            self._token_registry.upsert_access_token(
                token=token,
                payload=_access_token_to_payload(access),
                external_payload=_external_bundle_to_payload(external),
                expires_at=access.expires_at,
            )
        except Exception as exc:
            LOGGER.warning("persist refreshed external token mapping failed: %s", exc)

    async def _exchange_entra_code_for_graph_tokens(self, code: str) -> ExternalTokenBundle:
        token_endpoint = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "authorization_code",
            "client_id": self.entra_client_id,
            "client_secret": self.entra_client_secret,
            "code": code,
            "redirect_uri": self.callback_url,
            "scope": " ".join(self.entra_scopes),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(token_endpoint, data=data)

        if response.status_code >= 400:
            raise ValueError(f"entra token endpoint error ({response.status_code})")

        payload = response.json()
        graph_access_token = str(payload.get("access_token", "") or "").strip()
        if not graph_access_token:
            raise ValueError("entra response missing access_token")

        graph_refresh_token = str(payload.get("refresh_token", "") or "").strip() or None
        expires_in = payload.get("expires_in")
        graph_expires_at = int(time.time()) + int(expires_in) if isinstance(expires_in, int) else None

        return ExternalTokenBundle(
            graph_access_token=graph_access_token,
            graph_refresh_token=graph_refresh_token,
            graph_expires_at=graph_expires_at,
        )

    async def _refresh_external_graph_token(self, external: ExternalTokenBundle) -> ExternalTokenBundle:
        if not external.graph_refresh_token:
            raise TokenError("invalid_grant", "delegated refresh token is unavailable")

        token_endpoint = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.entra_client_id,
            "client_secret": self.entra_client_secret,
            "refresh_token": external.graph_refresh_token,
            "scope": " ".join(self.entra_scopes),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(token_endpoint, data=data)

        if response.status_code >= 400:
            raise TokenError("invalid_grant", "delegated refresh token exchange failed")

        payload = response.json()
        graph_access_token = str(payload.get("access_token", "") or "").strip()
        if not graph_access_token:
            raise TokenError("invalid_grant", "missing delegated access_token")

        graph_refresh_token = str(payload.get("refresh_token", "") or "").strip() or external.graph_refresh_token
        expires_in = payload.get("expires_in")
        graph_expires_at = int(time.time()) + int(expires_in) if isinstance(expires_in, int) else None

        return ExternalTokenBundle(
            graph_access_token=graph_access_token,
            graph_refresh_token=graph_refresh_token,
            graph_expires_at=graph_expires_at,
        )

    def _prune_expired(self, now: float) -> None:
        expired_states = [k for k, v in self._pending_auth.items() if v.expires_at <= now]
        for key in expired_states:
            self._pending_auth.pop(key, None)


def get_dynamic_oauth_config_from_env() -> dict[str, Any] | None:
    enabled = os.getenv("MCP_OAUTH_DYNAMIC_DISCOVERY_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    issuer_url = (os.getenv("MCP_OAUTH_ISSUER_URL") or os.getenv("MCP_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    callback_url = (os.getenv("MCP_OAUTH_CALLBACK_URL") or "").strip()
    tenant_id = (os.getenv("MCP_OAUTH_TENANT_ID") or "").strip()
    client_id = (os.getenv("MCP_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("MCP_OAUTH_CLIENT_SECRET") or "").strip()
    scope_str = (
        os.getenv("MCP_OAUTH_ENTRA_SCOPES")
        or "openid profile offline_access User.Read Mail.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite"
    )
    scopes = [part for part in scope_str.split() if part]

    if not issuer_url or not tenant_id or not client_id or not client_secret:
        return None

    if not callback_url:
        callback_url = f"{issuer_url}/oauth/callback"

    return {
        "issuer_url": issuer_url,
        "callback_url": callback_url,
        "tenant_id": tenant_id,
        "entra_client_id": client_id,
        "entra_client_secret": client_secret,
        "entra_scopes": scopes,
    }


def _pending_auth_to_payload(value: PendingAuthorization) -> dict[str, Any]:
    return {
        "client_id": value.client_id,
        "expires_at": value.expires_at,
        "params": {
            "redirect_uri": str(value.params.redirect_uri),
            "state": value.params.state,
            "scopes": list(value.params.scopes or []),
            "code_challenge": value.params.code_challenge,
            "resource": value.params.resource,
            "redirect_uri_provided_explicitly": value.params.redirect_uri_provided_explicitly,
        },
    }


def _pending_auth_from_payload(payload: dict[str, Any]) -> PendingAuthorization | None:
    try:
        params_raw = payload["params"]
        params = AuthorizationParams(
            redirect_uri=str(params_raw["redirect_uri"]),
            state=params_raw.get("state"),
            scopes=list(params_raw.get("scopes") or []),
            code_challenge=str(params_raw.get("code_challenge") or ""),
            resource=(
                str(params_raw.get("resource"))
                if params_raw.get("resource") is not None
                else None
            ),
            redirect_uri_provided_explicitly=bool(
                params_raw.get("redirect_uri_provided_explicitly", False)
            ),
        )
        return PendingAuthorization(
            client_id=str(payload["client_id"]),
            params=params,
            expires_at=float(payload["expires_at"]),
        )
    except Exception:
        return None


def _authorization_code_to_payload(value: AuthorizationCode) -> dict[str, Any]:
    return {
        "code": value.code,
        "scopes": list(value.scopes or []),
        "expires_at": value.expires_at,
        "client_id": value.client_id,
        "code_challenge": value.code_challenge,
        "redirect_uri": str(value.redirect_uri),
        "redirect_uri_provided_explicitly": value.redirect_uri_provided_explicitly,
        "resource": value.resource,
    }


def _authorization_code_from_payload(payload: dict[str, Any]) -> AuthorizationCode | None:
    try:
        return AuthorizationCode(
            code=str(payload["code"]),
            scopes=list(payload.get("scopes") or []),
            expires_at=float(payload["expires_at"]),
            client_id=str(payload["client_id"]),
            code_challenge=str(payload.get("code_challenge") or ""),
            redirect_uri=str(payload["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(payload.get("redirect_uri_provided_explicitly", False)),
            resource=(str(payload.get("resource")) if payload.get("resource") is not None else None),
        )
    except Exception:
        return None


def _access_token_to_payload(value: AccessToken) -> dict[str, Any]:
    return {
        "token": value.token,
        "client_id": value.client_id,
        "scopes": list(value.scopes or []),
        "expires_at": value.expires_at,
        "resource": value.resource,
    }


def _access_token_from_payload(payload: dict[str, Any]) -> AccessToken | None:
    try:
        return AccessToken(
            token=str(payload["token"]),
            client_id=str(payload["client_id"]),
            scopes=list(payload.get("scopes") or []),
            expires_at=(int(payload["expires_at"]) if payload.get("expires_at") is not None else None),
            resource=(str(payload.get("resource")) if payload.get("resource") is not None else None),
        )
    except Exception:
        return None


def _refresh_token_to_payload(value: RefreshToken) -> dict[str, Any]:
    return {
        "token": value.token,
        "client_id": value.client_id,
        "scopes": list(value.scopes or []),
        "expires_at": value.expires_at,
    }


def _refresh_token_from_payload(payload: dict[str, Any]) -> RefreshToken | None:
    try:
        return RefreshToken(
            token=str(payload["token"]),
            client_id=str(payload["client_id"]),
            scopes=list(payload.get("scopes") or []),
            expires_at=(int(payload["expires_at"]) if payload.get("expires_at") is not None else None),
        )
    except Exception:
        return None


def _external_bundle_to_payload(value: ExternalTokenBundle) -> dict[str, Any]:
    return {
        "graph_access_token": value.graph_access_token,
        "graph_refresh_token": value.graph_refresh_token,
        "graph_expires_at": value.graph_expires_at,
    }


def _external_bundle_from_payload(payload: dict[str, Any]) -> ExternalTokenBundle | None:
    try:
        access_token = str(payload.get("graph_access_token") or "").strip()
        if not access_token:
            return None

        refresh_token = payload.get("graph_refresh_token")
        if refresh_token is not None:
            refresh_token = str(refresh_token).strip() or None

        expires_at = payload.get("graph_expires_at")
        if expires_at is not None:
            expires_at = int(expires_at)

        return ExternalTokenBundle(
            graph_access_token=access_token,
            graph_refresh_token=refresh_token,
            graph_expires_at=expires_at,
        )
    except Exception:
        return None
