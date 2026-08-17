from mail_mcp.stores.exchange_server import CalendarStore, EmailSendQueueStore, EmailStore
from mail_mcp.stores.exchange_server.base import ExchangeServerStoreBase


def test_exchange_server_store_exports() -> None:
    assert EmailStore is not None
    assert CalendarStore is not None
    assert EmailSendQueueStore is not None


def test_exchange_server_base_credentials_builds_oauth_token(monkeypatch) -> None:
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_ID", "client-id")
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_SECRET", "client-secret")
    store = ExchangeServerStoreBase(token_provider=lambda: "Bearer my-access-token")

    creds = store._credentials()

    assert creds.access_token["access_token"] == "my-access-token"
    assert creds.access_token["token_type"] == "Bearer"
