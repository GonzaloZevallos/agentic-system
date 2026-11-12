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

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentic.shared.configuration import Configuration, experiment_snapshot

from ..domain import (
    ci_workflow,
    cost_history,
    docs,
    plan_revision,
    plan_route,
    plan_view,
    rules,
    schemas,
    task_path,
)
from ..domain.roles import (
    INTERVIEW_SOLUTION,
    INTERVIEW_SOLUTION_PLATFORM,
    PLAN_SIZER,
    TECH_LEAD_CRITIC,
    TECH_LEAD_CRITIC_PLATFORM,
    TECH_LEAD_REVIEWER,
    Interview,
)
from ..domain.task import INTEGRATION, PlanRoute, Subtask, TaskState, apply
from . import promotion
from .checkouts import SetupFailed, task_base, task_checkout_in_thread
from .interview import interview
from .ports import GitError, Ports
from .reflect_run import closed_documents
from .run_role import Check, Output, finish, run_role

# How the comment that says a part is built whole without asking starts: a resume looks past
# the system's comments (`resume.SYSTEM_COMMENTS`).
PART_BUILT_WHOLE = "Part built whole: "

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
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(spec.author), ports)
    history = cost_history.build(closed_documents(ports))
    carried: TaskState = {}
    pre_estimate: float | None = None
    files: int | None = None
    if _revising(state):
        return await _replan(state, spec, cfg, ports, task.path, history)
    if plan_route.sizes(state.get("assignment"), cfg.plan_review):
        carried, size = await _size(state, cfg, ports, task.path, history)
        if carried.get("escalation_class") is not None:
            return finish(state, carried, ports)
        pre_estimate = size["estimated_cost_usd"] if size else None
        files = size["estimated_files"] if size else None
        route = plan_route.route(cfg.plan_review, pre_estimate, cfg.plan_interview_min_usd)
        recorded = _route_record(pre_estimate, files, cfg, route)
        if plan_route.takes_critic(route):
            return await _plan_with_critic(
                state, spec, cfg, ports, task.path, history, carried, recorded
            )
        carried = apply(carried, {"plan_route": recorded})
    if cfg.skip_ephemeral:
        return await _plan_cold(state, spec, cfg, ports, task.path, history, carried)
    return await _interview_and_plan(state, spec, cfg, ports, task.path, history, carried)


def _revising(state: TaskState) -> bool:
    """Whether this plan call revises the plan the task already has (GH-591): subtasks exist and
    a promotion re-planned the task. Anything else is a first plan, byte for byte as before."""
    return bool(state.get("subtasks")) and bool(state.get("promotion"))


def _revised_from(state: TaskState) -> dict[str, Subtask] | None:
    return dict(state["subtasks"]) if _revising(state) else None


async def _replan(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
) -> TaskState:
    """The plan stage of a promoted task (GH-591): it revises, it never starts a new
    interview. `plan_route.after_promotion` picks the existing interview's resume (the retake
    gives it two rounds of margin) or the critic once; `plan_route` records `after_promotion`."""
    stage = plan_route.after_promotion(bool(state.get("sections", {}).get(spec.section)))
    prior = state.get("plan_route")
    recorded: PlanRoute = {
        **(prior or _route_record(None, None, cfg, "interview" if stage == "resume" else "critic")),
        "after_promotion": True,
    }
    if stage == "critic":
        return await _plan_with_critic(state, spec, cfg, ports, cwd, history, {}, recorded)
    carried = apply({}, {"plan_route": recorded})
    return await _interview_and_plan(state, spec, cfg, ports, cwd, history, carried)


def _plan_over_cap(landed: TaskState) -> task_path.PromoteRequest | None:
    """The `plan_over_cap` request (GH-591): `rules.plan_over_cap_request` decides when a light
    plan has more subtasks than the cap; it is applied in the same node after any role request."""
    return rules.plan_over_cap_request(landed)


def _finished(state: TaskState, patch: TaskState, ports: Ports) -> TaskState:
    """`finish` after the plan node's system step: a promotion the model asked for, or the cap
    asked for, is applied here through `task_path.promote` (the node is serialized) and never
    left in the document; a refusal's notice goes to the Tech Lead's section."""
    landed = apply(state, patch)
    request = patch.get("plan_promote_request") or _plan_over_cap(landed)
    kept: TaskState = {k: v for k, v in patch.items() if k != "plan_promote_request"}
    if request is None:
        return finish(state, kept, ports)
    moved, notice = task_path.promote(
        landed, request["from_role"], request["kind"], ports.now(), request["text"]
    )
    kept = apply(kept, dict(moved))
    # The system is no section of the document: its notice, if any, goes to the Tech Lead's.
    section = (
        request["from_role"] if request["from_role"] != "system" else promotion.NOTICE_FALLBACK
    )
    kept = apply(kept, promotion.refusal_notice(apply(state, kept), section, notice))
    return finish(state, kept, ports)


