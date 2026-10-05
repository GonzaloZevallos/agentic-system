"""Rules of the process that the use cases apply (README.md, 3 and 6).

Pure functions over the task state, so each one is a unit test and not a graph run.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from . import presubmit
from .denials import denial_for
from .document import HUMAN, demote
from .roles import (
    DEVELOPER,
    FINDINGS,
    INTERVIEWS,
    PAIR_PM_PA,
    PAIR_PM_TECH_LEAD,
    PAIR_UI_LEAD,
    PAIR_UX_LEAD,
    PM,
    PM_CONSOLIDATOR,
    PM_FINAL,
    PRODUCT_ANALYST,
    SOLUTION_INTERVIEW,
    TECH_LEAD,
    UI_DESIGNER,
    UI_LEAD,
    UI_LEAD_FINAL,
    UX_DESIGNER,
    UX_LEAD,
    UX_LEAD_FINAL,
    Interview,
    Pair,
    Role,
)
from .task import (
    INTEGRATION,
    WAITING_ON_HUMAN,
    ChangeCategory,
    ChangesRequested,
    DependencyState,
    DesignItemRecord,
    EscalationClass,
    EscalationPatch,
    EscalationPauseEntry,
    Failure,
    FailurePauseEntry,
    FailureReason,
    FindingCategory,
    FindingRecord,
    MergeOutcome,
    PauseEntry,
    ResumedPauseEntry,
    ReviewKind,
    RoleRun,
    Status,
    Subtask,
    TaskState,
)

# The category the system stamps on PM's returns of the PRD (ADR-0016).
PRD_RETURN: ChangeCategory = "definition"


def remaining_changes(state: TaskState, pair: Pair, limit: int) -> int:
    """Returns `pair` may still use before PjM escalates."""
    return limit - state.get("changes_requested", {}).get(pair.key, 0)


def record_return(state: TaskState, pair: Pair) -> dict[str, int]:
    """The `returns` patch after one more return of `pair`."""
    return {pair.key: state.get("changes_requested", {}).get(pair.key, 0) + 1}


def change_entry(pair: Pair, category: ChangeCategory, reason: str) -> ChangesRequested:
    """One entry of the return history: who returned, in which category, and the reason,
    so README §3's "same motive between the same roles" is computable."""
    return {"pair": pair.key, "category": category, "reason": reason}


def review_kind(subtask_id: str, reviewed_before: bool) -> ReviewKind:
    """Which review this is (ADR-0016), for `agentic.review.findings`'s `review.kind`
    label: the fan-in's own subtask is always `integration`, whatever pass it is on
    (its review always reads the whole branch); any other subtask is `first` until a
    return sends it back, and every pass after that is `incremental` (ADR-0015)."""
    if subtask_id == INTEGRATION:
        return "integration"
    return "incremental" if reviewed_before else "first"


def change_category(findings: list[dict[str, Any]]) -> FindingCategory | None:
    """The verdict of a review, derived (ADR-0016): the category of the return, the
    first blocking finding's since the reviewer lists them most severe first, or None
    when nothing blocks and the review approves."""
    return next((f["category"] for f in findings if f["severity"] == "blocking"), None)


def lower_unbacked_blocks(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The findings as the verdict counts them, and a line for each block the system
    lowered (ADR-0016). A block needs evidence, or it is an `observation`; and something
    this subtask's Developer can fix, or it is `important`. Lowering instead of failing
    the role: a field filled badly must not cost a retry, nor escalate the task."""
    settled, lowered = [], []
    for item in findings:
        if item["severity"] == "blocking" and not item["evidence"].strip():
            item = {**item, "severity": "observation"}
            lowered.append(f"`{item['location']}` blocking lowered to observation: no evidence")
        elif item["severity"] == "blocking" and item["scope"] != "subtask":
            item = {**item, "severity": "important"}
            lowered.append(
                f"`{item['location']}` blocking lowered to important: scope {item['scope']}, "
                "not fixable in this subtask"
            )
        settled.append(item)
    return settled, lowered


def review_markdown(
    category: FindingCategory | None, reason: str, text: str, lowered: Sequence[str] = ()
) -> str:
    """A review's section as the document keeps it: the derived verdict first, written by
    the system, with the blocks it lowered, then what the reviewer wrote. The reviewer
    never writes the verdict, so the section cannot contradict it (ADR-0016)."""
    verdict = "**approved**" if category is None else f"**returned** (`{category}`)"
    parts = [f"### Verdict\n\n{verdict}: {reason}"]
    if lowered:
        parts.append("Lowered by the system:\n\n" + "\n".join(f"- {line}" for line in lowered))
    return "\n\n".join([*parts, text])


# The findings table (issue #42): the channel between reviews. Every review appends its
# findings and nothing is ever rewritten, because branches run at the same time and
# cannot see each other; the fold happens here, when a prompt or the document is built.


def finding_records(
    subtask_id: str, version: int, findings: list[dict[str, Any]]
) -> list[FindingRecord]:
    """This review's findings as the table keeps them, already lowered by the system.

    A finding nobody can check is `dismissed` on the spot: an `observation` with no
    evidence is either the reviewer's own hunch or a `blocking` the system lowered for
    having none, and neither is something to hand the next role as a live objection.
    Everything else enters `open`; only a reviewer saying so closes it."""
    return [
        {
            "subtask": subtask_id,
            "version": version,
            "location": item["location"],
            "claim": item["claim"],
            "severity": item["severity"],
            "scope": item["scope"],
            "category": item["category"],
            "status": "dismissed"
            if item["severity"] == "observation" and not item["evidence"].strip()
            else "open",
        }
        for item in findings
    ]


def findings_rows(state: TaskState) -> list[FindingRecord]:
    """The table: one row per `location` + `claim`, in the order it was first raised, and
    `subtask` on a row names every subtask whose review raised it.

    Two reviewer runs do not write the same string for the same objection, so the key is
    normalised the way the interview normalises the human's questions. The first verdict
    is the one that stands: a later review re-raising what an earlier one dismissed does
    not reopen the argument.

    **A row closes only because a reviewer said it did.** Every pass after the first is
    incremental (ADR-0015) and is told not to look again at what it already approved, so
    a pass that stops mentioning a row usually means it did not look. Closing on silence
    would make the first return of any subtask wipe that subtask's carried-forward
    remarks, which is the exact class of thing issue #17 is about."""
    settled = {_norm(location) for location in state.get("settled_findings", [])}
    rows: dict[tuple[str, str], FindingRecord] = {}
    for record in state.get("findings", []):
        key = (_norm(record["location"]), _norm(record["claim"]))
        if (row := rows.get(key)) is None:
            rows[key] = record
        elif record["subtask"] not in row["subtask"].split(", "):
            rows[key] = {**row, "subtask": f"{row['subtask']}, {record['subtask']}"}
    return [
        {**row, "status": "fixed"}
        if row["status"] == "open" and _norm(row["location"]) in settled
        else row
        for row in rows.values()
    ]


def _norm(text: str) -> str:
    """Two reviews of the same objection do not write the same string; a trailing space
    or a capital must not split one row into two."""
    return " ".join(text.split()).casefold()


def open_findings(state: TaskState) -> list[FindingRecord]:
    """What the roles read: the rows still open, worst first, the ones the plan has to
    settle before the subtask ones. An `important` that came with an approval lives
    here too: that is the finding of run 4 that came back as an integration return."""
    order = {"blocking": 0, "important": 1, "observation": 2}
    rows = [row for row in findings_rows(state) if row["status"] == "open"]
    return sorted(rows, key=lambda row: (row["scope"] != "plan", order[row["severity"]]))


def findings_markdown(rows: list[FindingRecord]) -> str:
    """The table as a Markdown table. No evidence column: it is the review's own section,
    and a command's output does not fit in a cell. No status column either: every row
    here is open, and a cell that always says the same thing is context nobody reads."""
    head = (
        "| Severity | Scope | Category | Subtask | Location | Claim |\n"
        "| --- | --- | --- | --- | --- | --- |"
    )
    lines = [
        f"| {row['severity']} | {row['scope']} | {row['category']} "
        f"| {row['subtask']} | `{row['location']}` | {_cell(row['claim'])} |"
        for row in rows
    ]
    return "\n".join([head, *lines]) if lines else "None open."


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def findings_section(state: TaskState) -> dict[str, str]:
    """The `sections` patch with the table as the roles read it: what is still open.

    Only the open rows, because this section is what lands whole in the next prompt; the
    raw record of every finding of every pass is the state, and the document's
    frontmatter from it. Written at the fan-in, the one place that sees every branch."""
    return {FINDINGS: findings_markdown(open_findings(state))}


def invariants_markdown(state: TaskState) -> str:
    """The plan's invariants as a prompt shows them."""
    return "\n".join(f"- {line}" for line in state.get("invariants", [])) or "- none"


def escalation(cls: EscalationClass, reason: str) -> EscalationPatch:
    """The patch that escalates the task: `escalated`, the text for the human, and
    `escalation_class`, the closed reason `agentic.task.paused` groups pauses by. The
    only place either key is written, so a patch cannot carry one without the other.

    Returns a plain patch, not a full `TaskState`: every caller folds it into a bigger
    literal with `**`, the same shape `apply` folds any patch into the whole state."""
    return {"escalated": reason, "escalation_class": cls}


def unmergeable(subtask_id: str, dependency: str, conflict: str) -> dict[str, Any]:
    """The patch when a subtask's dependency cannot be brought into its branch: the
    Developer never starts, so nothing is paid, and the human untangles the two
    branches. Not a role failure and not a return: no role did anything wrong."""
    return {
        # No role ran, so nothing seeded `current_role` the way `run_role` does: the
        # human reading "paused: <role>" must see whose subtask stopped, not whoever
        # wrote last.
        "current_role": DEVELOPER.name,
        "status": WAITING_ON_HUMAN,
        **escalation(
            "unmergeable",
            (
                f"subtask {subtask_id!r} cannot start on {dependency!r}: "
                f"the merge does not apply. Merge the two branches by hand in the "
                f"subtask's worktree, then resume; an answer alone changes nothing here."
                f"\n\n{conflict}"
            ),
        ),
    }


# Leftover conflict markers at the start of a line, the only shape git itself ever
# writes: a marker mid-line is prose about the format (docs, a shell one-liner), never
# what a merge left behind. `re.M` so `^` matches the start of every line, not just the
# start of the string.
_CONFLICT_MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.M)


def has_conflict_markers(text: str) -> bool:
    """Whether `text` still carries a leftover conflict marker (the request's own
    closing criterion: staging or committing a marker-laden file is not a resolution,
    whatever git's own conflict bookkeeping says once it is added or committed). Pure,
    over the file's own content: `Workspace.dependency_state` is the only caller that
    reads a path off disk to get that content in the first place."""
    return bool(_CONFLICT_MARKER.search(text))


