from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    """Return naive UTC for consistent SQLite storage and comparison."""
    return datetime.now(UTC).replace(tzinfo=None)


def parse_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def start_of_day(day: date | None = None) -> datetime:
    selected = day or utcnow().date()
    return datetime.combine(selected, datetime.min.time())


def configured_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc


def local_today(timezone_name: str) -> date:
    return datetime.now(configured_timezone(timezone_name)).date()


def utc_bounds_for_local_day(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    timezone = configured_timezone(timezone_name)
    local_start = datetime.combine(day, time.min, tzinfo=timezone)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
    return (
        local_start.astimezone(UTC).replace(tzinfo=None),
        local_end.astimezone(UTC).replace(tzinfo=None),
    )
