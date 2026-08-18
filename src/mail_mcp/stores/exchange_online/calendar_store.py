from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import quote

from .graph_gateway import GraphGateway
from ...models import map_graph_calendar_event
from ...schemas.request_models import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarGetEventInput,
    CalendarListEventsInput,
    CalendarRespondInvitationInput,
    CalendarUpdateEventInput,
)
from ...utils.datetime_utils import (
    normalize_query_datetime_with_mailbox_timezone,
    to_utc_iso,
)


GRAPH_QUERY_SAFE = "()':,=-"
GRAPH_DATETIME_SAFE = "-:.TZ"


class CalendarStore(GraphGateway):
    """基于 Microsoft Graph 日历 API 的日历相关操作。"""

    def get_calendar_event(self, req: CalendarGetEventInput) -> dict[str, Any] | None:
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()
        payload = self._request(
            "GET",
            f"{self._event_path(req.event_id, req.calendar_id)}"
            "?$select=id,subject,bodyPreview,organizer,attendees,responseStatus,start,end,location,isAllDay,webLink",
        )
        return map_graph_calendar_event(payload, mailbox_time_zone=mailbox_time_zone)

    def create_calendar_event(self, req: CalendarCreateEventInput) -> dict[str, Any]:
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()
        start_utc = to_utc_iso(
            req.start,
            preferred_time_zone=req.time_zone,
            mailbox_time_zone=mailbox_time_zone,
        )
        end_utc = to_utc_iso(
            req.end,
            preferred_time_zone=req.time_zone,
            mailbox_time_zone=mailbox_time_zone,
        )
        payload: dict[str, Any] = {
            "subject": req.subject,
            "start": {"dateTime": start_utc, "timeZone": "UTC"},
            "end": {"dateTime": end_utc, "timeZone": "UTC"},
            "isAllDay": bool(req.is_all_day),
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
            "attendees": self._emails_to_attendees(req.attendees or []),
        }

        if req.description:
            description_html = self._plain_text_to_html(req.description)
            payload["body"] = {
                "contentType": "HTML",
                "content": f"<div>{description_html}</div><br/>",
            }

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
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()

        patch_payload: dict[str, Any] = {}
        if req.subject is not None:
            patch_payload["subject"] = req.subject
        if start_value and end_value:
            start_utc = to_utc_iso(
                start_value,
                preferred_time_zone=req.time_zone,
                mailbox_time_zone=mailbox_time_zone,
            )
            end_utc = to_utc_iso(
                end_value,
                preferred_time_zone=req.time_zone,
                mailbox_time_zone=mailbox_time_zone,
            )
            patch_payload["start"] = {"dateTime": start_utc, "timeZone": "UTC"}
            patch_payload["end"] = {"dateTime": end_utc, "timeZone": "UTC"}
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
        mailbox_time_zone = self.get_mailbox_time_zone_if_available()
        size = self._normalize_limit(req.limit)
        search_value = req.search or ""
        start_value = req.start or ""
        end_value = req.end or ""

        select_clause = "id,subject,bodyPreview,organizer,attendees,responseStatus,start,end,location,isAllDay,webLink"
        params: list[str] = [f"$top={size}", "$orderby=start/dateTime", f"$select={select_clause}"]

        if search_value:
            encoded_search = quote(search_value, safe=GRAPH_QUERY_SAFE)
            params.append(f"$search={encoded_search}")

        headers = {"ConsistencyLevel": "eventual"} if search_value else None

        if start_value and end_value:
            normalized_start = normalize_query_datetime_with_mailbox_timezone(start_value, mailbox_time_zone)
            normalized_end = normalize_query_datetime_with_mailbox_timezone(end_value, mailbox_time_zone)
            encoded_start = quote(normalized_start, safe=GRAPH_DATETIME_SAFE)
            encoded_end = quote(normalized_end, safe=GRAPH_DATETIME_SAFE)
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
        return self._map_calendar_events(payload.get("value", []), mailbox_time_zone=mailbox_time_zone)
