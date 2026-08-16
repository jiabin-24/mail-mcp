from __future__ import annotations

import os
from typing import Callable

from ..email_send_queue_store import EmailSendQueueStoreBase
from ..graph_store import GraphStoreBase


class EmailSendQueueStore(EmailSendQueueStoreBase, GraphStoreBase):
    """Graph-backed adapter for Azure Table scheduled send jobs."""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        GraphStoreBase.__init__(self, token_provider=token_provider)
        EmailSendQueueStoreBase.__init__(self, token_provider, table_name=os.getenv("AZURE_STORAGE_TABLE_NAME"))

    def _resolve_user_upn(self) -> str:
        return self.resolve_current_user_upn()

    def _send_draft_for_job(self, *, user_upn: str, draft_email_id: str) -> None:
        self._send_draft_as_service_principal(user_upn=user_upn, draft_email_id=draft_email_id)

    def _send_draft_as_service_principal(self, *, user_upn: str, draft_email_id: str) -> None:
        _ = user_upn, draft_email_id
        raise NotImplementedError("Override in service-principal implementation")
