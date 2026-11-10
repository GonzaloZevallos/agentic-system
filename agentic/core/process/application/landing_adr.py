"""`apply_adr_decision` (GH-544, addendum to ADR-0049): the landing step that applies an owner's
decision on an `escalate_adr` question. The step is chosen by `landing_step`, released by
`landing_plan.release`, and recorded by `landing.land` (with its `decision_at`) before it is
dispatched here.

The rule chooses every number: the collision is recomputed against the round's master sha
(`adr_decided`, `adr_numbers.renumber_decided`), the mechanical renaming is written and
committed, and a Developer pass edits only the `spots` -- the own lines the grammar cannot
decide -- told the owner's `### Decision`, never a number to pick. The system checks the
bounds of that edit and that no collision is left, then pushes with a lease on the head it
read. Every outcome but `applied` leaves the checkout at that head and asks again through
`landing_fix.escalate_adr`; `LANDING_PASSES_MAX` is never spent here."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from ..domain import landing, rules
from ..domain import landing as landing_rules
from ..domain.adr_numbers import DecidedRenaming, kept_spots, outside_bounds
from ..domain.check_schedule import CheckPriority
from ..domain.presubmit import RedCheck, describe_checks, red_checks
from ..domain.roles import DEVELOPER
from ..domain.task import AdrDecisionOutcome, TaskState, apply
from .adr_collision import adr_collision, adr_decided
from .checkouts import SetupFailed, task_base, task_checkout
from .landing_fix import _accounting, escalate_adr
from .landing_plan import LandingContext, adr_decision_at, all_entries
from .landing_sync import _carries, _message, _owned, merges_cleared, record_landing
from .ports import GitError, Workspace
from .presubmit import CheckoutPresubmit, readiness_of, reuse_of
from .resume import state_from
from .run_role import run_role

Spot = tuple[str, int, str]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _document(context: LandingContext) -> dict[str, object]:
    return dict(context.ports.documents.load(context.task_id) or {})


def decision_text(raw: Mapping[str, object]) -> str:
    """The owner's last `### Decision` of the `Human` section, the whole section when it has no
    such heading."""
    sections = raw.get("sections")
    human = sections.get("Human", "") if isinstance(sections, Mapping) else ""
    text = human if isinstance(human, str) else ""
    marker = "### Decision"
    return text[text.rindex(marker) :].strip() if marker in text else text.strip()


def brief(
    decision: str,
    moves: Sequence[tuple[str, str]],
    spots: Sequence[Spot],
    bound: Sequence[Spot] = (),
) -> str:
    """What the Developer is told: the owner's decision, the renaming already applied, the
    exact lines it may change, and the lines the system already rewrote, which it may move
    inside their file (to keep an index sorted) but not change; never a number to choose."""
    applied = "\n".join(f"- {old} -> {new}" for old, new in moves)
    lines = "\n".join(f"- {path}:{line}: {text}" for path, line, text in spots)
    fixed = "\n".join(f"- {path}:{line}: {text}" for path, line, text in bound)
    return (
        "## Landing: apply the owner's decision on an ADR collision\n\n"
        "The numbering rule already renamed the ADRs below and rewrote every reference it can "
        f"decide; that commit is in your checkout.\n\nMoves already applied:\n{applied}\n\n"
        f"The owner's decision:\n\n{decision}\n\n"
        "You may change ONLY these lines (path:line: text), one for one: no line added or "
        "removed. Never choose an ADR number: the new numbers are the moves "
        f"above. Commit your edit.\n\nLines you may change:\n{lines}\n\n"
        "Every line listed above that the decision leaves unchanged must be named in a line of "
        "a commit message of the form `keep <path>:<line>`, with the path and the line number "
        "exactly as listed. If nothing needs editing, make one empty commit "
        "(`git commit --allow-empty`) whose message has those `keep` lines. A listed line that "
        "is neither edited nor named by a `keep` line fails your delivery.\n\n"
        "The system already rewrote the lines below, among them the ones where an `ADR-NNNN` "
        "or an `ADRs` list was renumbered. You may move them inside their file (for "
        "example, to keep an index sorted) but you must not change their text, and no other "
        f"line may change in any file. Keep line endings and the final newline of every file "
        f"as they are.\n\nLines already rewritten:\n{fixed or '- (none)'}\n"
    )


def _released_entry(raw: Mapping[str, object], head: str) -> Mapping[str, object] | None:
    """The `escalate_adr` entry of `head` the owner's decision released."""
    decided = adr_decision_at(all_entries(raw), head)
    for entry in reversed(all_entries(raw)):
        if entry.get("step") == "escalate_adr" and entry.get("released_at") == decided:
            return landing.entry_of(entry)
    return None


