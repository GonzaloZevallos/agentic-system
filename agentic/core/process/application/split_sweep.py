"""The re-point of a split's dependents (GH-619, owner's rule 2026-11-12): a task that ends
`split` never closes its issue, so every open issue whose `Blocked by:` line names it would
wait for ever. The system rewrites those lines itself, to the last part(s) of the split,
and says so with one comment on each issue it edited.

Idempotent over a cut `gh`: each dependent is re-read right before its edit and skipped when
`rules.repoint_blockers` finds nothing left to change, so a second pass edits and comments
nothing. No compare-and-swap exists on an issue body: an edit a human makes between the
re-read and the write is lost (ADR-0062, GH-619 addendum).

The sweep (part 2 of GH-619) finishes what a failed pass left, every poller round: for each
open `split` parent without `split_repointed_at` it re-points the open dependents over one shared
`Inbox.open_issue_refs` read, and records the key through `Documents.record_closure` only when
the pass was complete. Steady state reads and writes nothing. A rate limit or an unreachable
tracker ends the round (`ROUND_STOPPERS`); anything else skips one parent. It never raises."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain import rules
from ..domain.landing import IssueRef
from ..domain.task import TaskState
from .ports import Ports, TrackerRateLimited, TrackerTimedOut, TrackerUnreachable

# The failures that say the tracker will not answer this round: they end the pass and the
# sweep instead of failing one issue after another (GH-619, invariant 7).
ROUND_STOPPERS = (TrackerRateLimited, TrackerUnreachable, TrackerTimedOut)

Rewrite = Callable[[str, int, Sequence[int]], str | None]


@dataclass(frozen=True)
class RepointResult:
    """What one pass did: the dependents whose listed body names the parent (`considered`),
    how many were edited (`changed`), how many failed (`failed`: a read, an edit, a comment or
    a refused post-condition), whether the list of open issues could not be read and whether
    the tracker stopped answering (`ROUND_STOPPERS`) before the pass ended."""

    considered: int = 0
    changed: int = 0
    failed: int = 0
    read_failed: bool = False
    stopped: bool = False

    @property
    def complete(self) -> bool:
        return not self.read_failed and not self.stopped and self.failed == 0


def repoint_dependents(
    ports: Ports,
    parent: int,
    targets: Sequence[int],
    refs: Sequence[IssueRef],
    excluded: Collection[int],
    rewrite: Rewrite = rules.repoint_blockers,
) -> RepointResult:
    """Re-points the `Blocked by:` lines naming `parent` in every issue of `refs` (from
    `Inbox.open_issue_refs`) that is not in `excluded` (the split's parts and the task's own
    issue). One edit and one comment per issue changed; a failure on one issue never stops
    the others."""
    considered = changed = failed = 0
    stopped = False
    for ref in refs:
        number = _issue_number(ref.url)
        if number is None or number in excluded or parent not in rules.blockers_from_body(ref.body):
            continue
        considered += 1
        try:
            done = _repoint(ports, parent, targets, ref, number, rewrite)
        except ROUND_STOPPERS:
            stopped = True
            break
        if done:
            changed += 1
        else:
            failed += 1
    return RepointResult(considered=considered, changed=changed, failed=failed, stopped=stopped)


def _repoint(
    ports: Ports, parent: int, targets: Sequence[int], ref: IssueRef, number: int, rewrite: Rewrite
) -> bool:
    """Whether the issue is done: edited and commented, or found already pointing past the
    parent. A failed read, edit or comment, or a refused post-condition, is `False`; a
    `ROUND_STOPPERS` failure propagates to `repoint_dependents`, which ends the pass."""
    try:
        old = ports.inbox.task(number)["description"]
    except ROUND_STOPPERS:
        raise
    except (ValueError, OSError):
        return False
    new = rewrite(old, parent, targets)
    if new is None:
        return True
    if not _post_condition(old, new, parent, targets):
        return False
    try:
        ports.tracker.update_issue_body(number, new)
    except ROUND_STOPPERS:
        raise
    except (ValueError, OSError):
        return False
    listed = ", ".join(f"#{n}" for n in targets)
    try:
        ports.tracker.comment_on_issue(
            ref.url,
            f"#{parent} was replaced by {listed} in this issue's blockers: #{parent} ended "
            "split into parts and is not closed by them, so this issue waits on the last "
            "part(s) instead.",
        )
    except ROUND_STOPPERS:
        raise
    except (ValueError, OSError):
        return False
    return True


def _post_condition(old: str, new: str, parent: int, targets: Sequence[int]) -> bool:
    """The rewrite changed the blockers exactly as asked -- `parent` replaced in place by
    `targets`, without repeats -- and touched only the lines that named `parent`."""
    swapped: list[int] = []
    for number in rules.blockers_from_body(old):
        swapped.extend(targets if number == parent else (number,))
    expected = tuple(dict.fromkeys(swapped))
    return rules.blockers_from_body(new) == expected and rules.repoint_confined(old, new, parent)


def _issue_number(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def repoint_targets(state: Mapping[str, object] | TaskState) -> tuple[list[int], set[int]] | None:
    """Where a split parent's dependents go: the issue numbers of `rules.last_part_numbers` of
    its parts, and every part's issue number (never edited). The one place that combines the
    two (GH-619). `None` for a split cut halfway (`split_issues` shorter than `parts`), a key
    missing or a URL that does not parse: nothing may be re-pointed to a part that is not there."""
    parts = state.get("parts")
    issues = state.get("split_issues")
    if not isinstance(parts, list) or not parts or not isinstance(issues, dict):
        return None
    if len(issues) != len(parts):
        return None
    try:
        numbers = {n: _issue_number(issues[str(n)]) for n in range(1, len(parts) + 1)}
    except (KeyError, AttributeError):
        return None
    if any(number is None for number in numbers.values()):
        return None
    targets = [numbers[n] for n in rules.last_part_numbers(parts)]
    return [t for t in targets if t is not None], {n for n in numbers.values() if n is not None}


def sweep(
    ports: Ports, documents: Mapping[int, TaskState | None], *, report_failures: bool = False
) -> list[str]:
    """One pass of the poller over the round's queue documents (`poll.queue_documents`): every
    `split` parent without `split_repointed_at` and with all its parts recorded gets its
    dependents re-pointed over one shared `open_issue_refs` read, and the record when the pass
    was complete. No such parent, no tracker call. Never raises. The lines are
    `agentic: split: GH-N: K dependents re-pointed`; with `report_failures` also the skipped
    round and the parent left unfinished."""
    pending = [
        (state, found)
        for state in documents.values()
        if state is not None
        and state.get("status") == "split"
        and not state.get("split_repointed_at")
        and (found := repoint_targets(state)) is not None
    ]
    if not pending:
        return []
    lines: list[str] = []
    try:
        refs = ports.inbox.open_issue_refs()
    except ROUND_STOPPERS as failed:
        return _skipped(failed, report_failures)
    except (ValueError, OSError) as failed:
        # No list, nothing to work from: the round ends here too, and the next one tries again.
        return _skipped(failed, report_failures)
    for state, (targets, parts) in pending:
        task_id = state["id"]
        try:
            parent = ports.inbox.issue_of(task_id)
            if parent is None:
                continue
            result = repoint_dependents(ports, parent, targets, refs, {*parts, parent})
            if result.stopped:
                return [*lines, *_skipped(None, report_failures)]
            if result.complete:
                ports.documents.record_closure(
                    task_id,
                    {"split_repointed_at": datetime.now(UTC).isoformat(timespec="seconds")},
                )
            elif report_failures:
                lines.append(f"agentic: split: {task_id}: re-point unfinished")
        except ROUND_STOPPERS as failed:
            return [*lines, *_skipped(failed, report_failures)]
        except (ValueError, OSError, KeyError):
            continue
        if result.changed:
            lines.append(f"agentic: split: {task_id}: {result.changed} dependents re-pointed")
    return lines


def _skipped(failed: Exception | None, report_failures: bool) -> list[str]:
    if not report_failures:
        return []
    name = type(failed).__name__ if failed is not None else "TrackerCallFailed"
    return [f"agentic: split: re-point skipped: {name}"]
