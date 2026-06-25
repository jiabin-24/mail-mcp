from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .graph_store import GraphStoreBase
from .models import map_graph_calendar_event


GRAPH_QUERY_SAFE = "()':,=-"
GRAPH_DATETIME_SAFE = "-:.TZ"


class CalendarStore(GraphStoreBase):
    """Calendar-focused operations backed by Microsoft Graph calendar APIs."""

    def create_calendar_event(
        self,
        subject: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
        is_all_day: bool = False,
        time_zone: str = "UTC",
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        subject_value = subject.strip()
        start_value = start.strip()
        end_value = end.strip()
        time_zone_value = time_zone.strip()

        if not subject_value:
            raise ValueError("subject cannot be empty")
        if not start_value:
            raise ValueError("start cannot be empty")
        if not end_value:
            raise ValueError("end cannot be empty")
        if not time_zone_value:
            raise ValueError("time_zone cannot be empty")

        payload: dict[str, Any] = {
            "subject": subject_value,
            "start": {"dateTime": start_value, "timeZone": time_zone_value},
            "end": {"dateTime": end_value, "timeZone": time_zone_value},
            "isAllDay": bool(is_all_day),
            "attendees": self._emails_to_attendees(attendees or []),
        }

        description_value = (description or "").strip()
        if description_value:
            payload["body"] = {"contentType": "Text", "content": description_value}

        location_value = (location or "").strip()
        if location_value:
            payload["location"] = {"displayName": location_value}

        calendar_id_value = (calendar_id or "").strip()
        if calendar_id_value:
            path = f"{self._mailbox_prefix}/calendars/{calendar_id_value}/events"
        else:
            path = f"{self._mailbox_prefix}/events"

        created = self._request(
            "POST",
            path,
            json=payload,
        )
        return map_graph_calendar_event(created)

    def list_calendar_events(
        self,
        limit: int = 20,
        search: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        size = self._normalize_limit(limit)
        search_value = (search or "").strip()
        start_value = (start or "").strip()
        end_value = (end or "").strip()

        if bool(start_value) != bool(end_value):
            raise ValueError("start and end must be provided together")

        select_clause = "id,subject,bodyPreview,organizer,attendees,start,end,location,isAllDay,webLink"
        params: list[str] = [f"$top={size}", "$orderby=start/dateTime", f"$select={select_clause}"]

        if search_value:
            encoded_search = quote(search_value, safe=GRAPH_QUERY_SAFE)
            params.append(f"$search={encoded_search}")

        headers = {"ConsistencyLevel": "eventual"} if search_value else None

        if start_value and end_value:
            encoded_start = quote(start_value, safe=GRAPH_DATETIME_SAFE)
            encoded_end = quote(end_value, safe=GRAPH_DATETIME_SAFE)
            path = (
                f"{self._mailbox_prefix}/calendarView"
                f"?startDateTime={encoded_start}&endDateTime={encoded_end}&{'&'.join(params)}"
            )
        else:
            now = datetime.now(timezone.utc)
            default_end = now + timedelta(days=30)
            path = (
                f"{self._mailbox_prefix}/calendarView"
                f"?startDateTime={quote(now.isoformat(), safe=GRAPH_DATETIME_SAFE)}&"
                f"endDateTime={quote(default_end.isoformat(), safe=GRAPH_DATETIME_SAFE)}&{'&'.join(params)}"
            )

        payload = self._request("GET", path, headers=headers)
        return [map_graph_calendar_event(item) for item in payload.get("value", [])]

    def _emails_to_attendees(self, emails: list[str]) -> list[dict[str, Any]]:
        attendees: list[dict[str, Any]] = []
        for email in emails:
            address = email.strip()
            if not address:
                continue
            attendees.append(
                {
                    "emailAddress": {"address": address},
                    "type": "required",
                }
            )
        return attendees