async def _interview_and_plan(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
    carried: TaskState,
) -> TaskState:
    """The full solution stage (ADR-0014): the interview, then the Tech Lead from its own
    session. `carried` is what earlier calls of this node already put in the patch (the
    sizer, a cold draft); the roles read `state` with it applied and every cap sees its spend."""
    base = apply(state, carried) if carried else state
    transcript, patch = await interview(base, spec, cfg, ports, cwd=cwd)
    ports.workspace.commit_all(cwd, f"{state['id']}: solution interview")
    patch = _joined(carried, patch)
    if transcript is None:
        return finish(state, patch, ports)
    interviewed = apply(state, patch)
    part_of = _part_of(state)
    output, written = await run_role(
        interviewed,
        spec.author,
        cfg,
        ports,
        check=_estimate_check(cfg, interviewed, history, part_of, ports),
        cwd=cwd,
        resume=transcript.sessions.get(spec.author_side.slug),
        proposed_invariants=_proposed(state, spec, transcript.invariants),
        cost_history=history.text(),
        plan_shape_rule=rules.plan_shape_text(cfg.plan_shape),
        split_part=rules.split_part_text(part_of),
        docs_plan=docs.plan_text(ports.stack.docs(task_base(state, ports))),
        parts_remedy=rules.parts_remedy_text(part_of),
        parts_step=rules.parts_step_text(part_of),
        parts_field=rules.parts_field_text(part_of),
        tracker_with_parts=rules.TRACKER_WITH_PARTS,
    )
    output, floored = _floored(output, interviewed, history)
    delivered = apply(
        patch, _delivered(cfg, output, written, spec.author.name, floored, _revised_from(state))
    )
    delivered = _covered(state, delivered, floored)
    judged, built_whole = _after_delivery(
        state, delivered, part_of, output, history, at=ports.now()
    )
    _tell_built_whole(ports, state["id"], part_of, built_whole)
    return _finished(state, judged, ports)


def _joined(carried: TaskState, patch: TaskState) -> TaskState:
    """`patch` after what the node carried: the reducers fold both, and with nothing carried
    the patch is returned untouched, so the default path builds exactly what it always did."""
    return apply(carried, patch) if carried else patch


def _route_record(
    pre_estimate: float | None,
    files: int | None,
    cfg: Configuration,
    route: plan_route.Route,
    *,
    misrouted: bool = False,
    critic: plan_route.CriticState = "skipped",
) -> PlanRoute:
    return {
        "pre_estimate_usd": pre_estimate,
        "files": files,
        "threshold_usd": cfg.plan_interview_min_usd,
        "route": route,
        "misrouted": misrouted,
        "critic": critic,
    }


def _soft(patch: TaskState) -> tuple[TaskState, bool]:
    """A role's patch when it delivered nothing, as `(what to keep, whether the node stops)`.
    A role that failed twice is an optional step failing: its failed attempts were paid, so
    their cost and records are kept and the escalation is not. A cap, or anything that is not
    a plain failure, stops the node and the patch is the escalation itself."""
    if patch.get("escalation_class") != "failure":
        return patch, True
    kept: TaskState = {"failures": patch["failures"], "cost_usd": patch["cost_usd"]}
    if "prompts" in patch:
        kept["prompts"] = patch["prompts"]
    return kept, False


async def _size(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
) -> tuple[TaskState, dict | None]:
    """The sizer's pre-estimate (GH-472): its patch and its data, `None` when it failed. It
    writes no section, so the run is the only trace it leaves besides its cost. A stop
    (cap) comes back as the escalation itself, with `escalation_class` set."""
    output, patch = await run_role(
        state, PLAN_SIZER, cfg, ports, cwd=cwd, cost_history=history.text()
    )
    if output is None:
        kept, _ = _soft(patch)
        return kept, None
    patch.pop("sections", None)
    return patch, output.data


