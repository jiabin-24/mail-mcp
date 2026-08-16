from mail_mcp.stores.exchange_server import CalendarStore, EmailSendQueueStore, EmailStore


def test_exchange_server_store_exports() -> None:
    assert EmailStore is not None
    assert CalendarStore is not None
    assert EmailSendQueueStore is not None
