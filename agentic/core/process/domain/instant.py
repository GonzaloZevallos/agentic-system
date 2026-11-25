"""The one public parser of ISO-8601 instants of the process domain (ADR-0108).

A leaf module: it imports only from `datetime` and reads no clock. An instant without an
offset is unreadable (`None`), never UTC: it cannot be ordered against an aware one, and
guessing a zone would decide a pause or a hold on a time nobody wrote down.
"""

from __future__ import annotations

from datetime import datetime


def parse_instant(value: object) -> datetime | None:
    """`value` as the aware datetime it spells, in the zone it was written in.

    `None` when `value` is not text, is empty, is not ISO-8601 or carries no offset. A `Z` or
    an offset comes back as parsed: no `astimezone`, no `replace`.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
