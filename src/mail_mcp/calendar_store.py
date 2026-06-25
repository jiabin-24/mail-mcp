from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .graph_store import GraphStoreBase, recipient_address, recipient_addresses


GRAPH_QUERY_SAFE = "()':,=-"
GRAPH_DATETIME_SAFE = "-:.TZ"


class CalendarStore(GraphStoreBase):
    """Calendar-focused operations backed by Microsoft Graph calendar APIs."""

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
        return [self._map_calendar_event(item) for item in payload.get("value", [])]

    def _map_calendar_event(self, event: dict[str, Any]) -> dict[str, Any]:
        organizer = event.get("organizer", {}) or {}
        location = event.get("location", {}) or {}
        start = event.get("start", {}) or {}
        end = event.get("end", {}) or {}
        return {
            "id": event.get("id", ""),
            "subject": event.get("subject", "") or "",
            "bodyPreview": event.get("bodyPreview", "") or "",
            "organizer": recipient_address(organizer),
            "attendees": recipient_addresses(event.get("attendees", [])),
            "location": str(location.get("displayName", "") or ""),
            "isAllDay": bool(event.get("isAllDay", False)),
            "start": {
                "dateTime": str(start.get("dateTime", "") or ""),
                "timeZone": str(start.get("timeZone", "") or ""),
            },
            "end": {
                "dateTime": str(end.get("dateTime", "") or ""),
                "timeZone": str(end.get("timeZone", "") or ""),
            },
            "webLink": event.get("webLink", "") or "",
        }