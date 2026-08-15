"""Backward-compatible import for the Exchange Online calendar store."""

from .exchange_online.calendar_store import CalendarStore

__all__ = ["CalendarStore"]
