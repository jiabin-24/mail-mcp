from __future__ import annotations

from datetime import datetime
from typing import Any

from ...schemas.request_models import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarGetEventInput,
    CalendarListEventsInput,
    CalendarRespondInvitationInput,
    CalendarUpdateEventInput,
)
from .base import ExchangeServerStoreBase


class CalendarStore(ExchangeServerStoreBase):
    """基于 Exchange Server EWS 的日历操作。"""

    def get_calendar_event(self, req: CalendarGetEventInput) -> dict[str, Any] | None:
        account = self._build_account()
        event = account.calendar.get(req.event_id)
        return self._map_event(event)

    def create_calendar_event(self, req: CalendarCreateEventInput) -> dict[str, Any]:
        account = self._build_account()
        event = account.calendar.create_item()
        event.subject = req.subject
        event.start = datetime.fromisoformat(req.start)
        event.end = datetime.fromisoformat(req.end)
        if req.location:
            event.location = req.location
        if req.description:
            event.body = req.description
        if req.attendees:
            event.required_attendees = req.attendees
        event.save()
        return self._map_event(event)

    def update_calendar_event(self, req: CalendarUpdateEventInput) -> dict[str, Any] | None:
        account = self._build_account()
        event = account.calendar.get(req.event_id)
        if req.subject is not None:
            event.subject = req.subject
        if req.start is not None and req.end is not None:
            event.start = datetime.fromisoformat(req.start)
            event.end = datetime.fromisoformat(req.end)
        if req.location is not None:
            event.location = req.location
        if req.description is not None:
            event.body = req.description
        if req.attendees is not None:
            event.required_attendees = req.attendees
        event.save()
        return self._map_event(event)

    def delete_calendar_event(self, req: CalendarDeleteEventInput) -> dict[str, Any] | None:
        account = self._build_account()
        event = account.calendar.get(req.event_id)
        event.delete()
        return {"id": req.event_id, "deleted": True, "status": "deleted"}

    def respond_calendar_invitation(self, req: CalendarRespondInvitationInput) -> dict[str, Any] | None:
        account = self._build_account()
        event = account.calendar.get(req.event_id)
        response = {
            "accept": "Accept",
            "decline": "Decline",
            "tentative": "TentativelyAccept",
        }[req.response]
        getattr(event, response.lower())(comment=req.comment or "")
        return {"id": req.event_id, "status": "responded", "response": req.response}

    def list_calendar_events(self, req: CalendarListEventsInput) -> list[dict[str, Any]]:
        account = self._build_account()
        start_dt = None
        end_dt = None
        if req.start and req.end:
            start_dt = datetime.fromisoformat(req.start)
            end_dt = datetime.fromisoformat(req.end)
        items = account.calendar.view(start=start_dt, end=end_dt)[: req.limit]
        return [self._map_event(item) for item in items]

    def _map_event(self, event: Any) -> dict[str, Any]:
        start = getattr(event, "start", None)
        end = getattr(event, "end", None)
        organizer = getattr(event, "organizer", None)
        return {
            "id": str(getattr(event, "id", "")),
            "subject": self._safe_text(getattr(event, "subject", "")),
            "bodyPreview": self._preview_text(getattr(event, "body", None)),
            "organizer": {
                "emailAddress": {"address": self._safe_text(getattr(organizer, "email_address", ""))}
            } if organizer and getattr(organizer, "email_address", None) else None,
            "attendees": [
                {"emailAddress": {"address": self._safe_text(getattr(attendee, "email_address", ""))}}
                for attendee in getattr(event, "required_attendees", []) or []
                if getattr(attendee, "email_address", None)
            ],
            "start": self._utc_iso(start),
            "end": self._utc_iso(end),
            "location": {"displayName": self._safe_text(getattr(event, "location", ""))},
            "isAllDay": bool(getattr(event, "is_all_day", False)),
            "webLink": "",
        }
