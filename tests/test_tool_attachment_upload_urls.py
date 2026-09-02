from unittest.mock import MagicMock

from mail_mcp.tools.calendar_tools import register_calendar_tools
from mail_mcp.tools.email_tools import register_email_tools


class FakeApp:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def register(func):
            self.tools[func.__name__] = func
            return func

        return register


def test_email_create_tools_add_attachment_upload_url(monkeypatch) -> None:
    monkeypatch.setenv("MAIL_ATTACHMENT_SERVICE_HOST", "https://attachments.example.com/")
    app = FakeApp()
    email_store = MagicMock()
    email_store.create_draft.return_value = {"draft_id": "draft/id"}
    email_store.create_reply_draft.return_value = {"id": "reply/id"}

    register_email_tools(app, email_store, MagicMock())

    composed = app.tools["mailbox_compose"](
        to=["user@example.com"],
        subject="Subject",
        body="Body",
    )
    replied = app.tools["mailbox_reply_compose"](
        message_id="source-id",
        subject="Re: Subject",
        body="Reply",
    )

    assert composed["attachment_upload_url"] == (
        "https://attachments.example.com/mails/draft%2Fid/attachments"
    )
    assert replied["attachment_upload_url"] == (
        "https://attachments.example.com/mails/reply%2Fid/attachments"
    )


def test_calendar_create_tool_adds_attachment_upload_url(monkeypatch) -> None:
    monkeypatch.setenv("MAIL_ATTACHMENT_SERVICE_HOST", "https://attachments.example.com/")
    app = FakeApp()
    calendar_store = MagicMock()
    calendar_store.create_calendar_event.return_value = {"id": "event/id"}

    register_calendar_tools(app, calendar_store)
    created = app.tools["calendar_create_event"](
        subject="Planning",
        start="2026-09-03T09:00:00+08:00",
        end="2026-09-03T10:00:00+08:00",
    )

    assert created["attachment_upload_url"] == (
        "https://attachments.example.com/mails/event%2Fid/attachments"
    )