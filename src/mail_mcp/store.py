from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class MailStore:
    """Simple JSON-backed mailbox store for MCP scaffolding."""

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write(self._seed())

    def list_folders(self) -> list[str]:
        data = self._read()
        return sorted(data["folders"].keys())

    def list_messages(self, folder: str = "inbox", limit: int = 20) -> list[dict[str, Any]]:
        data = self._read()
        messages = data["folders"].get(folder, [])
        limit = max(1, min(limit, 100))
        return messages[:limit]

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        data = self._read()
        for folder_messages in data["folders"].values():
            for message in folder_messages:
                if message["id"] == message_id:
                    return message
        return None

    def search_messages(self, query: str, folder: str = "inbox", limit: int = 20) -> list[dict[str, Any]]:
        data = self._read()
        messages = data["folders"].get(folder, [])
        q = query.strip().lower()
        if not q:
            return []

        matched = []
        for message in messages:
            haystack = " ".join(
                [
                    message.get("from", ""),
                    " ".join(message.get("to", [])),
                    message.get("subject", ""),
                    message.get("body", ""),
                ]
            ).lower()
            if q in haystack:
                matched.append(message)
            if len(matched) >= max(1, min(limit, 100)):
                break
        return matched

    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        message = {
            "id": f"msg_{uuid4().hex[:10]}",
            "folder": "drafts",
            "from": "assistant@local",
            "to": to,
            "cc": cc or [],
            "bcc": bcc or [],
            "subject": subject,
            "body": body,
            "sent": False,
            "created_at": _now_iso(),
        }
        data["folders"]["drafts"].insert(0, message)
        self._write(data)
        return message

    def send_draft(self, draft_id: str) -> dict[str, Any] | None:
        data = self._read()
        drafts = data["folders"].get("drafts", [])
        for idx, draft in enumerate(drafts):
            if draft["id"] == draft_id:
                draft["sent"] = True
                draft["folder"] = "sent"
                draft["sent_at"] = _now_iso()
                moved = draft.copy()
                data["folders"]["sent"].insert(0, moved)
                del drafts[idx]
                self._write(data)
                return moved
        return None

    def _read(self) -> dict[str, Any]:
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _seed(self) -> dict[str, Any]:
        return {
            "folders": {
                "inbox": [
                    {
                        "id": "msg_inbox_001",
                        "folder": "inbox",
                        "from": "pm@contoso.com",
                        "to": ["you@contoso.com"],
                        "cc": [],
                        "bcc": [],
                        "subject": "Kickoff reminder",
                        "body": "Tomorrow 10:00 project kickoff, please prepare your update.",
                        "sent": True,
                        "created_at": _now_iso(),
                    },
                    {
                        "id": "msg_inbox_002",
                        "folder": "inbox",
                        "from": "hr@contoso.com",
                        "to": ["you@contoso.com"],
                        "cc": [],
                        "bcc": [],
                        "subject": "Leave balance",
                        "body": "Your annual leave balance has been updated.",
                        "sent": True,
                        "created_at": _now_iso(),
                    },
                ],
                "drafts": [],
                "sent": [],
            }
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
