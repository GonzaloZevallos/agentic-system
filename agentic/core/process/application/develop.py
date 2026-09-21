"""One Developer builds one subtask in its own checkout (README.md, 4; ADR-0011).
The integration is a subtask like the others, built by the integrator on the task
branch once every real subtask is approved.

A delivery is a commit on a clean tree whose product checks pass. The presubmit
checks it when the model hands the delivery back, inside the turn, and refuses it up to
twice with the reason (`presubmit.py`); the checks' results open the Developer's section,
written by the system. A Developer that ends its turn with the branch where it started
has delivered nothing, whatever its section says, and one that leaves changes
uncommitted delivered something else than what it tested (run 4: the suite passed
against a working tree the branch did not hold). Past the presubmit, either is still a role
failure, retried once like any other: the backstop."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import presubmit, rules
from ..domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR
from ..domain.task import INTEGRATION, TaskState
from .checkouts import SetupFailed, subtask_checkout
from .ports import Ports
from .presubmit import CheckoutPresubmit, declared_checks
from .run_role import finish, run_role, with_context


async def develop(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    role = DEVELOPER_INTEGRATOR if subtask_id == INTEGRATION else DEVELOPER
    try:
        checkout = subtask_checkout(state, subtask_id, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(role), ports)
    # A dependency gates the order and provides the code: its approved branch is
    # brought in before the Developer starts. The integration skips this: the fan-in
    # already brought every approved branch into the task branch, and what it could not
    # merge is exactly the integrator's job. A dependency that does not merge clean no
    # longer stops the subtask on the spot (gym #16's own two escalations, an adjacent
    # hunk a human deleted in one turn): the checkout is left conflicted instead of
    # undone (`merge_keeping_conflicts`, never `Workspace.merge`'s own abort), and the
    # Developer that already owns the worktree gets one turn to resolve it before its
    # own brief. Only a merge with nothing for anyone to resolve — an unknown branch, a
    # dirty tree, a git error — still escalates immediately, `rules.unmergeable`'s own
    # message, byte for byte.
    handled_deps: list[tuple[str, str]] = []
    notices: list[str] = []
    conflicted_paths: list[str] = list(subtask.get("conflicted_paths", []))
    if subtask_id != INTEGRATION:
        for dep in subtask.get("depends_on", []):
            branch = state["subtasks"][dep]["branch"]
            dep_state = ports.workspace.dependency_state(checkout.path, branch, conflicted_paths)
            step = rules.dependency_before(dep_state)
            if step == "skip":
                continue
            if step == "attempt":
                outcome = ports.workspace.merge_keeping_conflicts(checkout.path, branch)
                after = rules.dependency_after_attempt(outcome)
                if after == "clean":
                    continue
                if after == "unmergeable":
                    patch = rules.unmergeable(subtask_id, dep, outcome.reason or "")
                    return finish(state, patch, ports)
                paths = list(outcome.conflicted_paths)
            else:  # "hand_over": a merge already in progress, from a resume
                paths = list(dep_state.marked_paths)
            handled_deps.append((dep, branch))
            conflicted_paths = [*conflicted_paths, *(p for p in paths if p not in conflicted_paths)]
            notices.append(rules.merge_notice(dep, branch, paths))
    notice_text = "\n\n".join(notices)
    before = ports.workspace.head(checkout.path)
    checks = declared_checks(state, ports)
    delivery_presubmit = CheckoutPresubmit(
        ports, checkout.path, before, checks, cfg.check_timeout_s
    )

    def committed(_: dict) -> None:
        moved = ports.workspace.head(checkout.path) != before
        if reason := presubmit.refusal(moved, ports.workspace.is_clean(checkout.path), []):
            raise ValueError(reason)

    section = rules.developer_section(subtask_id)
    extra_sections = (section, rules.review_section(subtask_id))
    # A return goes back to the same Developer, over the session that wrote this
    # section, with only what the return added as its new input (README.md, 3; ADR-0004,
    # sixth addendum): the session already holds everything else. `role.name`, not
    # `DEVELOPER.name`: the integrator shares the name but not the slug, and this keys
    # its own session on its own section. No session on record (a fresh subtask, a
    # reseeded document) keeps today's full context, resume and cold prompt both unset.
    #
    # A session can exist with no review ever having run it now (this task's own new
    # shape): a dependency merge escalated `unmergeable` before the Developer's delivery
    # could be reviewed. `returned_developer_delta` reads a review section that is not
    # there, so the gate is the subtask's own status, not merely a session on record.
    resume = rules.last_session(state, role.name, section)
    prompt = cold_prompt = None
    if resume:
        if subtask.get("status") == "returned":
            prompt = rules.returned_developer_delta(state, subtask_id, checkout.branch, before)
        else:
            prompt = rules.resumed_after_unmergeable(state, subtask_id, checkout.branch, before)
        if notice_text:
            prompt = f"{prompt}\n\n{notice_text}"
        cold_prompt = with_context(
            state, role, ports, checkout.path, prompt, extra_sections, subtask_id
        )
    elif notice_text:
        prompt = with_context(
            state, role, ports, checkout.path, notice_text, extra_sections, subtask_id
        )
    output, patch = await run_role(
        state,
        role,
        cfg,
        ports,
        check=committed,
        section=section,
        extra_sections=extra_sections,
        cwd=checkout.path,
        resume=resume,
        prompt=prompt,
        cold_prompt=cold_prompt,
        presubmit=delivery_presubmit,
        subtask=subtask_id,
        invariants=rules.invariants_markdown(state),
        branches=_branches(state),
        checks=presubmit.describe_checks(checks),
    )
    if output is not None:
        record = delivery_presubmit.markdown()
        lead = [rules.cold_fallback_note()] if output.cold_fallback else []
        if record is not None:
            lead.append(record)
        if lead:
            patch["sections"] = {**patch["sections"], section: "\n\n".join([*lead, output.text])}
    if delivery_presubmit.invocations:
        # Measurement only (like `subtask_id`): read once by the adapter for
        # `agentic.presubmit.checks`/`.duration`, never persisted.
        patch["presubmit_checks"] = delivery_presubmit.invocations

    # The system decides the dependency landed, never the model's own word for it
    # (README.md's own rule for this task): a marker scan runs only on a delivery whose
    # run merged a dependency or found one already in progress, over the paths the
    # merge itself reported, checking their actual content — never git's own conflict
    # bookkeeping, which a `git add` past a marker already clears. A verified delivery
    # stands and `conflicted_paths` is cleared; anything else composes `unmergeable`
    # onto the patch a delivering Developer already produced, its `runs` and
    # `cost_usd` intact, with the subtask's status left at what it was and the task
    # never moved to `developing`.
    verified = output is not None
    if verified and handled_deps:
        evidence = []
        for dep, branch in handled_deps:
            after_state = ports.workspace.dependency_state(checkout.path, branch, conflicted_paths)
            if after_state.evidence:
                evidence.append(f"Dependency `{dep}` (`{branch}`):\n\n{after_state.evidence}")
            if not rules.dependency_delivered(after_state):
                verified = False
                reason = rules.dependency_unresolved_reason(after_state)
                patch = {**patch, **rules.unmergeable(subtask_id, dep, reason)}
                break
        else:
            conflicted_paths = []
        record = "\n\n".join(
            [
                "### Dependency merge",
                f"Conflicted paths: {', '.join(conflicted_paths) or '(none left)'}",
                *evidence,
            ]
        )
        patch["sections"] = {
            **patch["sections"],
            section: f"{patch['sections'][section]}\n\n{record}",
        }

    subtask_patch: dict = {
        **subtask,
        "branch": checkout.branch,
        "base": checkout.base,
        "status": "delivered" if verified else subtask.get("status", "pending"),
    }
    if handled_deps or subtask.get("conflicted_paths"):
        subtask_patch["conflicted_paths"] = conflicted_paths
    patch["subtasks"] = {subtask_id: subtask_patch}
    if verified:
        patch["status"] = "developing"
    return finish(state, patch, ports)


def _branches(state: TaskState) -> str:
    """The subtask branches, in dependency order, for the integrator's prompt, with the
    ones the fan-in could not bring into the task branch marked: those are the merges
    left to do by hand, and the rest are already in."""
    subtasks = state.get("subtasks", {})
    conflicts = subtasks.get(INTEGRATION, {}).get("conflicts", [])
    lines = [
        f"- `{sub['branch']}` ({sid}: {sub.get('title', '')})"
        + (" — **conflicts, not merged**" if sub["branch"] in conflicts else " — already merged")
        for sid, sub in subtasks.items()
        if sid != INTEGRATION and sub.get("branch")
    ]
    return "\n".join(lines) or "- none"
