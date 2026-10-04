"""The shop's local day. Timestamps are stored in UTC; "today" in history and reports starts at
local midnight, `TIMEZONE_OFFSET_HOURS` away from UTC (Vietnam: +7, no daylight saving)."""

from datetime import UTC, datetime, timedelta


def local_midnight_utc(offset_hours: float, days_ago: int = 0, now: datetime | None = None) -> datetime:
    """The UTC instant of local midnight, `days_ago` days before the local today."""
    offset = timedelta(hours=offset_hours)
    local = (now or datetime.now(UTC)).astimezone(UTC) + offset
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days_ago) - offset


def local_date(value: datetime, offset_hours: float) -> str:
    """The local calendar date (YYYY-MM-DD) of a UTC instant."""
    return (value.astimezone(UTC) + timedelta(hours=offset_hours)).date().isoformat()