def dependency_before(dep_state: DependencyState) -> Literal["skip", "hand_over", "attempt"]:
    """What `develop()` does about one dependency before it ever calls
    `Workspace.merge_keeping_conflicts`, decided from `DependencyState` alone, no git
    call of its own: a merge already in progress (`merge_in_progress`) wins over
    everything else, because running a merge on top of an unfinished one is not a
    thing git allows and is exactly the state a human may have been resolving by hand
    after `unmergeable`'s own instruction — so it is handed to the Developer as it
    stands, never re-attempted. Failing that, a dependency already `contained` needs no
    merge at all. Anything else is worth a genuine attempt."""
    if dep_state.merge_in_progress:
        return "hand_over"
    if dep_state.contained:
        return "skip"
    return "attempt"


def dependency_after_attempt(outcome: MergeOutcome) -> Literal["clean", "hand_over", "unmergeable"]:
    """What `develop()` does with a fresh `MergeOutcome`, right after
    `Workspace.merge_keeping_conflicts` returns: no reason at all is a clean merge,
    nothing more to do; a reason with conflicted paths is a Developer's to resolve; a
    reason with none — an unknown branch, a dirty tree, a git error — is nothing a
    Developer turn could act on, so it escalates the same way today's `merge` already
    does, immediately, the cost this task must not add for a dependency nobody could
    ever have resolved."""
    if outcome.reason is None:
        return "clean"
    if outcome.conflicted_paths:
        return "hand_over"
    return "unmergeable"


def dependency_delivered(dep_state: DependencyState) -> bool:
    """Whether a dependency verifiably landed, once the Developer's turn is over: the
    branch is an ancestor of the delivered head, no merge is left in progress, and none
    of the paths scanned for markers still holds one — ancestry alone is satisfiable by
    a `git add` on a marker-laden file followed by a commit, exactly the shape the
    request's own closing criterion names, so all three facts have to hold together."""
    return dep_state.contained and not dep_state.merge_in_progress and not dep_state.marked_paths


def dependency_unresolved_reason(dep_state: DependencyState) -> str:
    """The material `unmergeable`'s own `conflict` argument carries when a Developer's
    attempt did not settle a dependency: which of the three facts
    `dependency_delivered` checks is still unsettled, named plainly, so the escalation
    says more than a bare "no"."""
    if dep_state.merge_in_progress:
        return "a merge is still in progress in the worktree."
    if dep_state.marked_paths:
        listed = ", ".join(f"`{p}`" for p in dep_state.marked_paths)
        return f"conflict markers are still in: {listed}."
    return "the dependency is still not on the branch."


def merge_notice(dependency: str, branch: str, conflicted_paths: Sequence[str]) -> str:
    """The per-case text a Developer reads when its dependency does not apply clean
    (gym #16's own two escalations, a hunk a human deleted in one turn): what carries
    markers, how to take one side of a file without the strategy the cage denies
    outright (`git checkout --theirs`, `hooks.SWITCH`), and what never to reach for
    instead — `-s ours`, `-X ours` and `-X theirs` decide a whole file or a whole
    merge by strategy, never the conflicting lines a hand resolution keeps both
    intents over."""
    listed = "\n".join(f"- `{p}`" for p in conflicted_paths) or "(none reported)"
    return (
        f"The dependency `{dependency}` (`{branch}`) does not apply clean on top of "
        f"your branch. These paths carry conflict markers:\n\n{listed}\n\n"
        "Resolve them, run the declared checks, and commit the merge before you start "
        "your own brief. `git restore --ours <path>` or `git restore --theirs <path>` "
        "is how you take one side of a file whole; `git checkout --theirs <path>` is "
        "denied by the cage, so do not ask to widen it, use `git restore` instead. "
        "Never `git merge -s ours`, `-X ours` or `-X theirs`: those decide a whole "
        "side of the merge by strategy, and this one is resolved file by file, keeping "
        "both intents, the way a human already did for this exact shape of conflict. "
        "The conflicted paths and the diff against the dependency branch will be "
        "written into your section for the review to check."
    )


def screens_notice(screens: Sequence[str], compare: str | None) -> str:
    """The turn-message text telling the Developer to look at its comparisons before
    delivering (ADR-0040), the way `merge_notice` tells it about a dependency. Empty
    `screens` means this subtask names none: the caller does not add this notice to
    the turn message at all, the same way `plan_view._brief` projects no `Screens:`
    line. `compare` is the product's declared command (`design.compare`), or `None`
    when the product declares no `design` block: the Developer still gets the names of
    its screens, told plainly that no comparison exists to run."""
    listed = "\n".join(f"- `{s}`" for s in screens)
    if compare is None:
        return (
            f"Your subtask's screens, from the plan:\n\n{listed}\n\n"
            "The product declares no `design` block, so there is no comparison to run: "
            "build to the brief and the screens named above."
        )
    return (
        f"Before you deliver, look at the comparisons for your screens:\n\n{listed}\n\n"
        f"Run `{compare}` in your checkout and read what it writes, one comparison per "
        "screen above, side by side with the prototype at the same width."
    )


# The definition-side design branch (ADR-0040): UX Designer writes the flow, UX Lead
# validates it with a walk, UI Designer writes the screens design over the validated
# flow, UI Lead validates it, and PM consolidates both into the PRD. All five run
# between PM's own validation and the plan, with `status` left at `definition_ready`
# throughout -- ADR-0026 freezes `Status`, so no new value marks a position inside this
# branch. `design_step` is the one place that answers "which node is next", from run
# recency alone; `after_validate`, each design node's own `after_*` and `resume_target`
# all call it, and only it, before falling through to `plan` (no other code computes
# this position, the plan's own invariant).


def design_applies(state: TaskState) -> bool:
    """Whether this task runs the design branch at all (ADR-0040): a feature, with
    `touches_ui` among the validated flags. Never a bug, never a platform enhancement,
    and never a feature PM did not flag -- `FIRST_NODE`'s own fork decides the path by
    `type`, and this is the narrower condition inside the feature path alone."""
    return state.get("type") == "feature" and bool(state.get("flags", {}).get("touches_ui"))


def _latest_index(state: TaskState, section: str) -> int | None:
    """The position, in `state["runs"]`, of the latest run that wrote `section`
    (`run.get("section", run["role"])`, the same key every version-counting reader of
    `runs` already uses), or `None` when none ever did. Positions, not timestamps:
    every node of this branch runs one at a time, never two at once, so the order
    runs land in this one list already is the order they happened in, and comparing
    indices is comparing recency."""
    idx = None
    for i, run in enumerate(state.get("runs", [])):
        if run.get("section", run["role"]) == section:
            idx = i
    return idx


def _pair_returns(state: TaskState, pair_key: str) -> int:
    """How many returns `change_history` records for `pair_key`, read by its own key
    (never by `category` alone: PM's PRD returns and a lead's own returns share the
    `definition` category, and only the pair tells them apart)."""
    return sum(1 for h in state.get("change_history", []) if h["pair"] == pair_key)


def _run_count(state: TaskState, section: str) -> int:
    return sum(1 for r in state.get("runs", []) if r.get("section", r["role"]) == section)


def _lead_satisfied(
    state: TaskState,
    thinker_section: str,
    lead_section: str,
    pair_key: str,
    newer_than: int | None,
) -> bool:
    """Whether `lead_section`'s latest run stands as a current, approving verdict over
    `thinker_section`'s latest write: a run exists, it is at least as new as that write
    and than `newer_than` (the staleness baseline: the latest Product Analyst PRD run
    for a UX Lead check, the latest UX Designer run for a UI Lead one; `None` reads
    as nothing to be newer than), and it was not itself a return.

    No `RoleRun` carries a verdict, so a return is told apart from an approval by
    counting instead: `_pair_returns(pair_key) < _run_count(lead_section)` holds
    exactly when the lead's latest run has not yet been matched by a return of its
    own pair, true for every path this branch's own routing can reach -- a return
    always sends the thinker back before the lead can run again, in the same pass or,
    past the pair's limit, after the human's resume -- except one no acceptance
    scenario exercises: a return immediately following an unrelated stale re-run of
    the same lead, which this reads as an approval instead of a pending return."""
    thinker_idx = _latest_index(state, thinker_section)
    lead_idx = _latest_index(state, lead_section)
    if thinker_idx is None or lead_idx is None or lead_idx < thinker_idx:
        return False
    if newer_than is not None and newer_than > lead_idx:
        return False
    return _pair_returns(state, pair_key) < _run_count(state, lead_section)


def design_step(state: TaskState) -> str | None:
    """Which design node runs next (ADR-0040), ordered by run recency alone -- no new
    persisted status, ADR-0026's own freeze on `Status`. `None` when this task does
    not run the branch at all (`design_applies`), or when every step is already
    satisfied: either way, the caller falls through to `plan`.

    The ladder, in order: UX Designer writes when it has no run; UX Lead walks when it
    has not yet validated UX Designer's latest write; UX Designer writes again when
    UX Lead's latest run returned it, or when a newer PRD run (a consolidation return
    to Product Analyst, followed by a new PRD) makes its old approval stale. The same
    three-way ladder repeats for UI Designer/UI Lead, staled by a newer UX Designer
    run instead of a newer PRD one (`ui_screens is stale when UX Designer is newer than
    UI Lead's approval`, the plan's own wording) -- never by the PRD directly,
    since a PRD new enough to matter already reopened `ux_flow` first, and from there a
    fresh UX Designer run is what makes UI's own step stale in turn. Consolidation is
    last, due once both leads are satisfied and no `PM: consolidation` run is newer
    than both of their approvals."""
    if not design_applies(state):
        return None
    prd_idx = _latest_index(state, PRODUCT_ANALYST.name)
    ux_idx = _latest_index(state, UX_DESIGNER.name)
    if ux_idx is None:
        return "ux_flow"
    ux_lead_idx = _latest_index(state, UX_LEAD.name)
    if ux_lead_idx is None or ux_lead_idx < ux_idx:
        return "ux_walk"
    if not _lead_satisfied(state, UX_DESIGNER.name, UX_LEAD.name, PAIR_UX_LEAD.key, prd_idx):
        return "ux_flow"
    ui_idx = _latest_index(state, UI_DESIGNER.name)
    if ui_idx is None:
        return "ui_screens"
    ui_lead_idx = _latest_index(state, UI_LEAD.name)
    if ui_lead_idx is None or ui_lead_idx < ui_idx:
        return "design_review"
    if not _lead_satisfied(state, UI_DESIGNER.name, UI_LEAD.name, PAIR_UI_LEAD.key, ux_idx):
        return "ui_screens"
    consolidation_idx = _latest_index(state, PM_CONSOLIDATOR.name)
    if consolidation_idx is None or consolidation_idx < max(ux_lead_idx, ui_lead_idx):
        return "consolidate"
    return None


