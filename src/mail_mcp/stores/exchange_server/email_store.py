from __future__ import annotations

from typing import Any

from ...schemas.request_models import (
    MailboxComposeInput,
    MailboxDraftIdInput,
    MailboxGetMessageInput,
    MailboxListMessagesInput,
    MailboxReplyComposeInput,
    MailboxSearchInput,
    MailboxUpdateDraftInput,
)
from .ews_gateway import EwsGateway


class EmailStore(EwsGateway):
    """基于 Exchange Server EWS 的邮件操作。"""

    def list_folders(self) -> list[str]:
        account = self._build_account()
        folders = []
        for folder in account.msg_folder_root.children:
            folders.append(folder.name)
        return folders

    def list_messages(self, req: MailboxListMessagesInput) -> list[dict[str, Any]]:
        folder = self._folder_for_name(req.folder)
        items = folder.all().order_by("-datetime_received")[: req.limit]
        return [self._map_message(item, folder=req.folder) for item in items]

    def get_message(self, req: MailboxGetMessageInput) -> dict[str, Any] | None:
        account = self._build_account()
        message = account.get_item(req.message_id)
        return self._map_message(message, folder="inbox")

    def search_messages(self, req: MailboxSearchInput) -> list[dict[str, Any]]:
        folder = self._folder_for_name(req.folder)
        results: list[dict[str, Any]] = []
        for item in folder.all():
            haystack = " ".join(
                [
                    self._safe_text(item.subject),
                    self._safe_text(item.body),
                    self._safe_text(item.sender.email_address if getattr(item, "sender", None) else ""),
                ]
            ).lower()
            filter_ok = True
            if req.filter:
                filter_ok = req.filter.lower() in haystack
            if req.search:
                filter_ok = filter_ok and (req.search.lower() in haystack)
            if filter_ok:
                results.append(self._map_message(item, folder=req.folder))
            if len(results) >= req.limit:
                break
        return results

    def create_draft(self, req: MailboxComposeInput) -> dict[str, Any]:
        account = self._build_account()
        message = account.compose()
        message.subject = req.subject
        message.body = req.body
        message.to_recipients = req.to
        if req.cc:
            message.cc_recipients = req.cc
        if req.bcc:
            message.bcc_recipients = req.bcc
        message.save()
        result = self._map_message(message, folder="drafts")
        result["draft_id"] = str(message.id)
        result["webLink"] = ""
        return result

    def create_reply_draft(self, req: MailboxReplyComposeInput) -> dict[str, Any]:
        account = self._build_account()
        source = account.get_item(req.message_id)
        response = source.create_reply()
        response.body = req.body
        response.save()
        result = self._map_message(response, folder="drafts")
        result["draft_id"] = str(response.id)
        result["webLink"] = ""
        return result

    def update_draft(self, req: MailboxUpdateDraftInput) -> dict[str, Any] | None:
        account = self._build_account()
        message = account.get_item(req.draft_id)
        if req.subject is not None:
            message.subject = req.subject
        if req.body is not None:
            message.body = req.body
        if req.to is not None:
            message.to_recipients = req.to
        if req.cc is not None:
            message.cc_recipients = req.cc
        if req.bcc is not None:
            message.bcc_recipients = req.bcc
        message.save()
        result = self._map_message(message, folder="drafts")
        result["webLink"] = ""
        return result

    def send_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        account = self._build_account()
        message = account.get_item(req.draft_id)
        message.send()
        return {
            "id": req.draft_id,
            "folder": "sent",
            "sent": True,
            "status": "sent",
            "sent_summary": {
                "subject": self._safe_text(getattr(message, "subject", "")),
                "to": [
                    recipient.email_address
                    for recipient in getattr(message, "to_recipients", [])
                    if getattr(recipient, "email_address", None)
                ],
                "cc": [
                    recipient.email_address
                    for recipient in getattr(message, "cc_recipients", [])
                    if getattr(recipient, "email_address", None)
                ],
                "bcc": [
                    recipient.email_address
                    for recipient in getattr(message, "bcc_recipients", [])
                    if getattr(recipient, "email_address", None)
                ],
                "bodyPreview": self._preview_text(getattr(message, "body", None)),
            },
        }

    def revoke_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        account = self._build_account()
        message = account.get_item(req.draft_id)
        message.delete()
        return {
            "id": req.draft_id,
            "revoked": True,
            "status": "revoked",
            "folder": "drafts",
            "subject": self._safe_text(getattr(message, "subject", "")),
        }

    def _map_message(self, item: Any, folder: str | None = None) -> dict[str, Any]:
        sender = getattr(item, "sender", None)
        to_recipients = getattr(item, "to_recipients", []) or []
        cc_recipients = getattr(item, "cc_recipients", []) or []
        bcc_recipients = getattr(item, "bcc_recipients", []) or []
        body = getattr(item, "body", None)
        if hasattr(item, "text_body") and item.text_body is not None:
            body = item.text_body
        return {
            "id": str(getattr(item, "id", "")),
            "subject": self._safe_text(getattr(item, "subject", "")),
            "bodyPreview": self._preview_text(body),
            "from": {
                "emailAddress": {"address": self._safe_text(getattr(sender, "email_address", ""))}
            } if sender and getattr(sender, "email_address", None) else None,
            "toRecipients": self._recipient_list(to_recipients),
            "ccRecipients": self._recipient_list(cc_recipients),
            "bccRecipients": self._recipient_list(bcc_recipients),
            "isDraft": bool(getattr(item, "is_draft", False)),
            "receivedDateTime": self._utc_iso(getattr(item, "datetime_received", None)),
            "sentDateTime": self._utc_iso(getattr(item, "datetime_sent", None)),
            "folder": folder or "inbox",
            "webLink": "",
        }
