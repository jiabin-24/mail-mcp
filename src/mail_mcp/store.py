from __future__ import annotations

from html import escape
import os
from typing import Any, Callable
from urllib.parse import quote

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
        messages_path = f"{self._mailbox_prefix}/mailFolders/{self._folder_segment(folder)}/messages"
        select_clause = (
            "id,subject,bodyPreview,from,toRecipients,ccRecipients,bccRecipients,isDraft,"
            "receivedDateTime,sentDateTime"
        )
        filter_expr, search_term = self._parse_filter_and_search(q)

        if filter_expr and search_term:
            # 混合条件：先用 Graph 的 filter 缩小范围，再在本地做关键词匹配。
            candidate_top = max(size * 5, 50)
            candidate_top = min(candidate_top, 100)
            encoded_filter = quote(filter_expr, safe="()':,=-")
            payload = self._request(
                "GET",
                f"{messages_path}?$filter={encoded_filter}&$top={candidate_top}&$orderby=receivedDateTime desc"
                f"&$select={select_clause}",
            )
            messages = [self._map_message(item, folder=folder, prefer_preview=True) for item in payload.get("value", [])]
            matched = [msg for msg in messages if self._matches_keyword(msg, search_term)]
            return matched[:size]

        if filter_expr:
            # 纯过滤条件：直接走 $filter（例如 receivedDateTime 时间区间）。
            encoded_filter = quote(filter_expr, safe="()':,=-")
            payload = self._request(
                "GET",
                f"{messages_path}?$filter={encoded_filter}&$top={size}&$orderby=receivedDateTime desc"
                f"&$select={select_clause}",
            )
            return [self._map_message(item, folder=folder, prefer_preview=True) for item in payload.get("value", [])]

        if search_term:
            # 纯关键词：走 $search，保持与 Outlook 搜索语义一致。
            encoded_search = quote(search_term, safe="")
            payload = self._request(
                "GET",
                f"{messages_path}?$search=%22{encoded_search}%22&$top={size}&$select={select_clause}",
                headers={"ConsistencyLevel": "eventual"},
            )
            return [self._map_message(item, folder=folder, prefer_preview=True) for item in payload.get("value", [])]

        return []

    def _parse_filter_and_search(self, query: str) -> tuple[str | None, str | None]:
        raw = query.strip()
        # 支持显式标签格式："filter: ... search: ..."。
        tagged = self._parse_tagged_query(raw)
        if tagged is not None:
            return tagged

        # 未显式标注时，尝试把整句识别为 filter；否则按关键词搜索处理。
        if self._looks_like_graph_filter(query):
            return query, None

        return None, query

    def _parse_tagged_query(self, raw: str) -> tuple[str | None, str | None] | None:
        lowered = raw.lower()
        filter_tag = "filter:"
        search_tag = "search:"
        filter_idx = lowered.find(filter_tag)
        search_idx = lowered.find(search_tag)

        if filter_idx == -1 and search_idx == -1:
            return None

        if filter_idx != -1 and search_idx != -1:
            if filter_idx < search_idx:
                filter_expr = raw[filter_idx + len(filter_tag) : search_idx].strip()
                search_term = raw[search_idx + len(search_tag) :].strip()
            else:
                search_term = raw[search_idx + len(search_tag) : filter_idx].strip()
                filter_expr = raw[filter_idx + len(filter_tag) :].strip()
            return filter_expr or None, search_term or None

        if filter_idx != -1:
            return raw[filter_idx + len(filter_tag) :].strip() or None, None

        return None, raw[search_idx + len(search_tag) :].strip() or None

    def _matches_keyword(self, message: dict[str, Any], keyword: str) -> bool:
        q = keyword.strip().lower()
        if not q:
            return True
        haystack = " ".join(
            [
                str(message.get("subject", "") or ""),
                str(message.get("bodyPreview", "") or ""),
                str(message.get("from", "") or ""),
                " ".join(message.get("to", [])),
                " ".join(message.get("cc", [])),
                " ".join(message.get("bcc", [])),
            ]
        ).lower()
        return q in haystack

    def _looks_like_graph_filter(self, query: str) -> bool:
        q = query.lower()
        has_date_field = "receiveddatetime" in q
        has_op = any(op in q for op in (" ge ", " gt ", " le ", " lt ", " eq "))
        return has_date_field and has_op

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
        result["webLink"] = payload.get("webLink", "")
        return result

    def create_reply_draft(self, message_id: str, body: str) -> dict[str, Any]:
        if not message_id.strip():
            raise ValueError("message_id cannot be empty")
        if not body.strip():
            raise ValueError("body cannot be empty")

        # createReply 会生成带历史引用的草稿。
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
                "to": _recipient_addresses(draft.get("toRecipients", [])),
                "cc": _recipient_addresses(draft.get("ccRecipients", [])),
                "bcc": _recipient_addresses(draft.get("bccRecipients", [])),
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
        token = self._token_provider() or os.getenv("OUTLOOK_ACCESS_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "No Outlook token available. Provide bearer token in Authorization header or set OUTLOOK_ACCESS_TOKEN."
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

    def _plain_text_to_html(self, text: str) -> str:
        safe = escape(text.strip())
        return safe.replace("\n", "<br/>")

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
