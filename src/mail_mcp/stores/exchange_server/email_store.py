from __future__ import annotations

import re
import logging
from datetime import datetime
from typing import Any

from exchangelib import HTMLBody, Message

from ...schemas.request_models import (
    MailboxComposeInput,
    MailboxDraftIdInput,
    MailboxGetMessageInput,
    MailboxListMessagesInput,
    MailboxReplyComposeInput,
    MailboxSearchInput,
    MailboxUpdateDraftInput,
)
from ...utils.datetime_utils import format_utc_iso
from ...utils.recipient_utils import recipient_addresses
from mail_mcp.utils.email_helper import EmailHelper
from .ews_gateway import EwsGateway

LOGGER = logging.getLogger(__name__)


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
        message = self._get_item_by_id(req.message_id, folder="inbox")
        return self._map_message(message, folder="inbox")

    def search_messages(self, req: MailboxSearchInput) -> list[dict[str, Any]]:
        folder = self._folder_for_name(req.folder)
        effective_limit = max(1, min(int(req.limit), 100))
        search_text = (req.search or "").strip()
        filter_text = (req.filter or "").strip()
        filter_kwargs = self._parse_received_datetime_filter(filter_text)
        order_by_args = self._parse_orderby(req.orderby)
        query = folder.all()
        if filter_kwargs:
            query = query.filter(**filter_kwargs)
        elif filter_text:
            query = query.filter(subject__icontains=filter_text)

        if search_text:
            query = query.filter(subject__icontains=search_text)

        query = query.order_by(*order_by_args)
        items = query[: effective_limit]
        results = [self._map_message(item, folder=req.folder) for item in items]
        LOGGER.info("search_messages: found %s results for filter=%s, search=%s", len(results), req.filter, req.search)
        return results

    @staticmethod
    def _parse_orderby(orderby: str | None) -> tuple[str, ...]:
        raw = (orderby or "").strip()
        if not raw:
            return ("-datetime_received",)

        mapping = {
            "receiveddatetime": "datetime_received",
            "datetime_received": "datetime_received",
            "sentdatetime": "datetime_sent",
            "datetime_sent": "datetime_sent",
            "subject": "subject",
        }

        fields: list[str] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            pieces = token.split()
            source_field = pieces[0].lower()
            direction = pieces[1].lower() if len(pieces) > 1 else "asc"
            target_field = mapping.get(source_field)
            if not target_field:
                continue
            fields.append(f"-{target_field}" if direction == "desc" else target_field)

        return tuple(fields) if fields else ("-datetime_received",)

    @classmethod
    def _parse_received_datetime_filter(cls, filter_text: str | None) -> dict[str, datetime]:
        raw = (filter_text or "").strip()
        if not raw:
            return {}

        # Support Graph-like OData fragments such as:
        # receivedDateTime ge 2026-08-01T00:00:00+08:00 and receivedDateTime lt 2026-09-01T00:00:00+08:00
        parts = [part.strip() for part in re.split(r"\s+and\s+", raw, flags=re.IGNORECASE) if part.strip()]
        parsed: dict[str, datetime] = {}
        op_to_lookup = {
            "ge": "datetime_received__gte",
            "gt": "datetime_received__gt",
            "le": "datetime_received__lte",
            "lt": "datetime_received__lt",
            "eq": "datetime_received",
        }

        for part in parts:
            match = re.match(
                r"(?i)^(receivedDateTime|datetime_received)\s+(ge|gt|le|lt|eq)\s+(.+)$",
                part,
            )
            if not match:
                continue
            operator = match.group(2).lower()
            raw_value = match.group(3).strip().strip("\"'")
            value = cls._parse_iso_datetime(raw_value)
            parsed[op_to_lookup[operator]] = value

        return parsed

    def create_draft(self, req: MailboxComposeInput) -> dict[str, Any]:
        account = self._build_account()
        body = HTMLBody(req.body) if self._body_content_type(req.body) == "HTML" else req.body
        message = Message(
            account=account,
            folder=account.drafts,
            subject=req.subject,
            body=body,
            to_recipients=EmailHelper.mailboxes_from_addresses(req.to),
            cc_recipients=EmailHelper.mailboxes_from_addresses(req.cc),
            bcc_recipients=EmailHelper.mailboxes_from_addresses(req.bcc),
        )
        draft = message.save()
        draft_id = str(draft.id)
        result = self._map_message(draft, folder="drafts")
        result["id"] = draft_id
        result["draft_id"] = draft_id
        result["webLink"] = ""
        return result

    def create_reply_draft(self, req: MailboxReplyComposeInput) -> dict[str, Any]:
        source = self._get_item_by_id(req.message_id, folder="inbox")
        reply_item = source.create_reply(
            subject=req.subject,
            body=EmailHelper.plain_text_to_html_body(req.body),
        )
        saved_draft = reply_item.save(folder=source.account.drafts)
        draft_id = str(saved_draft.id)
        draft = self._get_item_by_id(draft_id, folder="drafts")
        result = self._map_message(draft, folder="drafts")
        result["id"] = draft_id
        result["draft_id"] = draft_id
        result["webLink"] = ""
        return result

    def update_draft(self, req: MailboxUpdateDraftInput) -> dict[str, Any] | None:
        message = self._get_item_by_id(req.draft_id, folder="drafts")
        if req.subject is not None:
            message.subject = req.subject
        if req.body is not None:
            message.body = req.body
        if req.to is not None:
            message.to_recipients = EmailHelper.mailboxes_from_addresses(req.to)
        if req.cc is not None:
            message.cc_recipients = EmailHelper.mailboxes_from_addresses(req.cc)
        if req.bcc is not None:
            message.bcc_recipients = EmailHelper.mailboxes_from_addresses(req.bcc)
        message.save()
        result = self._map_message(message, folder="drafts")
        result["webLink"] = ""
        return result

    def send_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        message = self._get_item_by_id(req.draft_id, folder="drafts")
        message.send()
        return {
            "id": req.draft_id,
            "folder": "sent",
            "sent": True,
            "status": "sent",
            "sent_summary": {
                "subject": EmailHelper.safe_text(getattr(message, "subject", "")),
                "to": recipient_addresses(getattr(message, "to_recipients", None)),
                "cc": recipient_addresses(getattr(message, "cc_recipients", None)),
                "bcc": recipient_addresses(getattr(message, "bcc_recipients", None)),
                "bodyPreview": EmailHelper.preview_text(getattr(message, "body", None)),
            },
        }

    def revoke_draft(self, req: MailboxDraftIdInput) -> dict[str, Any] | None:
        message = self._get_item_by_id(req.draft_id, folder="drafts")
        message.delete()
        return {
            "id": req.draft_id,
            "revoked": True,
            "status": "revoked",
            "folder": "drafts",
            "subject": EmailHelper.safe_text(getattr(message, "subject", "")),
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
            "subject": EmailHelper.safe_text(getattr(item, "subject", "")),
            "bodyPreview": EmailHelper.preview_text(body),
            "from": {
                "emailAddress": {"address": EmailHelper.safe_text(getattr(sender, "email_address", ""))}
            } if sender and getattr(sender, "email_address", None) else None,
            "toRecipients": EmailHelper.recipient_list(to_recipients),
            "ccRecipients": EmailHelper.recipient_list(cc_recipients),
            "bccRecipients": EmailHelper.recipient_list(bcc_recipients),
            "isDraft": bool(getattr(item, "is_draft", False)),
            "receivedDateTime": format_utc_iso(getattr(item, "datetime_received", None)),
            "sentDateTime": format_utc_iso(getattr(item, "datetime_sent", None)),
            "folder": folder or "inbox",
            "webLink": "",
        }