def design_effect_by_role(state: TaskState) -> dict[str, int]:
    """Per role slug, how many of this task's returns and blocking design items trace
    to it (§11): a lead's own returns (`change_history`, by its pair), plus every
    `blocking` item any design role raised (`design_items`, by `role_slug`). Ships with
    its own test; nothing in this task reads it back."""
    counts: dict[str, int] = {}
    for h in state.get("change_history", []):
        if h["pair"] == PAIR_UX_LEAD.key:
            counts[UX_LEAD.slug] = counts.get(UX_LEAD.slug, 0) + 1
        elif h["pair"] == PAIR_UI_LEAD.key:
            counts[UI_LEAD.slug] = counts.get(UI_LEAD.slug, 0) + 1
    for item in state.get("design_items", []):
        if item["kind"] == "blocking":
            counts[item["role_slug"]] = counts.get(item["role_slug"], 0) + 1
    return counts


def design_observations_markdown(items: Sequence[DesignItemRecord]) -> str:
    """The `## Observations on the design` table `deliver` appends (ADR-0040): role,
    step, text, the same cell-escaping `findings_markdown` uses so a stray `|` in a
    design item's own text cannot break the table. Called only when `items` is
    non-empty; an empty table is not a shape this ever has to render."""
    head = "| Role | Step | Text |\n| --- | --- | --- |"
    lines = [f"| {i['role_slug']} | {_cell(i['step'])} | {_cell(i['text'])} |" for i in items]
    return "\n".join([head, *lines])


def resumed_after_unmergeable(state: TaskState, subtask_id: str, branch: str, head: str) -> str:
    """What a Developer with a session on record but no review section reads on this
    run: the document shape only this task's path creates, because the previous run
    escalated `unmergeable` before any review could happen, so
    `returned_developer_delta` — gated on the subtask actually carrying `returned` —
    is not what resumes it. Names why it is running, since a resumed session otherwise
    has no idea; the branch and its current head, since the checkout may have moved;
    which paths were conflicted when this session last ran; and that a human may
    already have resolved the merge by hand in this worktree, reading `unmergeable`'s
    own instruction — so if the dependency is in and the tree is clean, it proceeds
    with its brief instead of hunting for work to redo."""
    subtask = state.get("subtasks", {}).get(subtask_id, {})
    paths = subtask.get("conflicted_paths", [])
    listed = "\n".join(f"- `{p}`" for p in paths) if paths else "(none recorded)"
    return (
        f"This run resumes your session on branch `{branch}`, now at `{head}`. Your "
        "previous attempt escalated because a dependency did not merge clean; the task "
        "paused, and a human may already have resolved it by hand in this worktree "
        "before resuming — that is exactly what the escalation asked for. These paths "
        f"carried conflict markers when this session last ran:\n\n{listed}\n\n"
        "If the dependency is already in and the tree is clean, proceed with your "
        "brief; do not go looking for work to redo."
    )


def setup_unprepared(
    role: Role, branch: str, step: str, command: str, output: str
) -> dict[str, Any]:
    """The patch when the product's setup does not prepare a checkout the system just
    made. Not a role failure and not a return: no role has run, and an environment that
    will not build is not something a model fixes (issue #37). The step, the command and
    its last lines travel, because the human fixes this outside the system."""
    return {
        # No role ran, so nothing seeded `current_role`: the human must see who was
        # about to start, not whoever wrote last.
        "current_role": role.name,
        "status": WAITING_ON_HUMAN,
        **escalation(
            "setup",
            (
                f"the product's setup step {step!r} failed on {branch!r}: the checkout "
                f"has no environment to work in, so no role starts. Fix the step, or "
                f"the product's `setup` in `.agentic/stack.yaml`, and resume; an answer "
                f"alone changes nothing here."
                f"\n\n```text\n$ {command}\n{presubmit.tail(output)}\n```"
            ),
        ),
    }


def limit_reached(pair: Pair, limit: int, reason: str) -> str:
    """The escalation text when `pair` used up its returns: the pair, the limit and the
    verifier's reason, written for the human."""
    return f"{pair.key} hit {limit} returns: {reason}"


# The motive of a failed attempt (issue #66; `docs/product/role-failures.md`): "the
# motive of an attempt that failed, and of the escalation that follows two of them,
# never has an empty or tautological diagnostic part". Two SDK shapes report a broken
# attempt (`ResultError`, which structurally carries an exit code, and the error
# `ResultMessage`, which structurally cannot), so `failure_motive` is fed a value
# object instead of either one directly: the tiered rule becomes one function with
# pure inputs and outputs, testable with no SDK and no CLI. `exception_motive` is the
# sibling for the raise sites whose material is a caught exception, not an SDK report.
# `motive`, last, is the choke point every `AttemptFailed` passes through
# (`application/ports.py`): it is what makes the guarantee hold at every raise site by
# construction, the ten today and any not yet written, instead of by each site's care.


# The exact tier-3 "nothing at all" text `failure_motive` falls back to when `report`
# carries neither a cause nor any metadata nor an exit code: `max_turns_motive`
# compares against it, once, instead of re-deriving that emptiness itself.
_NO_DETAIL_REPORTED = "the SDK reported no cause and no exit code"


@dataclass(frozen=True)
class ReportedFailure:
    """What the harness reported about a broken attempt, in the shape `failure_motive`
    composes from, whichever SDK object carried it. A value object (equality by value,
    no identity): it exists only to let the tiered rule be tested without the SDK."""

    subtype: str | None = None
    errors: tuple[str, ...] = ()
    result: str | None = None
    api_error_status: int | None = None
    terminal_reason: str | None = None
    exit_code: int | None = None


def failure_motive(report: ReportedFailure) -> str:
    """The motive for a broken attempt, composed from everything the SDK reported, in
    the order that names the cause first: the incident this task fixes (issue #66) is
    exactly the order gone missing — `subtype="success"` and empty `errors`, with the
    "API Error: ..." prose sitting unread in `result`.

    Tier 1: `result`'s prose when it strips non-empty, else the stripped, non-empty
    `errors` joined by `; ` (an entry equal to the prose is dropped, so nothing
    repeats). Tier 2, when tier 1 has nothing: "the SDK reported no message", plus
    whatever of `subtype` (never `success`, which names the loop finishing, not a
    cause), `terminal_reason` and `api_error_status` is present. Tier 3, when tier 2
    has nothing either: `exit_code` named in words when a `ResultError` carried one,
    or a report that carries none said as such — the `ResultMessage` site structurally
    has none, so a formatted `None` is a tautology this never reaches. `exit_code`
    itself rides alongside tier 1 and tier 2's parenthesised metadata too, last, never
    formatted when absent."""
    cause = _cause(report)
    core = _metadata_core(report)
    exit_part = f"exit code {report.exit_code}" if report.exit_code is not None else None
    metadata = ", ".join([*core, *([exit_part] if exit_part else [])])
    parens = f" ({metadata})" if metadata else ""
    if cause:
        return f"{cause}{parens}"
    if core:
        return f"the SDK reported no message{parens}"
    if exit_part:
        return f"the SDK ended with exit code {report.exit_code} and reported no cause"
    return _NO_DETAIL_REPORTED


def failure_reason(report: ReportedFailure) -> FailureReason:
    """`agentic.role.failures`'s reason for a broken attempt (ADR-0029), fed the same
    `ReportedFailure` `failure_motive` composes from — one value, one reading, so the
    two can never disagree. Reads no prose (ADR-0004, sixth addendum) and no
    `exit_code` (`_reported_result` structurally carries none). Precedence, since more
    than one clause can hold at once:

    1. A budget subtype (`"budget" in (report.subtype or "")`) -> `limit`: our own
       per-attempt cap is a fact about our configuration, and it is definitive over
       whatever else the report carries.
    2. `api_error_status == 429` -> `account_limit`: an account's own quota (session,
       spend or rate), never the sub-cause, which is prose and stays unread here.
    3. `500 <= api_error_status < 600` -> `upstream`: the provider's own server, by
       range rather than by an enumeration a new status code could silently miss.
    4. `subtype == "error_max_structured_output_retries"` or
       `terminal_reason == "structured_output_retry_exhausted"` -> `schema`: the CLI
       exhausting its own structured-output retries, a relabelling of what the
       glossary's Fallo de rol already names, not a new value.
    5. `terminal_reason == "api_error"` with no `api_error_status` at all -> `network`
       (issue #137): the harness reported an API error with no HTTP status to read,
       the shape a downed network or DNS (`EAI_AGAIN`) leaves, never read from the
       prose that happens to name it.
    6. Anything else -> `sdk`: the harness broke in a way none of the named facts
       covers.
    """
    subtype = report.subtype or ""
    if "budget" in subtype:
        return "limit"
    if report.api_error_status == 429:
        return "account_limit"
    if report.api_error_status is not None and 500 <= report.api_error_status < 600:
        return "upstream"
    if subtype == "error_max_structured_output_retries":
        return "schema"
    if report.terminal_reason == "structured_output_retry_exhausted":
        return "schema"
    if report.terminal_reason == "api_error" and report.api_error_status is None:
        return "network"
    return "sdk"


def max_turns_motive(max_turns: int, report: ReportedFailure) -> str:
    """The motive for an attempt the harness cut for `error_max_turns`
    (`docs/product/role-failures.md` §1; issues #69, #81): the cutoff, named first, so
    a spent budget never reads as a ruling about the product (the invariant every
    raise site of this kind honours). `report.subtype` is always `error_max_turns`
    here — that is how a raise site knows to call this — so folding it into the detail
    the way `failure_motive` folds every other `subtype` would repeat what the leading
    sentence already says, the same tautology issue #66 fixed for a different field;
    dropped, so only genuine SDK prose, a terminal reason, an HTTP status or an exit
    code tells the human what the role was doing when its turns ran out. When there is
    none of that either, the bare sentence stands alone (tier 3 of the definition):
    `failure_motive`'s own "no cause" tier would only echo it back as new information
    it is not."""
    exhausted = f"max_turns ({max_turns}) exhausted without a result"
    detail = failure_motive(replace(report, subtype=None))
    return exhausted if detail == _NO_DETAIL_REPORTED else f"{exhausted}: {detail}"


def _cause(report: ReportedFailure) -> str:
    """Tier 1: the prose, then whatever of `errors` does not just repeat it."""
    result = (report.result or "").strip()
    errors = [text for e in report.errors if (text := e.strip())]
    if result:
        return "; ".join([result, *(e for e in errors if e != result)])
    return "; ".join(errors)


