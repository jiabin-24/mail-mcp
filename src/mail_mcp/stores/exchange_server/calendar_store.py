from __future__ import annotations

import logging
from typing import Any, cast

from exchangelib import Attendee, CalendarItem, Mailbox
from exchangelib.errors import DoesNotExist

from ...schemas.request_models import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarGetEventInput,
    CalendarListEventsInput,
    CalendarRespondInvitationInput,
    CalendarUpdateEventInput,
)
from ...utils.datetime_utils import format_utc_iso
from mail_mcp.utils.email_helper import EmailHelper
from .ews_gateway import EwsGateway


LOGGER = logging.getLogger(__name__)


class CalendarStore(EwsGateway):
    """基于 Exchange Server EWS 的日历操作。"""

    def get_calendar_event(self, req: CalendarGetEventInput) -> dict[str, Any] | None:
        event = self._get_event_or_none(req.event_id)
        if event is None:
            return None
        return self._map_event(event)

    def create_calendar_event(self, req: CalendarCreateEventInput) -> dict[str, Any]:
        account = self._build_account()
        event: Any = CalendarItem(folder=account.calendar)
        event.subject = req.subject
        event.start = self._parse_iso_datetime_with_time_zone(req.start, req.time_zone)
        event.end = self._parse_iso_datetime_with_time_zone(req.end, req.time_zone)
        if req.location:
            event.location = req.location
        if req.description:
            event.body = req.description
        if req.attendees:
            event.required_attendees = self._attendees_from_addresses(req.attendees)
        if req.is_all_day:
            event.is_all_day = True
        event.save()
        return self._map_event(event)

    def update_calendar_event(self, req: CalendarUpdateEventInput) -> dict[str, Any] | None:
        event = self._get_event_or_none(req.event_id)
        if event is None:
            return None
        if req.subject is not None:
            event.subject = req.subject
        if req.start is not None and req.end is not None:
            event.start = self._parse_iso_datetime_with_time_zone(req.start, req.time_zone)
            event.end = self._parse_iso_datetime_with_time_zone(req.end, req.time_zone)
        if req.location is not None:
            event.location = req.location
        if req.description is not None:
            event.body = req.description
        if req.attendees is not None:
            attendees = self._attendees_from_addresses(req.attendees)
            event.required_attendees = attendees
            LOGGER.info("EWS update calendar attendees requested: event_id=%s attendees=%s", req.event_id, req.attendees)
        if req.is_all_day is not None:
            event.is_all_day = bool(req.is_all_day)
        event.save()
        return self._map_event(event)

    def delete_calendar_event(self, req: CalendarDeleteEventInput) -> dict[str, Any] | None:
        event = self._get_event_or_none(req.event_id)
        if event is None:
            return None
        event.delete()
        return {"id": req.event_id, "deleted": True, "status": "deleted"}

    def respond_calendar_invitation(self, req: CalendarRespondInvitationInput) -> dict[str, Any] | None:
        event = self._get_event_or_none(req.event_id)
        if event is None:
            return None
        response = {
            "accept": "Accept",
            "decline": "Decline",
            "tentative": "TentativelyAccept",
        }[req.response]
        getattr(event, response.lower())(comment=req.comment or "")
        return {"id": req.event_id, "status": "responded", "response": req.response}

    def list_calendar_events(self, req: CalendarListEventsInput) -> list[dict[str, Any]]:
        account = self._build_account()
        calendar = account.calendar
        size = self._normalize_limit(req.limit)
        start_dt = None
        end_dt = None
        if req.start and req.end:
            start_dt = self._parse_iso_datetime_with_time_zone(req.start)
            end_dt = self._parse_iso_datetime_with_time_zone(req.end)
        items = cast(Any, calendar).view(start=start_dt, end=end_dt)[:size]
        return [self._map_event(item) for item in items]

    def _map_event(self, event: Any) -> dict[str, Any]:
        start = getattr(event, "start", None)
        end = getattr(event, "end", None)
        organizer = getattr(event, "organizer", None)
        return {
            "id": str(getattr(event, "id", "")),
            "subject": EmailHelper.safe_text(getattr(event, "subject", "")),
            "bodyPreview": EmailHelper.preview_text(getattr(event, "body", None)),
            "organizer": {
                "emailAddress": {"address": EmailHelper.safe_text(getattr(organizer, "email_address", ""))}
            } if organizer and getattr(organizer, "email_address", None) else None,
            "attendees": EmailHelper.recipient_list(getattr(event, "required_attendees", []) or []),
            "start": format_utc_iso(start),
            "end": format_utc_iso(end),
            "location": {"displayName": EmailHelper.safe_text(getattr(event, "location", ""))},
            "isAllDay": bool(getattr(event, "is_all_day", False)),
            "webLink": "",
        }

    @staticmethod
    def _attendees_from_addresses(addresses: list[str] | None) -> list[Attendee]:
        attendees: list[Attendee] = []
        for address in addresses or []:
            normalized = (address or "").strip()
            if not normalized:
                continue
            attendees.append(Attendee(mailbox=Mailbox(email_address=normalized)))
        return attendees

    def _get_event_or_none(self, event_id: str) -> Any | None:
        account = self._build_account()
        calendar = account.calendar
        normalized_event_id = self._normalize_event_id(event_id)
        try:
            return cast(Any, calendar).get(id=normalized_event_id)
        except DoesNotExist:
            pass

        try:
            # Fallback to mailbox-wide fetch in case the item is not in the default calendar folder.
            items = list(cast(Any, account).fetch(ids=[normalized_event_id]))
            return items[0] if items else None
        except Exception:
            return None

    @staticmethod
    def _normalize_event_id(event_id: str) -> str:
        # Event IDs should not contain whitespace; chat copy/paste may introduce line breaks.
        return "".join((event_id or "").split())
