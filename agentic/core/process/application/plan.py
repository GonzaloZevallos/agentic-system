"""The technical solution (README.md, 4; ADR-0014): the ephemeral Tech Lead
interviews Tech Lead, who then writes the plan from its own session. The ADRs the
interview leaves are committed on the task branch (ADR-0011); the subtasks enter the
state pending, each to get its branch when a Developer starts it.

The plan also leaves the invariants, the rules every subtask honours (issue #42), and
what the subtasks need beyond a merge to work together. The invariants are the one
channel between reviews that works in the first wave: the findings table is empty
while the first Developers build, and these are written before anybody codes. The
ephemeral proposes them from what the grill uncovered; the Tech Lead adopts them or
rejects them with a reason in the plan, which is what makes the ephemeral's grilling
measurable (issue #28)."""

from __future__ import annotations

from pathlib import Path

from agentic.shared.configuration import Configuration, experiment_snapshot

from ..domain import cost_history, plan_view, schemas
from ..domain.roles import (
    INTERVIEW_SOLUTION,
    INTERVIEW_SOLUTION_PLATFORM,
    TECH_LEAD_REVIEWER,
    Interview,
)
from ..domain.task import Subtask, TaskState, apply
from .checkouts import SetupFailed, task_checkout
from .interview import interview
from .ports import Ports
from .reflect_run import closed_documents
from .run_role import Check, Output, finish, run_role

# The ephemeral-arm skip path's own value for the existing `{{proposed_invariants}}`
# placeholder (issue #28): no prompt file is edited -- doing so would move
# `prompts.fingerprint` for both arms and every future task (ADR-0005, ADR-0008) -- so
# what changes is this value, never the text around it.
_SKIPPED_PROPOSAL = (
    "No ephemeral interview ran this time (issue #28, ephemeral-arm): there is nothing "
    "proposed to adopt or reject. Write the invariants yourself, from the definition "
    "and the product knowledge alone."
)


async def plan(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    spec = solution_interview(state)
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(spec.author), ports)
    history = cost_history.build(closed_documents(ports))
    if cfg.skip_ephemeral:
        return await _plan_cold(state, spec, cfg, ports, task.path, history)
    transcript, patch = await interview(state, spec, cfg, ports, cwd=task.path)
    ports.workspace.commit_all(task.path, f"{state['id']}: solution interview")
    if transcript is None:
        return finish(state, patch, ports)
    interviewed = apply(state, patch)
    output, written = await run_role(
        interviewed,
        spec.author,
        cfg,
        ports,
        check=_estimate_check(interviewed, history),
        cwd=task.path,
        resume=transcript.sessions.get(spec.author_side.slug),
        proposed_invariants=_proposed(state, spec, transcript.invariants),
        cost_history=history.text(),
    )
    return finish(state, apply(patch, _delivered(cfg, output, written, spec.author.name)), ports)


def _estimate_check(state: TaskState, history: cost_history.History) -> Check:
    """The plan check both arms share: the structural one plus the estimate's refusals,
    which read the history and what the task has spent so far (ADR-0053)."""
    spent = state.get("cost_usd", 0.0) + state.get("landing_cost_carried", 0.0)
    return schemas.plan_check_for(history, spent, fit_waived=bool(state.get("fit_waived")))


async def _plan_cold(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
) -> TaskState:
    """The ephemeral-arm skip path (issue #28): no interviewer, no interviewee, no
    consultation, no transcript -- the Tech Lead's own cold call is the whole solution
    stage, with no `resume` and the interview's section absent from `state["sections"]`
    (`run_role.context_for` already tolerates a `reads` entry the state lacks; a test at
    that seam proves it, since this brief asks it never be assumed).

    `commit_all` still runs, on the checkout `task_checkout` just prepared: the tree is
    clean, so it returns `None` and no branch is needed, the same call the interview
    path makes right before its own Tech Lead call -- kept here for the same reason,
    not special-cased away."""
    ports.workspace.commit_all(cwd, f"{state['id']}: solution interview")
    output, written = await run_role(
        state,
        spec.author,
        cfg,
        ports,
        check=_estimate_check(state, history),
        cwd=cwd,
        proposed_invariants=_SKIPPED_PROPOSAL,
        cost_history=history.text(),
    )
    return finish(state, _delivered(cfg, output, written, spec.author.name), ports)


