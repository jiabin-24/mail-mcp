from __future__ import annotations

import os
from typing import Any, Callable

import httpx


class MailStore:
    """Outlook mailbox store backed by Microsoft Graph."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        self._token_provider = token_provider
        self._graph_base = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

    def list_folders(self) -> list[str]:
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/mailFolders?$select=id,displayName,wellKnownName",
        )
        folders = payload.get("value", [])
        names: list[str] = []
        for folder in folders:
            well_known = (folder.get("wellKnownName") or "").strip().lower()
            display_name = (folder.get("displayName") or "").strip()
            names.append(well_known or display_name)
        return [name for name in names if name]

    def list_messages(self, folder: str = "inbox", limit: int = 20) -> list[dict[str, Any]]:
        size = self._normalize_limit(limit)
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(folder)}/messages"
            f"?$top={size}&$orderby=receivedDateTime desc"
            "&$select=id,subject,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,receivedDateTime,sentDateTime",
        )
        return [self._map_message(item, folder=folder, prefer_preview=True) for item in payload.get("value", [])]

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        if not message_id.strip():
            return None
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{message_id}"
            "?$select=id,subject,body,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,receivedDateTime,sentDateTime,parentFolderId",
        )
        return self._map_message(payload)

    def search_messages(self, query: str, folder: str = "inbox", limit: int = 20) -> list[dict[str, Any]]:
        q = query.strip()
        if not q:
            return []

        size = self._normalize_limit(limit)
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(folder)}/messages"
            f"?$search=\"{q}\"&$top={size}"
            "&$select=id,subject,body,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,receivedDateTime,sentDateTime",
            headers={"ConsistencyLevel": "eventual"},
        )
        return [self._map_message(item, folder=folder) for item in payload.get("value", [])]

    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"{self._mailbox_prefix}/messages",
            json={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": self._emails_to_recipients(to),
                "ccRecipients": self._emails_to_recipients(cc or []),
                "bccRecipients": self._emails_to_recipients(bcc or []),
            },
        )
        return self._map_message(payload, folder="drafts")

    def send_draft(self, draft_id: str) -> dict[str, Any] | None:
        if not draft_id.strip():
            return None

        self._request("POST", f"{self._mailbox_prefix}/messages/{draft_id}/send", expect_json=False)
        return {
            "id": draft_id,
            "folder": "sent",
            "sent": True,
            "status": "sent",
        }

    @property
    def _mailbox_prefix(self) -> str:
        return "/me"

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        token = self._token_provider()
        if not token:
            raise ValueError(
                "No Outlook token available. Provide bearer token in Authorization header."
            )

        req_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        with httpx.Client(base_url=self._graph_base, timeout=30.0) as client:
            response = client.request(method, path, headers=req_headers, json=json)

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(f"Graph API request failed ({response.status_code}): {body}")

        if not expect_json:
            return {}
        if not response.content:
            return {}
        return response.json()

    def _map_message(
        self,
        message: dict[str, Any],
        folder: str | None = None,
        prefer_preview: bool = False,
    ) -> dict[str, Any]:
        body = message.get("body", {}) or {}
        body_preview = message.get("bodyPreview", "") or ""
        body_content = body.get("content", "") or ""
        result = {
            "id": message.get("id", ""),
            "folder": folder or message.get("parentFolderId", ""),
            "from": _recipient_address(message.get("from", {})),
            "to": _recipient_addresses(message.get("toRecipients", [])),
            "cc": _recipient_addresses(message.get("ccRecipients", [])),
            "bcc": _recipient_addresses(message.get("bccRecipients", [])),
            "subject": message.get("subject", "") or "",
            "bodyPreview": body_preview,
            "sent": not bool(message.get("isDraft", False)),
            "received_at": message.get("receivedDateTime", ""),
            "sent_at": message.get("sentDateTime", ""),
        }
        if not prefer_preview:
            result["body"] = body_content or body_preview
        return result

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(limit, 100))

    def _folder_segment(self, folder: str) -> str:
        value = folder.strip().lower()
        if not value:
            return "inbox"
        return value

    def _emails_to_recipients(self, emails: list[str]) -> list[dict[str, Any]]:
        return [{"emailAddress": {"address": email}} for email in emails if email.strip()]


def _recipient_addresses(recipients: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for recipient in recipients:
        address = _recipient_address(recipient)
        if address:
            result.append(address)
    return result


def _recipient_address(recipient: dict[str, Any]) -> str:
    email_address = recipient.get("emailAddress", {}) if isinstance(recipient, dict) else {}
    return str(email_address.get("address", "") or "")
