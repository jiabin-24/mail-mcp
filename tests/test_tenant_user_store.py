from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mail_mcp.stores.graph_application_store import GRAPH_SCOPE, GraphApplicationStore


def test_list_tenant_users_uses_client_credential_token() -> None:
    credential = MagicMock()
    credential.get_token.return_value = SimpleNamespace(token="application-token")
    response = MagicMock(
        status_code=200,
        content=b'{"value": []}',
    )
    response.json.return_value = {"value": []}

    with patch("mail_mcp.utils.graph_request_client.httpx.Client") as client_class:
        client_class.return_value.__enter__.return_value.request.return_value = response
        store = GraphApplicationStore(credential)

        assert store.list_tenant_users() == []

    credential.get_token.assert_called_once_with(GRAPH_SCOPE)
    request = client_class.return_value.__enter__.return_value.request
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer application-token"