def _part_of(state: TaskState) -> tuple[str, int] | None:
    """The `(task id, part)` of a task that is a part of a split, else `None`."""
    return rules.split_part_of(state.get("description", ""))


def _estimate_check(
    cfg: Configuration,
    state: TaskState,
    history: cost_history.History,
    part_of: tuple[str, int] | None,
    ports: Ports,
) -> Check:
    """The plan check both arms share: the structural one plus the estimate's refusals,
    which read the history, what the task has spent so far and whether the task is a part
    (ADR-0054). It also holds the names the base's workflows can report, so a `ci_required`
    name no check run carries is refused while the plan is written (GH-485)."""
    base = task_base(state, ports)
    return schemas.plan_check_for(
        history,
        _spent(state),
        fit_waived=bool(state.get("fit_waived")),
        part_of=part_of,
        workflow=ci_workflow.check_run_names(_workflow_texts(ports, base)),
        stack_ci=_stack_ci(ports, base),
        plan_shape=cfg.plan_shape,
        # The integration unit is the system's, not the model's: a plan never lists it.
        existing_ids=(
            tuple(i for i in state["subtasks"] if i != INTEGRATION) if _revising(state) else ()
        ),
        path=task_path.path_of(state),
        path_label=task_path.path_label_of(state),
    )


def _spent(state: TaskState) -> float:
    """What the task has spent before the call that `state` is read for: the one figure the
    plan check and the raise of the estimate share (GH-529)."""
    return state.get("cost_usd", 0.0) + state.get("landing_cost_carried", 0.0)


@dataclass(frozen=True)
class _Floored:
    """An estimate the system raised, with the line the plan's section carries about it."""

    raised: cost_history.Raise
    line: str


def _floored(
    output: Output | None, state: TaskState, history: cost_history.History
) -> tuple[Output | None, _Floored | None]:
    """The delivered output with its estimate raised, when the Tech Lead's figure is below a
    floor. `state` is the one the plan check was built from, so the spent figure and the
    subtask count are the ones the check judged fit with, and `cost_history.raised_estimate`
    is the only place that computes the figure (GH-529). Everything downstream reads the
    returned output: the regate, the fit for parts and the estimate write."""
    if output is None:
        return None, None
    data = output.data
    spent = _spent(state)
    size = schemas.floor_size(data)
    raised = cost_history.raised_estimate(data["estimated_cost_usd"], spent, history, size)
    if raised is None:
        return output, None
    median = None if size is None else cost_history.bucket_median(history, size)
    line = plan_view.raised_estimate_line(
        raised.declared_usd, raised.cost_usd, raised.cause, spent, median
    )
    floored = dataclasses.replace(output, data={**data, "estimated_cost_usd": raised.cost_usd})
    return floored, _Floored(raised, line)


def _covered(state: TaskState, patch: TaskState, floored: _Floored | None) -> TaskState:
    """A raised estimate never stays below what the task has spent once the plan node's own
    calls are paid: its stored figure is lifted to the cost after the node. A plan the
    system did not raise is left as the Tech Lead wrote it."""
    if floored is None:
        return patch
    after = state.get("cost_usd", 0.0) + patch.get("cost_usd", 0.0)
    after += state.get("landing_cost_carried", 0.0)
    estimate = patch["estimate"]
    return {**patch, "estimate": {**estimate, "cost_usd": max(estimate["cost_usd"], after)}}


def _workflow_texts(ports: Ports, base: str) -> dict[str, str]:
    """The workflows of the task base, path to text: read-only, and empty when the base has
    none or git cannot say -- the plan check then proves nothing and refuses nothing."""
    try:
        names = ports.workspace.tree_names(base, ci_workflow.WORKFLOWS_DIR)
        texts = {
            path: ports.workspace.show(base, path)
            for path in (f"{ci_workflow.WORKFLOWS_DIR}/{n}" for n in names)
            if path.endswith((".yml", ".yaml"))
        }
    except (GitError, OSError):
        return {}
    return {path: text for path, text in texts.items() if text is not None}


def _stack_ci(ports: Ports, base: str) -> tuple[str, ...]:
    """The stack's `ci:` names at the base; none when the stack cannot be read."""
    try:
        return ports.stack.ci(base)
    except ValueError:
        return ()


