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
from .ports import IssueState, Ports, TrackerRateLimited, TrackerTimedOut, TrackerUnreachable

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
    # The class name of the stopper that ended the pass (`stopped` is set with it).
    stopped_by: str = ""

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
    stopped = ""
    for ref in refs:
        number = _issue_number(ref.url)
        if number is None or number in excluded or parent not in rules.blockers_from_body(ref.body):
            continue
        considered += 1
        try:
            done = _repoint(ports, parent, targets, ref, number, rewrite)
        except ROUND_STOPPERS as stopper:
            stopped = type(stopper).__name__
            break
        if done:
            changed += 1
        else:
            failed += 1
    return RepointResult(
        considered=considered,
        changed=changed,
        failed=failed,
        stopped=bool(stopped),
        stopped_by=stopped,
    )


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


def part_numbers(state: TaskState) -> list[int] | None:
    """The issue number of each part of a split, in the order of `parts` (part 1 first). `None`
    for a split cut halfway (`split_issues` shorter than `parts`), a key missing or a URL that
    does not parse: nothing may be judged or re-pointed to a part that is not there."""
    parts = state.get("parts", [])
    issues = state.get("split_issues", {})
    if not parts or len(issues) != len(parts):
        return None
    numbers: list[int] = []
    for n in range(1, len(parts) + 1):
        number = _issue_number(issues.get(str(n), ""))
        if number is None:
            return None
        numbers.append(number)
    return numbers


def repoint_targets(state: TaskState) -> tuple[list[int], set[int]] | None:
    """Where a split parent's dependents go: the issue numbers of `rules.last_part_numbers` of
    its parts, and every part's issue number (never edited). The one place that combines the
    two (GH-619). `None` when `part_numbers` finds no complete set of parts."""
    numbers = part_numbers(state)
    if numbers is None:
        return None
    return [numbers[n - 1] for n in rules.last_part_numbers(state["parts"])], set(numbers)


def _judges_nothing(number: int) -> rules.BlockerVerdict:
    return "unknown"


