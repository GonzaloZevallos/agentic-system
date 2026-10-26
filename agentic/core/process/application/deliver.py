"""The delivery to the human (ADR-0011): the task branch is pushed, a pull request
opened with the definition and the plan, and the issue told where both the pull
request and the task document are. The task is then ready for review: the human's
merge is the closing while the path has no QA.

No role runs here; it is the system talking to the human, which ADR-0001 allows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import assert_never

from agentic.shared.configuration import Configuration

from ..domain import document, landing, rules
from ..domain.pull_request import BoundedBody, bounded_body
from ..domain.roles import DEVELOPER_INTEGRATOR, PJM, PM, TECH_LEAD
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import SetupFailed, task_base, task_checkout
from .ports import BranchPullRequest, GitError, Ports
from .preflight import over_cap
from .release import release_task
from .run_role import finish
from .tracker_only import post_tracker_checklist

# How the delivery comment starts: a resume looks past the system's comments.
READY = "Ready for review: "

_LOG = logging.getLogger(__name__)


async def deliver(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    relative = document.relative_path(state["id"]).as_posix()
    recorded = state.get("replaced_remote_tip")
    # The commit point is the pull request: everything up to it is a visible escalation
    # the human resumes (the branch is integrated, so a resume only delivers again).
    # What is read of the remote is read here, once and in this order (GH-204): the tip,
    # then local reads, then the pull requests only when the remote diverged. Nothing
    # remote runs between the last of these and the push, so the plan stays true.
    try:
        step = "remote branch read failed"
        tip = ports.workspace.remote_tip(task.path, task.branch)
        in_head = tip is None or ports.workspace.is_ancestor(task.path, tip, "HEAD")
        in_archive = False
        prs: list[BranchPullRequest] = []
        if tip is not None and not in_head:
            archives = rules.archived_task_branches(
                ports.workspace.branches("archive/"), state["id"]
            )
            in_archive = any(ports.workspace.is_ancestor(task.path, tip, a) for a in archives)
            prs = ports.tracker.pull_requests_on(task.branch)
        plan = rules.push_plan(tip, in_head, prs, in_archive)
        kept = (
            plan.kind == "push"
            and tip is not None
            and recorded is not None
            and (recorded == tip or ports.workspace.is_ancestor(task.path, recorded, tip))
        )
    except (GitError, ValueError) as failed:
        return finish(state, _undelivered(step, failed, state, recorded), ports)
    # Exhaustive on purpose, so a new cell of the table cannot reach the push unhandled.
    match plan.kind:
        case "refuse":
            return _refused(state, plan, prs, task.branch, ports)
        case "push" | "replace":
            pass
        case _:
            assert_never(plan.kind)
    # A tiered product pushes only what a full run covers (GH-358): this is the last point
    # before anything is posted, committed or pushed, so a refusal leaves no trace.
    refused = _uncovered(state, task.path, relative, ports)
    if refused is not None:
        return finish(state, refused, ports)
    # A plan with a tracker checklist tells the human before the pull request exists; the pull
    # request closes the issue all the same (GH-336, ADR-0062, addendum GH-344). None of this
    # runs without one.
    # Bookkeeping never strands a finished branch (GH-342): each guard below keeps the pull
    # request coming and leaves a `step: ExceptionClass` line in `lapses`.
    checklist = state.get("tracker_checklist", [])
    posted: TaskState = {}
    lapses: list[str] = []
    unconfirmed = ""
    if checklist:
        try:
            record_posted = post_tracker_checklist(
                ports, state["id"], checklist, state.get("tracker_checklist_posted")
            )
        except Exception as failed:
            # No record is written, so a resume asks again; the list rides in the body and
            # in the ready comment meanwhile, bounded and without the marker.
            record_posted = None
            _lapsed(lapses, "tracker checklist comment", failed)
            unconfirmed = rules.tracker_failure_block(checklist, type(failed).__name__, relative)
        if record_posted:
            posted = {"tracker_checklist_posted": record_posted}
            state = {**state, **posted}
            try:
                finish(state, posted, ports)
            except Exception as failed:
                # The comment is on the issue and the record travels in the terminal patch.
                _lapsed(lapses, "tracker record save", failed)
    replaced = rules.replace_record(recorded, plan, kept)
    # Only the key's own value moves: a state with no replace keeps being the very object
    # the node received, and is written as it always has been.
    record: TaskState = {"replaced_remote_tip": replaced} if replaced or recorded else {}
    if record:
        state = {**state, **record}
    replaced_note = rules.replaced_note(replaced, prs, task.branch) if replaced else ""
    over = _criteria_over_cap(task.branch, ports)
    if over:
        state = {**state, "criteria_over_cap": over}
    preface = "\n\n".join(
        part
        for part in (
            replaced_note,
            _guarded_preface(state, task.branch, ports),
            _criteria_preface(over),
            _perf_preface(state),
            unconfirmed,
        )
        if part
    )
    # A re-delivery under a landing pass closes its entry with what the pass cost; a sync
    # is closed as resolved only when the head holds the upstream it was dispatched for.
    reopened = state.get("landing")
    if reopened:
        state = {**state, "landing": _closed_landing(reopened, state, task.path, ports)}
    body = _bounded(state, task.branch, relative, ignored=False, preface=preface)
    if body.bounded and ports.workspace.is_ignored(task.path, relative):
        body = _bounded(state, task.branch, relative, ignored=True, preface=preface)
        committing = False
    else:
        committing = body.bounded
    try:
        step = "task document commit failed"
        if committing:
            ports.documents.save(state)
            ports.documents.write_into(state["id"], task.path)
            ports.workspace.commit_paths(task.path, [relative], f"{state['id']}: task document")
        if replaced and not committing:
            # The sha is durable before the destructive step, whether or not the branch
            # carries the document.
            ports.documents.save(state)
        # The head the push carries, read after the last commit and before the push:
        # nothing is committed after it (GH-216), so the CI the push starts is of this sha.
        step = "head read failed"
        pushed_head = ports.workspace.full_head(task.path)
        step = "push failed"
        ports.workspace.push(task.branch, lease=plan.old_sha if plan.kind == "replace" else None)
        pushed_at = datetime.now(UTC).isoformat(timespec="seconds")
        step = "gh pr create failed"
        # A pull request the task already has is kept: the push alone re-delivers it.
        url = state.get("pull_request") or ports.tracker.open_pull_request(
            state["id"], task.branch, state["title"], body.text, closes_issue=True
        )
    except (GitError, ValueError) as failed:
        return finish(
            state, {**_undelivered(step, failed, state, replaced), **record, **posted}, ports
        )
    patch: TaskState = {
        "status": "ready_for_review",
        "pull_request": url,
        "branch": task.branch,
        "current_role": PJM.name,
        "pushed_head": pushed_head,
        "pushed_at": pushed_at,
        **record,
        **posted,
        **({"criteria_over_cap": over} if over else {}),
        **({"landing": state["landing"]} if state.get("landing") else {}),
    }
    # Best effort from here: the pull request exists, so a crash would only make the
    # next attempt hit "already exists".
    try:
        ready = f"{READY}{url}\n\nTask document: `{relative}`"
        if _perf_unverified(state):
            ready += f"\n\n{PERF_NOT_VERIFIED_LINE}"
        if replaced:
            ready += f"\n\n{replaced_note}"
        if unconfirmed:
            ready += f"\n\n{unconfirmed}"
        ports.tracker.comment(state["id"], ready)
    except Exception as failed:
        _lapsed(lapses, "ready comment", failed)
    try:
        reaped = release_task(ports, state["id"])
    except Exception as failed:
        reaped = ()
        _lapsed(lapses, "release task", failed)
    if reaped:
        patch["reaped_at_cleanup"] = list(reaped)
    if lapses:
        patch = _with_lapses(patch, lapses)
    done = finish(state, patch, ports)
    if state.get("landing"):
        ports.documents.record_closure(state["id"], {"landing": state["landing"]})
    return done


def _uncovered(state: TaskState, path: Path, relative: str, ports: Ports) -> TaskState | None:
    """The patch of a refused delivery, or None when the push may go on. Only a product that
    declares `delivery_checks` is asked: a passed `full_checks` record must exist whose head
    differs from HEAD in nothing but this task's own document (`differs_outside`). A missing
    or stale record pushes nothing and heals (`rules.full_checks_heal`); a head that cannot be
    read is a failure the human resumes, never a push."""
    if not ports.stack.delivery_checks(task_base(state, ports)):
        return None
    recorded = state.get("full_checks")
    try:
        head = ports.workspace.full_head(path)
        differs = (
            recorded is not None
            and recorded["passed"]
            and not rules.same_commit(recorded["head"], head)
            and ports.workspace.differs_outside(path, recorded["head"], head, relative)
        )
    except (GitError, ValueError) as failed:
        return _undelivered("full checks cover read failed", failed, state, None)
    verdict = rules.full_checks_cover(recorded, differs)
    if verdict == "ok":
        return None
    return rules.full_checks_heal(state, head, verdict)


def _lapsed(lapses: list[str], step: str, failed: Exception) -> None:
    """Notes a bookkeeping step that failed after the branch was ready: the line for the
    task document and the traceback for the process log. Never the exception text, which
    can carry what the tracker answered."""
    lapses.append(f"{step}: {type(failed).__name__}")
    _LOG.warning("delivery: %s failed; delivering anyway", step, exc_info=failed)


def _with_lapses(patch: TaskState, lapses: list[str]) -> TaskState:
    """The terminal patch with the lapses in the task document's log, once: one `lapse`
    entry per line in `pause_log` (`rules.logged`). They are neither attempts nor
    escalations, so `failures` -- what the measurement counts -- and the poller's reading of
    the epoch are left alone."""
    return rules.logged(patch, lapses=lapses)


def _closed_landing(
    entries: list[landing.LandingEntry], state: TaskState, path: Path, ports: Ports
) -> list[landing.LandingEntry]:
    """The `landing` list with its open last entry closed (GH-267): `resolve_open` as ever,
    unless `landing.sync_verdict` says the delivered head lacks the upstream the sync was
    dispatched for, which `close_unsynced` records instead. The head is delivered either way."""
    at = datetime.now(UTC).isoformat(timespec="seconds")
    cost = state.get("cost_usd", 0.0)
    last = entries[-1]
    upstream = last.get("upstream")
    if landing.entry_closed(last) or not isinstance(upstream, str):
        return landing.resolve_open(entries, at, cost)
    try:
        contains: bool | None = ports.workspace.is_ancestor(path, upstream, "HEAD")
    except (GitError, ValueError):
        contains = None
    if landing.sync_verdict(last, contains) == "unsynced":
        return landing.close_unsynced(entries, at, cost, upstream)
    return landing.resolve_open(entries, at, cost)


def _guarded_preface(state: TaskState, branch: str, ports: Ports) -> str:
    """The `Guarded changes` block that leads the pull request body (GH-239, ADR-0048): the
    system's own record, standards or workflows are in the diff and nobody gates them, so
    the body says so. Empty when the diff holds none -- or cannot be read, which the merge
    reads again from the same diff before it lands anything."""
    try:
        paths = ports.workspace.changed_paths(task_base(state, ports), branch)
    except GitError:
        return ""
    guarded = [path for path in paths if rules.guarded_path(path, state["id"])]
    if not guarded:
        return ""
    listed = "\n".join(f"- `{path}`" for path in guarded)
    return (
        "## Guarded changes\n\nThis pull request changes paths the system guards (its own "
        "record, the standards or the workflows). The landing merges it without a human "
        f"gate (ADR-0048):\n\n{listed}"
    )


def _criteria_over_cap(branch: str, ports: Ports) -> list[str]:
    """The criteria files past the line cap at the head of the branch being delivered
    (GH-295, ADR-0055). A file the delivery deleted reads None there and is not measured;
    a head that cannot be read says nothing, as `_guarded_preface` does."""
    try:
        return over_cap(ports, branch)
    except GitError:
        return []


def _criteria_preface(over: list[str]) -> str:
    """The `Criteria over cap` block, after `Guarded changes`: the cap is a fact for the
    human, never a refusal. Empty when nothing is over it."""
    if not over:
        return ""
    listed = "\n".join(f"- {message}" for message in over)
    return (
        "## Criteria over cap\n\nA criteria document of the product is past the line cap "
        f"(ADR-0055); cut it or split it by area:\n\n{listed}"
    )


PERF_NOT_VERIFIED_LINE = (
    "Performance not verified: QA Performance could not decide every budget; see the pull "
    "request body."
)
_QA_PERFORMANCE_SECTION = "QA Performance"


def _perf_unverified(state: TaskState) -> bool:
    """Whether the plan convened QA Performance and its latest run ended `unverified`: the
    run counts as done and the delivery says so. `rules.perf_merge_facts` is the one reader
    of both facts, so a task that is not convened never matches."""
    convened, verdict, _ = rules.perf_merge_facts(state)
    return convened and verdict == "unverified"


def _first_table(section: str) -> list[str]:
    """The first run of consecutive `|` lines: the system's own measurement table, which
    the node writes first. Any table the role writes after it is not copied."""
    block: list[str] = []
    for line in section.splitlines():
        if line.startswith("|"):
            block.append(line)
        elif block:
            break
    return block


def _perf_preface(state: TaskState) -> str:
    """The `Performance not verified` block (GH-318, ADR-0060), after `Criteria over cap`:
    the measurement table the node wrote into the QA Performance section -- its rows carry
    each scenario's reason, spread and load averages -- under a fixed sentence. Nothing is
    added for a task that is not convened or whose run was not `unverified`; no label."""
    if not _perf_unverified(state):
        return ""
    section = state.get("sections", {}).get(_QA_PERFORMANCE_SECTION, "")
    table = _first_table(section)
    rows = "\n".join(table) if table else "No measurement table was recorded."
    return (
        "### Performance not verified\n\nQA Performance could not decide every budget "
        "(reason, spread and load averages per metric below), so the auto-merge is held and "
        f"the pull request is left to the human:\n\n{rows}"
    )


def _refused(
    state: TaskState,
    plan: rules.PushPlan,
    prs: list[BranchPullRequest],
    branch: str,
    ports: Ports,
) -> TaskState:
    """Nothing is pushed: the reason names the branch, the remote sha and the pull
    requests that decided it."""
    reason = rules.push_refused_reason(plan, prs, branch)
    patch = _undelivered("push refused", reason, state, None)
    if state.get("replaced_remote_tip"):
        # A refusal clears the record (`replace_record`).
        patch["replaced_remote_tip"] = None
    return finish(state, patch, ports)


def _undelivered(
    step: str, failed: Exception | str, state: TaskState, replaced: str | None
) -> TaskState:
    reason = f"{step}: {failed}; the branch is integrated, resume to deliver again"
    if replaced:
        reason += f". {rules.replaced_note(replaced, [], rules.task_branch(state['id']))}"
    return rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation("failure", reason),
        }
    )


def _bounded(
    state: TaskState, branch: str, relative: str, *, ignored: bool, preface: str
) -> BoundedBody:
    def pointer(omitted: tuple[str, ...]) -> str:
        named = ", ".join(omitted)
        if ignored:
            return (
                f"Full sections ({named}): the task document in the product checkout at "
                f"`{relative}`; this repository does not version it."
            )
        return (
            f"Full sections ({named}): the task document as of delivery, `{relative}` on "
            f"branch `{branch}`. Its status and later fields are kept in the product "
            f"checkout's copy."
        )

    return bounded_body(_parts(state), state["title"], pointer, preface=preface)


def _parts(state: TaskState) -> list[tuple[str, str]]:
    """The definition and the plan, as the human reads them before the diff, plus the
    design branch's own observations (ADR-0040) when there are any -- never when there
    are none, so a task this branch never touched renders byte for byte as it always
    has."""
    sections = state.get("sections", {})
    parts = [(name, sections[name]) for name in (PM.name, TECH_LEAD.name) if name in sections]
    observations = [i for i in state.get("design_items", []) if i["kind"] == "observation"]
    if observations:
        parts.append(
            ("Observations on the design", rules.design_observations_markdown(observations))
        )
    return parts