def _after_delivery(
    state: TaskState,
    patch: TaskState,
    part_of: tuple[str, int] | None,
    output: Output | None,
    history: cost_history.History,
    *,
    at: str,
) -> tuple[TaskState, schemas.FitDecision | None]:
    """The one place a part's plan is judged over the line (ADR-0054), on both arms. Pure:
    it returns the patch and, when the system builds the part whole by itself, the decision
    that says so (the patch is then unchanged: no escalation, no `fit_question`, no
    `fit_waived`; only a human's waiver is state, this one is evaluated again on every
    delivered plan). A part that delivered subtasks the fit does not accept, with no waiver
    and over what `rules.part_builds_whole` allows, keeps its plan and pauses once to ask
    the human whether to build it whole. Anything else is left as it is;
    `schemas.plan_fits` decides, so the text quotes the figures the check used."""
    if output is None or part_of is None or state.get("fit_waived") or not patch.get("subtasks"):
        return patch, None
    fit = schemas.plan_fits(output.data, history)
    if fit.fits:
        return patch, None
    if rules.part_builds_whole(fit):
        return patch, fit
    return apply(patch, rules.part_over_line(state["id"], part_of, fit, at=at)), None


def _tell_built_whole(
    ports: Ports,
    task_id: str,
    part_of: tuple[str, int] | None,
    fit: schemas.FitDecision | None,
) -> None:
    """Tells the issue, once, that a part is built whole without asking (the receptor
    pattern of `tracker_only`): the comments are read first and the line is posted only if
    none carries its content marker, so a retry or a resumed run does not repeat it. A
    tracker that fails does not stop the part: it goes on and the line is missing."""
    if part_of is None or fit is None:
        return
    try:
        marker = rules.part_whole_marker(task_id, fit)
        if any(marker in body for body in ports.tracker.issue_comments(task_id)):
            return
        ports.tracker.comment(
            task_id, PART_BUILT_WHOLE + rules.part_whole_line(task_id, part_of, fit)
        )
    except (ValueError, OSError):
        return


async def _plan_cold(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
    carried: TaskState | None = None,
) -> TaskState:
    """The ephemeral-arm skip path (issue #28): no interviewer, no interviewee, no
    consultation, no transcript -- the Tech Lead's own cold call is the whole solution
    stage, with no `resume` and the interview's section absent from `state["sections"]`
    (`run_role.context_for` already tolerates a `reads` entry the state lacks; a test at
    that seam proves it, since this brief asks it never be assumed).

    `commit_all` still runs, on the checkout `task_checkout` just prepared: the tree is
    clean, so it returns `None` and no branch is needed, the same call the interview
    path makes right before its own Tech Lead call -- kept here for the same reason,
    not special-cased away. `carried` is what the node already put in its patch (the sizer)."""
    ports.workspace.commit_all(cwd, f"{state['id']}: solution interview")
    part_of = _part_of(state)
    base = apply(state, carried) if carried else state
    output, written = await _cold_draft(base, spec, cfg, ports, cwd, history, _SKIPPED_PROPOSAL)
    output, floored = _floored(output, base, history)
    delivered = _joined(
        carried or {},
        _delivered(cfg, output, written, spec.author.name, floored, _revised_from(state)),
    )
    delivered = _covered(state, delivered, floored)
    judged, built_whole = _after_delivery(
        state, delivered, part_of, output, history, at=ports.now()
    )
    _tell_built_whole(ports, state["id"], part_of, built_whole)
    return _finished(state, judged, ports)


async def _cold_draft(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
    proposed_invariants: str,
    *,
    resume: str | None = None,
    extra_input: str = "",
) -> tuple[Output | None, TaskState]:
    """One Tech Lead call over `state` with no interview behind it: the whole solution stage
    of the skip arm, the draft of the critic arm and, with `resume` and `extra_input`, its one
    revision. It delivers nothing and saves nothing: `_delivered` and `finish` are the
    caller's, once."""
    part_of = _part_of(state)
    return await run_role(
        state,
        spec.author,
        cfg,
        ports,
        check=_estimate_check(cfg, state, history, part_of, ports),
        cwd=cwd,
        resume=resume,
        extra_input=extra_input,
        proposed_invariants=proposed_invariants,
        cost_history=history.text(),
        plan_shape_rule=rules.plan_shape_text(cfg.plan_shape),
        split_part=rules.split_part_text(part_of),
        docs_plan=docs.plan_text(ports.stack.docs(task_base(state, ports))),
        parts_remedy=rules.parts_remedy_text(part_of),
        parts_step=rules.parts_step_text(part_of),
        parts_field=rules.parts_field_text(part_of),
        tracker_with_parts=rules.TRACKER_WITH_PARTS,
    )