def _recorded_moves(raw: Mapping[str, object], head: str) -> list[list[str]] | None:
    """The `moves` the released `escalate_adr` entry of `head` recorded, `None` for one written
    before GH-544 (read with `.get`, never indexed)."""
    entry = _released_entry(raw, head)
    return entry.get("moves") if entry is not None else None


def _recorded_upstream(raw: Mapping[str, object], head: str) -> str | None:
    """The upstream sha the released `escalate_adr` entry of `head` asked about (GH-579), `None`
    for one written before: such an entry is applied as it always was."""
    entry = _released_entry(raw, head)
    upstream = entry.get("upstream") if entry is not None else None
    return upstream if isinstance(upstream, str) and upstream else None


def _unit(state: TaskState) -> str | None:
    unit = rules.reviewed_unit(state)
    return unit["id"] if unit is not None and "id" in unit else None


RedDetail = tuple[list[RedCheck], int]
"""Which checks were red and how many more were left out (`presubmit.red_checks`)."""


@dataclasses.dataclass(frozen=True)
class CheckedRed:
    """The Developer pass ended `checks_red`; `detail` says which checks were red."""

    detail: RedDetail


@dataclasses.dataclass(frozen=True)
class OutOfBounds:
    """The Developer pass changed lines `outside_bounds` refuses; `pairs` are their
    `(path, line)`, as it returns them."""

    pairs: tuple[tuple[str, int], ...]


Result = tuple[AdrDecisionOutcome, str | None, bool, RedDetail | None, OutOfBounds | None]
"""The outcome, the head pushed (`applied` only), whether the verdict is carried to it, for
`checks_red` only which checks were red, and for `out_of_bounds` only which lines."""


def apply_adr_decision(context: LandingContext) -> None:
    """Carry out the recorded `apply_adr_decision` of `context.head` (see the module docstring)."""
    raw = _document(context)
    state = state_from(raw)
    sha = context.upstream.sha
    unit = _unit(state)
    if unit is None or sha is None:
        context.log(f"agentic: land: {context.task_id}: apply_adr_decision: nothing to apply")
        return
    outcome, head_after, carried, red, out = _apply(context, state, unit, sha)
    _finish(context, state, unit, outcome, head_after, carried, red, out)


def _apply(context: LandingContext, state: TaskState, unit: str, sha: str) -> Result:
    ports, head = context.ports, context.head
    workspace = ports.workspace
    asked_upstream = _recorded_upstream(_document(context), head)
    if asked_upstream is not None and asked_upstream != sha:  # master moved since the question
        return "stale_moves", None, False, None, None
    try:
        found = adr_decided(ports, sha, head)
    except GitError:
        return "stale_moves", None, False, None, None
    recorded = _recorded_moves(_document(context), head)
    if not isinstance(found, DecidedRenaming) or not found.spots:
        return "stale_moves", None, False, None, None
    moves = found.renaming.moves
    if recorded is not None and recorded != [list(pair) for pair in moves]:
        return "stale_moves", None, False, None, None
    try:
        checkout = task_checkout(state, context.cfg, ports)
    except SetupFailed:
        return "role_failed", None, False, None, None
    pushed = False
    try:
        with merges_cleared(workspace, checkout.path, _owned(context, state)):
            try:
                result = _write_and_ask(context, state, unit, checkout.path, found, sha)
                if isinstance(result, CheckedRed):
                    return "checks_red", None, False, result.detail, None
                if isinstance(result, OutOfBounds):
                    return "out_of_bounds", None, False, None, result
                if isinstance(result, str):
                    return result, None, False, None, None
                new_head, carried = result
                workspace.push(checkout.branch, lease=head)
                pushed = True
                return "applied", new_head, carried, None, None
            except GitError:
                return "stale_moves", None, False, None, None
    finally:
        if not pushed:
            try:
                workspace.reset_hard(checkout.path, head)
            except GitError as failed:
                context.log(f"agentic: land: {context.task_id}: apply_adr_decision: {failed}")


