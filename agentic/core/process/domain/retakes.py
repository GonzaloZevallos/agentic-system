"""The one retake budget of a task (GH-466, ADR 0080): pure, no I/O.

A retake is the poller resuming a run that is gone: after a reboot, after the run raised under
the graph, after a kill inside the same boot, or with no lock at all. Every shape draws on the
same budget of `RETAKE_MAX` retakes per task, follows the same calendar (`RETAKE_SCHEDULE`) and
resets the same way: when a human answers or resumes the task. This module is the only place
that counts retake comments, holds the cap, finds the reset boundary and applies a delay, so the
shapes cannot drift apart again. The callers hand over plain values (comment bodies with their
`createdAt`, the prefixes the system posts, the prefix of a human's `agentic resume` comment); this
module imports nothing from the application or the adapters.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# The owner's decision of 2026-11-03: three automatic retakes per task, whatever killed the run.
RETAKE_MAX = 3

# How the three comments a retake posts start (GH-176, GH-308, GH-403). Kept byte for byte: the
# ones posted before this budget existed count toward it. None is a prefix of another.
RETAKEN = "Retaken after reboot: "
RETAKEN_AFTER_CRASH = "Retaken after crash: "
RETAKEN_ORPHANED = "Retaken with no lock: "
RETAKE_PREFIXES = (RETAKEN, RETAKEN_AFTER_CRASH, RETAKEN_ORPHANED)

# The comment a spent budget leaves on the issue, once per budget (`notice_posted`).
RETAKES_SPENT = "Retakes spent: "

# The comment of a resume the poller itself spawned (`--by-poller`): the system's, so it never
# resets the budget the way a human's `Resumed: ` does.
RESUMED_BY_POLLER = "Resumed by the poller: "

# Minutes to wait before each retake, first to last, indexed by the retakes already made. The
# account-limit calendar (`rules.RETAKE_SCHEDULE`) is this same tuple: that name is a re-export.
RETAKE_SCHEDULE: tuple[int, ...] = (15, 30, 60, 120, 120, 120)

# How long a dead lock's document may have gone unwritten before the task is the human's rather
# than the poller's. A judgement, not a measurement (ADR 0080).
STALE_AFTER = timedelta(hours=24)

# What killed the run, as the poller names it in the notice and the comment.
RetakeShape = Literal["reboot", "crash", "orphan", "same_boot", "stale"]


@dataclass(frozen=True)
class Budget:
    """The retakes a task already used: how many, and when the first and the last one were
    posted (`None` for both when none was)."""

    used: int
    first_at: datetime | None
    last_at: datetime | None


def reset_index(bodies: Sequence[str], system_prefixes: tuple[str, ...], human_resumed: str) -> int:
    """The index of the last comment that resets the budget, -1 when none does: one that is
    not the system's, or one that starts with `human_resumed` (a human's `agentic resume`), which
    is in `system_prefixes` only so it is not read as an answer. The poller's own resume
    (`RESUMED_BY_POLLER`) never resets it, nor does any retake or spent-budget comment."""
    last = -1
    for i, body in enumerate(bodies):
        if body.startswith(RESUMED_BY_POLLER):
            continue
        if body.startswith(human_resumed) or not body.startswith(system_prefixes):
            last = i
    return last


def budget(
    comments: Sequence[tuple[str, datetime]],
    system_prefixes: tuple[str, ...],
    human_resumed: str,
) -> Budget:
    """The budget used since the last reset: every comment after the boundary that starts with
    one of `RETAKE_PREFIXES`, the legacy ones included, with the `createdAt` of the first and the
    last. `comments` are `(body, createdAt)` in the order the tracker gave them."""
    boundary = reset_index([body for body, _ in comments], system_prefixes, human_resumed)
    times = [at for body, at in comments[boundary + 1 :] if body.startswith(RETAKE_PREFIXES)]
    if not times:
        return Budget(0, None, None)
    return Budget(len(times), times[0], times[-1])


def spent(b: Budget) -> bool:
    return b.used >= RETAKE_MAX


def due(b: Budget, reference: datetime, first_delay: timedelta) -> datetime:
    """When the next retake may start: `first_delay` after `reference` (what the first retake
    waits on: the document's last write, say) when none was made, `RETAKE_SCHEDULE[used]` minutes
    after the last retake otherwise."""
    if b.used == 0 or b.last_at is None:
        return reference + first_delay
    minutes = RETAKE_SCHEDULE[min(b.used, len(RETAKE_SCHEDULE) - 1)]
    return b.last_at + timedelta(minutes=minutes)


def stale(document_written_at: datetime | None, now: datetime) -> bool:
    """Whether the document has gone `STALE_AFTER` or more unwritten. An unknown write time is
    not stale: the poller never gives a task up on a fact it does not have."""
    return document_written_at is not None and now - document_written_at >= STALE_AFTER


def notice_posted(
    bodies: Sequence[str], system_prefixes: tuple[str, ...], human_resumed: str
) -> bool:
    """Whether a `RETAKES_SPENT` comment already follows the last reset: the spent budget was
    announced once, and is not announced again."""
    boundary = reset_index(bodies, system_prefixes, human_resumed)
    return any(body.startswith(RETAKES_SPENT) for body in bodies[boundary + 1 :])