def _metadata_core(report: ReportedFailure) -> list[str]:
    """`subtype`, `terminal_reason` and `api_error_status`, present and stripped
    non-empty, in the order the motive shows them. `exit_code` is not here: it decides
    tier 3's wording on its own (`failure_motive`), and is appended to this same list,
    last, whenever either tier renders a parenthetical at all."""
    parts = []
    subtype = (report.subtype or "").strip()
    if subtype and subtype != "success":
        parts.append(f"subtype={subtype}")
    terminal = (report.terminal_reason or "").strip()
    if terminal:
        parts.append(f"terminal reason: {terminal}")
    if report.api_error_status is not None:
        parts.append(f"HTTP {report.api_error_status}")
    return parts


def exception_motive(exc: BaseException) -> str:
    """The motive's material when the raise site caught an exception instead of an SDK
    report: `str(exc)` when it strips non-empty, else the type name, so an exception
    raised with no arguments (`str(exc) == ""`) still names something."""
    text = str(exc)
    return text if text.strip() else f"{type(exc).__name__}, with no message"


MOTIVE_MAX_CHARS = 2000
_MOTIVE_HEAD_CHARS = 1600
_MOTIVE_TAIL_CHARS = 300
# C0 controls other than `\n` (0x0a) and `\t` (0x09): a stray one in a CLI's stderr or
# an API error's prose must not reach a Markdown comment or the document's frontmatter.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f]")
# Three or more backticks: long enough to open a Markdown fence. Breaking every run
# with a zero-width space keeps the text readable while none of them can close or open
# one in the issue comment this motive lands in.
_CODE_FENCE = re.compile(r"`{3,}")


def _break_fences(text: str) -> str:
    return _CODE_FENCE.sub(lambda m: "\u200b".join(m.group()), text)


def _elide(text: str) -> str:
    """The choke point's cap: unchanged under `MOTIVE_MAX_CHARS`, otherwise the first
    `_MOTIVE_HEAD_CHARS` (where the prose leads, `API Error: ...` included) and the
    last `_MOTIVE_TAIL_CHARS` (where a CLI error often puts the actionable part,
    `resets 11:20pm` included), joined by a marker that says what it removed instead
    of silently splicing two halves together."""
    if len(text) <= MOTIVE_MAX_CHARS:
        return text
    head, tail = text[:_MOTIVE_HEAD_CHARS], text[-_MOTIVE_TAIL_CHARS:]
    elided = len(text) - _MOTIVE_HEAD_CHARS - _MOTIVE_TAIL_CHARS
    return f"{head}\n…[{elided} characters elided]…\n{tail}"


def motive(text: str, reason: FailureReason) -> str:
    """The choke point every `AttemptFailed` passes through (`application/ports.py`):
    the guarantee that a motive never has an empty or tautological diagnostic part
    holds here, once, instead of at every raise site's own care.

    In order: collapse `\\r\\n` to `\\n`; drop the C0 controls a stray one in stderr or
    an API error's prose could carry, keeping `\\n` and `\\t`; neutralise a run of
    backticks long enough to open a Markdown fence in the issue comment; then, if what
    is left strips to empty or ends at a colon (optionally trailing whitespace) — the
    shape of the 2026-09-17 incident's `run failed (success): ` — append a named
    placeholder after whatever prefix the site wrote, so the text still says which
    raise site had nothing; and last, cap the length, since none of the above bounds
    it and a CLI's stderr or a model's prose can run well past what a comment should
    carry."""
    normalized = _break_fences(_CONTROL_CHARS.sub("", text.replace("\r\n", "\n")))
    stripped = normalized.strip()
    if not stripped or stripped.endswith(":"):
        prefix = normalized.rstrip()
        joiner = " " if prefix else ""
        normalized = f"{prefix}{joiner}no cause reported (reason: {reason})"
    return _elide(normalized)


_LIMIT_REACHED = re.compile(r"^(.+?) hit \d+ returns: ")


def pair_at_limit(reason: str | None) -> str | None:
    """The key of the pair whose limit an escalation reason reports, the one whose
    counter the human's answer resets (README.md, 3); None for any other escalation.

    Parses the text; kept as the fallback `returns_pair` uses for a document written
    before `escalation_class` existed (ADR-0003, addendum)."""
    match = _LIMIT_REACHED.match(reason or "")
    return match.group(1) if match else None


def returns_pair(state: TaskState) -> str | None:
    """Which pair's return counter a resume must reset: the pair a `returns`-class
    escalation named. `escalation_class` only says *that* it is a pair at its limit, not
    *which* pair (`returns` covers PM<->Product Analyst and Tech Lead<->Developer alike),
    so the pair itself still comes from the text `rules.escalation` wrote it into
    (`pair_at_limit`) — gated by the class when the state carries one, or by the text
    alone for a document written before `escalation_class` existed."""
    reason = state.get("escalated")
    if (cls := state.get("escalation_class")) is not None and cls != "returns":
        return None
    return pair_at_limit(reason)


def next_version(state: TaskState, section: str) -> int:
    """The number this delivery of the section gets: one more than its runs so far.
    Older runs without `section` wrote the section named after their role."""
    runs = state.get("runs", [])
    return 1 + sum(1 for r in runs if r.get("section", r["role"]) == section)


def superseded(section: str, version: int) -> str:
    """Section name a delivery keeps once a return replaces it."""
    return f"{section} (v{version})"


def last_session(state: TaskState, role_name: str, section: str) -> str | None:
    """The session id of the latest run of `role_name` that wrote `section`, to resume
    it; None when that role never wrote there."""
    for run in reversed(state.get("runs", [])):
        if run["role"] == role_name and run.get("section", run["role"]) == section:
            return run.get("session_id") or None
    return None


def sections_for(state: TaskState, section: str, text: str) -> dict[str, str]:
    """The new section, plus its previous delivery as history on a return."""
    sections = {section: text}
    previous = state.get("sections", {}).get(section)
    if previous is not None:
        sections[superseded(section, next_version(state, section) - 1)] = previous
    return sections


# A retake (ADR-0014 addendum): an escalated interview resumes instead of repeating,
# whether it closed at the cap with questions the human just answered, or was cut short
# by a role failure or the task cap and carries no decision at all (README.md, 3).

INTERVIEW_RETAKE_ROUNDS = 2


@dataclass(frozen=True)
class InterviewProgress:
    """Where a retaken interview picks up, computed once from `state["runs"]` so every
    reader of "how many rounds" — `interview()`'s own seeding, a test, a future panel —
    agrees, never a local counter, `len(transcript.rounds)`, or the section's Markdown.

    `rounds_done` is the interviewer's own runs of this interview's section, closing
    turn included: every round has exactly one interviewer run, whether it asked or
    closed, so counting those alone counts the rounds. `rounds_left` is the budget a
    retake gets on top of it: the whole cap for an interview that never ran a round,
    `INTERVIEW_RETAKE_ROUNDS` for one a retake resumes, whether that one closed having
    spent the ordinary cap or was cut short earlier. `sessions` is each seat — the
    interviewer, the interviewee — that has a session to resume, keyed by its slug; a
    seat that never delivered is simply absent, which is the cold path `interview.turn`
    already knows how to take. `invariants` is what `state["proposed_invariants"]`
    already holds (issue #42): an interview whose interviewer proposes invariants seeds
    its ephemeral transcript from it instead of starting empty; a fresh interview, or
    one with no such field in its schema, simply gets none."""

    rounds_done: int
    rounds_left: int
    sessions: dict[str, str]
    invariants: list[str] = field(default_factory=list)


def interview_progress(state: TaskState, spec: Interview, max_rounds: int) -> InterviewProgress:
    """`InterviewProgress` for `spec` at this state, against `max_rounds` (the ordinary
    cap, `cfg.interview_max_rounds`): zero rounds done and the whole budget for an
    interview that never ran, whatever a retake already spent otherwise."""
    runs = [r for r in state.get("runs", []) if r.get("section", r["role"]) == spec.section]
    rounds_done = sum(1 for r in runs if r["role"] == spec.interviewer.name)
    rounds_left = max(max_rounds - rounds_done, INTERVIEW_RETAKE_ROUNDS)
    sessions = {
        role.slug: session
        for role in (spec.interviewer, spec.interviewee)
        if (session := last_session(state, role.name, spec.section))
    }
    invariants = list(state.get("proposed_invariants", []))
    return InterviewProgress(rounds_done, rounds_left, sessions, invariants)


def interview_retake_delta(state: TaskState, spec: Interview, branch: str, head: str) -> str:
    """What a resumed seat of a retaken interview reads instead of the context its
    session already holds (ADR-0004, sixth addendum; ADR-0014 addendum). Copies the
    shape of `returned_developer_delta`, which already solved this for the Developer:
    the branch and its head first, since the checkout may have moved since the
    escalation paused the task; that the glossary entries and the ADRs this interview
    already committed there are to be edited, never re-created under a new number; then
    `## Human` unconditionally, so a decider whose resumed session never reads
    `context_for` again still sees what the human settled and which questions it
    answers (`human_section`'s own numbering).

    Never the original request, the product knowledge, the standards, or the
    interview's own transcript: the resumed session already holds them, which is the
    point of resuming it. `None` of those three exist for a retake with no decision (a
    role failure, the task cap): only the first paragraph then, unconditionally too."""
    parts = [
        f"Branch `{branch}` is at `{head}`. The checkout may have changed since your "
        "previous round: a review may have run its checks there, or the worktree may "
        "have been recreated. Your session and any commits it made are the same; the "
        "surroundings may not be. The glossary entries and the ADRs this interview "
        "already wrote there are committed on this branch: edit them, never re-create "
        "them under a new number."
    ]
    if human := state.get("sections", {}).get(HUMAN):
        parts.append(f"## {HUMAN}\n\n{human}")
    return "\n\n".join(parts)


# Subtasks (ADR-0011): each in its own branch, sections named after it.


def developer_section(subtask_id: str) -> str:
    return f"Developer: {subtask_id}"


REVIEW_PREFIX = f"{TECH_LEAD.name}: review "


def review_section(subtask_id: str) -> str:
    return f"{REVIEW_PREFIX}{subtask_id}"


def return_section(state: TaskState, subtask_id: str) -> str:
    """Whose section a returned subtask's delta and the incremental reviewer's own spec
    come from (ADR-0040): `review_section(subtask_id)`, as today, unless PM's early,
    UI-only final validation is the open return (`subtasks[id]["returned_by"] ==
    'final_validation'`), absent reading as `'review'` for every subtask this never
    touched. The plan's own invariant: a returned item's delta and the incremental
    reviewer's spec both come from this one function, never from `review_section`
    directly."""
    subtask = state.get("subtasks", {}).get(subtask_id, {})
    if subtask.get("returned_by", "review") == "final_validation":
        return PM_FINAL.name
    return review_section(subtask_id)