def _write_and_ask(
    context: LandingContext,
    state: TaskState,
    unit: str,
    path: Path,
    found: DecidedRenaming,
    sha: str,
) -> tuple[str, bool] | AdrDecisionOutcome | CheckedRed | OutOfBounds:
    """The mechanical commit, the Developer pass and the checks on what it left: the new head
    and whether the verdict carries to it, or the closed outcome that stops the step
    (`CheckedRed` for `checks_red`, with which checks were red, `OutOfBounds` for
    `out_of_bounds`, with which lines)."""
    ports, head = context.ports, context.head
    workspace = ports.workspace
    renaming = found.renaming
    for old, _ in renaming.moves:
        (path / old).unlink(missing_ok=True)
    for relative, text in renaming.texts.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    relatives = sorted({old for old, _ in renaming.moves} | set(renaming.texts))
    if workspace.commit_paths(path, relatives, _message(renaming.moves)) is None:
        return "stale_moves"
    mechanical = workspace.full_head(path)
    outcome = _developer(context, state, path, mechanical, found)
    if outcome is not None:
        return outcome
    new_head = workspace.full_head(path)
    if new_head == mechanical:
        return "role_failed"
    changed = workspace.changed_paths(mechanical, new_head)
    before = {relative: workspace.show(mechanical, relative) or "" for relative in changed}
    after = {relative: workspace.show(new_head, relative) or "" for relative in changed}
    if bad := outside_bounds(before, after, found):
        return OutOfBounds(bad)
    if _unanswered_spots(workspace, mechanical, new_head, found.spots):
        return "role_failed"
    if adr_collision(ports, sha, new_head) is not None:
        return "still_colliding"
    source = {new: old for old, new in renaming.moves}
    combined = [
        change
        for relative in sorted(set(renaming.texts) | set(changed))
        for change in landing.line_changes(
            relative,
            workspace.show(head, source.get(relative, relative)) or "",
            workspace.show(new_head, relative) or "",
        )
    ]
    reviewed = state["subtasks"][unit].get("reviewed_head")
    carried = landing.renumber_carries(combined) and rules.same_commit(reviewed, head)
    return new_head, carried


def _unanswered_spots(
    workspace: Workspace, mechanical: str, new_head: str, spots: Sequence[Spot]
) -> list[Spot]:
    """The spots a pass neither edited nor named in a `keep <path>:<line>` line of its commit
    messages (GH-579; the line numbers are the mechanical commit's). Judged by content: in one
    file, the lines of one text that are not kept must be fewer at `new_head` than at
    `mechanical`, as many as the spots left to answer. Empty means every spot is answered."""
    kept = kept_spots(workspace.commit_messages(mechanical, new_head), spots)
    unkept: dict[tuple[str, str], list[Spot]] = {}
    for spot in spots:
        if (spot[0], spot[1]) not in kept:
            unkept.setdefault((spot[0], spot[2]), []).append(spot)
    left: list[Spot] = []
    for (path, text), group in sorted(unkept.items()):
        count_before = (workspace.show(mechanical, path) or "").splitlines().count(text)
        count_after = (workspace.show(new_head, path) or "").splitlines().count(text)
        if count_before - count_after < len(group):
            left.extend(group)
    return left


def _developer(
    context: LandingContext, state: TaskState, path: Path, mechanical: str, found: DecidedRenaming
) -> AdrDecisionOutcome | CheckedRed | None:
    """The Developer pass over the spot lines, through the delivery gate every Developer
    delivers through. `None` when it delivered with green checks; `CheckedRed` when the last
    gate run left a red row (which checks, from `presubmit.red_checks`)."""
    ports, cfg = context.ports, context.cfg
    base = task_base(state, ports)
    checks = ports.stack.checks(base)
    gate = CheckoutPresubmit(
        ports,
        path,
        mechanical,
        checks,
        cfg.check_timeout_s,
        priority=CheckPriority.SUBTASK,
        wait_max_s=cfg.check_wait_max_s,
        witness=ports.stack.witness(base),
        parallel=ports.stack.parallel_checks(base),
        reuse=reuse_of(ports, base),
        rerun_forms=ports.stack.rerun_checks(base),
        readiness=readiness_of(ports, base, cfg.check_cores),
    )

    def committed(_: dict) -> None:
        moved = ports.workspace.head(path) != mechanical
        if reason := presubmit_refusal(moved, ports.workspace.is_clean(path), gate):
            raise ValueError(reason)

    text = brief(decision_text(_document(context)), found.renaming.moves, found.spots, found.bound)
    span = context.role_span(DEVELOPER) if context.role_span else nullcontext(None)
    with span as run:
        output, patch = asyncio.run(
            run_role(
                state,
                DEVELOPER,
                cfg,
                ports,
                check=committed,
                section="Developer",
                cwd=path,
                presubmit=gate,
                extra_input=text,
                subtask="the ADR decision of the landing",
                invariants="(none: apply the owner's decision to the lines listed below)",
                checks=describe_checks(checks),
            )
        )
        if run is not None:
            run.finish({**patch, "landing": state.get("landing", [])})
    # Paid whatever came of it: the cost and the run, never a section or a failure.
    ports.documents.save(apply(state_from(_document(context)), state_from(_accounting(patch))))
    if output is None:
        return "role_failed"
    if any(not row["passed"] for row in gate.results or []):
        return CheckedRed(red_checks(gate.results or []))
    return None


