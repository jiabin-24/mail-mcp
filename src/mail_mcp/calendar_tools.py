from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from .calendar_store import CalendarStore
from .request_models import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarGetEventInput,
    CalendarListEventsInput,
    CalendarRespondInvitationInput,
    CalendarUpdateEventInput,
    validate_input,
)


def register_calendar_tools(app: FastMCP, calendar_store: CalendarStore) -> None:
    @app.tool()
    def calendar_list_events(
        start: str | None = None,
        end: str | None = None,
        search: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List calendar events, optionally scoped by ISO datetime range and keyword search."""
        req = validate_input(
            CalendarListEventsInput,
            {"start": start, "end": end, "search": search, "limit": limit},
        )
        return calendar_store.list_calendar_events(req)

    @app.tool()
    def calendar_get_event(event_id: str, calendar_id: str | None = None) -> dict:
        """Get a calendar event by ID."""
        req = validate_input(
            CalendarGetEventInput,
            {"event_id": event_id, "calendar_id": calendar_id},
        )

        event = calendar_store.get_calendar_event(req)
        if not event:
            raise ValueError(f"event not found: {req.event_id}")
        return event

    @app.tool()
    def calendar_create_event(
        subject: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
        is_all_day: bool = False,
        time_zone: str = "UTC",
        calendar_id: str | None = None,
    ) -> dict:
        """Create a calendar event in Outlook mailbox."""
        req = validate_input(
            CalendarCreateEventInput,
            {
                "subject": subject,
                "start": start,
                "end": end,
                "attendees": attendees,
                "description": description,
                "location": location,
                "is_all_day": is_all_day,
                "time_zone": time_zone,
                "calendar_id": calendar_id,
            },
        )
        return calendar_store.create_calendar_event(req)

    @app.tool()
    def calendar_update_event(
        event_id: str,
        subject: str | None = None,
        start: str | None = None,
        end: str | None = None,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
        is_all_day: bool | None = None,
        time_zone: str = "UTC",
        calendar_id: str | None = None,
    ) -> dict:
        """Update an existing calendar event in Outlook mailbox."""
        req = validate_input(
            CalendarUpdateEventInput,
            {
                "event_id": event_id,
                "subject": subject,
                "start": start,
                "end": end,
                "attendees": attendees,
                "description": description,
                "location": location,
                "is_all_day": is_all_day,
                "time_zone": time_zone,
                "calendar_id": calendar_id,
            },
        )
        updated = calendar_store.update_calendar_event(req)
        if not updated:
            raise ValueError(f"event not found: {req.event_id}")
        return updated

    @app.tool()
    def calendar_delete_event(event_id: str, calendar_id: str | None = None) -> dict:
        """Delete an existing calendar event in Outlook mailbox."""
        req = validate_input(
            CalendarDeleteEventInput,
            {"event_id": event_id, "calendar_id": calendar_id},
        )
        deleted = calendar_store.delete_calendar_event(req)
        if not deleted:
            raise ValueError(f"event not found: {req.event_id}")
        return deleted

    @app.tool()
    def calendar_respond_invitation(
        event_id: str,
        response: Literal["accept", "decline", "tentative"],
        comment: str | None = None,
        send_response: bool = True,
        calendar_id: str | None = None,
    ) -> dict:
        """Respond to a meeting invitation: accept, decline, or tentative."""
        req = validate_input(
            CalendarRespondInvitationInput,
            {
                "event_id": event_id,
                "response": response,
                "comment": comment,
                "send_response": send_response,
                "calendar_id": calendar_id,
            },
        )
        result = calendar_store.respond_calendar_invitation(req)
        if not result:
            raise ValueError(f"event not found: {req.event_id}")
        return result