def returned_developer_delta(state: TaskState, subtask_id: str, branch: str, head: str) -> str:
    """What a returned Developer reads instead of the full context (ADR-0004, sixth
    addendum): what changed since its previous delivery. In order: the branch's current
    head, so the model is told the checkout may not be exactly as it left it; `## Human`;
    the open findings table as the fan-in last wrote it; and the review that returned
    this subtask, led by its one-line reason. Unconditional, because reasoning about
    what a resumed session already holds is not reliable, and because these are the only
    sections that move between waves.

    Never the original request, PM's section, the plan or the Developer's own previous
    section: its session already holds them, which is the point of resuming it."""
    sections = state.get("sections", {})
    parts = [
        f"Branch `{branch}` is at `{head}`. The checkout may have changed since your "
        "previous delivery: a review ran its checks there, a dependency may have been "
        "re-merged, or the worktree may have been recreated. Your commits and this "
        "conversation are the same; the surroundings may not be."
    ]
    if human := sections.get(HUMAN):
        parts.append(f"## {HUMAN}\n\n{human}")
    if findings := sections.get(FINDINGS):
        parts.append(f"## {FINDINGS}\n\n{findings}")
    review = return_section(state, subtask_id)
    reason = state.get("subtasks", {}).get(subtask_id, {}).get("reason", "").strip()
    lead = f"**Returned:** {reason}\n\n" if reason else ""
    parts.append(f"## {review}\n\n{lead}{sections.get(review, '')}")
    return "\n\n".join(parts)


# The retry message after a `max_turns` cut (ADR-0004, seventh addendum; issue #85).
# `_retry_input` (`application/run_role.py`) hands the attempt after a `max_turns` cut
# this message instead of its ordinary "Retry. Fix exactly what failed.": the cut
# attempt did not fail at the work, so an order to fix it sends a model whose work was
# fine back to re-read what it already read — the defect the request measured at
# $16.38 lost over three runs, all of it spent re-discovering "the previous work is
# intact, uncommitted".

# Slugs of the roles an `Interview` (`domain/roles.py`) seats as its interviewer,
# interviewee or consultant: `INTERVIEW_DEFINITION`, `INTERVIEW_SOLUTION` and
# `INTERVIEW_SOLUTION_PLATFORM`'s own three or two seats, each one run as its own
# resumed session, so a cut mid-turn is cut mid-answer, never mid-interview (that cap
# is `cfg.interview_max_rounds`, a different counter `interview.py`'s own loop already
# closes on, with the `CLOSE` text this arm echoes for the SDK's cut instead). Named
# here, not derived by walking the three specs at import time, so the set reads on its
# own; `test_rules.py` pins it against those same specs so the two cannot drift apart
# unnoticed.
INTERVIEW_PARTICIPANT_SLUGS = frozenset(
    {
        "product_analyst_interviewer",
        "pm_interviewee",
        "tech_lead_ephemeral",
        "tech_lead_interviewee",
        "pm_consultant",
        "tech_lead_ephemeral_platform",
        "tech_lead_interviewee_platform",
        "tech_lead_ephemeral_stack",
        "tech_lead_interviewee_stack",
    }
)

# What every arm of `turn_cut_retry_message` may honestly say, and nothing past it: the
# turn cap ended the run, not the work; the session and its commits are intact; the
# checkout around it may have moved. Never a branch or a head — `returned_developer_delta`
# is the one function that names those, from state the runner does not have here.
_TURN_CAP_FACT = (
    "The turn cap ended your previous attempt; nothing about it failed. The "
    "conversation and any commits you already made are intact. The checkout around it "
    "may have moved since — a review may have run its checks there, or the worktree "
    "may have been recreated."
)


def turn_cut_retry_message(role: Role) -> str:
    """What the attempt after a `max_turns` cut reads in place of the ordinary retry
    error (ADR-0004, seventh addendum). Three arms over `role`, the conservative one
    the default:

    - `role.executes` (a Developer, on its own subtask or the integration): commit
      before delivering — a delivery with nothing committed still spends turns, and so
      does one the presubmit refuses — so with the checks red, commit and deliver
      anyway, the gap named in `summary`. Safe to say plainly: `develop.committed`
      raises on a dirty tree, so the commit is not optional, and the presubmit's own
      refusals are bounded at two, so "deliver anyway" cannot loop.
    - An enumerated interview participant (`INTERVIEW_PARTICIPANT_SLUGS`): close this
      turn with what it has — the same instruction the interview's own `CLOSE` gives
      the interviewer when the rounds run out, now for the turn the SDK itself cut.
    - Everyone else, by default — `product_analyst_bug` is the role this arm exists
      for: do not deliver a verdict not yet reached; spend what is left on the reading
      that decides the delivery; if that still is not enough, say in the output what
      was left unread (`docs/product/role-failures.md`: "Un presupuesto agotado no se
      disfraza de entrega")."""
    if role.executes:
        action = (
            "Commit what you have before you deliver. A delivery with nothing "
            "committed still spends turns, and so does a delivery the presubmit "
            "refuses: if the checks are red, commit and deliver anyway, and name the "
            "gap in `summary`."
        )
    elif role.slug in INTERVIEW_PARTICIPANT_SLUGS:
        action = "Close this turn with what you have."
    else:
        action = (
            "Do not deliver a verdict you have not reached. Spend what is left on the "
            "reading that decides your delivery, and if it still is not enough, say in "
            "your own output what you could not read."
        )
    return f"{_TURN_CAP_FACT} {action}"


def system_note(text: str) -> str:
    """One line the system writes into a role's section, never the model's own words
    (glossary: Sección): every system-written line goes through this one function —
    `denial_note`'s, `cold_fallback_note`'s, and `turn_notice.notice_note`'s — so the
    invariant that the system's own remarks are composed by one helper is a fact about
    this module, not three independent copies of the same wrapping."""
    return f"_{text}_"


def denial_note(role: Role, count: int) -> str:
    """The trace a denied write leaves on the run's section (glossary: Negación): one
    line, system-written, only when the run had at least one denial — the same seam
    `cold_fallback_note` uses for a dropped session, so a run spent fighting the
    permission layer is legible without opening logs. `count` is the run's total, the
    delivering attempt's plus every recorded failure's, never a per-attempt figure
    (`run_role.run_role`'s own rule)."""
    plural = "" if count == 1 else "s"
    return system_note(f"The system denied {count} write attempt{plural}: {denial_for(role)}")


def deliveries_note(count: int) -> str:
    """The trace repeated calls to the delivery tool leave on the run's section
    (glossary: Emisión de entrega): one line, system-written, only when the run had
    more than one -- the same seam `denial_note`/`cold_fallback_note`/
    `turn_notice.notice_note` use, so a run that spent turns re-emitting an invalid
    shape (issue #32) is legible without opening logs. `count` is the run's total, the
    delivering attempt's plus every recorded failure's (`run_role._run_deliveries`'s
    own fold, the same shape `denial_note`'s `count` has): attempt 1 can spend several
    emissions on a shape the schema keeps rejecting and attempt 2 still deliver on its
    first try."""
    return system_note(
        f"the delivery tool was called {count} times over this run before it delivered"
    )


def cold_fallback_note() -> str:
    """The trace a dropped caller session leaves on the delivered path (ADR-0004, sixth
    addendum): system-written, at the same seam as the presubmit record, never asked of
    the model, so an expensive rework run can be explained without opening logs. The
    escalated path needs none: `Failure.cold_fallback` already shows in the frontmatter
    as the record is rendered."""
    return system_note(
        "The system asked to resume the previous session; the harness could not, and "
        "this attempt started cold with the full context instead."
    )


def task_branch(task_id: str) -> str:
    return f"agentic/{task_id}"


def subtask_branch(task_id: str, subtask_id: str) -> str:
    # A dot, not a slash: git cannot hold `agentic/GH-12` and `agentic/GH-12/api` at once.
    return f"{task_branch(task_id)}.{subtask_id}"


# Archivado (ADR-0011, sixth addendum): what `--fresh` does with a previous run's
# branches instead of building the next one on top of them. Naming and numbering are
# pure, so they are unit-tested with no git call; the adapter only lists, renames,
# commits and asks about the product's own ignore rules.


def archive_prefix(n: int, system_commit: str) -> str:
    """The prefix every branch of one archived run is renamed under: `run<N>`, the
    archive's own ordinal for this task, and the system's commit at archive time (or
    the literal `unknown`, when the document could not be read or parsed -- that
    literal decides nothing here, it is only ever a name)."""
    return f"archive/run{n}-{system_commit}"


def archive_ref(branch: str, n: int, system_commit: str) -> str:
    """The archived name for `branch`: the prefix, then the whole branch name kept
    intact (`agentic/<task>` or `agentic/<task>.<subtask>`), never shortened -- that is
    what lets `next_archive_run` tell a task branch from a subtask branch apart once
    both live under the same prefix."""
    return f"{archive_prefix(n, system_commit)}/{branch}"


def branches_of_task(names: Sequence[str], task_id: str) -> list[str]:
    """`names` (whatever a `Workspace` prefix verb answered) narrowed to this task's
    own: the task branch itself, or a subtask branch of it, and nothing a shared prefix
    only looks like. Compares whole segments -- after the id comes end of string or the
    dot `subtask_branch` defines -- never `startswith`: `branches("agentic/GH-1")` also
    answers `agentic/GH-12` in a repo that is at GH-88, and this is the one predicate
    every such caller goes through instead of filtering the prefix verb's answer by
    hand."""
    base = task_branch(task_id)
    dotted = f"{base}."
    return [name for name in names if name == base or name.startswith(dotted)]


# Both spellings `next_archive_run` reads off an archived ref: the one this task writes
# (`archive_ref`, `agentic/<task>` kept whole) and the one gym-saas GH-12's runs 2 and 3
# were archived under by hand, with a script run with `!` -- `archive/run2-04abd2c/GH-12`,
# `archive/run3-75de807/GH-12`, gym-saas refs, not this repo's (agentic-system has nothing
# under `archive/` yet). Both are `archive/run<N>-<sha>/`, then optionally `agentic/`,
# then the task id and, for a subtask branch, `.<subtask>`.
_ARCHIVED_REF = re.compile(r"^archive/run(\d+)-[^/]*/(?:agentic/)?(.+)$")


def _archived_runs(refs: Sequence[str], task_id: str) -> list[int]:
    """The `N` of every archived ref, of either spelling, that belongs to `task_id`:
    the same whole-segment comparison `branches_of_task` makes, so `GH-1` never reads
    `GH-12`'s archive as its own."""
    runs = []
    for ref in refs:
        match = _ARCHIVED_REF.match(ref)
        if not match:
            continue
        n, rest = match.groups()
        if rest == task_id or rest.startswith(f"{task_id}."):
            runs.append(int(n))
    return runs


