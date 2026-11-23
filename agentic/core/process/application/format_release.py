"""The release of a noted format hold once its refusal issue is fixed (GH-1020, ADR-0105).

A held task waits on the system's own issue about the rule. `release_fixed_holds` is the poller's
step that lets it go: it reads the issue and whether its closing commit runs in this system
(`Ports.format_fixes`, local reads only), hands the facts to `rules.format_hold_released`, and
saves `format_retakes.fixed_release_patch` when that says so. It decides nothing itself: no state,
no commit and no ancestry is compared here.

Every port read happens before the task's lock is taken. The lock is taken only to write: the
document is reloaded, the hold confirmed again, saved, and the lock released. Only the step's own
dead `format_release` lock is ever taken over; a record of any other command, alive or dead, waits
for a person. Nothing here runs in a dry round or one that starts nothing, and with no
`format_fixes` it makes no call. It imports only `..domain*`, `.ports` and `.stack_hold`: never
`poll`, which calls it.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field

from ..domain import format_retakes, rules
from ..domain.task import apply
from . import stack_hold
from .ports import IssueState, LockHeld, Ports, RunLock

COMMAND = "format_release"
_ISSUE_NUMBER = re.compile(r"/issues/(\d+)")


@dataclass
class FormatReleaseCarry:
    """What the poll loop keeps between rounds, as `SweepCarry` does. `said` is the
    `(rule, issue_url, sha)` triples whose line was told, `spent` the `(issue_url, hold at)` pairs
    whose release was done before (so they are never read again) and `took` the dead locks of the
    step's own that were taken over and said, as `(task_id, pid)`."""

    said: set[tuple[str, str, str]] = field(default_factory=set)
    spent: set[tuple[str, str]] = field(default_factory=set)
    took: set[tuple[str, int]] = field(default_factory=set)


def release_fixed_holds(
    ports: Ports,
    lock: RunLock,
    busy: Collection[str],
    carry: FormatReleaseCarry,
    skip: Collection[int] = frozenset(),
) -> list[str]:
    """The lines of the holds released this round, and of the fixes that merged but do not run
    yet. Visits only a task with a hold that has `noted_at` and `issue_url`, whose issue is not
    in `skip` and that has a free writer (`stack_hold.free_writer`, `busy` the task ids with a
    live run), and whose pair is not spent in `carry`. The issue is read once per URL and the
    commit once per sha in the round, never kept after it."""
    reads = ports.format_fixes
    if reads is None:
        return []
    out: list[str] = []
    issues: dict[str, IssueState | None] = {}
    contained: dict[str, bool | None] = {}
    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None or number in skip:
            continue
        raw = ports.documents.load(task_id) or {}
        hold = raw.get("format_hold") if rules.format_held(raw) else None
        if not hold or not hold.get("noted_at") or not hold.get("issue_url"):
            continue
        url, at = hold["issue_url"], hold["at"]
        if (url, at) in carry.spent or not stack_hold.free_writer(task_id, busy, lock):
            continue
        if url not in issues:
            issues[url] = reads.closing(url)
        issue = issues[url]
        if issue is None:
            continue
        closed, commit = rules.format_fix_facts(issue.state, issue.closing_merge_commit)
        before = format_retakes.released_before(raw, url, commit)
        if closed and before:
            carry.spent.add((url, at))
        found = None
        if closed and commit is not None and not before:
            if commit not in contained:
                contained[commit] = reads.contains(commit)
            found = contained[commit]
        if not rules.format_hold_released(closed, commit, found, released_before=before):
            if closed and commit is not None and not before:
                _say_waiting(out, carry, hold["rule"], url, commit, number, found)
            continue
        if _write_release(ports, lock, task_id, url, at, commit, carry, number, out):
            out.append(f"agentic: poll: #{number} format hold released ({hold['rule']})")
    return out


def _say_waiting(
    out: list[str],
    carry: FormatReleaseCarry,
    rule: str,
    url: str,
    commit: str,
    number: int,
    found: bool | None,
) -> None:
    """The fix merged and does not run (yet), once per process per `(rule, issue_url, sha)`:
    `found` False is 'not in stage0 yet', `None` is 'stage0 unreadable'."""
    key = (rule, url, commit)
    if key in carry.said:
        return
    carry.said.add(key)
    fix = _ISSUE_NUMBER.search(url)
    where = "not in stage0 yet" if found is False else "stage0 unreadable"
    out.append(
        f"agentic: poll: #{number} format hold: fix #{fix.group(1) if fix else '?'} merged, {where}"
    )


def _write_release(
    ports: Ports,
    lock: RunLock,
    task_id: str,
    url: str,
    at: str,
    commit: str | None,
    carry: FormatReleaseCarry,
    number: int,
    out: list[str],
) -> bool:
    """Takes the lock of `task_id` (its own dead `format_release` one only), reloads the document
    and, when it is still the very hold read (held, noted, same `issue_url` and `at`, not
    released for this pair), saves the release. True only when it saved. The lock is released on
    every way out."""
    try:
        record = lock.read(task_id)
    except (OSError, ValueError, KeyError):
        return False
    if record is not None and record.command != COMMAND:
        return False
    try:
        taken = lock.acquire(task_id, COMMAND)
    except (LockHeld, OSError, ValueError, KeyError):
        return False
    try:
        if taken is not None and (task_id, taken.pid) not in carry.took:
            carry.took.add((task_id, taken.pid))
            out.append(
                f"agentic: poll: #{number} format release took over its own dead lock "
                f"(pid {taken.pid})"
            )
        fresh = ports.documents.load(task_id)
        hold = (fresh or {}).get("format_hold")
        if (
            fresh is None
            or not hold
            or not hold.get("noted_at")
            or hold.get("issue_url") != url
            or hold["at"] != at
            or format_retakes.released_before(fresh, url, commit)
        ):
            return False
        patch = format_retakes.fixed_release_patch(fresh, ports.now(), commit)
        if not patch:
            return False
        ports.documents.save(apply(fresh, patch))
        return True
    finally:
        lock.release(task_id)
