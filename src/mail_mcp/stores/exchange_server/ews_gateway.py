from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC
from html import escape
from typing import Any, Callable, cast

import msal
from exchangelib import Account, Configuration, DELEGATE, HTMLBody, Mailbox, OAUTH2
from exchangelib.credentials import OAuth2Credentials
from oauthlib.oauth2 import OAuth2Token

from ..gateway_base import GatewayBase


LOGGER = logging.getLogger(__name__)


class EwsGateway(GatewayBase):
    """Exchange Server EWS gateway with configuration and shared helper logic."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        super().__init__(token_provider=token_provider)
        self._token_provider = token_provider
        raw_server_url = (os.getenv("EXCHANGE_SERVER_URL") or "").strip()
        self._server_url = raw_server_url.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client_id = (os.getenv("EXCHANGE_SERVER_CLIENT_ID") or "").strip()
        self._client_secret = (os.getenv("EXCHANGE_SERVER_CLIENT_SECRET") or "").strip()
        self._tenant_id = (os.getenv("EXCHANGE_SERVER_TENANT_ID") or "").strip()
        self._time_zone = (os.getenv("EXCHANGE_SERVER_TIME_ZONE") or "UTC").strip()
        self._account: Account | None = None

    def _exchange_token_via_obo(self, user_token: str) -> str:
        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_secret,
            authority=f"https://login.microsoftonline.com/{self._tenant_id}",
        )
        result = app.acquire_token_on_behalf_of(
            user_assertion=user_token,
            scopes=["EWS.AccessAsUser.All"],
        )

        if not result or not result.get("access_token"):
            error_text = result.get("error_description") or result.get("error") or "unknown reason"
            raise ValueError(f"Exchange Server EWS OBO token exchange failed: {error_text}")

        return result["access_token"]

    def _credentials(self):
        raw_token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not raw_token:
            raise ValueError(
                "Exchange Server EWS requires a bearer access token from the current request header or OUTLOOK_ACCESS_TOKEN."
            )

        delegated_access_token = self._exchange_token_via_obo(raw_token)

        return OAuth2Credentials(
            client_id=self._client_id,
            client_secret=self._client_secret,
            tenant_id=self._tenant_id or None,
            access_token=OAuth2Token({
                "access_token": delegated_access_token,
                "token_type": "Bearer",
            }),
        )

    def _resolve_current_mailbox(self) -> str:
        token = self._strip_bearer_prefix(self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip())
        if not token:
            return ""

        parts = token.split(".")
        if len(parts) < 2:
            return ""

        payload_part = parts[1]
        padding = "=" * (-len(payload_part) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload_part + padding)
            payload = json.loads(decoded.decode("utf-8"))
        except Exception:
            return ""

        if not isinstance(payload, dict):
            return ""

        mailbox = (
            str(payload.get("preferred_username", "") or "").strip().lower()
            or str(payload.get("upn", "") or "").strip().lower()
            or str(payload.get("email", "") or "").strip().lower()
            or str(payload.get("mail", "") or "").strip().lower()
        )
        return mailbox

    def get_user_time_zone(self, fallback: str = "UTC") -> dict[str, str]:
        fixed_time_zone = "Asia/Shanghai"
        return {"time_zone": fixed_time_zone, "source": "ews_fixed"}

    def get_mailbox_time_zone_if_available(self) -> str | None:
        time_zone_info = self.get_user_time_zone(fallback="")
        resolved = str(time_zone_info.get("time_zone", "") or "").strip()
        return resolved or None

    def list_tenant_users(self, search: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        return []

    def _build_account(self) -> Account:
        if self._account is not None:
            return self._account

        if not self._server_url:
            raise ValueError(
                "EXCHANGE_SERVER_URL is required when using Exchange Server EWS mode."
            )

        mailbox = self._resolve_current_mailbox().strip()
        if not mailbox:
            raise ValueError(
                "The current bearer token does not expose a mailbox identity for Exchange Server EWS."
            )

        creds = self._credentials()
        config = Configuration(
            server=self._server_url,
            credentials=creds,
            auth_type=OAUTH2,
        )
        account = Account(
            primary_smtp_address=mailbox,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )
        self._account = account
        return account

    @staticmethod
    def _normalize_folder_name(value: str | None) -> str:
        normalized = (value or "").strip()
        if not normalized:
            return "inbox"
        mapping = {
            "inbox": "inbox",
            "sent": "sentitems",
            "sentitems": "sentitems",
            "drafts": "drafts",
            "archive": "archive",
            "deleted": "deleteditems",
            "deleteditems": "deleteditems",
            "junk": "junkemail",
            "junkemail": "junkemail",
        }
        lowered = normalized.lower()
        return mapping.get(lowered, normalized)

    def _folder_for_name(self, folder: str | None):
        account = self._build_account()
        name = self._normalize_folder_name(folder)
        mapping = {
            "inbox": account.inbox,
            "sentitems": account.sent,
            "drafts": account.drafts,
            "archive": getattr(account, "archive", None) or account.root,
            "deleteditems": account.trash,
            "junkemail": account.junk,
        }
        if name in mapping and mapping[name] is not None:
            return mapping[name]
        return cast(Any, account.root) / name

    def _get_item_by_id(self, item_id: str, folder: str | None = None) -> Any:
        folder_obj = self._folder_for_name(folder)
        try:
            return folder_obj.get(id=item_id)
        except Exception:
            LOGGER.exception("EWS get item failed: item_id=%s folder=%s", item_id, folder or "inbox")
            raise

    def _utc_iso(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if hasattr(value, "astimezone"):
            try:
                return value.astimezone(UTC).isoformat()
            except Exception:
                return value.isoformat()
        return str(value)

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _preview_text(body: Any) -> str:
        text = ""
        if body is None:
            return ""
        if hasattr(body, "text_body") and body.text_body:
            text = body.text_body
        elif isinstance(body, str):
            text = body
        elif isinstance(body, dict):
            text = body.get("content", "") or ""
        text = str(text).replace("\r", "").replace("\n", " ")
        return text[:200]

    @staticmethod
    def _recipient_list(value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        recipients: list[dict[str, Any]] = []
        for recipient in value:
            address = getattr(recipient, "email_address", None) or getattr(recipient, "address", None) or ""
            if address:
                recipients.append({"emailAddress": {"address": address}})
        return recipients

    @staticmethod
    def _mailboxes_from_addresses(addresses: list[str] | None) -> list[Mailbox]:
        # 将邮箱字符串列表（允许 None）转换为 exchangelib Mailbox 列表，并过滤空值。
        return [
            Mailbox(email_address=address.strip())
            for address in (addresses or [])
            if isinstance(address, str) and address.strip()
        ]

    @staticmethod
    def _plain_text_to_html_body(text: str) -> HTMLBody:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        escaped = escape(normalized)
        html = escaped.replace("\n", "<br/>")
        return HTMLBody(f"<div>{html}</div>")

    @staticmethod
    def _account_id(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return getattr(value, "id", str(value))

    @staticmethod
    def _strip_bearer_prefix(token: str | None) -> str:
        if not token:
            return ""
        return token.replace("Bearer ", "", 1).strip()


ExchangeServerStoreBase = EwsGateway

__all__ = ["EwsGateway", "ExchangeServerStoreBase"]
