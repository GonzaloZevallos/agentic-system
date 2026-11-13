"""The partition of a plan that does not fit (ADR-0054): the Tech Lead wrote `parts`
instead of subtasks, so nothing is built here. Each part becomes an issue of the product
repo, labelled `agentic` so the poller takes it up, chained to the parts it depends on
with a `Blocked by:` line, each with its own small pull request that lands before the
next one starts. The original task ends in `split`; it is not relaunched.

Idempotent over a cut `gh` (the receptor pattern): every body carries the marker
`<!-- agentic-split: <task id> part <k> -->`, looked for among the open issues before a
part is opened, and every part opened is recorded in `split_issues` at once. A retry
therefore completes what is missing and opens nothing twice.

A task that is itself a part (its own description carries a marker) splits again up to
`rules.MAX_SPLIT_DEPTH`: its children's markers name this task and carry the depth after its
own. A part at the cap does not split; the plan stage asks the human before one gets here, and
if one does, it escalates as `rules.part_over_line` does. The marker's depth is optional when
read: a part opened before depth existed is found again by a retry, read at depth 1.

Tracker work (GH-350, ADR-0062): a plan with parts may carry a `tracker_checklist`, posted on
the original issue through `post_tracker_checklist` before the `Split:` comment. The task ends
`split` without closing its issue, so every open issue that waits on it with a `Blocked by:`
line would wait for ever; since the owner's rule of 2026-11-12 the system re-points those lines
itself (`split_sweep.repoint_dependents`, GH-619), to `rules.last_part_numbers`, once the parts
exist and before the checklist is posted, on every run, resumes included. A read or an edit that
fails never stops the split: the `Split:` comment says the re-point did not finish and the sweep
of part 2 finishes it. The checklist is only the plan's own items; a split in flight before
GH-619 may still hold derived items in its saved checklist, and posts them on resume."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import document, lead_time, pull_request, rules
from ..domain.landing import IssueRef
from ..domain.roles import PJM
from ..domain.task import WAITING_ON_HUMAN, PartRecord, TaskState, apply
from .ports import Ports
from .run_role import finish
from .split_sweep import ROUND_STOPPERS, RepointResult, repoint_dependents, repoint_targets
from .tracker_only import post_tracker_checklist

# The label of the queue itself: the parts are tasks the poller must take up.
PART_LABEL = "agentic"
# How the comment on the original issue starts: a resume looks past the system's comments.
SPLIT = "Split: "


async def split(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task_id = state["id"]
    part_of = rules.split_part_of(state.get("description", ""))
    if part_of is not None and part_of[2] >= rules.MAX_SPLIT_DEPTH:
        # Defensive: the plan stage asks the human before a part at the cap reaches here.
        over = _paused(state, rules.part_over_line(task_id, part_of, None, at=ports.now()))
        over["fit_question"] = True
        return finish(state, over, ports)
    depth = 1 if part_of is None else part_of[2] + 1
    parts: list[PartRecord] = [
        {**part, "depth": part.get("depth", depth)} for part in state.get("parts", [])
    ]
    current = state
    # What the node returns besides the end: the graph's channels take the checklist and its
    # record from the patch, never from the document `finish` saved (invariant: the record's
    # hash is that of the checklist in the same state).
    carried = _base(state)
    if parts != state.get("parts", []):
        # The depth each part is opened at is saved with the record, so a retry opens the
        # same one.
        stamped = _base(current)
        stamped["parts"] = parts
        finish(current, stamped, ports)
        current = apply(current, stamped)
        carried.update(stamped)
    try:
        open_bodies = list(ports.inbox.open_issues())
    except (ValueError, OSError) as exc:
        return _stopped(current, "reading the open issues", exc, ports, _LEFT_PARTS)
    # A part that splits: the one terminal child's pull request closes this part's issue too.
    terminal = rules.last_part_numbers(parts)
    parent = ports.inbox.issue_of(task_id) if part_of is not None else None
    for number, part in enumerate(parts, 1):
        key = str(number)
        closes = parent if parent is not None and terminal == [number] else None
        if key in current.get("split_issues", {}):
            continue
        # Found by what the marker names, not by its text: an old marker has no depth.
        wanted = (task_id, number, depth)
        url = next(
            (found for found, body in open_bodies if rules.split_part_of(body) == wanted), None
        )
        if url is None:
            body = _body(current, number, part, len(parts), depth, closes)
            try:
                url = ports.tracker.open_issue(part["title"], body, label=PART_LABEL)
            except (ValueError, OSError) as exc:
                return _stopped(current, f"opening part {number}", exc, ports, _LEFT_PARTS)
            open_bodies.append((url, body))
        patch = _base(current)
        patch["split_issues"] = {key: url}
        finish(current, patch, ports)  # saved as soon as the part exists
        current = apply(current, patch)
    swept = _repoint(current, parts, ports)
    if items := current.get("tracker_checklist"):
        try:
            record = post_tracker_checklist(
                ports, task_id, items, current.get("tracker_checklist_posted")
            )
        except (ValueError, OSError) as exc:
            return _stopped(current, "posting the tracker checklist", exc, ports, _LEFT_CHECKLIST)
        if record is not None:
            posted = _base(current)
            posted["tracker_checklist_posted"] = record
            finish(current, posted, ports)  # saved before the split comment can fail
            current = apply(current, posted)
            carried.update(posted)
    issues = current.get("split_issues", {})
    listed = "\n".join(
        f"{n}. {parts[n - 1]['title']}: {issues[str(n)]}" for n in range(1, len(parts) + 1)
    )
    unfinished = "" if swept.complete else f"{_unfinished(swept)}\n\n"
    try:
        ports.tracker.comment(
            task_id,
            f"{SPLIT}the estimate does not fit one task, so it was split into {len(parts)} "
            f"chained issues, each with its own pull request:\n\n{listed}\n\n"
            "This task is NOT relaunched: the parts replace it.\n\n"
            f"{unfinished}Task document: `{document.relative_path(task_id)}`",
        )
    except (ValueError, OSError) as exc:
        return _stopped(current, "commenting the list of parts", exc, ports, _LEFT_COMMENT)
    ended = rules.finished("split", datetime.now(UTC).isoformat(timespec="seconds"))
    end = _base(current)
    end.update(carried)
    end["current_role"] = PJM.name
    end["status"] = ended["status"]
    end["finished_at"] = ended["finished_at"]
    end["lead_time"] = lead_time.NO_MERGE[lead_time.LEAD_TIME_KEY]
    return finish(current, end, ports)


def _repoint(state: TaskState, parts: list[PartRecord], ports: Ports) -> RepointResult:
    """The dependents of the task re-pointed to the last part(s) (GH-619). Never raises: a
    failed read of the open issues is a result, and the split goes on."""
    parent = ports.inbox.issue_of(state["id"])
    if parent is None:
        return RepointResult()
    stamped: TaskState = {**state, "parts": parts}
    found = repoint_targets(stamped)
    if found is None:
        return RepointResult(read_failed=True)
    targets, excluded = found
    try:
        refs: list[IssueRef] = ports.inbox.open_issue_refs()
    except ROUND_STOPPERS as stopper:
        return RepointResult(stopped=True, stopped_by=type(stopper).__name__)
    except (ValueError, OSError):
        return RepointResult(read_failed=True)
    return repoint_dependents(ports, parent, targets, refs, {*excluded, parent})


def _unfinished(swept: RepointResult) -> str:
    """The one line of the `Split:` comment when the re-point did not finish (no exception
    text: it is for the human, and part 2's sweep does the rest)."""
    head = "The re-point of blocked issues did not finish: "
    if swept.read_failed:
        return f"{head}the open issues could not be read; the sweep of part 2 finishes it."
    if swept.stopped:
        return f"{head}the tracker stopped answering; the sweep of part 2 finishes it."
    return f"{head}{swept.failed} of {swept.considered} failed; the sweep of part 2 finishes it."


# What is left when a step fails, so the human reads what a resume still has to do.
_LEFT_PARTS = "the missing parts, the tracker checklist and the split comment are left"
_LEFT_CHECKLIST = "the tracker checklist and the split comment are left"
_LEFT_COMMENT = "the split comment is left"


def _stopped(state: TaskState, step: str, exc: Exception, ports: Ports, left: str) -> TaskState:
    done = len(state.get("split_issues", {}))
    paused = _paused(
        state,
        rules.logged(
            {
                "current_role": PJM.name,
                "status": WAITING_ON_HUMAN,
                **rules.escalation(
                    "failure",
                    f"split: {step} failed ({exc}); {done} of "
                    f"{len(state.get('parts', []))} parts are open and {left}, resume to finish",
                ),
            },
            at=ports.now(),
        ),
    )
    return finish(state, paused, ports)


def _paused(state: TaskState, logged: dict[str, Any]) -> TaskState:
    """The pause `rules.logged` composed, key by key into a typed patch: the rules layer
    answers a plain dict, so each value is copied to the field it belongs to."""
    patch = _base(state)
    patch["current_role"] = logged["current_role"]
    patch["status"] = logged["status"]
    patch["escalated"] = logged["escalated"]
    patch["escalation_class"] = logged["escalation_class"]
    patch["escalation_cause"] = logged["escalation_cause"]
    patch["pause_log"] = logged["pause_log"]
    return patch


def _base(state: TaskState) -> TaskState:
    """The start of every patch of the node: `TaskInput` requires these three keys to type a
    patch, so they are written back unchanged (as `pre_pr_fix` does)."""
    return {"id": state["id"], "title": state["title"], "description": state["description"]}


def _body(
    state: TaskState, number: int, part: PartRecord, total: int, depth: int, closes: int | None
) -> str:
    task_id = state["id"]
    issues = state.get("split_issues", {})
    blockers = ", ".join(f"#{_issue_number(issues[str(dep)])}" for dep in part["depends_on"])
    invariants = "\n".join(f"{n}. {line}" for n, line in enumerate(state.get("invariants", []), 1))
    sections = [
        # One line for every dependency, first in the body by convention: `rules.blockers_from_body`
        # reads every whole `Blocked by:` line outside a code fence (GH-415), so the position
        # no longer matters. See `test_the_reader_of_blockers_unites_every_blocked_by_line`.
        *([f"Blocked by: {blockers}"] if blockers else []),
        part["body"].strip(),
        *_kind_lines(part),
        *([f"## Invariants\n\n{invariants}"] if invariants else []),
        f"Part {number} of {total} of {task_id}. The plan and its interview are in "
        f"`{document.relative_path(task_id)}`; the original task branch "
        f"`{rules.task_branch(task_id)}` is context for reading, nothing to bring in.",
        rules.split_marker(task_id, number, depth),
        *([pull_request.closes_marker(closes)] if closes is not None else []),
    ]
    return "\n\n".join(sections)


def _kind_lines(part: PartRecord) -> list[str]:
    """What the issue says of the part's kind and, when it has one, its guard (GH-592): the
    reviewer of the part's pull request reads them from the issue."""
    lines = [f"Kind: {part['kind']}"] if "kind" in part else []
    if part.get("guard"):
        lines.append(f"Guard: {part['guard']}")
    return ["\n".join(lines)] if lines else []


def _issue_number(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])
