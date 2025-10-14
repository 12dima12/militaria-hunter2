"""Time utilities for consistent timezone handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo


BERLIN_TZ = ZoneInfo("Europe/Berlin")


def now_utc() -> datetime:
    """Return the current time as an aware UTC datetime."""

    return datetime.now(timezone.utc)


def to_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is timezone-aware in UTC.

    Naive datetimes are assumed to be stored in UTC and will get the UTC tzinfo
    assigned. Aware datetimes are converted to UTC.
    """

    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def berlin_fmt(dt_utc: Optional[datetime]) -> str:
    """Format a UTC datetime into the Europe/Berlin representation."""

    if dt_utc is None:
        return "/"

    aware_utc = to_utc_aware(dt_utc)
    berlin_dt = aware_utc.astimezone(BERLIN_TZ)
    return berlin_dt.strftime("%d.%m.%Y %H:%M Uhr")


__all__ = ["now_utc", "to_utc_aware", "berlin_fmt"]
