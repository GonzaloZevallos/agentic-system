"""The partition of a plan that does not fit (ADR-0054): the Tech Lead wrote `parts`
instead of subtasks, so nothing is built here. Each part becomes an issue of the product
repo, labelled `agentic` so the poller takes it up, chained to the parts it depends on
with a `Blocked by:` line, each with its own small pull request that lands before the
next one starts. The original task ends in `split`; it is not relaunched.

Idempotent over a cut `gh` (the receptor pattern): every body carries the marker
`<!-- agentic-split: <task id> part <k> -->`, looked for among the open issues before a
part is opened, and every part opened is recorded in `split_issues` at once. A retry
therefore completes what is missing and opens nothing twice.

A task that is itself a part (its own description carries a marker) never splits again:
depth is one. The plan stage asks the human before a part gets here; if one does, it
escalates as `rules.part_over_line` does, and a human decision builds it whole.

Tracker work (GH-350, ADR-0062): a plan with parts may carry a `tracker_checklist`, and the
task ends `split` without closing its issue, so every open issue that waits on it would wait
for ever. When the last part is recorded, the same patch saves the plan's items followed by
those `rules.split_tracker_items` derives for such issues; the list is then posted on the
original issue through `post_tracker_checklist` before the `Split:` comment. A resume that
finds every part already recorded never recomposes the list. The system edits no issue: the
human closes the task's issue or re-points the blocked one."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain.roles import PJM
from ..domain.task import WAITING_ON_HUMAN, PartRecord, TaskState, apply
from .ports import Ports
from .run_role import finish
from .tracker_only import post_tracker_checklist

# The label of the queue itself: the parts are tasks the poller must take up.
PART_LABEL = "agentic"
# How the comment on the original issue starts: a resume looks past the system's comments.
SPLIT = "Split: "


async def split(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task_id = state["id"]
    if (part_of := rules.split_part_of(state.get("description", ""))) is not None:
        # Defensive: the plan stage asks the human before a part reaches here.
        return finish(state, rules.part_over_line(task_id, part_of, None), ports)
    parts = state.get("parts", [])
    current = state
    # What the node returns besides the end: the graph's channels take the checklist and its
    # record from the patch, never from the document `finish` saved (invariant: the record's
    # hash is that of the checklist in the same state).
    carried: TaskState = {}
    try:
        open_bodies = list(ports.inbox.open_issues())
    except (ValueError, OSError) as exc:
        return _stopped(current, "reading the open issues", exc, ports, _LEFT_PARTS)
    for number, part in enumerate(parts, 1):
        key = str(number)
        if key in current.get("split_issues", {}):
            continue
        marker = rules.split_marker(task_id, number)
        url = next((found for found, body in open_bodies if marker in body), None)
        if url is None:
            body = _body(current, number, part, len(parts))
            try:
                url = ports.tracker.open_issue(part["title"], body, label=PART_LABEL)
            except (ValueError, OSError) as exc:
                return _stopped(current, f"opening part {number}", exc, ports, _LEFT_PARTS)
            open_bodies.append((url, body))
        patch: TaskState = {"split_issues": {key: url}}
        if len(current.get("split_issues", {})) + 1 == len(parts):
            # The last part exists: the list is composed once, saved with its URL.
            items = _checklist(
                current, ports, open_bodies, {**current.get("split_issues", {}), key: url}
            )
            if items:
                patch["tracker_checklist"] = items
                carried["tracker_checklist"] = items
        finish(current, patch, ports)  # saved as soon as the part exists
        current = apply(current, patch)
    if items := current.get("tracker_checklist"):
        try:
            record = post_tracker_checklist(
                ports, task_id, items, current.get("tracker_checklist_posted")
            )
        except (ValueError, OSError) as exc:
            return _stopped(current, "posting the tracker checklist", exc, ports, _LEFT_CHECKLIST)
        if record is not None:
            posted: TaskState = {"tracker_checklist_posted": record}
            finish(current, posted, ports)  # saved before the split comment can fail
            current = apply(current, posted)
            carried.update(posted)
    issues = current.get("split_issues", {})
    listed = "\n".join(
        f"{n}. {parts[n - 1]['title']}: {issues[str(n)]}" for n in range(1, len(parts) + 1)
    )
    try:
        ports.tracker.comment(
            task_id,
            f"{SPLIT}the estimate does not fit one task, so it was split into {len(parts)} "
            f"chained issues, each with its own pull request:\n\n{listed}\n\n"
            "This task is NOT relaunched: the parts replace it.\n\n"
            f"Task document: `{document.relative_path(task_id)}`",
        )
    except (ValueError, OSError) as exc:
        return _stopped(current, "commenting the list of parts", exc, ports, _LEFT_COMMENT)
    ended = rules.finished("split", datetime.now(UTC).isoformat(timespec="seconds"))
    return finish(current, {"current_role": PJM.name, **carried, **ended}, ports)


def _checklist(
    state: TaskState, ports: Ports, open_bodies: list[tuple[str, str]], issues: dict[str, str]
) -> list[str]:
    """The plan's items, then the ones the system derives for the issues the split blocks."""
    parts = state.get("parts", [])
    urls = [issues[str(n)] for n in range(1, len(parts) + 1)]
    derived = rules.split_tracker_items(
        ports.inbox.issue_of(state["id"]), open_bodies, urls, rules.last_part_numbers(parts)
    )
    return [*state.get("tracker_checklist", []), *derived]


# What is left when a step fails, so the human reads what a resume still has to do.
_LEFT_PARTS = "the missing parts, the tracker checklist and the split comment are left"
_LEFT_CHECKLIST = "the tracker checklist and the split comment are left"
_LEFT_COMMENT = "the split comment is left"


def _stopped(state: TaskState, step: str, exc: Exception, ports: Ports, left: str) -> TaskState:
    done = len(state.get("split_issues", {}))
    patch = rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "failure",
                f"split: {step} failed ({exc}); {done} of "
                f"{len(state.get('parts', []))} parts are open and {left}, resume to finish",
            ),
        }
    )
    return finish(state, patch, ports)


def _body(state: TaskState, number: int, part: PartRecord, total: int) -> str:
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
        *([f"## Invariants\n\n{invariants}"] if invariants else []),
        f"Part {number} of {total} of {task_id}. The plan and its interview are in "
        f"`{document.relative_path(task_id)}`; the original task branch "
        f"`{rules.task_branch(task_id)}` is context for reading, nothing to bring in.",
        rules.split_marker(task_id, number),
    ]
    return "\n\n".join(sections)


def _issue_number(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])
