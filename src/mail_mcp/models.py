from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphEmailAddress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    address: str = ""


class GraphRecipient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emailAddress: GraphEmailAddress = Field(default_factory=GraphEmailAddress)


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
    location: GraphEventLocation = Field(default_factory=GraphEventLocation)
    isAllDay: bool = False
    start: GraphEventDateTime = Field(default_factory=GraphEventDateTime)
    end: GraphEventDateTime = Field(default_factory=GraphEventDateTime)
    webLink: str = ""


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
        "received_at": parsed.receivedDateTime or "",
        "sent_at": parsed.sentDateTime or "",
    }

    if not prefer_preview:
        result["body"] = body_content or body_preview

    return result


def map_graph_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
    parsed = GraphCalendarEvent.model_validate(event or {})
    return {
        "id": parsed.id or "",
        "subject": parsed.subject or "",
        "bodyPreview": parsed.bodyPreview or "",
        "organizer": recipient_address(parsed.organizer),
        "attendees": recipient_addresses(parsed.attendees),
        "location": parsed.location.displayName or "",
        "isAllDay": bool(parsed.isAllDay),
        "start": {
            "dateTime": parsed.start.dateTime or "",
            "timeZone": parsed.start.timeZone or "",
        },
        "end": {
            "dateTime": parsed.end.dateTime or "",
            "timeZone": parsed.end.timeZone or "",
        },
        "webLink": parsed.webLink or "",
    }