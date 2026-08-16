from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any, Callable

from exchangelib import Account, Configuration, DELEGATE
from exchangelib.credentials import OAuth2Credentials


class ExchangeServerStoreBase:
    """Exchange Server EWS 连接及公共辅助逻辑。"""

    def __init__(self, token_provider: Callable[[], str | None] | None = None) -> None:
        self._token_provider = token_provider
        raw_server_url = (os.getenv("EXCHANGE_SERVER_URL") or "").strip()
        self._server_url = raw_server_url.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client_id = (os.getenv("EXCHANGE_SERVER_CLIENT_ID") or "").strip()
        self._client_secret = (os.getenv("EXCHANGE_SERVER_CLIENT_SECRET") or "").strip()
        self._tenant_id = (os.getenv("EXCHANGE_SERVER_TENANT_ID") or "").strip()
        self._time_zone = (os.getenv("EXCHANGE_SERVER_TIME_ZONE") or "UTC").strip()
        self._account: Account | None = None

    def _credentials(self):
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "Exchange Server EWS requires a bearer access token from the current request header or OUTLOOK_ACCESS_TOKEN."
            )
        if not self._client_id or not self._client_secret:
            raise ValueError(
                "Exchange Server EWS bearer auth requires EXCHANGE_SERVER_CLIENT_ID and "
                "EXCHANGE_SERVER_CLIENT_SECRET."
            )

        return OAuth2Credentials(
            client_id=self._client_id,
            client_secret=self._client_secret,
            tenant_id=self._tenant_id or None,
            access_token=token,
        )

    def _resolve_current_mailbox(self) -> str:
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
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
            "sentitems": account.sent_items,
            "drafts": account.drafts,
            "archive": getattr(account, "archive", None) or account.root,
            "deleteditems": account.deleted_items,
            "junkemail": account.junk_email,
        }
        if name in mapping and mapping[name] is not None:
            return mapping[name]
        return account.root / name

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
    def _account_id(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return getattr(value, "id", str(value))
