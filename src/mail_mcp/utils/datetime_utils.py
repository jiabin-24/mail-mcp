from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo


# Microsoft Graph mailboxSettings often returns Windows timezone names.
WINDOWS_TO_IANA_TIME_ZONES: dict[str, str] = {
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "India Standard Time": "Asia/Kolkata",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "Taipei Standard Time": "Asia/Taipei",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "W. Europe Standard Time": "Europe/Berlin",
    "GMT Standard Time": "Europe/London",
    "UTC": "UTC",
    "US Eastern Standard Time": "America/New_York",
    "Pacific Standard Time": "America/Los_Angeles",
}

# Fallback offsets are used only when ZoneInfo cannot be resolved (for example,
# in minimal runtime images without tzdata). Keep this list limited to zones
# with stable offsets and no DST ambiguity in typical enterprise usage.
WINDOWS_TIME_ZONE_FIXED_OFFSET_MINUTES: dict[str, int] = {
    "China Standard Time": 8 * 60,
    "Tokyo Standard Time": 9 * 60,
    "Korea Standard Time": 9 * 60,
    "India Standard Time": 5 * 60 + 30,
    "SE Asia Standard Time": 7 * 60,
    "Singapore Standard Time": 8 * 60,
    "Taipei Standard Time": 8 * 60,
    "UTC": 0,
}

TIMEZONE_SUFFIX_REGEX = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$", re.IGNORECASE)
MAIL_FILTER_TIME_LITERAL_REGEX = re.compile(
    r"(?P<prefix>\\b(?:receivedDateTime|sentDateTime)\\b\\s+(?:ge|gt|le|lt|eq|ne)\\s+)(?P<quote>'?)(?P<dt>\\d{4}-\\d{2}-\\d{2}T[0-9:.]+)(?P=quote)",
    re.IGNORECASE,
)


def resolve_zone_info(time_zone: str | None) -> tzinfo | None:
    tz_name = (time_zone or "").strip()
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        mapped = WINDOWS_TO_IANA_TIME_ZONES.get(tz_name, "")
        if mapped:
            try:
                return ZoneInfo(mapped)
            except Exception:
                pass

        fixed_offset_minutes = WINDOWS_TIME_ZONE_FIXED_OFFSET_MINUTES.get(tz_name)
        if fixed_offset_minutes is None:
            return None

        return timezone(timedelta(minutes=fixed_offset_minutes))


def resolve_effective_time_zone(preferred: str | None, mailbox_time_zone: str | None, fallback: str = "UTC") -> str:
    preferred_value = (preferred or "").strip()
    if preferred_value:
        return preferred_value
    mailbox_value = (mailbox_time_zone or "").strip()
    if mailbox_value:
        return mailbox_value
    return fallback


def to_utc_iso_from_datetime(
    value: datetime,
    *,
    preferred_time_zone: str | None = None,
    mailbox_time_zone: str | None = None,
) -> str:
    dt = value
    if dt.tzinfo is None:
        effective_time_zone = resolve_effective_time_zone(preferred_time_zone, mailbox_time_zone)
        zone = resolve_zone_info(effective_time_zone)
        if zone is None:
            raise ValueError(f"invalid time zone: {effective_time_zone}")
        dt = dt.replace(tzinfo=zone)

    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def to_utc_iso_from_text(
    value: str,
    *,
    preferred_time_zone: str | None = None,
    mailbox_time_zone: str | None = None,
) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("datetime text is required")

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid datetime value: {value}") from exc

    return to_utc_iso_from_datetime(
        dt,
        preferred_time_zone=preferred_time_zone,
        mailbox_time_zone=mailbox_time_zone,
    )


def normalize_query_datetime_with_mailbox_timezone(value: str, mailbox_time_zone: str | None) -> str:
    """Normalize query datetime text.

    If timezone info is missing, apply mailbox timezone and convert to UTC ISO (Z suffix).
    If timezone info already exists, preserve the original value.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("datetime text is required")

    if TIMEZONE_SUFFIX_REGEX.search(raw):
        return raw

    return to_utc_iso_from_text(raw, mailbox_time_zone=mailbox_time_zone)


def normalize_mail_filter_time_literals(filter_text: str, mailbox_time_zone: str | None) -> str:
    """Normalize naive datetime literals in received/sent datetime filter expressions."""
    raw = (filter_text or "").strip()
    if not raw:
        return raw

    def _replace(match: re.Match[str]) -> str:
        normalized = normalize_query_datetime_with_mailbox_timezone(
            match.group("dt"),
            mailbox_time_zone,
        )
        return f"{match.group('prefix')}{match.group('quote')}{normalized}{match.group('quote')}"

    return MAIL_FILTER_TIME_LITERAL_REGEX.sub(_replace, raw)