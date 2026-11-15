"""Whether an owner escalation is still open on a task (GH-784), the one place the instants
that say so are compared: the latest `Escalated:` comment against the latest `Ready` comment,
the last `resumed` pause entry and every landing `released_at`. Pure; the caller reads the
comments and the document and passes the marks in. `None` is never "clear": the merge
decision refuses on it.

Two clocks meet here: `created_at` is GitHub's, `resumed_at` and `released_at` are the local
one. No tolerance is applied, so a mark written a few seconds before the comment it answers
still holds the task: the error falls toward the human, and a later Ready, resume or release
clears it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

CommentKind = Literal["escalated", "ready"]


def _instant(value: str | None) -> datetime | None:
    """The aware instant of an ISO-8601 string (`Z` or an offset), `None` when it is missing,
    empty, garbage or carries no offset (it cannot be ordered against an aware one)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def open_escalation(
    comments: Sequence[tuple[CommentKind, str]] | None,
    resumed_at: str | None,
    released_at: Sequence[str],
) -> bool | None:
    """`True` when the latest escalated comment is at or after (a tie holds) the latest ready
    comment, `resumed_at` and every `released_at`; `False` when there is no escalated comment
    or a mark is strictly later; `None` when `comments` is `None` (unreadable) or a mark that
    decides the answer has no parseable instant.

    `comments` is in thread order, so the latest escalated (and ready) one is the last of its
    kind; an unreadable older one never matters. An unparseable mark is ignored when a parseable one
    is already later than the escalation. `resumed_at` of `None` is no mark at all."""
    if comments is None:
        return None
    escalated = [created for kind, created in comments if kind == "escalated"]
    if not escalated:
        return False
    latest = _instant(escalated[-1])
    if latest is None:
        return None
    readies = [created for kind, created in comments if kind == "ready"]
    marks = [
        *([readies[-1]] if readies else []),
        *([resumed_at] if resumed_at is not None else []),
        *released_at,
    ]
    parsed = [_instant(mark) for mark in marks]
    if any(mark is not None and mark > latest for mark in parsed):
        return False
    if any(mark is None for mark in parsed):
        return None
    return True