def presubmit_refusal(moved: bool, clean: bool, gate: CheckoutPresubmit) -> str | None:
    """Why a delivery is not one yet: nothing committed, or a dirty tree."""
    if not moved:
        return "Nothing was committed: edit the listed lines and commit them."
    if not clean:
        return "The checkout has uncommitted changes: commit them."
    return None


def _finish(
    context: LandingContext,
    state: TaskState,
    unit: str,
    outcome: AdrDecisionOutcome,
    head_after: str | None,
    carried: bool,
    red: RedDetail | None = None,
    out: OutOfBounds | None = None,
) -> None:
    """Resolves the entry with its closed `outcome`; `applied` carries the verdict as the
    domain said and logs it, any other outcome asks the owner again. `red` (`checks_red`
    only) is written next to the outcome: `red_checks` when not empty, `red_more` when
    above 0 (GH-570). Every apply entry records `bounds`, the `ADR_EDIT_BOUNDS` it ran under;
    `out` (`out_of_bounds` only) is written as `out_of_bounds_lines` when not empty and
    `out_of_bounds_more` when above 0 (GH-576), cleaned by `landing.out_of_bounds_detail`."""
    raw = _document(context)
    entries = [dict(entry) for entry in all_entries(raw)]
    mine = [
        entry
        for entry in entries
        if entry.get("step") == "apply_adr_decision"
        and entry.get("head") == context.head
        and not entry.get("resolved_at")
    ]
    if mine:
        mine[-1]["outcome"] = outcome
        mine[-1]["bounds"] = landing.ADR_EDIT_BOUNDS
        if out is not None:
            lines, left = landing.out_of_bounds_detail(out.pairs)
            if lines:
                mine[-1]["out_of_bounds_lines"] = lines
            if left > 0:
                mine[-1]["out_of_bounds_more"] = left
        if red is not None:
            shown, more = red
            if shown:
                mine[-1]["red_checks"] = [dict(check) for check in shown]
            if more > 0:
                mine[-1]["red_more"] = more
        if head_after is not None:
            mine[-1]["head_after"] = head_after
    cost = raw.get("cost_usd", 0.0)
    resolved = landing.resolve_open(
        entries, (context.clock or _now)(), cost if isinstance(cost, int | float) else 0.0
    )
    current = state_from(raw)
    if outcome == "applied" and head_after is not None:
        reviewed = current["subtasks"][unit].get("reviewed_head")
        subtasks = _carries(current, unit, reviewed, head_after, holds=carried)
        context.ports.documents.save({**current, "subtasks": subtasks, "landing": resolved})
        record_landing(context, resolved)
        context.log(
            f"agentic: land: {context.task_id}: apply_adr_decision applied, pushed {head_after}"
            + ("" if carried else " (review not carried)")
        )
        return
    context.ports.documents.save({**current, "landing": resolved})
    record_landing(context, resolved)
    context.log(f"agentic: land: {context.task_id}: apply_adr_decision {outcome}, asking again")
    if context.facts.adr_collision is None:
        return
    asked = [
        *resolved,
        landing.entry_of(
            {
                "head": context.head,
                "step": "escalate_adr",
                "refusal": "adr_number_taken",
                "at": (context.clock or _now)(),
            }
        ),
    ]
    record_landing(context, asked)
    escalate_adr(
        dataclasses.replace(
            context, step=landing_rules.LandingStep("escalate_adr", "adr_number_taken")
        )
    )
