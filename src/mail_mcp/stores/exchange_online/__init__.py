"""Exchange Online-specific store implementations.

These stores depend on Microsoft Graph / Exchange Online APIs and are kept
separate from the generic, provider-agnostic store layer in the parent package.
"""

from .calendar_store import CalendarStore
from .email_send_queue_store import EmailSendQueueStore
from .email_store import EmailStore
from .graph_gateway import GraphGateway

__all__ = [
    "CalendarStore",
    "EmailSendQueueStore",
    "EmailStore",
    "GraphGateway",
]
