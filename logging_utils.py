"""Logging helpers with timezone aware timestamps."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from time_utils import get_local_timezone


class TimezoneFormatter(logging.Formatter):
    """Formatter that renders timestamps with explicit timezone information."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        tzinfo=None,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
        self._tzinfo = tzinfo or get_local_timezone()

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone(self._tzinfo)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")


_configured = False


def configure_logging(level: int = logging.INFO, fmt: Optional[str] = None) -> None:
    """Configure the root logger to include timezone aware timestamps."""
    global _configured
    if _configured:
        return

    formatter = TimezoneFormatter(
        fmt or "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z%z",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    _configured = True
