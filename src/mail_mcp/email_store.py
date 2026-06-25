from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from .graph_store import GraphStoreBase, recipient_address, recipient_addresses


GRAPH_QUERY_SAFE = "()':,=-"


class EmailStore(GraphStoreBase):
    """Email-focused operations backed by Microsoft Graph mailbox APIs."""

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

    def search_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        search: str | None = None,
        filter: str | None = None,
    ) -> list[dict[str, Any]]:
        size = self._normalize_limit(limit)
        messages_path = f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(folder)}/messages"
        select_clause = (
            "id,subject,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,"
            "receivedDateTime,sentDateTime"
        )
        search_value = (search or "").strip()
        filter_value = (filter or "").strip()
        if not search_value and not filter_value:
            return []

        params: list[str] = [f"$top={size}", f"$select={select_clause}"]
        if filter_value:
            encoded_filter = quote(filter_value, safe=GRAPH_QUERY_SAFE)
            params.append(f"$filter={encoded_filter}")
        if search_value:
            encoded_search = quote(search_value, safe=GRAPH_QUERY_SAFE)
            params.append(f"$search={encoded_search}")
        if not search_value:
            params.append("$orderby=receivedDateTime desc")

        headers = {"ConsistencyLevel": "eventual"} if search_value else None
        payload = self._request(
            "GET",
            f"{messages_path}?{'&'.join(params)}",
            headers=headers,
        )
        return [self._map_message(item, folder=folder, prefer_preview=True) for item in payload.get("value", [])]

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
        result = self._map_message(payload, folder="drafts")
        result["draft_id"] = payload.get("id", "")
        result["webLink"] = payload.get("webLink", "")
        return result

    def create_reply_draft(self, message_id: str, body: str) -> dict[str, Any]:
        if not message_id.strip():
            raise ValueError("message_id cannot be empty")
        if not body.strip():
            raise ValueError("body cannot be empty")

        draft = self._request(
            "POST",
            f"{self._mailbox_prefix}/messages/{message_id}/createReply",
            json={},
        )
        draft_id = str(draft.get("id", "") or "").strip()
        if not draft_id:
            raise ValueError(f"createReply failed for message: {message_id}")

        quoted_html = str((draft.get("body") or {}).get("content", "") or "")
        reply_html = self._plain_text_to_html(body)
        merged_html = f"<div>{reply_html}</div><br/>{quoted_html}" if quoted_html else f"<div>{reply_html}</div>"

        updated = self._request(
            "PATCH",
            f"{self._mailbox_prefix}/messages/{draft_id}",
            json={"body": {"contentType": "HTML", "content": merged_html}},
        )
        result = self._map_message(updated, folder="drafts")
        result["webLink"] = updated.get("webLink", "")
        return result

    def update_draft(
        self,
        draft_id: str,
        to: list[str] | None = None,
        subject: str | None = None,
        body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if not draft_id.strip():
            return None

        current = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{draft_id}"
            "?$select=id,isDraft,webLink",
        )
        if not bool(current.get("isDraft", False)):
            raise ValueError(f"message is not a draft: {draft_id}")

        patch_payload: dict[str, Any] = {}
        if subject is not None:
            patch_payload["subject"] = subject
        if body is not None:
            patch_payload["body"] = {"contentType": "Text", "content": body}
        if to is not None:
            patch_payload["toRecipients"] = self._emails_to_recipients(to)
        if cc is not None:
            patch_payload["ccRecipients"] = self._emails_to_recipients(cc)
        if bcc is not None:
            patch_payload["bccRecipients"] = self._emails_to_recipients(bcc)

        if not patch_payload:
            return {
                "id": draft_id,
                "status": "no_change",
                "message": "no updates provided",
                "webLink": current.get("webLink", "") or "",
            }

        updated = self._request(
            "PATCH",
            f"{self._mailbox_prefix}/messages/{draft_id}",
            json=patch_payload,
        )
        result = self._map_message(updated, folder="drafts")
        result["webLink"] = updated.get("webLink", "")
        return result

    def send_draft(self, draft_id: str) -> dict[str, Any] | None:
        if not draft_id.strip():
            return None

        draft = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{draft_id}"
            "?$select=id,subject,bodyPreview,toRecipients,ccRecipients,bccRecipients",
        )

        self._request("POST", f"{self._mailbox_prefix}/messages/{draft_id}/send", expect_json=False)

        return {
            "id": draft_id,
            "folder": "sent",
            "sent": True,
            "status": "sent",
            "sent_summary": {
                "subject": draft.get("subject", "") or "",
                "to": recipient_addresses(draft.get("toRecipients", [])),
                "cc": recipient_addresses(draft.get("ccRecipients", [])),
                "bcc": recipient_addresses(draft.get("bccRecipients", [])),
                "bodyPreview": draft.get("bodyPreview", "") or "",
            },
        }

    def revoke_draft(self, draft_id: str) -> dict[str, Any] | None:
        if not draft_id.strip():
            return None

        draft = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{draft_id}"
            "?$select=id,subject,isDraft,parentFolderId",
        )

        if not bool(draft.get("isDraft", False)):
            raise ValueError(f"message is not a draft: {draft_id}")

        self._request("DELETE", f"{self._mailbox_prefix}/messages/{draft_id}", expect_json=False)

        return {
            "id": draft_id,
            "revoked": True,
            "status": "revoked",
            "folder": draft.get("parentFolderId", "") or "drafts",
            "subject": draft.get("subject", "") or "",
        }

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
            "from": recipient_address(message.get("from", {})),
            "to": recipient_addresses(message.get("toRecipients", [])),
            "cc": recipient_addresses(message.get("ccRecipients", [])),
            "bcc": recipient_addresses(message.get("bccRecipients", [])),
            "subject": message.get("subject", "") or "",
            "bodyPreview": body_preview,
            "sent": not bool(message.get("isDraft", False)),
            "received_at": message.get("receivedDateTime", ""),
            "sent_at": message.get("sentDateTime", ""),
        }
        if not prefer_preview:
            result["body"] = body_content or body_preview
        return result

    def _folder_segment(self, folder: str) -> str:
        value = folder.strip().lower()
        if not value:
            return "inbox"
        return value

    def _emails_to_recipients(self, emails: list[str]) -> list[dict[str, Any]]:
        return [{"emailAddress": {"address": email}} for email in emails if email.strip()]

    def _plain_text_to_html(self, text: str) -> str:
        safe = escape(text.strip())
        return safe.replace("\n", "<br/>")