from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import AnyUrl
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
            return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with self._lock:
            self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state_id = secrets.token_urlsafe(24)
        now = time.time()
        async with self._lock:
            self._prune_expired(now)
            self._pending_auth[state_id] = PendingAuthorization(
                client_id=client.client_id,
                params=params,
                expires_at=now + self.state_ttl_seconds,
            )

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
            if not code or code.client_id != client.client_id:
                return None
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

        if external is None:
            raise TokenError("invalid_grant", "authorization code not found or already consumed")

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
            if not token or token.client_id != client.client_id:
                return None
            return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with self._lock:
            external = self._refresh_external_tokens.get(refresh_token.token)

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
            if access is None:
                return None
            if access.expires_at is not None and access.expires_at <= now:
                self._access_tokens.pop(token, None)
                self._access_external_tokens.pop(token, None)
                return None
            return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with self._lock:
            access = self._access_tokens.pop(token.token, None)
            if access is not None:
                self._access_external_tokens.pop(access.token, None)
                return

            refresh = self._refresh_tokens.pop(token.token, None)
            if refresh is not None:
                self._refresh_external_tokens.pop(refresh.token, None)

    async def build_callback_redirect(self, query_params: dict[str, str]) -> RedirectResponse:
        state = query_params.get("state", "")

        async with self._lock:
            pending = self._pending_auth.pop(state, None)

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

        redirect_url = construct_redirect_uri(
            str(original_params.redirect_uri),
            code=issued_code,
            state=original_params.state,
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    async def resolve_graph_access_token(self, mcp_access_token: str) -> str | None:
        now = int(time.time())
        async with self._lock:
            access = self._access_tokens.get(mcp_access_token)
            external = self._access_external_tokens.get(mcp_access_token)

        if access is None or external is None:
            return None

        if access.expires_at is not None and access.expires_at <= now:
            return None

        if external.graph_expires_at is not None and external.graph_expires_at <= now:
            refreshed = await self._refresh_external_graph_token(external)
            async with self._lock:
                if mcp_access_token in self._access_external_tokens:
                    self._access_external_tokens[mcp_access_token] = refreshed
            return refreshed.graph_access_token

        return external.graph_access_token

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
