from __future__ import annotations

from datetime import UTC, datetime
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


def resolve_zone_info(time_zone: str | None) -> ZoneInfo | None:
    tz_name = (time_zone or "").strip()
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        mapped = WINDOWS_TO_IANA_TIME_ZONES.get(tz_name, "")
        if not mapped:
            return None
        try:
            return ZoneInfo(mapped)
        except Exception:
            return None


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