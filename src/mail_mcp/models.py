from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .utils.datetime_utils import resolve_zone_info


class GraphEmailAddress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    address: str = ""


class GraphRecipient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emailAddress: GraphEmailAddress = Field(default_factory=GraphEmailAddress)


class GraphResponseStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response: str = ""
    time: str = ""


class GraphMessageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = ""


class GraphMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = ""
    subject: str = ""
    bodyPreview: str = ""
    from_: GraphRecipient = Field(default_factory=GraphRecipient, alias="from")
    toRecipients: list[GraphRecipient] = Field(default_factory=list)
    ccRecipients: list[GraphRecipient] = Field(default_factory=list)
    bccRecipients: list[GraphRecipient] = Field(default_factory=list)
    isDraft: bool = False
    receivedDateTime: str = ""
    sentDateTime: str = ""
    parentFolderId: str = ""
    body: GraphMessageBody = Field(default_factory=GraphMessageBody)


class GraphEventDateTime(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dateTime: str = ""
    timeZone: str = ""


class GraphEventLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    displayName: str = ""


class GraphCalendarEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    subject: str = ""
    bodyPreview: str = ""
    organizer: GraphRecipient = Field(default_factory=GraphRecipient)
    attendees: list[GraphRecipient] = Field(default_factory=list)
    responseStatus: GraphResponseStatus = Field(default_factory=GraphResponseStatus)
    location: GraphEventLocation = Field(default_factory=GraphEventLocation)
    isAllDay: bool = False
    start: GraphEventDateTime = Field(default_factory=GraphEventDateTime)
    end: GraphEventDateTime = Field(default_factory=GraphEventDateTime)
    webLink: str = ""


def _convert_datetime_to_mailbox_timezone(
    value: str,
    mailbox_time_zone: str | None,
    source_time_zone: str | None = None,
) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    target_zone = resolve_zone_info(mailbox_time_zone)
    if target_zone is None:
        return raw

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw

    if parsed.tzinfo is None:
        source_zone = resolve_zone_info(source_time_zone)
        if source_zone is None:
            return raw
        parsed = parsed.replace(tzinfo=source_zone)

    try:
        return parsed.astimezone(target_zone).isoformat()
    except Exception:
        return raw


def recipient_address(recipient: GraphRecipient) -> str:
    return recipient.emailAddress.address or ""


def recipient_addresses(recipients: list[GraphRecipient]) -> list[str]:
    result: list[str] = []
    for recipient in recipients:
        address = recipient_address(recipient)
        if address:
            result.append(address)
    return result


def map_graph_message(
    message: dict[str, Any],
    folder: str | None = None,
    prefer_preview: bool = False,
    mailbox_time_zone: str | None = None,
) -> dict[str, Any]:
    parsed = GraphMessage.model_validate(message or {})
    body_preview = parsed.bodyPreview or ""
    body_content = parsed.body.content or ""

    result: dict[str, Any] = {
        "id": parsed.id or "",
        "folder": folder or parsed.parentFolderId or "",
        "from": recipient_address(parsed.from_),
        "to": recipient_addresses(parsed.toRecipients),
        "cc": recipient_addresses(parsed.ccRecipients),
        "bcc": recipient_addresses(parsed.bccRecipients),
        "subject": parsed.subject or "",
        "bodyPreview": body_preview,
        "sent": not bool(parsed.isDraft),
        "received_at": _convert_datetime_to_mailbox_timezone(parsed.receivedDateTime or "", mailbox_time_zone),
        "sent_at": _convert_datetime_to_mailbox_timezone(parsed.sentDateTime or "", mailbox_time_zone),
    }

    if not prefer_preview:
        result["body"] = body_content or body_preview

    return result


def map_graph_calendar_event(event: dict[str, Any], mailbox_time_zone: str | None = None) -> dict[str, Any]:
    parsed = GraphCalendarEvent.model_validate(event or {})
    start_time_zone = parsed.start.timeZone or ""
    end_time_zone = parsed.end.timeZone or ""
    resolved_time_zone = (mailbox_time_zone or "").strip()
    return {
        "id": parsed.id or "",
        "subject": parsed.subject or "",
        "bodyPreview": parsed.bodyPreview or "",
        "organizer": recipient_address(parsed.organizer),
        "attendees": recipient_addresses(parsed.attendees),
        "responseStatus": {
            "response": parsed.responseStatus.response or "",
            "time": parsed.responseStatus.time or "",
        },
        "location": parsed.location.displayName or "",
        "isAllDay": bool(parsed.isAllDay),
        "start": {
            "dateTime": _convert_datetime_to_mailbox_timezone(
                parsed.start.dateTime or "",
                mailbox_time_zone,
                source_time_zone=start_time_zone,
            ),
            "timeZone": resolved_time_zone or start_time_zone,
        },
        "end": {
            "dateTime": _convert_datetime_to_mailbox_timezone(
                parsed.end.dateTime or "",
                mailbox_time_zone,
                source_time_zone=end_time_zone,
            ),
            "timeZone": resolved_time_zone or end_time_zone,
        },
        "webLink": parsed.webLink or "",
    }