from __future__ import annotations

import os
from typing import Callable

from ..email_send_queue_store import EmailSendQueueStoreBase
from .ews_gateway import EwsGateway


class EmailSendQueueStore(EmailSendQueueStoreBase, EwsGateway):
    """共享 Azure Table 队列契约的 EWS 适配器。

    这个后端保留与 Graph 版本一致的 API 入口，但当前 EWS 环境下
    还没有实现真正的服务主体发送路径，需要后续按实际部署方式补齐。
    """

    def __init__(self, token_provider: Callable[[], str | None] | None = None) -> None:
        EwsGateway.__init__(self, token_provider=token_provider)
        EmailSendQueueStoreBase.__init__(self, token_provider or (lambda: None), table_name=os.getenv("AZURE_STORAGE_TABLE_NAME"))

    def _resolve_user_upn(self) -> str:
        mailbox = self._resolve_current_mailbox().strip()
        if mailbox:
            return mailbox
        raise ValueError("The current bearer token does not expose a mailbox identity for EWS queue operations")

    def _send_draft_for_job(self, *, user_upn: str, draft_email_id: str) -> None:
        _ = user_upn, draft_email_id
        raise NotImplementedError("EWS queue send execution is not implemented in this adapter")
