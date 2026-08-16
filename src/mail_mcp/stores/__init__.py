"""Store layer for mailbox and calendar operations.

The provider-specific implementations live in dedicated subpackages, such as
``exchange_online`` for Microsoft Graph and ``exchange_server`` for EWS-backed
Exchange Server access.
"""

from .exchange_online import CalendarStore, EmailSendQueueStore, EmailStore
from .exchange_server import CalendarStore as ExchangeServerCalendarStore
from .exchange_server import EmailSendQueueStore as ExchangeServerEmailSendQueueStore
from .exchange_server import EmailStore as ExchangeServerEmailStore
from .graph_store import GraphStoreBase

__all__ = [
    "CalendarStore",
    "EmailSendQueueStore",
    "EmailStore",
    "ExchangeServerCalendarStore",
    "ExchangeServerEmailSendQueueStore",
    "ExchangeServerEmailStore",
    "GraphStoreBase",
]
