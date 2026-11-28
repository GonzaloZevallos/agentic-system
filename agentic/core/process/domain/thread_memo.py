"""The memo of issue threads (GH-1220): a ready task's thread is read again only when the
issue's `updatedAt` moved since the last complete read, so thread reads per poller round do not
grow with the number of open pull requests.

Pure: no IO and no clock; the caller passes `read_at`. Only `store` writes a `ThreadMemo` and
only `served` reads it. Generic over the comment type, so the domain does not import the
application's `IssueComment`.

The stamp must be the `updatedAt` read BEFORE the thread read it labels (the round's queue
read): a comment posted between the two reads then moves the issue's stamp past the stored one
and the next round reads live. The comparison mixes GitHub's clock with this machine's: `store`
keeps a read only when its stamp is at least 60 s older than `read_at`, and a stamp later than
`read_at` (clock skew) is never kept; that margin is the only guard against skew.

Known risk: an edited or deleted comment may not move `updatedAt`. A deleted `Escalated:` fails
safe (the task stays refused). A stale `Decision:` line only delays a hold by one stamp change;
it never makes an early merge.

Any doubt means read: an empty, unparseable, too recent or different stamp is never stored or
served. The memo never hands out the object it stores: `store` copies into a tuple."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MIN_AGE = timedelta(seconds=60)


@dataclass
class ThreadMemo[T]:
    """The complete thread reads kept by issue number, as `(stamp, comments)`, for the life of
    the poller process. Written only by `store`, read only by `served`."""

    _reads: dict[int, tuple[str, tuple[T, ...]]] = field(
        default_factory=dict[int, tuple[str, tuple[T, ...]]]
    )


def _instant(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def store[T](
    memo: ThreadMemo[T],
    number: int,
    stamp: str,
    comments: Sequence[T],
    read_at: datetime,
) -> None:
    """Keep a complete read of `number`'s thread when `stamp` is non-empty, parses as an aware
    ISO-8601 instant and is at least 60 s older than `read_at`; keep nothing otherwise."""
    instant = _instant(stamp)
    if instant is None or read_at - instant < MIN_AGE:
        return
    memo._reads[number] = (stamp, tuple(comments))


def served[T](memo: ThreadMemo[T], number: int, stamp: str) -> tuple[T, ...] | None:
    """The kept comments of `number` when `stamp` is non-empty and exactly the stored one."""
    if not stamp:
        return None
    kept = memo._reads.get(number)
    if kept is None or kept[0] != stamp:
        return None
    return kept[1]