def next_archive_run(refs: Sequence[str], live: Sequence[str], task_id: str) -> int | None:
    """The `N` this archive uses, from three cases of observable state alone -- no git
    call of its own, `refs` and `live` are whatever the adapter already listed:

    - the task branch is live: a fresh archive, one past the highest `N` this task has
      archived before (0 when it has none);
    - the task branch is absent but some subtask branch of this task is still live: an
      archive that renamed the task branch and was cut before it reached every subtask
      branch, so the retry reuses the highest `N` this task already has instead of
      opening a new one;
    - neither: nothing of this task is live, so there is nothing to archive.

    Renaming the task branch first is what makes the second case legible at all: it is
    the one state only an unfinished archive leaves behind."""
    live_of_task = branches_of_task(live, task_id)
    archived = _archived_runs(refs, task_id)
    if task_branch(task_id) in live_of_task:
        return max(archived, default=0) + 1
    if live_of_task:
        return max(archived) if archived else 1
    return None


def ready_subtasks(state: TaskState) -> list[str]:
    """Subtasks to build now: pending or returned, with every dependency approved."""
    subtasks = state.get("subtasks", {})
    return [
        sid
        for sid, sub in subtasks.items()
        if sub.get("status") in ("pending", "returned")
        and all(
            subtasks.get(dep, {}).get("status") == "approved" for dep in sub.get("depends_on", [])
        )
    ]


def delivered_subtasks(state: TaskState) -> list[str]:
    return [
        sid for sid, sub in state.get("subtasks", {}).items() if sub.get("status") == "delivered"
    ]


def dispatched_subtasks(state: TaskState) -> list[str]:
    """Subtasks the next wave runs: the ones to build, and the ones already delivered
    whose review never happened, which is where a run stopped by the cap resumes."""
    return [*ready_subtasks(state), *delivered_subtasks(state)]


def pipeline_entry(state: TaskState) -> str:
    """Where a branch of the fan-out starts for its subtask: a delivery already made is
    reviewed, never built again."""
    subtask = state.get("subtasks", {}).get(state["subtask_id"], {})
    return "review" if subtask.get("status") == "delivered" else "develop"


def all_approved(state: TaskState) -> bool:
    subtasks = state.get("subtasks", {})
    return bool(subtasks) and all(sub.get("status") == "approved" for sub in subtasks.values())


def needs_integration(state: TaskState) -> bool:
    """Several subtasks approved and no integration yet. The integration exists whenever
    there is more than one subtask, because the whole branch is reviewed either way
    (README.md, 4); whether a Developer runs on it is `integration_subtask`."""
    subtasks = state.get("subtasks", {})
    return INTEGRATION not in subtasks and len(subtasks) > 1 and all_approved(state)


def integration_subtask(brief: str = "", conflicts: Sequence[str] = ()) -> Subtask:
    """The integration as the fan-in adds it.

    `pending` when there is something for the integrator to do: a merge the system could
    not settle, or work the plan declared when it wrote `integration`. `delivered` when
    the incremental merge was the whole job (README.md, 13), so the branch goes straight
    to its review with no Developer paid for a merge already done. Either way the
    integration review reads the whole branch, which is the only review that does."""
    work = integration_work(brief, conflicts)
    return {
        "id": INTEGRATION,
        "title": "Integration",
        **work,
        "seams": [],
        "depends_on": [],
        "status": "pending" if work["conflicts"] or brief.strip() else "delivered",
    }


def integration_work(brief: str, conflicts: Sequence[str]) -> Subtask:
    """The part of the integration subtask a wave can change: what is left to do and
    which branches are still out. Recomputed on every wave, so an integration the review
    sent back is not still telling its Developer there was nothing to do."""
    listed = ", ".join(f"`{branch}`" for branch in conflicts)
    work = [
        line
        for line in (
            brief.strip(),
            f"Merge into the task branch what the system could not: {listed}." if listed else "",
        )
        if line
    ]
    return {
        "brief": "\n\n".join(work)
        or "Every subtask branch is already on the task branch; make the whole thing work.",
        "conflicts": list(conflicts),
    }


# The task cap (README.md, 3; ADR-0004, fourth addendum): a base for the definition and
# the plan, a share per unit of work once the plan says how many there are.


def work_units(state: TaskState) -> int:
    """The subtasks of the plan, plus the integration when there is more than one, counted
    before it exists: the cap has to cover it from the start. Plus one more, from the
    very start, on a feature that runs the design branch (ADR-0040, `design_applies`):
    five extra role runs land before the plan even exists, and the cap has to cover
    them too, whether or not the plan has been written yet. Plus a second design unit,
    once there is a plan, for PM's early final validation: three more role runs the
    cap has to cover only from the point it could actually become due, not before --
    there is nothing to validate without a plan yet."""
    real = [sid for sid in state.get("subtasks", {}) if sid != INTEGRATION]
    units = len(real) + (1 if len(real) > 1 else 0)
    if design_applies(state):
        units += 1
        if real:
            units += 1
    return units


def task_cap(state: TaskState, base: float, per_unit: float, ceiling: float) -> float:
    """What the run may spend: the base until there is a plan, then the base plus a share
    per unit, never over the ceiling."""
    return min(ceiling, base + per_unit * work_units(state))


def cap_reached(role_name: str, spent_usd: float, cap_usd: float) -> str:
    return f"{role_name} stopped by the task cap: {spent_usd:.2f} of {cap_usd:.2f} USD spent"


_CAP_PHRASE = " stopped by the task cap: "


def stopped_by_cap(state: TaskState) -> bool:
    """Whether `state`'s escalation is the task cap, the one `escalate.comment` breaks
    spending down by phase and proposes a new budget for (`returns_pair`'s own
    pattern): `escalation_class` decides it when the state carries one, the text
    match only as the fallback for a document written before that field existed.

    Gating on the class first, not the text alone, matters once a motive can carry up
    to 2000 characters of CLI and model prose (`motive`): a quoted `cap_reached` phrase
    inside a `failure`-class escalation's motive must not make the comment tell the
    human to raise the cap on a run that died of something else entirely."""
    if (cls := state.get("escalation_class")) is not None:
        return cls == "cap"
    return _CAP_PHRASE in (state.get("escalated") or "")


def suggested_budget(state: TaskState, per_unit: float) -> int:
    """The `--task-budget` an escalation by the cap proposes: what was spent plus one more
    unit of work, in whole dollars. A starting point for the human, not an estimate."""
    return math.ceil(state.get("cost_usd", 0.0) + per_unit)


PHASES = ("definition", "plan", "development", "review")


_FINAL_VALIDATION_SECTIONS = frozenset((PM_FINAL.name, UX_LEAD_FINAL.name, UI_LEAD_FINAL.name))


def phase_of(section: str) -> str:
    """The phase a run's section belongs to. PM's consultations during the plan write
    PM's section, so they count as definition. PM's early, UI-only final validation and
    the two leads' own final sections (ADR-0040) map to `review`, same as any other
    review: it runs after the integration review approves, never before it."""
    if section in _FINAL_VALIDATION_SECTIONS:
        return "review"
    if section.startswith(REVIEW_PREFIX):
        return "review"
    if section.startswith(f"{DEVELOPER.name}: "):
        return "development"
    if section in (TECH_LEAD.name, SOLUTION_INTERVIEW):
        return "plan"
    return "definition"


def spent_by_phase(state: TaskState) -> dict[str, float]:
    """The task's cost by phase, summing its runs by section; `other` is what the runs do
    not hold, the failed attempts that escalated, when there is any."""
    phases = dict.fromkeys(PHASES, 0.0)
    for run in state.get("runs", []):
        phases[phase_of(run.get("section", run["role"]))] += run["cost_usd"]
    other = state.get("cost_usd", 0.0) - sum(phases.values())
    if other >= 0.005:
        phases["other"] = other
    return phases


# Routing of the paths of README §2: PjM -> Product Analyst -> PM -> Tech Lead ->
# Developers -> Tech Lead -> delivery on a feature; the same without the definition
# stage on a platform enhancement; a diagnosis in place of the definition on a bug,
# which PM may discard instead of sending to the plan. `None` is the end of the run;
# the graph adapter maps names to its nodes and fans `pipeline` out per dispatched
# subtask.
#
# Every wave of Developers goes through `integrate`, the system's node between waves
# (ADR-0004, fifth addendum): the plan hands it the first wave, each wave hands it
# back what it built, and a return is a wave like any other.

# The first node of each path after PjM. It is also where PM's return goes: back to
# the Product Analyst of the path, whichever it was.
FIRST_NODE = {"feature": "define", "bug": "diagnose", "platform_enhancement": "plan"}


def after_classify(state: TaskState) -> str | None:
    """The fork between the paths of README §2. A feature goes through the definition
    stage; a bug through the diagnosis, the same pair without the interview (issue
    #65); a platform enhancement has no product behaviour to define and starts at the
    plan, the shortest of the three (issue #46)."""
    if state.get("escalated"):
        return None
    return FIRST_NODE.get(state.get("type", ""), "plan")


def after_define(state: TaskState) -> str | None:
    return None if state.get("escalated") else "validate"


def after_diagnose(state: TaskState) -> str | None:
    return None if state.get("escalated") else "validate"


def after_validate(state: TaskState) -> str | None:
    """PM's verdict routes the task: a return goes back to whoever wrote what PM
    validated, an approval to the plan -- through the design branch first, on a
    feature PM flagged `touches_ui` (ADR-0040, `design_step`) -- unless PM ruled the
    behaviour expected, which ends a bug discarded (README.md, 3; issue #65)."""
    if state.get("escalated"):
        return None
    status = state.get("status", "")
    if status == "returned":
        return FIRST_NODE.get(state.get("type", ""), "define")
    if status == "definition_ready":
        return design_step(state) or "plan"
    return {"discarded": "discard"}.get(status)


def after_ux_flow(state: TaskState) -> str | None:
    return None if state.get("escalated") else (design_step(state) or "plan")


def after_ux_walk(state: TaskState) -> str | None:
    return None if state.get("escalated") else (design_step(state) or "plan")


def after_ui_screens(state: TaskState) -> str | None:
    return None if state.get("escalated") else (design_step(state) or "plan")


def after_design_review(state: TaskState) -> str | None:
    return None if state.get("escalated") else (design_step(state) or "plan")


def after_consolidate(state: TaskState) -> str | None:
    """PM's consolidation routes like PM's own validation does (`after_validate`): a
    return goes back to Product Analyst, under `PAIR_PM_PA`, like any other PRD
    return; an approval falls through `design_step` and on to `plan` once every design
    step is satisfied (ADR-0040)."""
    if state.get("escalated"):
        return None
    if state.get("status") == "returned":
        return FIRST_NODE.get(state.get("type", ""), "define")
    return design_step(state) or "plan"


def after_plan(state: TaskState) -> str | None:
    return None if state.get("escalated") else "integrate"


def after_develop(state: TaskState) -> str | None:
    """Inside a branch of the fan-out. An escalation ends the branch and nothing else:
    who tells the human is `integrate`, once, after every branch came back."""
    return None if state.get("escalated") else "review"


