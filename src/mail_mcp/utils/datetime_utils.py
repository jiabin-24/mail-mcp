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

# 当 ZoneInfo 无法解析时才使用备用偏移量（例如缺少 tzdata 的极简运行时环境）。
# 这里只保留那些偏移稳定且通常无 DST 歧义的时区，避免过度扩大覆盖范围。
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
    """将 Microsoft Graph 返回的时区名称解析为 Python 的 tzinfo。

    优先尝试直接使用 IANA 时区名称；若失败，则将常见的 Windows 时区名映射
    到对应的 IANA 时区；最后在缺少 tzdata 的情况下，回退为固定偏移时区。
    """
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


def to_utc_iso_from_datetime(
    value: datetime,
    *,
    preferred_time_zone: str | None = None,
    mailbox_time_zone: str | None = None,
) -> str:
    """将带时区或无时区的 datetime 转成 UTC ISO 8601 字符串。

    若传入时间没有 tzinfo，则根据 preferred_time_zone / mailbox_time_zone
    选择一个时区来解释该时间，再统一转换到 UTC。
    """
    dt = value
    if dt.tzinfo is None:
        effective_time_zone = (preferred_time_zone or mailbox_time_zone or "UTC").strip() or "UTC"
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
    """将 ISO 时间文本转换成 UTC 的 ISO 8601 字符串。

    若字符串末尾为 Z，则视为 UTC；若无时区信息，则按 mailbox_time_zone
    解释并转换为 UTC。 
    """
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
    """规范化查询中的日期时间文本。

    如果原值已经带有时区信息，则保留原值；如果没有时区信息，则使用邮箱时区
    来解释该时间，再转换成 UTC ISO 8601（带 Z 后缀）的形式。 
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("datetime text is required")

    if TIMEZONE_SUFFIX_REGEX.search(raw):
        return raw

    return to_utc_iso_from_text(raw, mailbox_time_zone=mailbox_time_zone)


def normalize_mail_filter_time_literals(filter_text: str, mailbox_time_zone: str | None) -> str:
    """规范化 received/sent 过滤表达式中的时间字面量。

    会在 Graph 查询过滤器中替换无时区的日期时间值，确保它们按邮箱时区解释
    后再统一转换为 UTC，避免查询条件误差。
    """
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