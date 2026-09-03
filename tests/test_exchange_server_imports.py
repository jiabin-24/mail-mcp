from types import SimpleNamespace

from datetime import datetime

from exchangelib import HTMLBody

from mail_mcp.schemas.request_models import MailboxComposeInput, MailboxGetMessageInput
from mail_mcp.schemas.request_models import MailboxSearchInput
from mail_mcp.stores.exchange_server import CalendarStore, EmailSendQueueStore, EmailStore
from mail_mcp.stores.exchange_server.ews_gateway import EwsGateway


def test_exchange_server_store_exports() -> None:
    assert EmailStore is not None
    assert CalendarStore is not None
    assert EmailSendQueueStore is not None


def test_exchange_server_base_credentials_builds_oauth_token(monkeypatch) -> None:
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_ID", "client-id")
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_SECRET", "client-secret")
    store = EwsGateway(token_provider=lambda: "Bearer my-access-token")

    creds = store._credentials()

    assert creds.access_token["access_token"] == "my-access-token"
    assert creds.access_token["token_type"] == "Bearer"


def test_exchange_server_ews_gateway_uses_fixed_plus8_timezone_and_empty_tenant_users() -> None:
    store = EwsGateway(token_provider=lambda: "Bearer my-access-token")

    assert store.get_user_time_zone() == {"time_zone": "Asia/Shanghai", "source": "ews_fixed"}
    assert store.get_mailbox_time_zone_if_available() == "Asia/Shanghai"


