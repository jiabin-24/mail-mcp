from unittest.mock import MagicMock

from mail_mcp.schemas.request_models import MailboxComposeInput
from mail_mcp.stores.exchange_online.email_store import EmailStore
from mail_mcp.stores.exchange_online.graph_gateway import GraphGateway
from mail_mcp.stores.gateway_base import GatewayBase
from mail_mcp.utils.graph_request_client import GraphRequestClient


def test_exchange_online_graph_store_is_the_graph_implementation() -> None:
    assert issubclass(EmailStore, GraphGateway)
    assert issubclass(GraphGateway, GatewayBase)


def test_graph_gateway_delegates_requests_to_reusable_client() -> None:
    request_client = MagicMock(spec=GraphRequestClient)
    request_client.request.return_value = {"value": "result"}
    gateway = GraphGateway(lambda: "token", request_client=request_client)

    result = gateway._request(
        "POST",
        "/resource",
        json={"name": "value"},
        headers={"Prefer": "example"},
        expect_json=False,
    )

    assert result == {"value": "result"}
    request_client.request.assert_called_once_with(
        "POST",
        "/resource",
        json={"name": "value"},
        headers={"Prefer": "example"},
        expect_json=False,
    )


def test_create_draft_leaves_attachment_upload_url_to_tool_layer() -> None:
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

    assert result["draft_id"] == "draft/id"
    assert "attachment_upload_url" not in result
