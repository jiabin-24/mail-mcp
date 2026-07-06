from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import quote

from .graph_store import GraphStoreBase
from ..models import map_graph_calendar_event
from ..schemas.request_models import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarGetEventInput,
    CalendarListEventsInput,
    CalendarRespondInvitationInput,
    CalendarUpdateEventInput,
)


GRAPH_QUERY_SAFE = "()':,=-"
GRAPH_DATETIME_SAFE = "-:.TZ"


class CalendarStore(GraphStoreBase):
    """Calendar-focused operations backed by Microsoft Graph calendar APIs."""

    def get_calendar_event(self, req: CalendarGetEventInput) -> dict[str, Any] | None:
        payload = self._request(
            "GET",
            f"{self._event_path(req.event_id, req.calendar_id)}"
            "?$select=id,subject,bodyPreview,organizer,attendees,start,end,location,isAllDay,webLink",
        )
        return map_graph_calendar_event(payload)

    def create_calendar_event(self, req: CalendarCreateEventInput) -> dict[str, Any]:
        event_time_zone = self._resolve_event_time_zone(req.time_zone)
        payload: dict[str, Any] = {
            "subject": req.subject,
            "start": {"dateTime": req.start, "timeZone": event_time_zone},
            "end": {"dateTime": req.end, "timeZone": event_time_zone},
            "isAllDay": bool(req.is_all_day),
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
            "attendees": self._emails_to_attendees(req.attendees or []),
        }

        if req.description:
            payload["body"] = {"contentType": "Text", "content": req.description}

        if req.location:
            payload["location"] = {"displayName": req.location}

        if req.calendar_id:
            path = f"{self._mailbox_prefix}/calendars/{req.calendar_id}/events"
        else:
            path = f"{self._mailbox_prefix}/events"

        created = self._request(
            "POST",
            path,
            json=payload,
        )
        result = map_graph_calendar_event(created)
        result["draft_id"] = created.get("id", "")
        return result

    def update_calendar_event(self, req: CalendarUpdateEventInput) -> dict[str, Any] | None:
        start_value = req.start
        end_value = req.end
        event_time_zone = self._resolve_event_time_zone(req.time_zone)
        current_event: dict[str, Any] | None = None

        patch_payload: dict[str, Any] = {}
        if req.subject is not None:
            patch_payload["subject"] = req.subject
        if start_value and end_value:
            patch_payload["start"] = {"dateTime": start_value, "timeZone": event_time_zone}
            patch_payload["end"] = {"dateTime": end_value, "timeZone": event_time_zone}
        if req.attendees is not None:
            patch_payload["attendees"] = self._emails_to_attendees(req.attendees)
        if req.description is not None:
            current_event = self._request(
                "GET",
                f"{self._event_path(req.event_id, req.calendar_id)}?$select=body,isOnlineMeeting",
            )
            if bool(current_event.get("isOnlineMeeting", False)):
                existing_body_html = str((current_event.get("body") or {}).get("content", "") or "")
                patch_payload["body"] = {
                    "contentType": "HTML",
                    "content": self._compose_online_meeting_body(req.description, existing_body_html),
                }
            else:
                patch_payload["body"] = {"contentType": "Text", "content": req.description}
        if req.location is not None:
            patch_payload["location"] = {"displayName": req.location}
        if req.is_all_day is not None:
            patch_payload["isAllDay"] = bool(req.is_all_day)

        if not patch_payload:
            return {
                "id": req.event_id,
                "status": "no_change",
                "message": "no updates provided",
            }

        patch_payload["isOnlineMeeting"] = True
        patch_payload["onlineMeetingProvider"] = "teamsForBusiness"

        updated = self._request(
            "PATCH",
            self._event_path(req.event_id, req.calendar_id),
            json=patch_payload,
        )
        
        return map_graph_calendar_event(updated)

    def delete_calendar_event(self, req: CalendarDeleteEventInput) -> dict[str, Any] | None:
        self._request("DELETE", self._event_path(req.event_id, req.calendar_id), expect_json=False)
        return {
            "id": req.event_id,
            "deleted": True,
            "status": "deleted",
        }

    def respond_calendar_invitation(self, req: CalendarRespondInvitationInput) -> dict[str, Any] | None:
        response_value = req.response.lower()

        action_map = {
            "accept": "accept",
            "decline": "decline",
            "tentative": "tentativelyAccept",
        }
        graph_action = action_map[response_value]

        payload = {
            "comment": req.comment or "",
            "sendResponse": bool(req.send_response),
        }
        self._request(
            "POST",
            f"{self._event_path(req.event_id, req.calendar_id)}/{graph_action}",
            json=payload,
            expect_json=False,
        )
        return {
            "id": req.event_id,
            "status": "responded",
            "response": response_value,
            "sendResponse": bool(req.send_response),
        }

    def list_calendar_events(self, req: CalendarListEventsInput) -> list[dict[str, Any]]:
        size = self._normalize_limit(req.limit)
        search_value = req.search or ""
        start_value = req.start or ""
        end_value = req.end or ""

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

    def _event_path(self, event_id: str, calendar_id: str | None = None) -> str:
        calendar_id_value = (calendar_id or "").strip()
        if calendar_id_value:
            return f"{self._mailbox_prefix}/calendars/{calendar_id_value}/events/{event_id}"
        return f"{self._mailbox_prefix}/events/{event_id}"

    def _resolve_event_time_zone(self, time_zone: str | None) -> str:
        if time_zone and time_zone.strip():
            return time_zone.strip()

        return self.get_user_time_zone().get("time_zone", "UTC")

    def _compose_online_meeting_body(self, description: str, existing_body_html: str) -> str:
        description_html = self._plain_text_to_html(description)
        meeting_block = self._extract_meeting_info_block(existing_body_html)
        if meeting_block:
            # Keep only meeting info block to avoid re-merging old business content repeatedly.
            return f"<div>{description_html}</div><br/>{meeting_block}"
        return f"<div>{description_html}</div>"

    def _plain_text_to_html(self, text: str) -> str:
        safe = escape(text.strip())
        return safe.replace("\n", "<br/>")

    def _extract_meeting_info_block(self, body_html: str) -> str:
        if not body_html:
            return ""

        lower = body_html.lower()
        marker_positions: list[int] = []
        for marker in (
            'id="x_join_info"',
            "microsoft teams meeting",
            "join microsoft teams meeting",
            "join:",
        ):
            idx = lower.find(marker)
            if idx >= 0:
                marker_positions.append(idx)

        if not marker_positions:
            return ""

        start = min(marker_positions)
        return body_html[start:].strip()