def test_exchange_server_base_credentials_exchange_via_obo(monkeypatch) -> None:
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_ID", "client-id")
    monkeypatch.setenv("EXCHANGE_SERVER_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("EXCHANGE_SERVER_TENANT_ID", "tenant-id")

    class FakeConfidentialClientApplication:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def acquire_token_on_behalf_of(self, params):
            assert params["obo_assertion"] == "user-token"
            assert params["scope"] == ["https://outlook.office365.com/.default"]
            return {"access_token": "obo-access-token"}

    import msal
    monkeypatch.setattr(msal, "ConfidentialClientApplication", FakeConfidentialClientApplication)

    store = EwsGateway(token_provider=lambda: "user-token")
    creds = store._credentials()

    assert creds.access_token["access_token"] == "obo-access-token"
    assert creds.access_token["token_type"] == "Bearer"


def test_exchange_server_email_store_uses_ews_folder_get_for_item_lookup(monkeypatch) -> None:
    store = EmailStore(token_provider=lambda: "Bearer my-access-token")

    class FakeFolder:
        def __init__(self):
            self.calls = []

        def get(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                id="msg-123",
                subject="hello",
                body="body text",
                sender=SimpleNamespace(email_address="sender@example.com"),
                to_recipients=[],
                cc_recipients=[],
                bcc_recipients=[],
                is_draft=False,
                datetime_received="2024-01-01T00:00:00+00:00",
                datetime_sent="2024-01-01T00:00:00+00:00",
                text_body="body text",
            )

    fake_account = SimpleNamespace(root=FakeFolder(), inbox=FakeFolder(), drafts=FakeFolder(), sent=FakeFolder())
    monkeypatch.setattr(store, "_build_account", lambda: fake_account)

    result = store.get_message(MailboxGetMessageInput(message_id="msg-123"))

    assert result["id"] == "msg-123"
    assert fake_account.inbox.calls == [{"id": "msg-123"}]


def test_exchange_server_create_draft_preserves_html_body(monkeypatch) -> None:
    store = EmailStore(token_provider=lambda: "Bearer my-access-token")
    fake_account = SimpleNamespace(drafts=object())
    captured = {}

    class FakeMessage:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.__dict__.update(kwargs)
            self.id = "draft-123"
            self.is_draft = True

        def save(self):
            return self

    monkeypatch.setattr(store, "_build_account", lambda: fake_account)
    monkeypatch.setattr("mail_mcp.stores.exchange_server.email_store.Message", FakeMessage)

    result = store.create_draft(
        MailboxComposeInput(
            to=["recipient@example.com"],
            subject="HTML draft",
            body="<p>Hello <strong>world</strong></p>",
        )
    )

    assert isinstance(captured["body"], HTMLBody)
    assert str(captured["body"]) == "<p>Hello <strong>world</strong></p>"
    assert result["id"] == "draft-123"
    assert result["draft_id"] == "draft-123"


def test_exchange_server_update_draft_preserves_html_body(monkeypatch) -> None:
    store = EmailStore(token_provider=lambda: "Bearer my-access-token")

    class FakeMessage:
        def __init__(self):
            self.id = "draft-123"
            self.body = ""

        def save(self):
            return self

    message = FakeMessage()
    monkeypatch.setattr(store, "_get_item_by_id", lambda *_args, **_kwargs: message)

    store.update_draft(
        MailboxUpdateDraftInput(
            draft_id="draft-123",
            body="<p>Hello <strong>world</strong></p>",
        )
    )

    assert isinstance(message.body, HTMLBody)
    assert str(message.body) == "<p>Hello <strong>world</strong></p>"


def test_exchange_server_email_store_parses_received_datetime_filter() -> None:
    parsed = EmailStore._parse_received_datetime_filter(
        "receivedDateTime ge 2026-08-01T00:00:00+08:00 and receivedDateTime lt 2026-09-01T00:00:00+08:00"
    )

    assert parsed["datetime_received__gte"] == datetime.fromisoformat("2026-08-01T00:00:00+08:00")
    assert parsed["datetime_received__lt"] == datetime.fromisoformat("2026-09-01T00:00:00+08:00")


def test_exchange_server_email_store_search_uses_server_side_query(monkeypatch) -> None:
    store = EmailStore(token_provider=lambda: "Bearer my-access-token")

    class FakeQuery:
        def __init__(self):
            self.filter_calls = []
            self.order_by_calls = []

        def filter(self, **kwargs):
            self.filter_calls.append(kwargs)
            return self

        def order_by(self, *args):
            self.order_by_calls.append(args)
            return self

        def __getitem__(self, key):
            return [
                SimpleNamespace(
                    id="msg-001",
                    subject="monthly",
                    body="body",
                    sender=SimpleNamespace(email_address="sender@example.com"),
                    to_recipients=[],
                    cc_recipients=[],
                    bcc_recipients=[],
                    is_draft=False,
                    datetime_received="2026-08-17T00:00:00+00:00",
                    datetime_sent="2026-08-17T00:00:00+00:00",
                    text_body="body",
                )
            ]

    fake_query = FakeQuery()
    fake_folder = SimpleNamespace(all=lambda: fake_query)
    monkeypatch.setattr(store, "_folder_for_name", lambda folder: fake_folder)

    req = MailboxSearchInput(
        filter="receivedDateTime ge 2026-08-01T00:00:00+08:00 and receivedDateTime lt 2026-09-01T00:00:00+08:00",
        orderby="receivedDateTime desc",
        limit=20,
        folder="inbox",
    )
    result = store.search_messages(req)

    assert result[0]["id"] == "msg-001"
    assert fake_query.filter_calls[0] == {
        "datetime_received__gte": datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
        "datetime_received__lt": datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
    }
    assert fake_query.order_by_calls[0] == ("-datetime_received",)


def test_exchange_server_email_store_search_hard_caps_limit_to_100(monkeypatch) -> None:
    store = EmailStore(token_provider=lambda: "Bearer my-access-token")

    class FakeQuery:
        def __init__(self):
            self.slice_keys = []

        def filter(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        def __getitem__(self, key):
            self.slice_keys.append(key)
            return []

    fake_query = FakeQuery()
    fake_folder = SimpleNamespace(all=lambda: fake_query)
    monkeypatch.setattr(store, "_folder_for_name", lambda folder: fake_folder)

    req = MailboxSearchInput.model_construct(
        filter=None,
        search=None,
        orderby="receivedDateTime desc",
        limit=500,
        folder="inbox",
    )
    store.search_messages(req)

    assert len(fake_query.slice_keys) == 1
    assert fake_query.slice_keys[0].stop == 100
