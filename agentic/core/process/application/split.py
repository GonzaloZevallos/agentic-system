"""The partition of a plan that does not fit (ADR-0053): the Tech Lead wrote `parts`
instead of subtasks, so nothing is built here. Each part becomes an issue of the product
repo, labelled `agentic` so the poller takes it up, chained to the parts it depends on
with a `Blocked by:` line, each with its own small pull request that lands before the
next one starts. The original task ends in `split`; it is not relaunched.

Idempotent over a cut `gh` (the receptor pattern): every body carries the marker
`<!-- agentic-split: <task id> part <k> -->`, looked for among the open issues before a
part is opened, and every part opened is recorded in `split_issues` at once. A retry
therefore completes what is missing and opens nothing twice.

A task that is itself a part (its own description carries a marker) never splits again:
depth is one. It escalates with the class `estimate` and only a human decision, which
`rules.resumed` turns into a waived fit, plans it whole."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain.roles import PJM
from ..domain.task import WAITING_ON_HUMAN, PartRecord, TaskState, apply
from .ports import Ports
from .run_role import finish

# The label of the queue itself: the parts are tasks the poller must take up.
PART_LABEL = "agentic"
# How the comment on the original issue starts: a resume looks past the system's comments.
SPLIT = "Split: "


async def split(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task_id = state["id"]
    if rules.split_part_of(state.get("description", "")) is not None:
        return finish(state, _part_does_not_fit(task_id), ports)
    parts = state.get("parts", [])
    current = state
    try:
        open_bodies = list(ports.inbox.open_issues())
    except (ValueError, OSError) as exc:
        return _stopped(current, "reading the open issues", exc, ports)
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
                return _stopped(current, f"opening part {number}", exc, ports)
            open_bodies.append((url, body))
        patch: TaskState = {"split_issues": {key: url}}
        finish(current, patch, ports)  # saved as soon as the part exists
        current = apply(current, patch)
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
        return _stopped(current, "commenting the list of parts", exc, ports)
    return finish(current, {"current_role": PJM.name, "status": "split"}, ports)


def _part_does_not_fit(task_id: str) -> TaskState:
    return rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "estimate",
                f"{task_id} is itself a part of a split and its plan does not fit either; a "
                "part is never split again. Decide in a comment whether to build it whole "
                "anyway, or to stop, then resume",
            ),
        }
    )


def _stopped(state: TaskState, step: str, exc: Exception, ports: Ports) -> TaskState:
    done = len(state.get("split_issues", {}))
    patch = rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "failure",
                f"split: {step} failed ({exc}); {done} of "
                f"{len(state.get('parts', []))} parts are open, resume to finish",
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
        # One line for every dependency, not one line each (invariant 8 cannot hold literally):
        # `rules.blockers_from_body` reads the first non-empty line only. See the test
        # `test_the_reader_of_blockers_loses_every_blocker_after_the_first_line`.
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
