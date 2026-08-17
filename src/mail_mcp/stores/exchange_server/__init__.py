"""Exchange Server EWS-backed store implementations.

This package mirrors the Graph-backed store API but uses Exchange Web Services
instead of Microsoft Graph for on-premises or self-hosted Exchange Server.
"""

from .calendar_store import CalendarStore
from .email_send_queue_store import EmailSendQueueStore
from .email_store import EmailStore
from .ews_gateway import EwsGateway

__all__ = [
    "CalendarStore",
    "EmailSendQueueStore",
    "EmailStore",
    "EwsGateway",
]
