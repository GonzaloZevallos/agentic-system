"""The reset time an account-limit error states, as a bounded datetime (ADR-0039, addendum
GH-381).

The provider's 429 text carries a clause like `resets 5:40am (America/Argentina/
Buenos_Aires)`. `reset_at` reads only that clause and returns the next occurrence of that
local wall time, but only when it lies inside the session window. Everything else -- no
clause, an unknown zone, an hour that is not a clock hour, a time too far away to be this
session's reset -- is `None`, and `None` always means today's calendar
(`rules.ACCOUNT_LIMIT_SCHEDULE`). A parsed time is only a wait hint: the failure's reason
is decided by the HTTP status alone (`rules.failure_reason`), never here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .rules import SESSION_WINDOW, SESSION_WINDOW_MARGIN

_CLAUSE = re.compile(
    r"resets\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<half>[ap]m)\s*"
    r"\((?P<zone>[A-Za-z0-9_+\-/]+)\)",
    re.IGNORECASE,
)


def reset_at(error: str, observed_at: datetime) -> datetime | None:
    """The stated reset in `error`, in its own zone, strictly after `observed_at` and no
    later than `observed_at` plus the session window and its margin; `None` otherwise.

    `observed_at` must be timezone-aware: a naive one has no instant to compare against,
    so it raises `ValueError` instead of guessing a zone."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    match = _CLAUSE.search(error)
    if match is None:
        return None
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if match["half"].lower() == "pm" else 0)
    try:
        zone = ZoneInfo(match["zone"])
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    today = observed_at.astimezone(zone).date()
    reset = datetime(today.year, today.month, today.day, hour, minute, tzinfo=zone)
    if reset <= observed_at:
        tomorrow = today + timedelta(days=1)
        reset = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=zone)
    if reset > observed_at + SESSION_WINDOW + SESSION_WINDOW_MARGIN:
        return None
    return reset
