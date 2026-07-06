from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from .graph_store import GraphStoreBase, recipient_addresses
from ..models import map_graph_message
from ..schemas.request_models import (
    MailboxComposeInput,
    MailboxDraftIdInput,
    MailboxGetMessageInput,
    MailboxListMessagesInput,
    MailboxReplyComposeInput,
    MailboxSearchInput,
    MailboxUpdateDraftInput,
)


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

    def list_messages(self, req: MailboxListMessagesInput) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(req.folder)}/messages"
            f"?$top={self._normalize_limit(req.limit)}&$orderby=receivedDateTime desc"
            "&$select=id,subject,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,receivedDateTime,sentDateTime",
        )
        return [map_graph_message(item, folder=req.folder, prefer_preview=True) for item in payload.get("value", [])]

    def get_message(self, req: MailboxGetMessageInput) -> dict[str, Any] | None:
        payload = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{req.message_id}"
            "?$select=id,subject,body,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,receivedDateTime,sentDateTime,parentFolderId",
        )
        return map_graph_message(payload)

    def search_messages(self, req: MailboxSearchInput) -> list[dict[str, Any]]:
        messages_path = f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(req.folder)}/messages"
        select_clause = (
            "id,subject,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,"
            "receivedDateTime,sentDateTime"
        )
        search_value = (req.search or "").strip()
        filter_value = (req.filter or "").strip()
        if not search_value and not filter_value:
            return []

        params: list[str] = [f"$top={self._normalize_limit(req.limit)}", f"$select={select_clause}"]
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
        return [map_graph_message(item, folder=req.folder, prefer_preview=True) for item in payload.get("value", [])]

    def create_draft(self, req: MailboxComposeInput) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"{self._mailbox_prefix}/messages",
            json={
                "subject": req.subject,
                "body": {"contentType": "Text", "content": req.body},
                "toRecipients": self._emails_to_recipients(req.to),
                "ccRecipients": self._emails_to_recipients(req.cc or []),
                "bccRecipients": self._emails_to_recipients(req.bcc or []),
            },
        )
        result = map_graph_message(payload, folder="drafts")
        result["draft_id"] = payload.get("id", "")
        result["webLink"] = payload.get("webLink", "")
        return result

    def create_reply_draft(self, req: MailboxReplyComposeInput) -> dict[str, Any]:
        draft = self._request(
            "POST",
            f"{self._mailbox_prefix}/messages/{req.message_id}/createReply",
            json={},
        )
        draft_id = str(draft.get("id", "") or "")
        if not draft_id:
            raise ValueError(f"createReply failed for message: {req.message_id}")

        quoted_html = str((draft.get("body") or {}).get("content", "") or "")
        reply_html = self._plain_text_to_html(req.body)
        merged_html = f"<div>{reply_html}</div><br/>{quoted_html}" if quoted_html else f"<div>{reply_html}</div>"

        updated = self._request(
            "PATCH",
            f"{self._mailbox_prefix}/messages/{draft_id}",
            json={"body": {"contentType": "HTML", "content": merged_html}},
        )
        result = map_graph_message(updated, folder="drafts")
        result["draft_id"] = updated.get("id", "")
        result["webLink"] = updated.get("webLink", "")
        return result

    def update_draft(self, req: MailboxUpdateDraftInput) -> dict[str, Any] | None:
        current = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{req.draft_id}"
            "?$select=id,isDraft,webLink",
        )
        if not bool(current.get("isDraft", False)):
            raise ValueError(f"message is not a draft: {req.draft_id}")

        patch_payload: dict[str, Any] = {}
        if req.subject is not None:
            patch_payload["subject"] = req.subject
        if req.body is not None:
            patch_payload["body"] = {"contentType": "Text", "content": req.body}
        if req.to is not None:
            patch_payload["toRecipients"] = self._emails_to_recipients(req.to)
        if req.cc is not None:
            patch_payload["ccRecipients"] = self._emails_to_recipients(req.cc)
        if req.bcc is not None:
            patch_payload["bccRecipients"] = self._emails_to_recipients(req.bcc)

        if not patch_payload:
            return {
                "id": req.draft_id,
                "status": "no_change",
                "message": "no updates provided",
                "webLink": current.get("webLink", "") or "",
            }

        updated = self._request(
            "PATCH",
            f"{self._mailbox_prefix}/messages/{req.draft_id}",
            json=patch_payload,
        )
        result = map_graph_message(updated, folder="drafts")
        result["webLink"] = updated.get("webLink", "")
        return result

    def send_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        draft = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{req.draft_id}"
            "?$select=id,subject,bodyPreview,toRecipients,ccRecipients,bccRecipients",
        )

        self._request("POST", f"{self._mailbox_prefix}/messages/{req.draft_id}/send", expect_json=False)

        return {
            "id": req.draft_id,
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

    def revoke_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        draft = self._request(
            "GET",
            f"{self._mailbox_prefix}/messages/{req.draft_id}"
            "?$select=id,subject,isDraft,parentFolderId",
        )

        if not bool(draft.get("isDraft", False)):
            raise ValueError(f"message is not a draft: {req.draft_id}")

        self._request("DELETE", f"{self._mailbox_prefix}/messages/{req.draft_id}", expect_json=False)

        return {
            "id": req.draft_id,
            "revoked": True,
            "status": "revoked",
            "folder": draft.get("parentFolderId", "") or "drafts",
            "subject": draft.get("subject", "") or "",
        }

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