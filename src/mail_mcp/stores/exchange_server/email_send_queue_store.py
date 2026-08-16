from __future__ import annotations

import os
from typing import Callable

from ..email_send_queue_store import EmailSendQueueStoreBase
from .base import ExchangeServerStoreBase


class EmailSendQueueStore(EmailSendQueueStoreBase, ExchangeServerStoreBase):
    """EWS adapter for the shared Azure Table queue contract.

    This backend keeps the same API surface as the Graph store, but it does not
    implement the actual service-principal sending path in the EWS environment.
    """

    def __init__(self, token_provider: Callable[[], str | None] | None = None) -> None:
        ExchangeServerStoreBase.__init__(self, token_provider=token_provider)
        EmailSendQueueStoreBase.__init__(self, token_provider or (lambda: None), table_name=os.getenv("AZURE_STORAGE_TABLE_NAME"))

    def _resolve_user_upn(self) -> str:
        mailbox = (self._mailbox or "").strip()
        if mailbox:
            return mailbox
        raise ValueError("EXCHANGE_SERVER_MAILBOX is required for EWS queue operations")

    def _send_draft_for_job(self, *, user_upn: str, draft_email_id: str) -> None:
        _ = user_upn, draft_email_id
        raise NotImplementedError("EWS queue send execution is not implemented in this adapter")
