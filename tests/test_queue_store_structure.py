from mail_mcp.stores.email_send_queue_store import EmailSendQueueStoreBase
from mail_mcp.stores.exchange_online.email_send_queue_store import EmailSendQueueStore as GraphEmailSendQueueStore
from mail_mcp.stores.exchange_server.email_send_queue_store import EmailSendQueueStore as EwsEmailSendQueueStore


def test_queue_store_base_is_shared_across_backends() -> None:
    assert issubclass(GraphEmailSendQueueStore, EmailSendQueueStoreBase)
    assert issubclass(EwsEmailSendQueueStore, EmailSendQueueStoreBase)
