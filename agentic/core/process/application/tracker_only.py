"""Work that is only on the issue tracker (GH-336, ADR-0062): no role has `gh`, so the plan
carries a `tracker_checklist` and the system tells the human what to do, once, as a comment
on the issue. The task ends `tracker_only`: no role runs, no pull request opens and no
`delivery_outcome` is set.

Idempotent over a lost checkpoint (the receptor pattern): the comment ends with
`rules.tracker_marker` -- the task and a hash of the items -- looked for among the issue's
comments before posting, and the document record `tracker_checklist_posted` is written only
after the comment succeeded. A repeated post is a no-op; a changed checklist is a new
comment that says it replaces the earlier one. `post_tracker_checklist` is the only function
that posts the checklist."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain.roles import PJM
from ..domain.task import TaskState, TrackerPosted
from .ports import Ports
from .release import release_task
from .run_role import finish

# How the comment starts: a resume looks past the system's comments (`resume.SYSTEM_COMMENTS`).
TRACKER_WORK = "Tracker work for you: "

_EARLIER = re.compile(r"<!-- agentic-tracker: (\S+) [0-9a-f]{12} -->")


def post_tracker_checklist(
    ports: Ports, task_id: str, items: Sequence[str], recorded: object
) -> TrackerPosted | None:
    """Posts the checklist unless it is already there, and returns the record to keep.

    `recorded` is whatever the state held: `rules.tracker_posted` reads it, so `{}` (the graph
    channel's start value), a partial record or nothing at all means "nothing posted yet".
    `None` when `recorded` holds this hash: nothing is read and nothing is posted. Otherwise
    the issue's comments are read once; the comment is posted only if none carries the
    marker of these items, and the record is returned after that succeeded, never before.
    Raises `ValueError`/`OSError` from the tracker; nothing is recorded then."""
    hash_ = rules.tracker_hash(items)
    held = rules.tracker_posted(recorded)
    if held is not None and held["hash"] == hash_:
        return None
    marker = rules.tracker_marker(task_id, items)
    comments = ports.tracker.issue_comments(task_id)
    if not any(marker in body for body in comments):
        replaces = any(
            (found := _EARLIER.search(body)) is not None and found.group(1) == task_id
            for body in comments
            if body.startswith(TRACKER_WORK)
        )
        ports.tracker.comment(task_id, _body(task_id, items, marker, replaces))
    return {"at": datetime.now(UTC).isoformat(timespec="seconds"), "hash": hash_}


def _body(task_id: str, items: Sequence[str], marker: str, replaces: bool) -> str:
    lead = (
        "this list replaces the earlier one on this issue; the system cannot edit it."
        if replaces
        else "the system has no access to do this part, so it is yours."
    )
    checks = "\n".join(f"- [ ] {item.strip()}" for item in items)
    return (
        f"{TRACKER_WORK}{lead}\n\n{checks}\n\n"
        f"Task document: `{document.relative_path(task_id)}`\n\n{marker}"
    )


async def tracker_only(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task_id = state["id"]
    try:
        record = post_tracker_checklist(
            ports,
            task_id,
            state.get("tracker_checklist", []),
            state.get("tracker_checklist_posted"),
        )
    except (ValueError, OSError) as exc:
        return _stopped(state, exc, ports)
    reaped = release_task(ports, task_id)
    ended = rules.finished("tracker_only", datetime.now(UTC).isoformat(timespec="seconds"))
    return finish(
        state,
        {
            "current_role": PJM.name,
            **ended,
            **({"tracker_checklist_posted": record} if record else {}),
            **({"reaped_at_cleanup": list(reaped)} if reaped else {}),
        },
        ports,
    )


def _stopped(state: TaskState, exc: Exception, ports: Ports) -> TaskState:
    patch = rules.logged(
        {
            "current_role": PJM.name,
            "status": "waiting_on_human",
            **rules.escalation(
                "failure",
                f"tracker only: posting the checklist failed ({exc}); resume to post it",
            ),
        }
    )
    return finish(state, patch, ports)