async def _plan_with_critic(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    history: cost_history.History,
    carried: TaskState,
    recorded: PlanRoute,
) -> TaskState:
    """The critic arm (GH-472, ADR-0086): the Tech Lead plans cold, one critic reviews the
    draft in a fresh session, the Tech Lead revises once from its own session. A draft whose
    estimate reaches the threshold was misrouted and gets the full interview instead.

    Every call adds its run and its cost to the one patch the node returns, and the caps of
    each see the spend before it. Exactly one delivery and one `finish`, over the final plan."""
    ports.workspace.commit_all(cwd, f"{state['id']}: solution interview")
    part_of = _part_of(state)
    name = spec.author.name
    # Recorded from the start, so a node that escalates before it delivers still says its route.
    carried = apply(carried, {"plan_route": recorded})
    drafting = apply(state, carried)
    draft, draft_patch = await _cold_draft(
        drafting, spec, cfg, ports, cwd, history, _SKIPPED_PROPOSAL
    )
    carried = apply(carried, draft_patch)
    if draft is None:
        return finish(state, carried, ports)
    draft, draft_floored = _floored(draft, drafting, history)
    drafted = apply(state, carried)
    regated = plan_route.regate(draft.data["estimated_cost_usd"], cfg.plan_interview_min_usd)
    if not recorded.get("after_promotion") and not plan_route.takes_critic(regated):
        carried = apply(carried, {"plan_route": {**recorded, "route": regated, "misrouted": True}})
        return await _interview_and_plan(state, spec, cfg, ports, cwd, history, carried)
    draft_text = drafted["sections"][name]
    if spec is INTERVIEW_SOLUTION_PLATFORM:
        critique, critic_patch = await run_role(
            drafted, TECH_LEAD_CRITIC_PLATFORM, cfg, ports, cwd=cwd, plan_draft=draft_text
        )
    else:
        critique, critic_patch = await run_role(
            drafted, TECH_LEAD_CRITIC, cfg, ports, cwd=cwd, plan_draft=draft_text
        )
    critic_state: plan_route.CriticState = "ran"
    final, floored = draft, draft_floored
    if critique is None:
        critic_patch, stopped = _soft(critic_patch)
        carried = apply(carried, critic_patch)
        if stopped:
            return finish(state, carried, ports)
        written, critic_state = {"sections": {name: drafted["sections"][name]}}, "failed"
    else:
        carried = apply(carried, critic_patch)
        revising = apply(state, carried)
        revision, revised = await _cold_draft(
            revising,
            spec,
            cfg,
            ports,
            cwd,
            history,
            _critic_proposed(critique.data["invariants_proposed"]),
            resume=draft.session_id or None,
            extra_input=_critique_input(critique.data["findings"]),
        )
        if revision is not None:
            final, floored = _floored(revision, revising, history)
            written = revised
        else:
            # The draft stands; the failed revision's attempts were paid and are kept.
            revised, stopped = _soft(revised)
            carried = apply(carried, revised)
            if stopped:
                return finish(state, carried, ports)
            written = {"sections": {name: drafted["sections"][name]}}
    delivered = _delivered(cfg, final, written, name, floored, _revised_from(state))
    route_state: TaskState = {"plan_route": {**recorded, "critic": critic_state}}
    judged, built_whole = _after_delivery(
        state,
        _covered(state, apply(apply(carried, delivered), route_state), floored),
        part_of,
        final,
        history,
        at=ports.now(),
    )
    _tell_built_whole(ports, state["id"], part_of, built_whole)
    return _finished(state, judged, ports)


def _critique_input(findings: list[dict]) -> str:
    """The critic's findings as the Tech Lead reads them on its one revision."""
    lines = [
        "## Plan critic: findings",
        "",
        "A critic reviewed your draft in a fresh context. Revise the plan once: fix what is "
        "blocking, take what is important unless you can say why not, and leave an "
        "observation if it does not change the plan.",
        "",
    ]
    for number, finding in enumerate(findings, 1):
        lines.append(f"{number}. [{finding['severity']}] {finding['place']}: {finding['claim']}")
        if finding.get("evidence"):
            lines.append(f"   Evidence: {finding['evidence']}")
    if not findings:
        lines.append("The critic found nothing it could back; deliver the plan as it stands.")
    return "\n".join(lines)


