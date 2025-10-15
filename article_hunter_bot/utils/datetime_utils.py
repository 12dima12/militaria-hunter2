"""Utilities for consistent timezone-aware datetime handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def now_utc() -> datetime:
    """Return the current time as an aware UTC datetime."""

    return datetime.now(timezone.utc)


def to_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert naive or localized datetimes to UTC-aware datetimes."""

    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def display_berlin(dt: Optional[datetime]) -> str:
    """Format a datetime for display in Europe/Berlin or return '/' if missing."""

    if dt is None:
        return "/"

    utc_dt = to_utc_aware(dt)
    berlin_dt = utc_dt.astimezone(BERLIN)
    return berlin_dt.strftime("%d.%m.%Y %H:%M Uhr")


__all__ = ["BERLIN", "now_utc", "to_utc_aware", "display_berlin"]
