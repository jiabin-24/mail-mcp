from __future__ import annotations

import base64
import os
from typing import Any

import httpx


DEFAULT_ATTACHMENT_SERVICE_HOST = (
    "https://app-mailattach-dev-6iuhcfhr5qgxo.azurewebsites.net"
)


def encode_message_id_path_segment(message_id: str) -> str:
    normalized_message_id = str(message_id or "")
    if not normalized_message_id:
        raise ValueError("message_id must not be empty")
    encoded = base64.urlsafe_b64encode(normalized_message_id.encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def build_attachment_upload_url(message_id: str, host: str | None = None) -> str:
    configured_host = host or os.getenv(
        "MAIL_ATTACHMENT_SERVICE_HOST",
        DEFAULT_ATTACHMENT_SERVICE_HOST,
    )
    normalized_host = configured_host.strip().rstrip("/")
    if not normalized_host:
        raise ValueError("MAIL_ATTACHMENT_SERVICE_HOST must not be empty")
    encoded_message_id = encode_message_id_path_segment(message_id)
    return f"{normalized_host}/mails/{encoded_message_id}/attachments"


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
        encoded_message_id = encode_message_id_path_segment(message_id)
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