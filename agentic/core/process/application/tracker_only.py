"""Work that is only on the issue tracker (GH-336, ADR-0062): no role has `gh`, so the plan
carries typed `tracker_items`: the system performs its `comment` items, reports them in one
`DONE` comment and raises the `human` ones to the owner (GH-705). An old document carries a
`tracker_checklist` instead, and the system tells the human what to do, once, as a comment
on the issue. The task ends `tracker_only`: no role runs, no pull request opens and no
`delivery_outcome` is set.

Idempotent over a lost checkpoint (the receptor pattern): the comment ends with
`rules.tracker_marker` -- the task and a hash of the items -- looked for among the issue's
comments before posting, and the document record `tracker_checklist_posted` is written only
after the comment succeeded. A repeated post is a no-op; a changed checklist is a new
comment that says it replaces the earlier one. `post_tracker_checklist` is the only function
that posts the checklist."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import document, lead_time, rules, tracker_items
from ..domain.roles import PJM
from ..domain.task import TaskState, TrackerActionDone, TrackerPosted, add_tracker_actions
from .ports import Ports
from .release import release_task
from .run_role import finish
from .tracker_items import run_system_items

_LOG = logging.getLogger(__name__)

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
    """Order (GH-705): the plan's system items, the DONE report of them, the `human` items as a
    needs-human escalation, then the legacy checklist. A failure of the first two escalates
    `failure` with the records written so far in the patch."""
    task_id = state["id"]
    before = state.get("tracker_actions_done", {})
    sent = run_system_items(state, ports)
    for lapse in sent.lapses:
        _LOG.warning("tracker_only: %s", lapse)
    state = sent.state
    records = {k: v for k, v in state.get("tracker_actions_done", {}).items() if k not in before}
    if sent.failed is not None:
        return _stopped(state, records, _step_reason("posting a plan note", sent.failed), ports)
    try:
        _post_done_report(state, ports)
    except Exception as failed:
        return _stopped(state, records, _step_reason("posting the done report", failed), ports)
    # GH-620: typed human items reach the owner as a needs-human escalation, after the DONE
    # report; the records of both sources are joined, never one written over the other.
    if human := tracker_items.escalate_human(state, ports.now()):
        joined = add_tracker_actions(records, human["tracker_actions_done"])
        return finish(
            state,
            {"current_role": PJM.name, **human, "tracker_actions_done": joined},
            ports,
        )
    record: TrackerPosted | None = None
    checklist = state.get("tracker_checklist", [])
    try:
        # Typed items all answered and no legacy checklist: nothing is left to post.
        if checklist or not state.get("tracker_items"):
            record = post_tracker_checklist(
                ports, task_id, checklist, state.get("tracker_checklist_posted")
            )
    except (ValueError, OSError) as exc:
        return _stopped(
            state,
            records,
            f"tracker only: posting the checklist failed ({exc}); resume to post it",
            ports,
        )
    reaped = release_task(ports, task_id)
    ended = rules.finished("tracker_only", datetime.now(UTC).isoformat(timespec="seconds"))
    return finish(
        state,
        {
            "current_role": PJM.name,
            **ended,
            **lead_time.NO_MERGE,
            **({"tracker_checklist_posted": record} if record else {}),
            **({"tracker_actions_done": records} if records else {}),
            **({"reaped_at_cleanup": list(reaped)} if reaped else {}),
        },
        ports,
    )


def _post_done_report(state: TaskState, ports: Ports) -> None:
    """Posts the report of what the system did, unless the issue already holds its marker. The
    body is `tracker_items.report` only and the marker is over the hashes of the items done,
    never over the report's text. Nothing is read and nothing posted when nothing was done."""
    items = tracker_items.parse(state.get("tracker_items", []))
    done = state.get("tracker_actions_done", {})
    did = tracker_items.report(items, done)
    if not did:
        return
    hashes = [
        key
        for item in tracker_items.system_items(items)
        if "kind" in done.get(key := tracker_items.item_hash(item), {})
    ]
    line = tracker_items.done_marker(state["id"], hashes)
    if any(line in body for body in ports.tracker.issue_comments(state["id"])):
        return
    ports.tracker.comment(state["id"], f"{tracker_items.DONE}\n{did}\n\n{line}")


def _step_reason(step: str, exc: Exception) -> str:
    # The step and the class only: the text of an exception from `gh` can hold anything.
    return f"tracker only: {step} failed ({type(exc).__name__}); resume to try again"


def _stopped(
    state: TaskState, records: dict[str, TrackerActionDone], reason: str, ports: Ports
) -> TaskState:
    patch = rules.logged(
        {
            "current_role": PJM.name,
            "status": "waiting_on_human",
            **rules.escalation("failure", reason),
            **({"tracker_actions_done": records} if records else {}),
        },
        at=ports.now(),
    )
    return finish(state, patch, ports)
