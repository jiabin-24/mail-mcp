from unittest.mock import MagicMock

from mail_mcp.schemas.request_models import MailboxComposeInput
from mail_mcp.stores.exchange_online.email_store import EmailStore
from mail_mcp.stores.exchange_online.graph_gateway import GraphGateway
from mail_mcp.stores.gateway_base import GatewayBase


def test_exchange_online_graph_store_is_the_graph_implementation() -> None:
    assert issubclass(EmailStore, GraphGateway)
    assert issubclass(GraphGateway, GatewayBase)


def test_create_draft_returns_configured_attachment_upload_url(monkeypatch) -> None:
    monkeypatch.setenv("MAIL_ATTACHMENT_SERVICE_HOST", "https://attachments.example.com/")
    store = EmailStore(lambda: "token")
    store._request = MagicMock(
        return_value={
            "id": "draft/id",
            "subject": "Subject",
            "body": {"contentType": "Text", "content": "Body"},
            "toRecipients": [{"emailAddress": {"address": "user@example.com"}}],
            "webLink": "https://outlook.example.com/draft",
        }
    )

    result = store.create_draft(
        MailboxComposeInput(
            to=["user@example.com"],
            subject="Subject",
            body="Body",
        )
    )

    assert result["attachment_upload_url"] == (
        "https://attachments.example.com/draft%2Fid/attachments?uploaded=1"
    )
