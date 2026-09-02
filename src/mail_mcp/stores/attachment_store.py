from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx


DEFAULT_ATTACHMENT_SERVICE_HOST = (
    "https://app-mailattach-dev-6iuhcfhr5qgxo.azurewebsites.net"
)


class AttachmentStore:
    """Access draft attachments managed by the external upload service."""

    def __init__(self, host: str | None = None) -> None:
        configured_host = host or os.getenv(
            "MAIL_ATTACHMENT_SERVICE_HOST",
            DEFAULT_ATTACHMENT_SERVICE_HOST,
        )
        self._host = configured_host.strip().rstrip("/")
        if not self._host:
            raise ValueError("MAIL_ATTACHMENT_SERVICE_HOST must not be empty")

    def list_message_attachments(self, message_id: str) -> Any:
        encoded_message_id = quote(message_id, safe="")
        path = f"/api/messages/{encoded_message_id}/attachments"

        with httpx.Client(base_url=self._host, timeout=30.0) as client:
            response = client.get(path, headers={"Accept": "application/json"})

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(
                f"Attachment service request failed ({response.status_code}): {body}"
            )

        if not response.content:
            return []
        return response.json()