def final_validation_due(state: TaskState) -> bool:
    """Whether PM's early, UI-only final validation (ADR-0040) still has to run before
    `deliver`: the design branch applies to this task, every subtask is approved, and no
    approving `PM: final validation` run is newer than the latest approving review of
    `integration` -- or of the single subtask, when there is no integration. `False` for
    every task outside the design branch, byte for byte as today (`design_applies`).

    "Approving", for both sides, is read the way `_lead_satisfied` already reads a
    lead's own verdict: `all_approved` already guarantees the target's *latest* review
    was an approval, so that review's own run index is enough; a PM final-validation run
    counts as the live approval over it only while `_pair_returns(PAIR_PM_TECH_LEAD)`
    has not yet caught up with how many times it ran -- the same return-matches-run
    counting `_lead_satisfied` uses, not a verdict stored per run."""
    if not design_applies(state):
        return False
    if not all_approved(state):
        return False
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks), None)
    if target is None:
        return False
    review_idx = _latest_index(state, review_section(target))
    if review_idx is None:
        return True
    final_idx = _latest_index(state, PM_FINAL.name)
    if final_idx is None or final_idx < review_idx:
        return True
    still_approving = _pair_returns(state, PAIR_PM_TECH_LEAD.key) < _run_count(state, PM_FINAL.name)
    return not still_approving