class _RoundStopped(Exception):
    """A re-point pass inside a close ended on a `ROUND_STOPPERS` failure, named by `name`."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class _Refs:
    """The round's one `Inbox.open_issue_refs` read, taken on first use and kept, a failure
    included: a failed read is not repeated for every parent."""

    def __init__(self, ports: Ports) -> None:
        self._ports = ports
        self._refs: list[IssueRef] | None = None
        self._failure: Exception | None = None

    def get(self) -> list[IssueRef]:
        if self._failure is not None:
            raise self._failure
        if self._refs is None:
            try:
                self._refs = list(self._ports.inbox.open_issue_refs())
            except (ValueError, OSError) as failed:
                self._failure = failed
                raise
        return self._refs


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sweep(
    ports: Ports,
    documents: Mapping[int, TaskState | None],
    *,
    verdict: Callable[[int], rules.BlockerVerdict] = _judges_nothing,
    report_failures: bool = False,
) -> list[str]:
    """One pass of the poller over the round's queue documents (`poll.queue_documents`). First,
    every `split` parent without `split_repointed_at` and with all its parts recorded gets its
    dependents re-pointed over one shared `open_issue_refs` read, and the record when the pass
    was complete. Then every such parent none of whose parts is in the queue is judged
    (`_judge`): closed when done, escalated once when a part was abandoned. `verdict` is the
    blocker verdict of one part (`poll._part_verdict`), asked only for a part closed
    `COMPLETED`. No parent to work on, no tracker call. Never raises. The lines are
    `agentic: split: GH-N: K dependents re-pointed`, `agentic: split: GH-N: parent closed` and
    `agentic: split: GH-N: part K abandoned, escalated`; with `report_failures` also the skipped
    round and the parent left unfinished."""
    refs = _Refs(ports)
    lines: list[str] = []
    finished: set[str] = set()
    pending = [
        (state, found)
        for state in documents.values()
        if state is not None
        and state.get("status") == "split"
        and not state.get("split_repointed_at")
        and (found := repoint_targets(state)) is not None
    ]
    if pending:
        try:
            shared = refs.get()
        except ROUND_STOPPERS as failed:
            return _skipped(type(failed).__name__, report_failures)
        except (ValueError, OSError) as failed:
            # No list, nothing to work from: the round ends here too, the next one tries again.
            return _skipped(type(failed).__name__, report_failures)
    for state, (targets, parts) in pending:
        task_id = state["id"]
        try:
            parent = ports.inbox.issue_of(task_id)
            if parent is None:
                continue
            result = repoint_dependents(ports, parent, targets, shared, {*parts, parent})
            if result.stopped:
                return [*lines, *_skipped(result.stopped_by, report_failures)]
            if result.complete:
                ports.documents.record_closure(task_id, {"split_repointed_at": _now()})
                finished.add(task_id)
            elif report_failures:
                lines.append(f"agentic: split: {task_id}: re-point unfinished")
        except ROUND_STOPPERS as failed:
            return [*lines, *_skipped(type(failed).__name__, report_failures)]
        except (ValueError, OSError, KeyError):
            continue
        if result.changed:
            lines.append(f"agentic: split: {task_id}: {result.changed} dependents re-pointed")
    states: dict[int, IssueState] = {}
    for document in documents.values():
        numbers = _judgeable(document, documents)
        if document is None or numbers is None:
            continue
        try:
            repointed = document["id"] in finished
            lines.extend(
                _judge(ports, document, numbers, verdict, states, refs, repointed, report_failures)
            )
        except _RoundStopped as stopped:
            return [*lines, *_skipped(stopped.name, report_failures)]
        except ROUND_STOPPERS as failed:
            return [*lines, *_skipped(type(failed).__name__, report_failures)]
        except (ValueError, OSError, KeyError):
            continue
    return lines


def _judgeable(state: TaskState | None, queue: Mapping[int, object]) -> list[int] | None:
    """The part numbers of a parent worth judging: a `split` document with every part recorded
    and none of them in this round's queue (a part still open and labelled is still being built:
    no call at all for that parent)."""
    if state is None or state.get("status") != "split":
        return None
    numbers = part_numbers(state)
    if numbers is None or any(number in queue for number in numbers):
        return None
    return numbers


def _judge(
    ports: Ports,
    state: TaskState,
    numbers: list[int],
    verdict: Callable[[int], rules.BlockerVerdict],
    states: dict[int, IssueState],
    refs: _Refs,
    repointed: bool,
    report_failures: bool,
) -> list[str]:
    """What one parent's parts come to. One `issue_state` read per part, memoised by number in
    `states`; `verdict` is asked only for a part closed `COMPLETED`. `ROUND_STOPPERS` and
    `ValueError`/`OSError`/`KeyError` propagate to `sweep`."""
    rows: list[tuple[str, str | None, rules.BlockerVerdict]] = []
    for number in numbers:
        if number not in states:
            states[number] = ports.tracker.issue_state(number)
        part = states[number]
        if part.state != "CLOSED":
            part_verdict: rules.BlockerVerdict = "open"
        elif part.state_reason == "COMPLETED":
            part_verdict = verdict(number)
        else:
            part_verdict = "unknown"
        rows.append((part.state, part.state_reason, part_verdict))
    match rules.split_parent_state(rows):
        case "done":
            return _close(ports, state, numbers, refs, repointed, report_failures)
        case "abandoned":
            first = next(
                i for i, (st, why, _) in enumerate(rows) if (st, why) == ("CLOSED", "NOT_PLANNED")
            )
            return _escalate(ports, state, numbers, first + 1)
        case _:
            return []


def _close(
    ports: Ports,
    state: TaskState,
    numbers: list[int],
    refs: _Refs,
    repointed: bool,
    report_failures: bool,
) -> list[str]:
    """Every part merged: one fresh re-point pass (tidiness: once `split_closed_at` is recorded
    the verdict of a dependent that still names the parent resolves anyway), then the record,
    and only then the close with its comment. A record that cannot be written means no close."""
    task_id = state["id"]
    targets, parts = repoint_targets(state) or ([], set())
    parent = ports.inbox.issue_of(task_id)
    if parent is None:
        return []
    if not repointed:
        result = repoint_dependents(ports, parent, targets, refs.get(), {*parts, parent})
        if result.stopped:
            raise _RoundStopped(result.stopped_by)
    unfinished = [f"agentic: split: {task_id}: close unfinished"] if report_failures else []
    if not state.get("split_closed_at"):
        try:
            ports.documents.record_closure(task_id, {"split_closed_at": _now()})
        except (ValueError, OSError):
            return unfinished
    try:
        ports.tracker.close_issue(task_id, comment=_closing_comment(ports, numbers))
    except ROUND_STOPPERS:
        raise
    except (ValueError, OSError):
        return unfinished
    return [f"agentic: split: {task_id}: parent closed"]


def _closing_comment(ports: Ports, numbers: Sequence[int]) -> str:
    """The note the parent closes with: each part and the pull request of its own document,
    `split again (see #n)` for a part that was split in its turn, `no PR recorded` for one with
    no document, an unreadable one or no pull request. Never raises."""
    rows = []
    for number in numbers:
        try:
            document = ports.documents.load(ports.inbox.task_id(number)) or {}
        except (ValueError, OSError):
            document = {}
        if document.get("status") == "split":
            shown = f"split again (see #{number})"
        else:
            shown = str(document.get("pull_request") or "no PR recorded")
        rows.append(f"- #{number}: {shown}")
    return "All parts of this split merged, so this issue is closed.\n\n" + "\n".join(rows)


def _escalate(ports: Ports, state: TaskState, numbers: list[int], part: int) -> list[str]:
    """One comment per parent when a part was abandoned (closed as not planned): none when
    `split_abandoned_at` is recorded; none either when the comments already carry the part's
    marker (a cut between the comment and the record), and then only the record is written.
    No label. `ROUND_STOPPERS` propagate; `ValueError`/`OSError` skip the parent."""
    task_id = state["id"]
    parent = ports.inbox.issue_of(task_id)
    if parent is None or state.get("split_abandoned_at"):
        return []
    marker = f"<!-- agentic-split-abandoned: {task_id} part {part} -->"
    if not any(marker in text for text in ports.tracker.issue_comments(task_id)):
        ports.tracker.comment_on_issue(str(parent), _escalation_text(state, numbers, part, marker))
    ports.documents.record_closure(task_id, {"split_abandoned_at": _now()})
    return [f"agentic: split: {task_id}: part {part} abandoned, escalated"]


def _escalation_text(state: TaskState, numbers: list[int], part: int, marker: str) -> str:
    parts = state["parts"]
    if part in rules.last_part_numbers(parts):
        effect = "Its re-pointed dependents are now free."
    else:
        waiting = ", ".join(f"#{numbers[n - 1]}" for n in rules.last_part_numbers(parts))
        effect = (
            f"The dependents still wait on part(s) {waiting}; the next parts of the chain may "
            "launch over this one."
        )
    return (
        f"Part {part} (#{numbers[part - 1]}) of this split was abandoned: its issue was closed "
        f"as not planned. {effect}\n\nWhat should happen with this parent? The system leaves it "
        f"open.\n\n{marker}"
    )


def _skipped(name: str, report_failures: bool) -> list[str]:
    return [f"agentic: split: re-point skipped: {name}"] if report_failures else []