def _critic_proposed(invariants: list[str]) -> str:
    """What the critic proposed, for the revision's `{{proposed_invariants}}`: the Tech Lead
    adopts or rejects each one with a reason, as with the ephemeral's."""
    listed = "\n".join(f"- {line}" for line in invariants) or "- none"
    return (
        "The plan critic proposed these invariants after reading your draft. Adopt or reject "
        f"each one, with a reason, in the plan:\n\n{listed}"
    )


def _delivered(
    cfg: Configuration,
    output: Output | None,
    written: TaskState,
    section: str,
    floored: _Floored | None = None,
    revising: Mapping[str, Subtask] | None = None,
) -> TaskState:
    """What a successful plan call adds to its own patch, on either path: the subtasks,
    the invariants, the integration brief, the estimate (ADR-0054), the status -- or, for a
    plan that does not fit, the parts instead of the subtasks -- and the
    experiment snapshot
    (issue #28) -- `{}` when both switches are at their default, which
    `document.render` then omits, so the default path's document does not move.

    `section` is the Tech Lead's own section key (`spec.author.name`, the same on both
    paths): once `written["subtasks"]` exists, that section's markdown is repaired in
    place to carry a complete `### Subtasks` block (issue #152, `plan_view.with_
    subtasks_block`), never touching `Tech Lead (vN)` or any other section -- the text
    being written here is never versioned (`rules.sections_for` versions only the
    earlier delivery).

    `floored` is set when the system raised the estimate (GH-529): the Tech Lead's own figure
    is kept as `declared_usd` and its section says so; it is `None`, and the document
    unchanged, for a plan that cleared both floors."""
    if output is not None:
        written["invariants"] = output.data["invariants"]
        written["integration_brief"] = output.data["integration"].strip()
        if output.data.get("performance"):
            written["performance"] = output.data["performance"]
        if output.data.get("ci_required"):
            # The checks the issue's done criteria need green (GH-371); absent otherwise,
            # so the document of an old task, or of one with no such check, is unchanged.
            written["ci_required"] = list(output.data["ci_required"])
        # Always written, an empty list included: a re-plan replaces the list it found
        # (GH-538). `document.render` omits an empty one.
        written["required_paths"] = list(output.data.get("required_paths") or [])
        written["estimate"] = {
            "cost_usd": output.data["estimated_cost_usd"],
            "files": output.data["estimated_files"],
        }
        if floored is not None:
            written["estimate"]["declared_usd"] = floored.raised.declared_usd
            written["sections"][section] = (
                f"{written['sections'][section].rstrip()}\n\n{floored.line}"
            )
        written["status"] = "planned"
        # A promotion the Tech Lead asks for (GH-591): stored, never applied here; the plan
        # node's serialized system step applies it after the model ran.
        written.update(promotion.plan_requested(written, section, output.data.get("promote")))
        if output.data.get("tracker_checklist"):
            # What the human must do on the tracker (GH-336); it may sit next to subtasks.
            written["tracker_checklist"] = list(output.data["tracker_checklist"])
            written["sections"][section] = plan_view.with_tracker_block(
                written["sections"][section], written["tracker_checklist"]
            )
        reviewer_model = cfg.model_for(TECH_LEAD_REVIEWER.slug, TECH_LEAD_REVIEWER.kind)
        written["experiment"] = experiment_snapshot(cfg, reviewer_model)
        if output.data.get("parts"):
            # A plan that does not fit (ADR-0054) builds nothing: no subtasks, no fan-out.
            # `rules.after_plan` sends the state holding `parts` to `split`.
            written["parts"] = list(output.data["parts"])
            written["sections"][section] = plan_view.with_parts_block(
                written["sections"][section], written["parts"]
            )
            return written
        if not output.data["subtasks"]:
            # Tracker work only (GH-336): the plan check let it through with no subtasks.
            return written
        if revising is None:
            written["subtasks"] = {
                s["id"]: plan_revision.pending(s) for s in output.data["subtasks"]
            }
            listed = list(written["subtasks"].values())
        else:
            # A promoted task revises the plan it has (GH-591): only the changed and the new
            # subtasks are written, an unchanged one keeps its state and `reviewed_head`.
            written["subtasks"] = plan_revision.revise(
                revising,
                output.data["subtasks"],
                approved_work=any(v.get("status") == "approved" for v in revising.values()),
            )
            listed = list({**revising, **written["subtasks"]}.values())
        written["sections"][section] = plan_view.with_subtasks_block(
            written["sections"][section], listed
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
