"""Backward-compatible import for the Exchange Online queued send store."""

from .exchange_online.email_send_queue_store import EmailSendQueueStore

__all__ = ["EmailSendQueueStore"]
