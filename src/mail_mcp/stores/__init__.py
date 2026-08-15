"""Store layer for Graph-backed mailbox and calendar operations.

The provider-specific implementations for Exchange Online live under the
``exchange_online`` subpackage. The parent package keeps the common ``GraphStoreBase``
and compatibility exports for existing imports.
"""

from .exchange_online import CalendarStore, EmailSendQueueStore, EmailStore
from .graph_store import GraphStoreBase

__all__ = [
    "CalendarStore",
    "EmailSendQueueStore",
    "EmailStore",
    "GraphStoreBase",
]
