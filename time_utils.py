"""Utility helpers for timezone-aware date handling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo always available on Py3.9+
    ZoneInfo = None  # type: ignore

_DEFAULT_TZ_OFFSET = timezone(timedelta(hours=8))


def get_local_timezone():
    """Return the system configured timezone or fall back to UTC+8."""
    local_dt = datetime.now().astimezone()
    tzinfo = local_dt.tzinfo
    if tzinfo is not None:
        offset = local_dt.utcoffset()
        if offset and offset != timedelta(0):
            return tzinfo
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:  # pragma: no cover - fallback handled below
            pass
    return _DEFAULT_TZ_OFFSET


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone information to naive datetimes."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a datetime to the local timezone."""
    if dt is None:
        return None
    return ensure_utc(dt).astimezone(get_local_timezone())


def format_local_datetime(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M:%S %Z%z") -> str:
    """Format a datetime into a string that includes timezone information."""
    local_dt = to_local(dt)
    if local_dt is None:
        return ""
    return local_dt.strftime(fmt)
