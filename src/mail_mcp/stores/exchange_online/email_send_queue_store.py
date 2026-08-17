from __future__ import annotations

import os
from typing import Callable
from urllib.parse import quote

import httpx

from ..email_send_queue_store import EmailSendQueueStoreBase
from .graph_gateway import GraphGateway


class EmailSendQueueStore(EmailSendQueueStoreBase, GraphGateway):
    """Azure Table 定时发送任务的 Exchange Online Graph 适配器。"""

    def __init__(self, token_provider: Callable[[], str | None]) -> None:
        GraphGateway.__init__(self, token_provider=token_provider)
        EmailSendQueueStoreBase.__init__(self, token_provider, table_name=os.getenv("AZURE_STORAGE_TABLE_NAME"))

    def _resolve_user_upn(self) -> str:
        return self.resolve_current_user_upn()

    def _send_draft_for_job(self, *, user_upn: str, draft_email_id: str) -> None:
        self._send_draft_as_service_principal(user_upn=user_upn, draft_email_id=draft_email_id)

    def _send_draft_as_service_principal(self, *, user_upn: str, draft_email_id: str) -> None:
        credential = getattr(self, "_credential", None)
        if credential is None:
            raise ValueError("Service-principal Graph send requires Azure Table credentials to be configured.")

        token = credential.get_token("https://graph.microsoft.com/.default").token
        path = f"/users/{quote(user_upn, safe='')}/messages/{quote(draft_email_id, safe='')}/send"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        with httpx.Client(base_url=self._graph_base, timeout=30.0) as client:
            response = client.post(path, headers=headers)

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}
            raise ValueError(f"Graph send failed ({response.status_code}): {body}")