def _delivered(
    cfg: Configuration, output: Output | None, written: TaskState, section: str
) -> TaskState:
    """What a successful plan call adds to its own patch, on either path: the subtasks,
    the invariants, the integration brief, the estimate (ADR-0053), the status -- or, for a
    plan that does not fit, the parts instead of the subtasks -- and the
    experiment snapshot
    (issue #28) -- `{}` when both switches are at their default, which
    `document.render` then omits, so the default path's document does not move.

    `section` is the Tech Lead's own section key (`spec.author.name`, the same on both
    paths): once `written["subtasks"]` exists, that section's markdown is repaired in
    place to carry a complete `### Subtasks` block (issue #152, `plan_view.with_
    subtasks_block`), never touching `Tech Lead (vN)` or any other section -- the text
    being written here is never versioned (`rules.sections_for` versions only the
    earlier delivery)."""
    if output is not None:
        written["invariants"] = output.data["invariants"]
        written["integration_brief"] = output.data["integration"].strip()
        written["estimate"] = {
            "cost_usd": output.data["estimated_cost_usd"],
            "files": output.data["estimated_files"],
        }
        written["status"] = "planned"
        reviewer_model = cfg.model_for(TECH_LEAD_REVIEWER.slug, TECH_LEAD_REVIEWER.kind)
        written["experiment"] = experiment_snapshot(cfg, reviewer_model)
        if output.data.get("parts"):
            # A plan that does not fit (ADR-0053) builds nothing: no subtasks, no fan-out.
            # `rules.after_plan` sends the state holding `parts` to `split`.
            written["parts"] = list(output.data["parts"])
            written["sections"][section] = plan_view.with_parts_block(
                written["sections"][section], written["parts"]
            )
            return written
        written["subtasks"] = {s["id"]: _pending(s) for s in output.data["subtasks"]}
        written["sections"][section] = plan_view.with_subtasks_block(
            written["sections"][section], list(written["subtasks"].values())
        )
    return written


def solution_interview(state: TaskState) -> Interview:
    """Which solution interview this task gets. A feature and a bug have a PM behind
    them to consult (issue #65); a platform enhancement has none, so the same two Tech
    Leads run with the human as the only door for a question that is really about the
    product (issue #46)."""
    if state.get("type") == "platform_enhancement":
        return INTERVIEW_SOLUTION_PLATFORM
    return INTERVIEW_SOLUTION


def _proposed(state: TaskState, spec: Interview, invariants: list[str]) -> str:
    """What the ephemeral proposed, for the plan's prompt. The Tech Lead decides on each
    one; the empty case says so plainly instead of leaving a dangling heading.

    A migration window (issue #42): a document written before `proposed_invariants`
    existed has no key for a retake to seed from, so a retake of an old interview
    resumes with `invariants` holding only what this resumption's own rounds proposed
    — the earlier rounds' invariants never left `state`, but they are still in the
    transcript section `state` already carries (`interview_progress`'s seeding, on
    `state` before this call): the note says so, instead of the Tech Lead reading a
    short list as if it were the whole proposal."""
    listed = "\n".join(f"- {line}" for line in invariants) or "- none"
    migrating = "proposed_invariants" not in state and bool(
        state.get("sections", {}).get(spec.section)
    )
    if migrating:
        return (
            "This task predates recording proposed invariants across a retake: earlier "
            f"rounds' invariants are already in the `{spec.section}` section above, not "
            "repeated here. Below is only what this resumption's own rounds proposed.\n\n"
            f"{listed}"
        )
    return listed


def _pending(item: dict) -> Subtask:
    pending: Subtask = {
        "id": item["id"],
        "title": item["title"],
        "brief": item["brief"],
        "seams": item["seams"],
        "depends_on": item["depends_on"],
        "status": "pending",
    }
    # Persisted only when non-empty (ADR-0040): a plan from before `screens` existed,
    # and a subtask with no screen of its own, both read back `.get("screens", [])`.
    if item.get("screens"):
        pending["screens"] = item["screens"]
    return pending