def after_integrate(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    if dispatched_subtasks(state):
        return "pipeline"
    if not all_approved(state):
        return None
    return "final_validation" if final_validation_due(state) else "deliver"


def after_final_validation(state: TaskState) -> str | None:
    """Always back to the fan-in (ADR-0040): on `approved`, `final_validation_due` now
    reads `False` and `after_integrate` falls through to `deliver`; on `returned`, the
    reopened subtask is exactly what `dispatched_subtasks` picks up, and the fan-in
    sends it back to its Developer like any other wave. No new predicate decides
    between the two -- `after_integrate` already does, from the same state."""
    return None if state.get("escalated") else "integrate"


def after_deliver(state: TaskState) -> str | None:
    return None


def after_discard(state: TaskState) -> str | None:
    return None


def run_ended(status: Status) -> bool:
    """Whether the Corrida (the whole task, ADR-0003) ended: delivered
    (`ready_for_review`) or discarded (issue #65). Every other status, including a
    pause on escalation, leaves the Corrida resumable — the checkpoint allows it and
    the system cannot know whether anyone will — so `TaskRun.finish` (ADR-0008,
    2026-09-18 second addendum) does not emit its task gauges for it: a task paused
    twice would otherwise leave two samples of its accumulated total. `"crashed"` is
    not a `Status`: the CLI fabricates it only for its own telemetry when a process
    dies mid-run, so it cannot appear in this predicate's own type."""
    return status in ("ready_for_review", "discarded")


# Pause and resume (ADR-0003): an escalation pauses the task; the human's answer resumes
# it where it stopped. The state says where that is, so the same rules serve a paused
# checkpoint and a state seeded from the document.


def resumed_status(state: TaskState) -> Status:
    """The status the task had before the escalation overwrote it, derived from what the
    state holds, since the document keeps only `waiting_on_human` too."""
    if state.get("pull_request"):
        return "ready_for_review"
    if state.get("status") == "discarded":
        # Nothing overwrote it: a discard is an end, not a pause, and the runs it holds
        # (a diagnosis, PM's ruling) would otherwise read as a definition ready to plan.
        return "discarded"
    subtasks = state.get("subtasks", {})
    if subtasks:
        started = any(sub.get("status") != "pending" for sub in subtasks.values())
        return "developing" if started else "planned"
    if returns_pair(state) == PAIR_PM_PA.key:
        return "returned"
    runs = state.get("runs", [])
    written = sum(1 for r in runs if _wrote_own_section(r, PRODUCT_ANALYST.name))
    validated = sum(1 for r in runs if _wrote_own_section(r, PM.name))
    # By pair key, never by `category` alone: a lead's own return (`PAIR_UX_LEAD`,
    # `PAIR_UI_LEAD`) shares the `definition` category with PM's PRD returns, and
    # only the pair tells them apart (ADR-0040). An entry written before `pair` existed
    # falls back to `PAIR_PM_PA`'s own key, the only pair there was then.
    history = sum(
        1
        for h in state.get("change_history", [])
        if h["category"] == PRD_RETURN and h.get("pair", PAIR_PM_PA.key) == PAIR_PM_PA.key
    )
    # A document from before the history has only the counter, which a resume resets.
    returned = max(history, state.get("changes_requested", {}).get(PAIR_PM_PA.key, 0))
    if validated > returned:
        return "definition_ready"
    if written > validated:
        return "defined"
    return "returned" if written else "open"


def _wrote_own_section(run: RoleRun, role_name: str) -> bool:
    """A run of the role writing its own section: a Product Analyst's definition, a PM
    verdict or consultation, never their turns in an interview."""
    return run["role"] == role_name and run.get("section", run["role"]) == role_name


_DECISION_HEADING = re.compile(r"^### Decision ", re.M)


def human_section(state: TaskState, reason: str | None, decision: str) -> str:
    """The `Human` section with one more decision: every answer stays, numbered, after
    the escalation it answers, paired with the questions it answers when the escalation
    carried any (`state["questions_for_human"]`, read before a resume clears it)."""
    previous = state.get("sections", {}).get(HUMAN, "")
    number = len(_DECISION_HEADING.findall(previous)) + 1
    escalation = f"Escalated: {reason}\n\n" if reason else ""
    questions = state.get("questions_for_human") or []
    answered = ""
    if questions:
        listed = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
        answered = demote(f"**Questions this answers:**\n\n{listed}") + "\n\n"
    entry = f"### Decision {number}\n\n{escalation}{answered}{demote(decision.strip())}"
    return f"{previous}\n\n{entry}".strip()


def resumed(state: TaskState, decision: str | None) -> dict[str, Any]:
    """The patch that resumes a paused task: no escalation, the status it had, the pair
    that hit its limit back at zero, and the human's decision as a section the roles read.

    Nothing about the integration: a resumed task goes through the fan-in, which adds it
    from the merges it just did, and only that one knows what conflicted. Returns a
    plain patch, not a full `TaskState` (see `escalation`)."""
    reason = state.get("escalated")
    patch: dict[str, Any] = {
        "escalated": None,
        "escalation_class": None,
        "status": resumed_status(state),
        "questions_for_human": [],
        # A fresh epoch (issue #137): `rules.auto_resumable` reads only the entries
        # after the last one of this kind, so a pause the human just resolved never
        # counts toward what the poller may retry on its own next time.
        "pause_log": [ResumedPauseEntry(kind="resumed")],
    }
    if pair := returns_pair(state):
        patch["changes_requested"] = {pair: 0}
    if decision and decision.strip():
        patch["sections"] = {HUMAN: human_section(state, reason, decision)}
    return patch


def resume_target(state: TaskState) -> str | None:
    """Where a resumed task goes next, from the same facts the routing reads."""
    if state.get("escalated") or state.get("pull_request"):
        return None
    if state.get("subtasks"):
        # Once there is a plan every road goes through the fan-in, which decides the
        # wave from the same facts: nothing here has to know about waves. Nothing to
        # dispatch and not everything approved is a plan that cannot move (a
        # `depends_on` on a subtask that does not exist, a cycle): there is no wave to
        # resume into, and the caller says so instead of taking the task out of the
        # queue to run nothing.
        if dispatched_subtasks(state) or all_approved(state):
            return "integrate"
        return None
    status = state.get("status", "open")
    if status == "open":
        # The same fork as `after_classify`, plus the road back to PjM for a task that
        # paused before it had a type: only PjM turns the human's answer into one.
        return FIRST_NODE.get(state.get("type", ""), "classify")
    if status == "returned":
        return FIRST_NODE.get(state.get("type", ""), "define")
    if status == "definition_ready":
        # Through the design branch first, on a feature PM flagged `touches_ui`
        # (ADR-0040): the same one function `after_validate` and every design node's
        # own `after_*` already call, so a resume lands exactly where the live routing
        # would have, however many lead returns or PRD versions came before the pause.
        return design_step(state) or "plan"
    # `discarded` is an end like `pull_request`: nothing left to run.
    return {"defined": "validate"}.get(status)


# The poller (issue #137, README.md, 14; ADR-0003's own forward reference: "the poller
# of stage 5 will do the search"). First slice: retake a pause an external API cut
# caused, close a task whose pull request merged, and launch the next unblocked issue,
# never past the machine's own concurrency cap. Every predicate here is pure: the clock
# and every fact it decides over arrive as arguments (README.md, 14), never a port, a
# `subprocess` or a `Path`.

# The three `FailureReason`s a pause never needed a human for (ADR-0029, extended by
# issue #137's own `network`): the account's own quota, the provider's own server, or
# the network down. `rules.auto_resumable` is the only reader of this tuple; the
# scorecard's own exclusion (`core.measurement.domain.scorecard.EXTERNAL_FAILURE_
# REASONS`) is a separate copy of the same three literals -- hexagonal keeps
# `core.process.domain` from importing `core.measurement`, so nothing here imports it.
EXTERNAL_REASONS: tuple[FailureReason, ...] = ("account_limit", "upstream", "network")

# Minutes before each of the poller's own automatic attempts, first to last (ADR-0029's
# addendum, issue #137): never read off an API's own "resets 7:30pm" prose. Indexed by
# how many automatic attempts already happened (`prior`), capped by `rules.
# auto_resume_limit`'s own 1 or 6.
AUTO_RESUME_SCHEDULE: tuple[int, ...] = (15, 30, 60, 120, 120, 120)

# Every interview's own section (`Interview.section`), computed once: what `rules.
# auto_resume_limit` compares a failure's own `section` against, never a hand-kept copy
# that could drift from `roles.INTERVIEWS` (the same worry `INTERVIEW_PARTICIPANT_
# SLUGS` already names above).
_INTERVIEW_SECTIONS = frozenset(spec.section for spec in INTERVIEWS)


def logged(patch: dict[str, Any], failures: Sequence[Failure] = ()) -> dict[str, Any]:
    """Every escalation composes with this (issue #137): the one place a `pause_log`
    entry is appended, so `rules.escalation` and `rules.unmergeable` stay exactly as
    they are and no call site ever merges a `pause_log` key by hand with `|`. Reads
    `patch["escalation_class"]` -- already written by `rules.escalation`/`rules.
    unmergeable`, never recomputed here -- for the `"escalation"` entry, and turns
    `failures` (`run_role.escalation`'s own `exc.failures`, `()` for `rules.
    unmergeable`'s own callers, which never ran a role) into one `"failure"` entry
    each, in the order they failed, before the `"escalation"` entry that followed them.
    A legacy `Failure` with no `reason` logs as `"other"`: a motive this ambiguous
    about its own cause must never read as `account_limit` and ease into an automatic
    retry."""
    entries: list[PauseEntry] = [
        FailurePauseEntry(
            kind="failure", value=f.get("reason", "other"), section=f.get("section", "")
        )
        for f in failures
    ]
    if (cls := patch.get("escalation_class")) is not None:
        entries.append(EscalationPauseEntry(kind="escalation", value=cls))
    return {**patch, "pause_log": entries}


def _current_epoch(state: TaskState) -> list[PauseEntry]:
    """The entries `rules.auto_resumable`/`rules.auto_resume_limit` read: everything
    after the last `"resumed"` entry (a human's decision, or the poller's own retake),
    or the whole log when the task has never been resumed."""
    log = state.get("pause_log", [])
    last_resumed = -1
    for i, entry in enumerate(log):
        if entry["kind"] == "resumed":
            last_resumed = i
    return log[last_resumed + 1 :]


def auto_resumable(state: TaskState) -> FailureReason | None:
    """Whether the poller may retake this pause on its own, and with which reason
    (issue #137): the only source of this decision, reading nothing else -- not
    `escalation_class`, not `escalated`'s own text, not any `failures[].error`. Three
    things must hold of the current epoch (`_current_epoch`) together: at least one
    `"failure"` entry (a role actually broke; a pair at its return limit or a bare
    `"escalation"` entry some other site composed alone is not this), every
    `"escalation"` entry in it reads `"failure"` (an epoch that also escalated as
    `"cap"` or `"returns"` is not purely an external cut, whatever its failures were),
    and every `"failure"` entry's own reason is in `EXTERNAL_REASONS`. The reason
    returned is the most recent failure's -- what still blocks this pause right now."""
    failures: list[FailurePauseEntry] = []
    escalations: list[EscalationPauseEntry] = []
    for entry in _current_epoch(state):
        if entry["kind"] == "failure":
            failures.append(entry)
        elif entry["kind"] == "escalation":
            escalations.append(entry)
    if not failures:
        return None
    if not all(e["value"] == "failure" for e in escalations):
        return None
    if not all(f["value"] in EXTERNAL_REASONS for f in failures):
        return None
    return failures[-1]["value"]


def auto_resume_limit(state: TaskState) -> int:
    """How many automatic attempts (`AUTO_RESUME_SCHEDULE`'s own length, at most) this
    pause's current epoch gets: one when any of its `"failure"` entries broke inside an
    interview's own section (`_INTERVIEW_SECTIONS`) -- a retake already gives an
    interview two rounds of margin (`INTERVIEW_RETAKE_ROUNDS`), so a long automatic
    chase on top of that is not this task's own risk to take -- six otherwise, the
    whole calendar."""
    in_interview = False
    for entry in _current_epoch(state):
        if entry["kind"] == "failure" and entry["section"] in _INTERVIEW_SECTIONS:
            in_interview = True
    return 1 if in_interview else len(AUTO_RESUME_SCHEDULE)


def _schedule_exhausted(prior: int, limit: int) -> bool:
    """Whether `prior` automatic attempts have already used up every slot
    `AUTO_RESUME_SCHEDULE[:limit]` has: `next_attempt_at`'s own exhaustion check,
    factored out so `poll_plan` can tell it apart from "not due yet" -- both of those
    read as `None` from `next_attempt_at` alone, and only the first (a calendar that
    ran out, not one still ticking) must stop blocking a launch."""
    return prior >= len(AUTO_RESUME_SCHEDULE[:limit])


def next_attempt_at(since: datetime, prior: int, limit: int, now: datetime) -> datetime | None:
    """The next automatic attempt's own time, but only once it is due (issue #137):
    `None` both when the calendar is spent (`_schedule_exhausted`, no slot left to
    schedule) and when the next slot has not arrived yet (`now` before it) -- the two
    reasons a caller does not act on this pause read alike, one check instead of two.
    `since` is the moment the wait is measured from: the last `Escalated:` comment's
    own time for a retake, the last launch attempt's own time for a launch (Tech Lead
    plan); `prior` is how many automatic attempts already happened since then. Never
    reads a clock of its own (README.md, 14): `now` arrives as an argument, like
    `since`."""
    if _schedule_exhausted(prior, limit):
        return None
    target = since + timedelta(minutes=AUTO_RESUME_SCHEDULE[prior])
    return target if target <= now else None


# The two `stateReason`s GitHub closes an issue with (Tech Lead plan): `NOT_PLANNED`
# resolves a blocker on its own -- the blocking issue was dropped, not built -- and
# `COMPLETED` resolves one only together with a merge the checkout can already see.
_BLOCKER_NOT_PLANNED = "NOT_PLANNED"
_BLOCKER_COMPLETED = "COMPLETED"


def blocker_resolved(state_reason: str | None, merged_into_checkout: bool) -> bool:
    """Whether one blocking issue no longer blocks a launch (Tech Lead plan): `True`
    for `NOT_PLANNED` outright, `True` for `COMPLETED` only when `merged_into_checkout`
    already says its pull request's merge commit is an ancestor of `HEAD` --
    `Workspace.is_ancestor`'s own call, made by the caller, never by this function.
    Anything else -- `OPEN`, an unknown or missing `stateReason`, a `COMPLETED` issue
    with no pull request found or whose merge commit the checkout cannot see yet -- is
    unresolved: on doubt, the launch predicate does not launch (Tech Lead plan)."""
    if state_reason == _BLOCKER_NOT_PLANNED:
        return True
    if state_reason == _BLOCKER_COMPLETED:
        return merged_into_checkout
    return False


# GH-142 (apostrophe #8): the poller launched an issue whose only blocker lived in
# the body's own `Blocked by:` line, because an empty native list read as "no
# blockers" instead of falling back to the body. GitHub answers `blockedBy.nodes: []`
# for an issue with no native dependency at all -- the same shape as one that
# genuinely has none, so the two are not told apart; both fall back to the body,
# exactly like a native field GitHub never exposed (`None`). `blocker_numbers` is the
# one place this product branches on `blocked_by` being `None` or `()`: no other site
# does, `poll._blocker_numbers` included.

_BLOCKED_BY_LINE = re.compile(r"^Blocked by:\s*(#\d+(?:\s*,\s*#\d+)*)\s*$", re.IGNORECASE)


def blockers_from_body(body: str) -> tuple[int, ...]:
    """The issue's own `Blocked by: #N[, #M]` line (the issue-tracker doc's fallback,
    ADR-0034): the first non-empty line of the body, matched whole -- never a loose
    search anywhere in the text, which could catch an unrelated mention in the
    description. Empty when the body has no such line, or no body at all."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _BLOCKED_BY_LINE.match(stripped)
        return tuple(int(n) for n in re.findall(r"\d+", match.group(1))) if match else ()
    return ()


def blocker_numbers(native: tuple[int, ...] | None, body: Callable[[], str]) -> tuple[int, ...]:
    """Which issue numbers block a launch (GH-142; ADR-0039): `native` wins whenever
    it is non-empty, read straight off with no call to `body` at all. `None` (no
    native field at all) and `()` (`blockedBy.nodes: []`) read the same way: both fall
    back to `blockers_from_body(body())`.

    `body` arrives as a thunk, not the string itself, for two reasons: a caller with
    non-empty native blockers never pays for reading the issue body at all -- an
    extra `gh` call `poll._blocker_numbers` would otherwise always make -- and the
    read itself stays the caller's own I/O, never this function's (this module has
    none, by design)."""
    if native:
        return native
    return blockers_from_body(body())


PollActionKind = Literal["close", "resume", "launch"]


@dataclass(frozen=True)
class PollAction:
    """One action `rules.poll_plan` orders into this round: which issue, and whether
    the poller's child process is `agentic close|resume|run` (the kind names the verb,
    the caller knows the mapping -- domain never names a CLI command)."""

    kind: PollActionKind
    issue: int


@dataclass(frozen=True)
class ResumeCandidate:
    """One paused task `rules.auto_resumable` already found eligible, as `rules.
    poll_plan` needs it: `reason` is what `auto_resumable` returned; `since`/`prior`/
    `limit` are `rules.next_attempt_at`'s own arguments (the last `Escalated:`
    comment's time, and how many `Auto-resumed:` comments followed it since the last
    non-system one -- Tech Lead plan)."""

    issue: int
    reason: FailureReason
    since: datetime
    prior: int
    limit: int


@dataclass(frozen=True)
class LaunchCandidate:
    """One open, unlaunched `agentic` issue, as `rules.poll_plan` needs it: `blocked`
    is whether any of its dependencies is still unresolved (`blocker_resolved`, folded
    by the caller over every blocker); `since`/`prior`/`limit` are `rules.
    next_attempt_at`'s own arguments for this issue's own launch attempts, `since`
    `None` for one never attempted, which is always due."""

    issue: int
    blocked: bool
    since: datetime | None
    prior: int
    limit: int


def poll_plan(
    *,
    closes: Sequence[int],
    resumes: Sequence[ResumeCandidate],
    launches: Sequence[LaunchCandidate],
    available_slots: int,
    now: datetime,
) -> list[PollAction]:
    """This round's actions, in the order the Tech Lead plan fixes: every close first
    (merging freed the slot it used, if any, so closing never spends one); the due
    retakes next, lowest issue first, while `available_slots` lasts; at most one
    launch, the lowest-numbered issue with every blocker resolved and its own wait (if
    any) elapsed -- and only when no eligible pause anywhere in the product is still
    waiting out its own calendar (its own next slot has not arrived yet, but one is
    still scheduled), whether or not a slot is free for it. A pause whose calendar is
    spent (`_schedule_exhausted`) does not count as "still waiting": it needs a human
    now, same as any other escalation, and must not freeze every launch in the product
    until one acts. `available_slots` already reflects the machine-wide cap
    (`AGENTIC_MAX_CONCURRENT_RUNS`) minus every run alive right now, counted by the
    caller; this function never reads the registry itself."""
    actions = [PollAction("close", issue) for issue in sorted(closes)]

    due_resumes = sorted(
        (r for r in resumes if next_attempt_at(r.since, r.prior, r.limit, now) is not None),
        key=lambda r: r.issue,
    )
    taken = due_resumes[: max(available_slots, 0)]
    actions.extend(PollAction("resume", r.issue) for r in taken)

    slots_left = max(available_slots - len(taken), 0)
    some_resume_still_waiting = any(
        not _schedule_exhausted(r.prior, r.limit)
        and next_attempt_at(r.since, r.prior, r.limit, now) is None
        for r in resumes
    )
    if slots_left > 0 and not some_resume_still_waiting:
        ready = sorted(
            (
                c
                for c in launches
                if not c.blocked
                and (c.since is None or next_attempt_at(c.since, c.prior, c.limit, now) is not None)
            ),
            key=lambda c: c.issue,
        )
        if ready:
            actions.append(PollAction("launch", ready[0].issue))
    return actions
