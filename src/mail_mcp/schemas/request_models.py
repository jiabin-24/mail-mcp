from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class MailboxListMessagesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    folder: str = "inbox"
    limit: int = Field(default=20, ge=1, le=100)


class MailboxGetMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message_id: str = Field(min_length=1)


class MailboxSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    search: str | None = None
    filter: str | None = None
    orderby: str | None = None
    folder: str = "inbox"
    limit: int = Field(default=20, ge=1, le=100)


class MailboxComposeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    to: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    cc: list[str] | None = None
    bcc: list[str] | None = None


class MailboxReplyComposeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message_id: str = Field(min_length=1)
    body: str = Field(min_length=1)


class MailboxUpdateDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str = Field(min_length=1)
    to: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None


class MailboxDraftIdInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str = Field(min_length=1)


class MailboxCreateSendJobInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    draft_email_id: str = Field(min_length=1)
    schedule_send_time: datetime
    status: Literal["scheduled", "sent", "failed"] = "scheduled"
    sent_time: datetime | None = None
    subject: str | None = None


class MailboxListSendJobsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    limit: int = Field(default=20, ge=1, le=100)


class MailboxSendJobIdInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(min_length=1)


class MailboxUpdateSendJobScheduleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(min_length=1)
    schedule_send_time: datetime


class CalendarListEventsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    start: str | None = None
    end: str | None = None
    search: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_start_end_pair(self) -> CalendarListEventsInput:
        has_start = bool(self.start)
        has_end = bool(self.end)
        if has_start != has_end:
            raise ValueError("start and end must be provided together")
        return self


class CalendarGetEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    calendar_id: str | None = None


class CalendarCreateEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    subject: str = Field(min_length=1)
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    attendees: list[str] | None = None
    description: str | None = None
    location: str | None = None
    is_all_day: bool = False
    time_zone: str | None = Field(default=None, min_length=1)
    calendar_id: str | None = None


class CalendarUpdateEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    subject: str | None = None
    start: str | None = None
    end: str | None = None
    attendees: list[str] | None = None
    description: str | None = None
    location: str | None = None
    is_all_day: bool | None = None
    time_zone: str | None = Field(default=None, min_length=1)
    calendar_id: str | None = None

    @model_validator(mode="after")
    def _validate_start_end_pair(self) -> CalendarUpdateEventInput:
        has_start = bool(self.start)
        has_end = bool(self.end)
        if has_start != has_end:
            raise ValueError("start and end must be provided together")
        return self


class CalendarDeleteEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    calendar_id: str | None = None


class CalendarRespondInvitationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    response: Literal["accept", "decline", "tentative"]
    comment: str | None = None
    send_response: bool = True
    calendar_id: str | None = None


ModelType = TypeVar("ModelType", bound=BaseModel)


def validate_input(model_type: type[ModelType], payload: dict) -> ModelType:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(exc.errors()[0]["msg"]) from exc
