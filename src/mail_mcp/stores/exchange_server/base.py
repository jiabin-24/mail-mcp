from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Callable

from exchangelib import Account, Configuration, DELEGATE
from exchangelib.credentials import Credentials


class ExchangeServerStoreBase:
    """Exchange Server EWS 连接及公共辅助逻辑。"""

    def __init__(self, token_provider: Callable[[], str | None] | None = None) -> None:
        self._token_provider = token_provider
        raw_server_url = (os.getenv("EXCHANGE_SERVER_URL") or "").strip()
        self._server_url = raw_server_url.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._username = (os.getenv("EXCHANGE_SERVER_USERNAME") or "").strip()
        self._password = (os.getenv("EXCHANGE_SERVER_PASSWORD") or "").strip()
        self._domain = (os.getenv("EXCHANGE_SERVER_DOMAIN") or "").strip()
        self._mailbox = (os.getenv("EXCHANGE_SERVER_MAILBOX") or self._username or "").strip()
        configured_auth = (os.getenv("EXCHANGE_SERVER_AUTH_TYPE") or "NTLM").strip()
        self._auth_type = self._normalize_auth_type(configured_auth)
        self._time_zone = (os.getenv("EXCHANGE_SERVER_TIME_ZONE") or "UTC").strip()
        self._account: Account | None = None

    @staticmethod
    def _normalize_auth_type(value: str) -> str:
        normalized = (value or "NTLM").strip()
        lowered = normalized.lower()
        if lowered in {"ntlm", "kerberos"}:
            return "NTLM"
        if lowered in {"basic", "basic_auth"}:
            return "basic"
        if lowered in {"oauth", "oauth2", "oauth 2.0"}:
            return "OAuth 2.0"
        if lowercase := lowered.replace(" ", ""):
            if lowercase in {"noauthentication", "noauthentication"}:
                return "no authentication"
        return normalized

    def _credentials(self):
        token = self._token_provider() if self._token_provider else None
        if token and self._server_url and self._mailbox:
            raise ValueError(
                "OAuth bearer token-based Exchange Server auth is not configured in this EWS adapter; "
                "set EXCHANGE_SERVER_USERNAME / EXCHANGE_SERVER_PASSWORD or EXCHANGE_SERVER_AUTH_TYPE."
            )
        if not self._username or not self._password:
            raise ValueError(
                "Exchange Server EWS requires EXCHANGE_SERVER_USERNAME and EXCHANGE_SERVER_PASSWORD."
            )

        if self._domain:
            return Credentials(username=f"{self._domain}\\{self._username}", password=self._password)
        return Credentials(username=self._username, password=self._password)

    def _build_account(self) -> Account:
        if self._account is not None:
            return self._account

        if not self._server_url:
            raise ValueError(
                "EXCHANGE_SERVER_URL is required when using Exchange Server EWS mode."
            )

        creds = self._credentials()
        config = Configuration(
            server=self._server_url,
            credentials=creds,
            auth_type=self._auth_type,
        )
        account = Account(
            primary_smtp_address=self._mailbox or self._username,
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
