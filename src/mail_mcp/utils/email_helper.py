from __future__ import annotations

from html import escape
from typing import Any

from exchangelib import HTMLBody, Mailbox


class EmailHelper:
    """Reusable conversions shared by email and calendar stores."""

    @staticmethod
    def normalize_folder_name(value: str | None) -> str:
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

    @staticmethod
    def safe_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def preview_text(body: Any) -> str:
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
    def recipient_list(value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        recipients: list[dict[str, Any]] = []
        for recipient in value:
            mailbox = getattr(recipient, "mailbox", None)
            address = (
                getattr(mailbox, "email_address", None)
                or getattr(recipient, "email_address", None)
                or getattr(recipient, "address", None)
                or ""
            )
            cleaned = str(address or "").strip()
            if cleaned:
                recipients.append({"emailAddress": {"address": cleaned}})
        return recipients

    @staticmethod
    def mailboxes_from_addresses(addresses: list[str] | None) -> list[Mailbox]:
        return [
            Mailbox(email_address=address.strip())
            for address in (addresses or [])
            if isinstance(address, str) and address.strip()
        ]

    @staticmethod
    def plain_text_to_html(text: str | None) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        escaped = escape(normalized)
        return escaped.replace("\n", "<br/>")

    @staticmethod
    def plain_text_to_html_body(text: str) -> HTMLBody:
        return HTMLBody(f"<div>{EmailHelper.plain_text_to_html(text)}</div>")

    @staticmethod
    def account_id(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return getattr(value, "id", str(value))

    @staticmethod
    def strip_bearer_prefix(token: str | None) -> str:
        if not token:
            return ""
        return token.replace("Bearer ", "", 1).strip()


__all__ = ["EmailHelper"]