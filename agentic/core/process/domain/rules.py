"""Rules of the process that the use cases apply (README.md, 3 and 6).

Pure functions over the task state, so each one is a unit test and not a graph run.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NamedTuple, Protocol, assert_never, get_args

from . import adr_numbers, ci, presubmit
from .copy import copy_location_parts
from .denials import denial_for
from .document import HUMAN, demote
from .reaping import ReapedProcess, ReapOutcome
from .roles import (
    DEVELOPER,
    FINDINGS,
    INTERVIEWS,
    PAIR_PM_PA,
    PAIR_PM_TECH_LEAD,
    PAIR_UI_LEAD,
    PAIR_UX_LEAD,
    PJM,
    PM,
    PM_CONSOLIDATOR,
    PM_FINAL,
    PRODUCT_ANALYST,
    QA_PERFORMANCE,
    SOLUTION_INTERVIEW,
    TECH_LEAD,
    TECH_WRITER,
    TECH_WRITER_REVIEW,
    UI_DESIGNER,
    UI_LEAD,
    UI_LEAD_FINAL,
    UX_DESIGNER,
    UX_LEAD,
    UX_LEAD_FINAL,
    Interview,
    Pair,
    Role,
    pair_for,
)
from .schemas import FitDecision, TaskType
from .scratch import INODE_FLOOR, SpaceReading
from .task import (
    INTEGRATION,
    WAITING_ON_HUMAN,
    Carried,
    ChangeCategory,
    ChangesRequested,
    DependencyState,
    DesignItemRecord,
    EscalationClass,
    EscalationPatch,
    EscalationPauseEntry,
    EstimateRevision,
    Failure,
    FailurePauseEntry,
    FailureReason,
    FindingCategory,
    FindingRecord,
    FinishedPatch,
    FullChecksRecord,
    LapsePauseEntry,
    MergeOutcome,
    PartRecord,
    PauseEntry,
    PullRequest,
    ResumedPauseEntry,
    ReturnedBy,
    ReviewKind,
    RoleRun,
    SettledRow,
    Status,
    Subtask,
    TaskInput,
    TaskState,
    TrackerPosted,
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
    the role: a field filled badly must not cost a retry, nor escalate the task. A finding
    it lowers carries `reported_severity`, what the reviewer said, for the record."""
    settled, lowered = [], []
    for item in findings:
        if item["severity"] == "blocking" and not item["evidence"].strip():
            item = {**item, "severity": "observation", "reported_severity": "blocking"}
            lowered.append(f"`{item['location']}` blocking lowered to observation: no evidence")
        elif item["severity"] == "blocking" and item["scope"] != "subtask":
            item = {**item, "severity": "important", "reported_severity": "blocking"}
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
    Everything else enters `open`; only a reviewer saying so closes it. `reported_severity`
    is written only when the system lowered the severity, never otherwise."""
    return [_record(subtask_id, version, item) for item in findings]


def _record(subtask_id: str, version: int, item: dict[str, Any]) -> FindingRecord:
    record: FindingRecord = {
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
    if "reported_severity" in item:
        record["reported_severity"] = item["reported_severity"]
    return record


# Where a `full_checks` finding row sits: `full_checks:<check>`, never a file of the diff.
FULL_CHECKS_PREFIX = "full_checks:"


def findings_rows(state: TaskState) -> list[FindingRecord]:
    """The table: one row per `location` + `claim`, in the order it was first raised, and
    `subtask` on a row names every subtask whose review raised it.

    Two reviewer runs do not write the same string for the same objection, so the key is
    normalised the way the interview normalises the human's questions. The first verdict
    is the one that stands: a later review re-raising what an earlier one dismissed does
    not reopen the argument.

    **A row closes only because a reviewer said it did**, with one exception: an approval
    (`settled_on_approval`, recorded in `approval_settled`) closes the blocking records of
    its own subtask that it does not raise again. Every pass after the first is
    incremental (ADR-0015) and is told not to look again at what it already approved, so
    a pass that stops mentioning a row usually means it did not look. Closing on silence
    would make the first return of any subtask wipe that subtask's carried-forward
    remarks, which is the exact class of thing issue #17 is about.

    The status, in order: a row whose first record is `dismissed` stays so; the
    reviewer's `settled_findings` closes an open row by location, first and for good;
    and an open row an approval covers closes only when *every* open record of its key is
    covered, record by record (`_covered`), so a later regression, a sister's re-raise or
    an `important` at the same key keeps it open, whatever order they were appended in."""
    # A reviewer's word never closes a `full_checks:` row: only `full_checks_settles` does.
    settled = {
        norm
        for location in state.get("settled_findings", [])
        if not (norm := _norm(location)).startswith(FULL_CHECKS_PREFIX)
    }
    approved = _approved_versions(state)
    qa_approved = qa_approved_version(state)
    green = full_checks_green_version(state)
    rows: dict[tuple[str, str], FindingRecord] = {}
    open_records: dict[tuple[str, str], list[FindingRecord]] = {}
    for record in state.get("findings", []):
        key = (_norm(record["location"]), _norm(record["claim"]))
        if record["status"] == "open":
            open_records.setdefault(key, []).append(record)
        if (row := rows.get(key)) is None:
            rows[key] = record
        elif record["subtask"] not in row["subtask"].split(", "):
            rows[key] = {**row, "subtask": f"{row['subtask']}, {record['subtask']}"}
    return [
        {**row, "status": "fixed"}
        if row["status"] == "open"
        and (
            _norm(row["location"]) in settled
            or all(
                _covered(record, key, approved)
                or qa_settles(record, qa_approved)
                or full_checks_settles(record, green)
                for record in open_records[key]
            )
        )
        else row
        for key, row in rows.items()
    ]


def qa_approved_version(state: TaskState) -> int:
    """The version of the latest approving QA Performance run, `0` when there is none. The
    only reader of that fact for the fold: `qa_settles` is the writer's one decision."""
    return max(
        (
            run.get("version", 1)
            for run in state.get("runs", [])
            if run.get("section", run["role"]) == QA_PERFORMANCE.name
            and run.get("perf_verdict") == "approved"
        ),
        default=0,
    )


def qa_settles(record: FindingRecord, approved_version: int) -> bool:
    """The named fold writer of QA Performance (GH-318, ADR-0060, ADR-0015 addendum): an
    approving run settles every record at its own location, `QA Performance`, from a version
    below its own -- whatever the severity or scope, since its rows are plan-scoped and the
    system built them from numbers the same run has just measured again. A later return
    writes records at a higher version, so it reopens the row. `settled_on_approval` does
    not know these rows and is unchanged."""
    return (
        _norm(record["location"]) == _norm(QA_PERFORMANCE.name)
        and record["version"] < approved_version
    )


def full_checks_green_version(state: TaskState) -> int:
    """The version of the latest `full_checks` run when it passed, `0` otherwise (no run, or
    the latest one red). The only reader of that fact for the fold: `full_checks_settles` is
    the writer's one decision."""
    recorded = state.get("full_checks")
    return recorded["version"] if recorded and recorded["passed"] else 0


def full_checks_settles(record: FindingRecord, green_version: int) -> bool:
    """The named fold writer of the full-tier run (GH-358, ADR-0015 addendum): a passed run
    settles every record at a `full_checks:<check>` location from a version below its own.
    Only a green run does -- a review approval never closes one -- and a later red run
    writes records at a higher version and drops `green_version` back to `0`, so the rows
    reopen. `settled_on_approval` does not know these rows and is unchanged."""
    return _norm(record["location"]).startswith(FULL_CHECKS_PREFIX) and (
        record["version"] < green_version
    )


def _approved_versions(state: TaskState) -> dict[tuple[str, tuple[str, str]], int]:
    """The highest approving version per (subtask, normalised key): a record below it is
    covered, which is "some `SettledRow` covers it" without scanning them per record."""
    approved: dict[tuple[str, tuple[str, str]], int] = {}
    for settled in state.get("approval_settled", []):
        key = (settled["subtask"], (_norm(settled["location"]), _norm(settled["claim"])))
        approved[key] = max(approved.get(key, 0), settled["version"])
    return approved


def _covered(
    record: FindingRecord,
    key: tuple[str, str],
    approved: dict[tuple[str, tuple[str, str]], int],
) -> bool:
    """Whether an approval settled this very record: its own subtask's, `blocking`, scope
    `subtask`, and from a version below the approving one. Anything else, an `important`
    included, is never covered."""
    if (version := approved.get((record["subtask"], key))) is None:
        return False
    return (
        record["severity"] == "blocking"
        and record["scope"] == "subtask"
        and record["version"] < version
    )


def settled_on_approval(
    state: TaskState, subtask_id: str, version: int, findings: list[dict[str, Any]]
) -> list[SettledRow]:
    """What an approval of `subtask_id` at `version` settles: one `SettledRow` per key
    with an earlier `blocking` record of this subtask and scope `subtask`, unless
    `findings`, the approving review's own (after `lower_unbacked_blocks`), raise the key
    again at any severity.

    Approval derives from "no blocking exists" (ADR-0016), so it is a verdict on this
    subtask's own earlier blocking records and nothing else: never an `important`, a
    `plan` row or a sister's record. Reads the raw records, never `findings_rows`, so the
    fold and this decision cannot feed each other."""
    raised = {(_norm(item["location"]), _norm(item["claim"])) for item in findings}
    rows: dict[tuple[str, str], SettledRow] = {}
    for record in state.get("findings", []):
        key = (_norm(record["location"]), _norm(record["claim"]))
        if (
            record["subtask"] == subtask_id
            and record["scope"] == "subtask"
            and record["severity"] == "blocking"
            and record["version"] < version
            and key not in raised
            and key not in rows
        ):
            rows[key] = {
                "subtask": subtask_id,
                "location": record["location"],
                "claim": record["claim"],
                "version": version,
            }
    return list(rows.values())


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


def row_owners(state: TaskState, row: FindingRecord) -> tuple[str, ...]:
    """The subtasks of the document whose review raised `row`, in the order the table first
    raised them. A name that is not a subtask of the document is no owner."""
    known = state.get("subtasks", {})
    return tuple(name for name in row["subtask"].split(", ") if name in known)


def reviewed_unit_name(state: TaskState) -> str | None:
    """The name of the subtask `reviewed_unit` returns, `None` when it returns none."""
    subtasks = state.get("subtasks", {})
    if INTEGRATION in subtasks:
        return INTEGRATION
    return next(iter(subtasks)) if len(subtasks) == 1 else None


@dataclass(frozen=True)
class ImportantPartition:
    """The open `important` rows, by who settles them: `owned` maps a unit to the
    `scope == "subtask"` rows its review raised (a row whose names are no subtask goes to the
    reviewed unit), `plan` holds the `scope == "plan"` rows, which reopen the reviewed unit,
    and `outside` the `scope == "outside"` rows, which go to the task's follow-up issue."""

    owned: Mapping[str, tuple[FindingRecord, ...]]
    plan: tuple[FindingRecord, ...]
    outside: tuple[FindingRecord, ...]


def important_partition(state: TaskState) -> ImportantPartition:
    """The one place the open `important` rows are split by `scope`; no other module
    filters them. A `subtask` row with no owner and no reviewed unit to fall to is counted
    with `plan`, which also reopens the reviewed unit (an empty `units`)."""
    owned: dict[str, list[FindingRecord]] = {}
    plan: list[FindingRecord] = []
    outside: list[FindingRecord] = []
    fallback = reviewed_unit_name(state)
    for row in open_findings(state):
        if row["severity"] != "important":
            continue
        if row["scope"] == "outside":
            outside.append(row)
        elif row["scope"] == "plan":
            plan.append(row)
        elif owners := row_owners(state, row) or ((fallback,) if fallback else ()):
            for name in owners:
                owned.setdefault(name, []).append(row)
        else:
            plan.append(row)
    return ImportantPartition(
        {name: tuple(rows) for name, rows in owned.items()}, tuple(plan), tuple(outside)
    )


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
        f"| {row['subtask']} | `{_shown_location(row['location'])}` | {_cell(row['claim'])} |"
        for row in rows
    ]
    return "\n".join([head, *lines]) if lines else "None open."


def _shown_location(location: str) -> str:
    """A copy row is shown by the place it points at; its identifier is for the Tech
    Writer's review alone."""
    parts = copy_location_parts(location)
    return location if parts is None else parts[2]


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def findings_section(state: TaskState) -> dict[str, str]:
    """The `sections` patch with the table as the roles read it: what is still open.

    Only the open rows, because this section is what lands whole in the next prompt; the
    raw record of every finding of every pass is the state, and the document's
    frontmatter from it. Written at the fan-in, the one place that sees every branch."""
    return {FINDINGS: findings_markdown(open_findings(state))}


def invariants_markdown(state: TaskState) -> str:
    """The plan's invariants as a prompt shows them, numbered in the order the state holds
    them: the Developer's coverage map (`domain/coverage.py`) names them `invariant N`,
    and both reviewers read the same numbers from here."""
    lines = state.get("invariants", [])
    return "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1)) or "- none"


def finished(status: Literal["discarded", "split", "tracker_only"], at: str) -> FinishedPatch:
    """The patch that ends a task with no delivery: its `status` and `finished_at`, the
    clock the Reflector's decision gate reads (ADR-0056). The only place the key is set."""
    if status not in ("discarded", "split", "tracker_only"):
        raise ValueError(
            f"finished_at is for discarded, split and tracker_only tasks, not {status}"
        )
    return {"status": status, "finished_at": at}


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


def copy_applies(state: TaskState) -> bool:
    """Whether this task has its texts written and reviewed (README.md, 4): a feature, with
    `touches_copy` among the validated flags. The only reader of that flag."""
    return state.get("type") == "feature" and bool(state.get("flags", {}).get("touches_copy"))


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
    than both of their approvals.

    The last rung is the texts (`write_copy`, README.md 4): due on a feature that touches
    copy once the design branch, when it applies, is done, and no `Tech Writer` run is
    newer than the latest Product Analyst run and the latest consolidation. The name stays
    though the ladder is no longer only the design's."""
    if not (design_applies(state) or copy_applies(state)):
        return None
    prd_idx = _latest_index(state, PRODUCT_ANALYST.name)
    if design_applies(state) and (step := _design_ladder(state, prd_idx)):
        return step
    if not copy_applies(state):
        return None
    writer_idx = _latest_index(state, TECH_WRITER.name)
    newest = max(
        (i for i in (prd_idx, _latest_index(state, PM_CONSOLIDATOR.name)) if i is not None),
        default=None,
    )
    return (
        "write_copy" if writer_idx is None or (newest is not None and writer_idx < newest) else None
    )


def _design_ladder(state: TaskState, prd_idx: int | None) -> str | None:
    """The design rungs of `design_step`, in order, `None` once all are satisfied."""
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


def space_narrowed(
    reason: FailureReason, space: SpaceReading | None, floor_bytes: int, enospc: bool
) -> FailureReason:
    """`reason` narrowed to `disk_full` when the disk, not the role, broke the attempt
    (GH-391, ADR-0069): an `ENOSPC` caught as structured data in the system's own process
    (`enospc`), or a reading taken after the attempt broke that shows free bytes under
    `floor_bytes` or free inodes under `INODE_FLOOR`. Derived only from those measured
    facts, never from the error's text. `limit` is our own per-attempt cap, a fact about
    our configuration that no disk reading outranks: it is returned unchanged, as is any
    reason when there is no reading and no `ENOSPC`."""
    if reason == "limit":
        return reason
    if enospc:
        return "disk_full"
    if space is not None and (space.free_bytes < floor_bytes or space.free_inodes < INODE_FLOOR):
        return "disk_full"
    return reason


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

# A request that carries a `## Decided` heading already settled its decisions in a grill
# with the human: its interviews run one round instead of the configured cap (ADR-0014,
# addendum of 2026-10-06). Removing a stage from this set disables the cap for it.
DECIDED_CAP_STAGES = frozenset({"definition", "solution"})


def decided(text: str, level: int = 2) -> bool:
    """Whether `text` has a heading of `level` that reads `Decided`: the one and only
    matcher of that heading. A mechanical check, never prose: a line outside fenced
    blocks (``` and ~~~ each toggle one) that is exactly `level` `#` characters, a
    space and the word, ignoring case, trailing whitespace and one trailing `:`. Level 2
    is the heading as the human writes it; level 3 reads the request back from a task
    document, where `document.demote` pushed it one level down."""
    heading = f"{'#' * level} decided"
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            candidate = line.rstrip()
            candidate = candidate.removesuffix(":")
            if candidate.lower() == heading:
                return True
    return False


def entering(task: TaskInput, *, cap: bool, carried: Carried | None = None) -> TaskInput:
    """`task` as it enters the graph: with `decided: True` when `cap` is on and its
    request carries the heading, and otherwise unchanged, with no key added. Called
    once, from `cli._run`; nothing else sets `decided`.

    `carried` is what the archived run hands this one (`landing.carry_over`; GH-239,
    ADR-0048, GH-297), read from its task document and never counted from issue comments.
    Its restart depth and cost become `landing_restarts` and `landing_cost_carried`, only
    when either is positive. Its `Human` section seeds `sections[HUMAN]` verbatim, when
    non-empty: the decisions keep their `### Decision N` headings, so `human_section`
    numbers the next one N+1, and the roles read them as settled. `None` adds no key."""
    entered = task
    if cap and decided(task["description"]):
        entered = {**entered, "decided": True}
    if carried is None:
        return entered
    if carried.restarts > 0 or carried.cost > 0:
        entered = {
            **entered,
            "landing_restarts": carried.restarts,
            "landing_cost_carried": carried.cost,
        }
    if carried.human:
        entered = {**entered, "sections": {HUMAN: carried.human}}
    return entered


def interview_cap(state: TaskState, spec: Interview, configured: int) -> int:
    """The rounds `spec` may run at this state: one for a decided request at a stage in
    `DECIDED_CAP_STAGES`, never above `configured`, and `configured` otherwise. A state
    with no `decided` key is not decided, whatever its request holds."""
    if state.get("decided") and spec.stage in DECIDED_CAP_STAGES:
        return min(1, configured)
    return configured


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


def interview_rounds(state: TaskState, spec: Interview) -> list[RoleRun]:
    """The interviewer's own runs of `spec`'s section, closing turn included: every
    round has exactly one, so this is what counts the rounds. The only filter over
    `state["runs"]` that decides what an interview round is -- `interview_progress` and
    the poller's productive-retake count both read it, never a copy of the predicate."""
    return [
        r
        for r in state.get("runs", [])
        if r.get("section", r["role"]) == spec.section and r["role"] == spec.interviewer.name
    ]


def interview_progress(state: TaskState, spec: Interview, max_rounds: int) -> InterviewProgress:
    """`InterviewProgress` for `spec` at this state, against `max_rounds` (the ordinary
    cap, `cfg.interview_max_rounds`): zero rounds done and the whole budget for an
    interview that never ran, whatever a retake already spent otherwise."""
    rounds_done = len(interview_rounds(state, spec))
    cap = interview_cap(state, spec, max_rounds)
    rounds_left = max(cap - rounds_done, min(INTERVIEW_RETAKE_ROUNDS, cap))
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
# The system node that runs the whole suite once on the integrated head (GH-358): the section
# it writes, and the one a Developer returned by it reads its delta from.
FULL_CHECKS = "Full checks"


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
    returned_by: ReturnedBy = subtask.get("returned_by", "review")
    match returned_by:
        case "review":
            return review_section(subtask_id)
        case "final_validation":
            return PM_FINAL.name
        case "qa_performance":
            return QA_PERFORMANCE.name
        case "full_checks":
            return FULL_CHECKS
        case _:
            assert_never(returned_by)


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
    if state.get("invariants"):
        parts.append(
            "The coverage map belongs to the whole delivery, not to what you change now: "
            "emit it again in full against the current head, because line numbers move."
        )
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


def reaped_note(reaped: Sequence[ReapedProcess]) -> str:
    """The trace a role's leftover background processes leave on its section (GH-282,
    ADR-0052): one line, system-written, only when the reaper stopped something, with how
    each of them ended. `reaped` is the run's total, the delivering attempt's plus every
    recorded failure's (`run_role._run_reaped`), so a leak is visible without opening the
    task's records."""
    counts = [
        f"{sum(1 for p in reaped if p['outcome'] == outcome)} {outcome}"
        for outcome in get_args(ReapOutcome)
        if any(p["outcome"] == outcome for p in reaped)
    ]
    return system_note(f"Reaped {len(reaped)} background process(es): {', '.join(counts)}")


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


def _parse_archived(ref: str) -> tuple[int, str] | None:
    """`(N, rest)` of an archived ref of either spelling -- `rest` is the task id and,
    for a subtask branch, `.<subtask>` -- or `None` for a ref that is not an archive."""
    match = _ARCHIVED_REF.match(ref)
    if not match:
        return None
    n, rest = match.groups()
    return int(n), rest


def _archived_runs(refs: Sequence[str], task_id: str) -> list[int]:
    """The `N` of every archived ref, of either spelling, that belongs to `task_id`:
    the same whole-segment comparison `branches_of_task` makes, so `GH-1` never reads
    `GH-12`'s archive as its own."""
    runs = []
    for ref in refs:
        parsed = _parse_archived(ref)
        if parsed is None:
            continue
        n, rest = parsed
        if rest == task_id or rest.startswith(f"{task_id}."):
            runs.append(n)
    return runs


def archived_task_branches(refs: Sequence[str], task_id: str) -> list[str]:
    """The refs in `refs` that are archives of `task_id`'s own task branch, either
    spelling and any `run<N>-<sha>` prefix (`unknown` included): never a subtask
    archive (`.<subtask>`) and never another task's (`GH-1` against `GH-12`). What a
    `--fresh` run left of a previous task branch, and so the one thing a remote that
    diverged may be told apart from somebody else's push by (`push_plan`)."""
    return [ref for ref in refs if (parsed := _parse_archived(ref)) and parsed[1] == task_id]


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


# The graph node that runs the subtasks. The graph's own `PIPELINE` constant has this value
# and keeps it; the domain cannot import the adapter, so the rule that reads a pending node
# name holds its own spelling, the way `after_integrate` returns the strings.
PIPELINE_NODE = "pipeline"


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


def _remaining_path(subtasks: Mapping[str, Subtask]) -> dict[str, int]:
    """Per subtask, the length of the longest chain of subtasks that transitively depend on
    it (0 for one nothing depends on). A cycle in a malformed plan is cut where it closes
    instead of recursing forever."""
    dependents: dict[str, list[str]] = {sid: [] for sid in subtasks}
    for sid, sub in subtasks.items():
        for dep in sub.get("depends_on", []):
            if dep in dependents:
                dependents[dep].append(sid)
    lengths: dict[str, int] = {}

    def length(sid: str, visiting: frozenset[str]) -> int:
        if sid in lengths:
            return lengths[sid]
        below = [length(d, visiting | {sid}) + 1 for d in dependents[sid] if d not in visiting]
        lengths[sid] = max(below, default=0)
        return lengths[sid]

    return {sid: length(sid, frozenset()) for sid in subtasks}


def next_to_start(state: TaskState, in_flight: Collection[str], limit: int) -> list[str]:
    """The subtasks the loop starts now (GH-364, ADR-0004's successor): the ones
    `dispatched_subtasks` names that are not already `in_flight`, the longest remaining
    dependency chain first and ties in plan order, at most what is left under `limit`. The
    only place readiness, ordering and the parallel bound are computed together. Nothing
    starts while the task is escalated; a role already running is never this rule's to stop."""
    if state.get("escalated"):
        return []
    room = max(0, limit - len(in_flight))
    path = _remaining_path(state.get("subtasks", {}))
    plan_order = {sid: n for n, sid in enumerate(state.get("subtasks", {}))}
    waiting = [sid for sid in dispatched_subtasks(state) if sid not in in_flight]
    waiting.sort(key=lambda sid: (-path[sid], plan_order[sid]))
    return waiting[:room]


def resume_source(
    next_node: Sequence[str] | str | None, crashed: bool
) -> Literal["checkpoint", "document"]:
    """Where a resume rebuilds the task from (GH-364): the document, only for a crash with
    the pipeline the pending node, because the loop's branches leave no checkpoint of their
    own; anything else resumes from the checkpoint. Decided from the pending node, never by
    comparing counts."""
    pending = (next_node,) if isinstance(next_node, str) else tuple(next_node or ())
    return "document" if crashed and pending == (PIPELINE_NODE,) else "checkpoint"


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


# The task cap (README.md, 3; ADR-0004, 2026-10-05 addendum): one flat ceiling, the same
# for any plan size, which the human may raise and never lower.


def task_cap(ceiling: float, override: float | None) -> float:
    """What the run may spend: the flat ceiling, or the human's override when it is
    higher. It takes no state, so the size of the plan cannot reach it."""
    return max(ceiling, override or 0.0)


def task_spent(state: Mapping[str, object]) -> float:
    """What the task has spent against its cap: its own `cost_usd` plus the cost a landing
    restart carried over (`landing_cost_carried`). The figure `run_role` measures against
    `task_cap`, so every cap check over a document agrees with it (GH-328)."""
    own, carried = state.get("cost_usd", 0.0), state.get("landing_cost_carried", 0.0)
    return float(own if isinstance(own, int | float) else 0.0) + float(
        carried if isinstance(carried, int | float) else 0.0
    )


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


def suggested_budget(state: TaskState, step: float) -> int:
    """The `--task-budget` an escalation by the cap proposes: what was spent plus one more
    attempt's cap, in whole dollars. A starting point for the human, not an estimate."""
    return math.ceil(state.get("cost_usd", 0.0) + step)


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


CLOSENESS: tuple[str, ...] = ("launch", *PHASES, "delivered", "land")
"""How close to merge a counted start is, nearest last (ADR-0045 addendum, GH-362; glossary:
Cercanía): a fresh launch, then each phase in `PHASES` order, then a task with a pull request,
then a land. The phases are never listed again, so one appended to `PHASES` is ranked by it."""


def closeness(state: TaskState) -> int:
    """The task's rank in `CLOSENESS`. A pull request makes it `delivered`; otherwise it is the
    furthest phase any run reached (not the last: a return to plan after development stays
    development); a task with no runs is in the first phase."""
    if state.get("pull_request"):
        return CLOSENESS.index("delivered")
    reached = (
        CLOSENESS.index(phase_of(run.get("section", run["role"]))) for run in state.get("runs", [])
    )
    return max(reached, default=CLOSENESS.index(PHASES[0]))


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


def after_write_copy(state: TaskState) -> str | None:
    """The texts are the last rung of `design_step`: a rerun of a design step the texts made
    stale is not possible from here, so the ladder answers `None` and the plan follows."""
    return None if state.get("escalated") else (design_step(state) or "plan")


def after_plan(state: TaskState) -> str | None:
    """A plan that did not fit carries `parts` and goes to `split` (ADR-0054); a plan whose
    only work is on the tracker (GH-336) goes to `tracker_only`; any other goes to the
    fan-in. The only routing decision between the three."""
    if state.get("escalated"):
        return None
    if state.get("parts"):
        return "split"
    return "tracker_only" if tracker_only_plan(state) else "integrate"


def tracker_only_plan(state: TaskState) -> bool:
    """The third plan shape (GH-336): no subtasks, no parts and a checklist for the human.
    The one place the shape is decided; a checklist next to subtasks rides the delivery, and
    next to parts the split posts it (GH-350), so neither is this shape."""
    return bool(
        state.get("tracker_checklist") and not state.get("subtasks") and not state.get("parts")
    )


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


def copy_review_due(state: TaskState) -> bool:
    """Whether Tech Writer's review of the texts still has to run before `deliver`: the task
    has texts (`copy_applies`), every subtask is approved, and no `Tech Writer: review` run is
    newer than the latest review of `integration` -- or of the single subtask. A fix after an
    important finding is reviewed again, so the review is due again. `False` outside
    `copy_applies`, byte for byte as today."""
    if not copy_applies(state) or not all_approved(state):
        return False
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks), None)
    if target is None:
        return False
    review_idx = _latest_index(state, review_section(target))
    if review_idx is None:
        return True
    copy_idx = _latest_index(state, TECH_WRITER_REVIEW.name)
    return copy_idx is None or copy_idx < review_idx


def qa_performance_due(state: TaskState) -> bool:
    """Whether QA Performance (GH-318, ADR-0060) still has to run before `deliver`: the
    plan convened performance (`TechPlan.performance`, the only source -- never the
    branch's stack), every subtask is approved, and no run of the node whose verdict
    approves or leaves the head unverified is newer than the latest approving review of
    `integration` -- or of the single subtask, when there is no integration. A `returned`
    run settles nothing: it reopens the subtask, and the review that approves it again is
    newer than any run that came before. `False` for a plan that convened nothing, byte
    for byte as today."""
    if not state.get("performance") or not all_approved(state):
        return False
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks), None)
    if target is None:
        return False
    review_idx = _latest_index(state, review_section(target))
    if review_idx is None:
        return True
    settled_idx = None
    for i, run in enumerate(state.get("runs", [])):
        if (
            run.get("section", run["role"]) == QA_PERFORMANCE.name
            and run.get("perf_verdict") in _QA_PERFORMANCE_SETTLES
        ):
            settled_idx = i
    return settled_idx is None or settled_idx < review_idx


_QA_PERFORMANCE_SETTLES = frozenset(("approved", "unverified"))


def full_checks_due(state: TaskState) -> bool:
    """Whether the whole suite still has to run once on the integrated head (GH-358): the
    product declares tiers (the fan-in stamped `task_head`, which a product without
    `delivery_checks` never has) and there is no passed record of that head. A head the
    task moved since -- a return, a landing pass -- is a head nobody ran the suite on."""
    head = state.get("task_head")
    if not head:
        return False
    recorded = state.get("full_checks")
    return not (recorded and recorded["passed"] and same_commit(recorded["head"], head))


def pre_pr_important_due(state: TaskState) -> bool:
    """Whether the important pass runs before the pull request opens (GH-354, ADR-0053
    amendment): every unit is approved, the final unit (the integration's, or the single
    subtask's when there is none) has been reviewed, no pull request exists yet, the
    task's `owned` or `plan` `important` rows (`important_partition`) hold one that was not
    lowered (`reported_severity`: the system's reading of an unbacked block, never a reason
    to run a pass), and the pass is not spent. `outside` rows are the follow-up issue's."""
    target = reviewed_unit_name(state)
    if target is None or state.get("pull_request") or not all_approved(state):
        return False
    if _latest_index(state, review_section(target)) is None or important_pass_spent(state):
        return False
    partition = important_partition(state)
    rows = [*(row for owned in partition.owned.values() for row in owned), *partition.plan]
    return any("reported_severity" not in row for row in rows)


def after_integrate(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    if dispatched_subtasks(state):
        return "pipeline"
    if not all_approved(state):
        return None
    if pre_pr_important_due(state):
        return FIX_IMPORTANT
    if full_checks_due(state):
        return "full_checks"
    if copy_review_due(state):
        return "copy_review"
    if final_validation_due(state):
        return "final_validation"
    return "qa_performance" if qa_performance_due(state) else "deliver"


def after_fix_important(state: TaskState) -> str | None:
    """Always back to the fan-in: the pass reopened the owning units, which
    `dispatched_subtasks` picks up; once they and the integration are approved again
    `important_pass_spent` reads `True` and `after_integrate` goes on to the checks."""
    return None if state.get("escalated") else "integrate"


def after_final_validation(state: TaskState) -> str | None:
    """Always back to the fan-in (ADR-0040): on `approved`, `final_validation_due` now
    reads `False` and `after_integrate` falls through to `deliver`; on `returned`, the
    reopened subtask is exactly what `dispatched_subtasks` picks up, and the fan-in
    sends it back to its Developer like any other wave. No new predicate decides
    between the two -- `after_integrate` already does, from the same state."""
    return None if state.get("escalated") else "integrate"


def after_qa_performance(state: TaskState) -> str | None:
    """Always back to the fan-in, like `after_final_validation`: on `approved` or
    `unverified`, `qa_performance_due` now reads `False` and `after_integrate` falls
    through to `deliver`; on `returned`, the reopened subtask is what `dispatched_subtasks`
    picks up."""
    return None if state.get("escalated") else "integrate"


def _failed_names(record: FullChecksRecord) -> list[str]:
    """The checks a run failed, in the order recorded; the step it stopped at when it failed
    before any check ran (a worktree, a setup), and `checks` when it says nothing else."""
    names = [entry["name"] for entry in record["results"] if not entry["passed"]]
    return names or [record.get("failed_step") or "checks"]


def full_checks_return(state: TaskState, record: FullChecksRecord, limit: int) -> dict[str, Any]:
    """The patch of a red `full_checks` run (GH-358), modelled on QA Performance's return:
    the integration (the single subtask when there is none) reopens with `returned_by:
    "full_checks"`, one blocking `plan` row per failed check at `full_checks:<check>` with a
    fixed claim, and the return counts on `pair_for(target)`; at its limit the same patch
    escalates as a pair at its limit. The record travels with it, so the state and the
    document hold the run the rows came from."""
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks))
    names = _failed_names(record)
    short = record["head"][:7]
    reason = f"full_checks failed on the integrated head {short}: {', '.join(names)}"
    patch: dict[str, Any] = {
        "full_checks": record,
        "subtasks": {
            target: {
                **subtasks[target],
                "status": "returned",
                "reason": reason,
                "returned_by": "full_checks",
            }
        },
        "findings": [
            {
                "subtask": target,
                "version": record["version"],
                "location": f"{FULL_CHECKS_PREFIX}{name}",
                "claim": f"{name} failed on the integrated head {short}",
                "severity": "blocking",
                "scope": "plan",
                "category": "process-suite-red",
                "status": "open",
            }
            for name in names
        ],
    }
    pair = pair_for(target)
    if remaining_changes(state, pair, limit) > 0:
        patch["changes_requested"] = record_return(state, pair)
        patch["change_history"] = [change_entry(pair, "process-suite-red", reason)]
    else:
        stopped = logged({**escalation("returns", limit_reached(pair, limit, reason))})
        patch = {**patch, **stopped, "status": WAITING_ON_HUMAN}
    return patch


def full_checks_cover(
    record: FullChecksRecord | None, differs: bool
) -> Literal["ok", "missing", "stale"]:
    """What `deliver` may do for a tiered product (GH-358): `missing` when no passed record
    exists, `stale` when one does but its head differs from HEAD in more than the task's own
    document (`differs`), `ok` otherwise. Only `ok` pushes."""
    if record is None or not record["passed"]:
        return "missing"
    return "stale" if differs else "ok"


def full_checks_heal(
    state: TaskState, head: str, verdict: Literal["missing", "stale"]
) -> dict[str, Any]:
    """The patch of a `deliver` refused for want of a covering record (GH-358): the first
    refusal on `head` stamps it as the task's head and as healed, and the fan-in runs the
    suite on it (`after_deliver`); a second refusal on the same head escalates with the
    `system_delivery` class, so the task can never loop silently. The stale record stays:
    `full_checks_due` compares heads, and a patch cannot drop a key."""
    if same_commit(state.get("full_checks_healed"), head):
        reason = (
            f"deliver refused twice to push {head[:7]}: its full_checks record is {verdict}, "
            "and running the suite again on that head did not cover it. Nothing was pushed."
        )
        stopped = logged({**escalation("system_delivery", reason)})
        return {**stopped, "status": WAITING_ON_HUMAN}
    return {"task_head": head, "full_checks_healed": head}


def after_full_checks(state: TaskState) -> str | None:
    """Always back to the fan-in, like `after_qa_performance`: on a green run
    `full_checks_due` now reads `False` and `after_integrate` goes on; on a red one the
    reopened subtask is what `dispatched_subtasks` picks up."""
    return None if state.get("escalated") else "integrate"


def after_copy_review(state: TaskState) -> str | None:
    """Always back to the fan-in, like `after_final_validation`: the review returns nothing,
    so `after_integrate` reads the same state and goes on to the next step or reopens the
    owner of an important finding."""
    return None if state.get("escalated") else "integrate"


def after_deliver(state: TaskState) -> str | None:
    """Back to the fan-in only when `deliver` refused to push for lack of a passed full run
    and healed (`full_checks_heal` wrote `full_checks_healed` for the head the suite is
    still due on); every other end of `deliver` is the end of the run, as it always was."""
    if state.get("escalated") or not full_checks_due(state):
        return None
    return (
        "integrate"
        if same_commit(state.get("full_checks_healed"), state.get("task_head"))
        else None
    )


def after_discard(state: TaskState) -> str | None:
    return None


def after_split(state: TaskState) -> str | None:
    return None


def after_tracker_only(state: TaskState) -> str | None:
    """The end of the tracker-only road: nothing follows, and an escalation (the comment
    failed) ends the run paused, like `split`'s."""
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
    return status in ("ready_for_review", "discarded", "split", "tracker_only")


# Pause and resume (ADR-0003): an escalation pauses the task; the human's answer resumes
# it where it stopped. The state says where that is, so the same rules serve a paused
# checkpoint and a state seeded from the document.


def resumed_status(state: TaskState) -> Status:
    """The status the task had before the escalation overwrote it, derived from what the
    state holds, since the document keeps only `waiting_on_human` too."""
    if state.get("pull_request") and not landing_open(state):
        # An owed publication is not delivered yet: `deliver` is what sets the end status.
        return "developing" if _publication_owed(state) else "ready_for_review"
    if state.get("status") == "discarded":
        # Nothing overwrote it: a discard is an end, not a pause, and the runs it holds
        # (a diagnosis, PM's ruling) would otherwise read as a definition ready to plan.
        return "discarded"
    if state.get("status") == "split":
        return "split"  # the same: an end, not a pause
    if state.get("status") == "tracker_only":
        return "tracker_only"  # the same: the checklist is posted, the human has it
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


def fit_question_text(task_id: str, part_of: tuple[str, int], fit: FitDecision | None) -> str:
    """What the human reads when a part's plan is over the line: the figures the decision
    used (`FitDecision`), the limits, which one was crossed, and the three ways out. No way
    out stops the task by resuming it: stopping is the absence of a resume."""
    origin, number = part_of
    head = f"{task_id} is part {number} of {origin}; a part is never split again. "
    if fit is None:
        figures = "The plan's figures are not known, so which limit it crossed is not either. "
    else:
        reference = (
            f"the history's reference for this many subtasks is {fit.reference_usd:.2f} USD"
            if fit.reference_usd is not None
            else "the history has no reference for this many subtasks"
        )
        limits = f"{SPLIT_COST_USD} USD and {SPLIT_FILES} files"
        crossed: list[str] = []
        if fit.reference_usd is not None and fit.reference_usd > max(fit.cost_usd, SPLIT_COST_USD):
            crossed.append(f"the history's reference is over the {SPLIT_COST_USD} USD limit")
        elif fit.cost_usd > SPLIT_COST_USD:
            crossed.append(f"the declared cost is over the {SPLIT_COST_USD} USD limit")
        if fit.files > SPLIT_FILES:
            crossed.append(f"the declared files are over the {SPLIT_FILES} file limit")
        verdict = (
            "; ".join(crossed)
            if crossed
            else "it is over the line by the larger of the declared cost and the reference"
        )
        figures = (
            f"Its plan declares {fit.cost_usd:.2f} USD and {fit.files} files, and {reference}. "
            f"One task holds at most {limits}: {verdict}. "
        )
    return (
        f"{head}{figures}Choose one:\n"
        "- Build it whole: comment on the issue and resume; the plan is kept and built as "
        "one task.\n"
        "- Stop and release the parts that depend on it: close the issue as not planned.\n"
        "- Stop and hold them: leave it open and do not resume.\n"
        "Closing the issue as completed without a merge leaves the parts behind it "
        "blocked. Any resume builds it whole."
    )


def part_over_line(
    task_id: str, part_of: tuple[str, int], fit: FitDecision | None
) -> dict[str, Any]:
    """The patch that pauses a part whose plan is over the line: one `estimate` escalation
    asking "build it whole or stop", marked by `fit_question` so only its resume waives the
    fit. It carries no `proposed_estimate`; the plan is kept."""
    return logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **escalation("estimate", fit_question_text(task_id, part_of, fit)),
            "fit_question": True,
        }
    )


def resumed(
    state: TaskState, decision: str | None, estimate: float | None = None
) -> dict[str, Any]:
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
    decided = bool(decision and decision.strip())
    if decided:
        patch["sections"] = {HUMAN: human_section(state, reason, decision or "")}
    if state.get("fit_question"):
        # A part whose plan is over the line asked "build it whole or stop"; any resume
        # answers "build it whole" (stopping is not resuming). Written explicitly: a `last`
        # reducer ignores a missing key. Only this pause waives, never the cap's.
        patch["fit_waived"] = True
        patch["fit_question"] = False
    if state.get("escalation_class") == "estimate" and state.get("parts"):
        # A part of a split does not fit and the human decided on it (ADR-0054): the plan
        # is made again as one task, and a second partition is refused.
        patch["fit_waived"] = True
        patch["parts"] = []
    if (
        state.get("escalation_class") == "estimate"
        and (decided or estimate is not None)
        and (revision := _estimate_revision(state, estimate))
    ):
        # Only here does `estimate_revisions` grow (ADR-0054): a resumption that carries
        # the human's decision (their comment, or the `--estimate` they typed). The plan's
        # own `estimate` is never rewritten.
        patch["estimate_revisions"] = [*state.get("estimate_revisions", []), revision]
        patch["proposed_estimate"] = None
    return patch


def _estimate_revision(state: TaskState, estimate: float | None) -> EstimateRevision | None:
    """The estimate a decided resume puts in force: the human's own value, else the one
    the stopped task proposed; `None` when there is neither."""
    if estimate is not None:
        return {"cost_usd": estimate, "source": "human"}
    if (proposed := state.get("proposed_estimate")) is not None:
        return {"cost_usd": proposed, "source": "proposed"}
    return None


def entry_closed(entry: Mapping[str, object]) -> bool:
    """Whether a `landing` entry is finished (GH-267): `resolved_at` (it succeeded) or
    `closed_at` (a sync that did not bring its upstream in) is a non-empty string. The only
    place that reads either key to decide it."""
    return any(
        isinstance(value := entry.get(key), str) and bool(value)
        for key in ("resolved_at", "closed_at")
    )


def before_pr(entry: Mapping[str, object]) -> bool:
    """Whether a `landing` entry was written before the pull request existed (the pre-PR
    important pass, GH-354): it says nothing about a pull request. The only place the key
    `before_pr` is read; `returns.before_pr` re-exports it."""
    return bool(entry.get("before_pr"))


LANDING_IMPORTANT_PASSES_MAX = 1
"""`fix_important` passes per task (GH-294, GH-354), before or after the pull request;
declared, not measured. `domain.landing` re-exports it."""

FIX_IMPORTANT = "fix_important"


def important_pass_spent(state: Mapping[str, object], *, resolved_only: bool = False) -> bool:
    """Whether the task's important pass (`LANDING_IMPORTANT_PASSES_MAX`) is spent: the
    `landing` list holds that many `fix_important` entries, written before the pull request
    (`before_pr`) or after it. With `resolved_only` an entry still in flight does not count --
    the merge reads the pass as done only once the re-delivery closed it. The one place the
    entries are counted: the pre-PR route, `landing_plan` and `MergeFacts.important_pass_done`
    ask this and none counts `fix_important` itself."""
    entries = state.get("landing")
    passes = sum(
        1
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, Mapping)
        and entry.get("step") == FIX_IMPORTANT
        and (not resolved_only or entry.get("resolved_at"))
    )
    return passes >= LANDING_IMPORTANT_PASSES_MAX


def landing_open(state: TaskState) -> bool:
    """A landing pass is in flight (GH-239): the last `landing` entry is not resolved and
    some subtask is reopened for it -- `returned`, `pending` or `delivered` awaiting its
    review. `domain.landing.landing_open` is this, under the landing's own name. An entry
    written before the pull request existed (`before_pr`) is never one."""
    document: Mapping[str, object] = state  # `landing` is a document key, not a state field
    entries = document.get("landing")
    if not isinstance(entries, list) or not entries or entry_closed(entries[-1]):
        return False
    if before_pr(entries[-1]):
        return False
    return any(
        unit.get("status") in ("returned", "pending", "delivered")
        for unit in state.get("subtasks", {}).values()
    )


def approved_head(state: TaskState) -> str | None:
    """The head the task may publish (GH-373): the one its last review approved. On a tiered
    product (the fan-in stamped `task_head`) that is `task_head`, only if the reviewed unit's
    `reviewed_head` is the same commit and `full_checks` passed on it; without tiers it is the
    reviewed unit's `reviewed_head`. `None` when there is none, and for a head the task moved
    after its review (a heal, a landing pass): nobody approved that one."""
    name = reviewed_unit_name(state)
    reviewed = state.get("subtasks", {}).get(name, {}).get("reviewed_head") if name else None
    if not reviewed:
        return None
    task_head = state.get("task_head")
    if not task_head:
        return reviewed
    recorded = state.get("full_checks")
    if not same_commit(reviewed, task_head):
        return None
    if not (recorded and recorded["passed"] and same_commit(recorded["head"], task_head)):
        return None
    return task_head


def publication_candidate(state: TaskState) -> str | None:
    """The head a stopped run may still owe (GH-373): `approved_head` of a task that has a
    pull request, no landing pass in flight, no escalation, every unit approved and nothing
    left for the full tier. `None` otherwise."""
    if not state.get("pull_request") or landing_open(state) or state.get("escalated"):
        return None
    if not all_approved(state) or full_checks_due(state):
        return None
    return approved_head(state)


def owed_publication(state: TaskState, published: bool, branch_at_head: bool) -> str | None:
    """The `publish_owed` value of a resume (GH-373), from the facts `prepare` read:
    `published` is whether the remote already holds the candidate, `branch_at_head` whether
    the task branch is at it or differs only in the task document. The candidate when the
    branch is there and either the remote lacks it or the status never reached the end of a
    delivery (`ready_for_review`) or a human's pause; `None` otherwise."""
    candidate = publication_candidate(state)
    if candidate is None or not branch_at_head:
        return None
    if not published or state.get("status") not in ("ready_for_review", "waiting_on_human"):
        return candidate
    return None


def _publication_owed(state: TaskState) -> bool:
    """`publish_owed` names the head the task may publish, and nothing has moved it."""
    if state.get("escalated") or not all_approved(state) or full_checks_due(state):
        return False
    return same_commit(state.get("publish_owed"), approved_head(state))


def resume_target(state: TaskState) -> str | None:
    """Where a resumed task goes next, from the same facts the routing reads. A pull
    request ends the road unless the landing reopened the stage under it, or a stopped run
    still owes it the approved head (`publish_owed`, GH-373): that goes to `deliver`."""
    if state.get("escalated"):
        return None
    if state.get("pull_request") and not landing_open(state):
        return "deliver" if _publication_owed(state) else None
    if state.get("status") == "split":
        return None  # an end like a pull request: the parts are other tasks now
    if state.get("status") == "tracker_only":
        return None  # an end: the checklist is with the human
    if state.get("parts") and not state.get("subtasks"):
        return "split"  # paused while opening the parts: finish them, do not plan again
    if tracker_only_plan(state):
        return "tracker_only"  # paused before the checklist was posted: post it, do not plan
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


def integration_increment(
    state: TaskState, task_id: str, branch_head: str, descends: bool
) -> Literal["agree", "increment", "refuse"]:
    """Whether the task branch at `branch_head` is the head the integration review approved
    (`agree`), a strict descendant of it that nobody reviewed (`increment`: a later subtask
    merge, a commit a stopped run left), or something a resume must not go on over
    (`refuse`). `descends` is the ancestry fact -- whether the approved head is an ancestor of
    `branch_head` -- read by the caller, so this stays pure and is the one place the rule lives.

    An increment needs all of: the reviewed unit approved at a head, on the task branch; every
    other unit approved; no pull request (its branch moving is the landing's, not a resume's);
    and a pause no human has anything to decide in -- a role failure or a run that stopped
    without escalating, since `resumed` clears exactly that and nothing else stands in the way
    (pending questions, a pair at its limit, an estimate or a cap are decisions)."""
    unit = reviewed_unit_name(state)
    sub = state.get("subtasks", {}).get(unit) if unit else None
    approved = sub.get("reviewed_head") if sub and sub.get("status") == "approved" else None
    if approved is None:
        return "refuse"
    if same_commit(branch_head, approved):
        return "agree"
    if not descends or sub is None or sub.get("branch") != task_branch(task_id):
        return "refuse"
    others = (s for name, s in state.get("subtasks", {}).items() if name != unit)
    if any(s.get("status") != "approved" for s in others) or state.get("pull_request"):
        return "refuse"
    if state.get("escalation_class") not in (None, "failure"):
        return "refuse"
    if state.get("questions_for_human") or state.get("fit_question"):
        return "refuse"
    if state.get("escalated") and state.get("escalation_class") is None:
        return "refuse"  # a document older than the class: what it asked is not known
    return "increment"


def increment_patch(state: TaskState) -> dict[str, Any]:
    """The patch that sends the reviewed unit back to review on an increment: its status is
    `delivered`, and its `reviewed_head` stays the approved head the next review is incremental
    from. The reducer replaces a subtask whole, so the unit goes back with all its keys. Nothing
    else is written: not `task_head`, not `full_checks`, not the findings table."""
    unit = reviewed_unit_name(state)
    if unit is None:
        raise ValueError("a task with no reviewed unit has no increment to review")
    sub = state["subtasks"][unit]
    patch: dict[str, Any] = {"subtasks": {unit: {**sub, "status": "delivered"}}}
    return patch


# The poller (issue #137, README.md, 14; ADR-0003's own forward reference: "the poller
# of stage 5 will do the search"). First slice: retake a pause an external API cut
# caused, close a task whose pull request merged, and launch the next unblocked issue,
# never past the machine's own concurrency cap. Every predicate here is pure: the clock
# and every fact it decides over arrive as arguments (README.md, 14), never a port, a
# `subprocess` or a `Path`.

# The four `FailureReason`s a pause never needed a human for (ADR-0029, extended by
# issue #137's own `network` and GH-391's `disk_full`): the account's own quota, the
# provider's own server, the network down, or the disk full. `rules.auto_resumable` is the
# only reader of this tuple; the scorecard's own exclusion (`core.measurement.domain.
# scorecard.EXTERNAL_FAILURE_REASONS`) is a separate copy of the same literals --
# hexagonal keeps `core.process.domain` from importing `core.measurement`, so nothing
# here imports it.
EXTERNAL_REASONS: tuple[FailureReason, ...] = ("account_limit", "upstream", "network", "disk_full")

# Minutes before each of the poller's own automatic attempts, first to last (ADR-0029's
# addendum, issue #137): never read off an API's own "resets 7:30pm" prose. Indexed by
# how many automatic attempts already happened (`prior`), capped by `rules.
# auto_resume_limit`'s own 1 or 6. Two calendars with the same values today, split so
# a launch that never registered a run (not a 429) never follows a retake's calendar.
LAUNCH_SCHEDULE: tuple[int, ...] = (15, 30, 60, 120, 120, 120)
RETAKE_SCHEDULE: tuple[int, ...] = (15, 30, 60, 120, 120, 120)

# After a 429 the only thing that matters is how soon after the account's own reset the
# next attempt lands, and an attempt the limit cuts again costs ~$0 (the 429 arrives on
# the role's first turn): a flat 15 minutes, 24 slots to cover a whole 5-hour window
# (ADR-0039, 2026-10-05 addendum). Never read off the API's "resets at" prose.
ACCOUNT_LIMIT_SCHEDULE: tuple[int, ...] = (15,) * 24

# The provider's session window: a stated reset further away than this is not a session
# reset (ADR-0039, addendum GH-381; `account_reset.reset_at`).
SESSION_WINDOW = timedelta(hours=5)

# Clock skew between this machine and the provider, added to the window so a reset the
# provider states at the window's edge is not refused (ADR-0039, addendum GH-381).
SESSION_WINDOW_MARGIN = timedelta(minutes=10)

# Backstop for a loop the per-window count cannot see: a cheap role keeps delivering
# runs (each one opens a fresh window) while another role keeps hitting a per-model
# 429. Compared against the whole-chase count a delivered run never resets, only a
# human comment does: four windows of 24.
ACCOUNT_LIMIT_CHASE_MAX = 96

# Past the chase max the account is not coming back within a window: the task follows an
# hourly calendar for a week instead of waiting for a human (ADR-0048, amending ADR-0039).
# Indexed by the retakes made since the chase max; spent, the task escalates as a failure.
# Never read off the API's "resets at" prose (ADR-0029).
ACCOUNT_LIMIT_HOURLY: tuple[int, ...] = (60,) * 168
ACCOUNT_LIMIT_PERSISTED = "account limit persisted 7 days"


def _failure_entry(failure: Failure) -> FailurePauseEntry:
    """The `pause_log` entry of one failed attempt; `resets_at` is copied only when the
    failure has one, so an entry without a stated reset has no such key."""
    entry = FailurePauseEntry(
        kind="failure", value=failure.get("reason", "other"), section=failure.get("section", "")
    )
    if "resets_at" in failure:
        entry["resets_at"] = failure["resets_at"]
    return entry


def logged(
    patch: dict[str, Any], failures: Sequence[Failure] = (), lapses: Sequence[str] = ()
) -> dict[str, Any]:
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
    retry. `lapses` (GH-342) are bookkeeping steps that failed after the work was done, one
    `"lapse"` entry each, after the failures: no reader of the epoch counts them."""
    entries: list[PauseEntry] = [_failure_entry(f) for f in failures]
    if (cls := patch.get("escalation_class")) is not None:
        entries.append(EscalationPauseEntry(kind="escalation", value=cls))
    entries.extend(LapsePauseEntry(kind="lapse", value=line) for line in lapses)
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


def stated_reset(state: TaskState) -> datetime | None:
    """The latest `resets_at` among the current epoch's `account_limit` failure entries
    (ADR-0039, addendum GH-381), the latest by time whatever order they were logged in;
    entries without one, or with one that is not ISO-8601 with an offset, are ignored.
    `None` when none states a reset: the pause then follows its calendar as ever."""
    stated = [
        _instant(entry.get("resets_at"))
        for entry in _current_epoch(state)
        if entry["kind"] == "failure" and entry["value"] == "account_limit"
    ]
    return max((t for t in stated if t is not None), default=None)


def schedule_for(reason: FailureReason, chase_prior: int = 0) -> tuple[int, ...]:
    """The calendar a retake of `reason` follows: the flat `ACCOUNT_LIMIT_SCHEDULE` for
    a 429, the hourly `ACCOUNT_LIMIT_HOURLY` for a 429 whose whole chase (`chase_prior`)
    reached `ACCOUNT_LIMIT_CHASE_MAX`, the growing `RETAKE_SCHEDULE` for everything
    else. The only place a reason maps to a calendar; no other reason is ever hourly."""
    if reason != "account_limit":
        return RETAKE_SCHEDULE
    return (
        ACCOUNT_LIMIT_HOURLY if chase_prior >= ACCOUNT_LIMIT_CHASE_MAX else ACCOUNT_LIMIT_SCHEDULE
    )


def low_confidence_type(guess: TaskType) -> TaskType:
    """The type a low-confidence classification continues with instead of escalating:
    the PjM's guess, except `platform_enhancement`, which the feature path takes (a
    wrong `bug` or `feature` guess costs a review pass; a wrong platform one would skip
    the PM's definition). The only place that resolution is written."""
    return "feature" if guess == "platform_enhancement" else guess


def interview_for_section(section: str) -> Interview | None:
    """The interview whose own section is `section`, `None` for any other."""
    for spec in INTERVIEWS:
        if spec.section == section:
            return spec
    return None


def failed_interviews(state: TaskState) -> list[Interview]:
    """The interviews any `"failure"` entry of the current epoch broke inside, each
    once, in order of failure: empty when none did."""
    specs: list[Interview] = []
    for entry in _current_epoch(state):
        if entry["kind"] != "failure":
            continue
        spec = interview_for_section(entry["section"])
        if spec is not None and spec not in specs:
            specs.append(spec)
    return specs


def auto_resume_limit(state: TaskState, reason: FailureReason) -> int:
    """How many window slots this pause's current epoch gets, never `0` and never a
    flag. An `account_limit` gets the whole flat calendar, interview or not: its
    interview ceiling is `interview_ceiling_spent`'s, which counts only the retakes
    that ran a round. Otherwise one when any `"failure"` entry broke inside an
    interview's own section (`failed_interviews`) -- a retake already gives an
    interview two rounds of margin (`INTERVIEW_RETAKE_ROUNDS`), so a long automatic
    chase on top of that is not this task's own risk to take -- six otherwise, the
    whole calendar."""
    if reason == "account_limit":
        return len(ACCOUNT_LIMIT_SCHEDULE)
    return 1 if failed_interviews(state) else len(RETAKE_SCHEDULE)


# Slack on every comparison between a child's own clock (`started_at`/`ended_at`, the
# machine's) and GitHub's `createdAt` of a comment the poller posted: it absorbs the
# `gh issue comment` round trip after the spawn, GitHub's whole-second timestamps and
# NTP skew, and is far below the 15 minutes between two slots. Every error it allows
# resolves toward spending (a retake counted, a ceiling spent), which sends the task to
# a human sooner and never pays for an attempt that should not happen.
CLOCK_TOLERANCE = timedelta(seconds=120)


def _instant(value: object) -> datetime | None:
    """`value` as an aware datetime, `None` when it is missing, not text, not ISO-8601
    or carries no offset (it could not be compared with GitHub's own `createdAt`)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def run_ends(state: TaskState) -> list[datetime] | None:
    """Every run's own `ended_at`, `None` when any is missing or unreadable: the caller
    then falls back to the whole-chase count, which fails toward the human."""
    ends: list[datetime] = []
    for run in state.get("runs", []):
        end = _instant(run.get("ended_at"))
        if end is None:
            return None
        ends.append(end)
    return ends


def round_starts(rounds: Sequence[RoleRun]) -> list[datetime] | None:
    """Every round's own `started_at`, `None` when any is missing or unreadable."""
    starts: list[datetime] = []
    for run in rounds:
        start = _instant(run.get("started_at"))
        if start is None:
            return None
        starts.append(start)
    return starts


def window_probes(retake_times: Sequence[datetime], last_run_end: datetime | None) -> int:
    """How many retakes of the current account window (ADR-0039, 2026-10-05 addendum):
    the `Auto-resumed:` comments' own times that fall after the last delivered run
    ended -- a delivered run proves the account's quota was back, so the window starts
    over. `last_run_end=None` means no run at all: they all count."""
    if last_run_end is None:
        return len(retake_times)
    return sum(1 for t in retake_times if t > last_run_end - CLOCK_TOLERANCE)


def productive_retakes(retake_times: Sequence[datetime], starts: Sequence[datetime] | None) -> int:
    """How many retakes ran at least one interview round: retake i is productive when
    some round started in `[t_i - CLOCK_TOLERANCE, t_{i+1} - CLOCK_TOLERANCE)`, the
    last window open-ended. A retake the 429 cut on turn 1 ran nothing, so it does not
    spend the interview's ceiling. `starts=None` (a round's start unreadable) returns
    1: the ceiling counts as spent, failing closed."""
    if starts is None:
        return 1
    times = sorted(retake_times)
    productive = 0
    for i, t in enumerate(times):
        lower = t - CLOCK_TOLERANCE
        upper = times[i + 1] - CLOCK_TOLERANCE if i + 1 < len(times) else None
        if any(s >= lower and (upper is None or s < upper) for s in starts):
            productive += 1
    return productive


def interview_ceiling_spent(state: TaskState, reason: FailureReason, productive: int) -> bool:
    """Whether an `account_limit` pause cut inside an interview already had a retake
    that ran a round: the ceiling ADR-0039 gives interviews (a retake gives it
    `INTERVIEW_RETAKE_ROUNDS` more rounds), spent only by retakes that ran something."""
    return reason == "account_limit" and bool(failed_interviews(state)) and productive >= 1


def _schedule_exhausted(prior: int, limit: int, *, schedule: tuple[int, ...]) -> bool:
    """Whether `prior` automatic attempts have already used up every slot
    `schedule[:limit]` has: `next_attempt_at`'s own exhaustion check,
    factored out so `poll_plan` can tell it apart from "not due yet" -- both of those
    read as `None` from `next_attempt_at` alone, and only the first (a calendar that
    ran out, not one still ticking) must stop blocking a launch."""
    return prior >= len(schedule[:limit])


def next_attempt_at(
    since: datetime, prior: int, limit: int, now: datetime, *, schedule: tuple[int, ...]
) -> datetime | None:
    """The next automatic attempt's own time, but only once it is due (issue #137):
    `None` both when the calendar is spent (`_schedule_exhausted`, no slot left to
    schedule) and when the next slot has not arrived yet (`now` before it) -- the two
    reasons a caller does not act on this pause read alike, one check instead of two.
    `since` is the moment the wait is measured from: the last `Escalated:` comment's
    own time for a retake, the last launch attempt's own time for a launch (Tech Lead
    plan); `prior` is how many automatic attempts already happened since then. Never
    reads a clock of its own (README.md, 14): `now` arrives as an argument, like
    `since`. `schedule` is the calendar the caller's own attempts follow, never
    defaulted: a launch passes `LAUNCH_SCHEDULE`, a retake the one its reason names."""
    if _schedule_exhausted(prior, limit, schedule=schedule):
        return None
    target = since + timedelta(minutes=schedule[prior])
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


@dataclass(frozen=True)
class LockFacts:
    """What `rules.interrupted_run` needs of a run's lock record: `other_boot` is
    whether the record was written in a previous boot (`None`: the boot is unknown, an
    old record or a machine with no `/proc`), `alive` is whether the process it names
    is running now. The caller derives both (`boot.boot_verdict` and `liveness.
    is_alive`); this module imports neither, it only decides."""

    other_boot: bool | None
    alive: bool
    # The version the run recorded when it raised under the graph (`RunLock.keep_crashed`);
    # `None` for a record with no crash mark, which reads exactly as before.
    crashed_version: str | None = None


RetakeVerdict = Literal["retake", "retake_crash", "human", "running", "none"]

# How many times the poller retakes one task after a reboot before the run is the
# human's. A judgement, not a measurement: a reboot is rare, so a run that keeps being
# found dead after three retakes is dying of something else.
RETAKE_AFTER_REBOOT_MAX = 3

# How many times the poller retakes one task after its run raised, under one stage0
# version, and how many in all, both counted from the last human comment. A judgement,
# not a measurement: a fix that landed gets two fresh tries under its new version, and a
# task that raises under every version is not going to be cured by a seventh retake.
CRASH_RETAKE_PER_VERSION_MAX = 2
CRASH_RETAKE_TOTAL_MAX = 6

# The statuses where a run has nothing left to retake: the work is delivered, ruled
# out, or waiting on a human's answer.
_NOT_INTERRUPTED_STATUSES = (
    "ready_for_review",
    "discarded",
    "split",
    "tracker_only",
    "waiting_on_human",
)


def crash_version_key(version: str | None) -> str | None:
    """The key a crash retake is counted under: the version without a trailing `+dirty`
    (an edited working tree is still the same sha), and `None` for a version that says
    nothing -- `None` or `unknown` -- which never produces a retake."""
    if version is None:
        return None
    key = version.removesuffix("+dirty")
    if not key or key == "unknown":
        return None
    return key


def _resuming_fragment(key: str) -> str:
    """The one place the `resuming under <key> ` fragment is spelled: the comment
    writes it and the counter reads it, so they cannot drift. The trailing space keeps
    a key from matching another it is a prefix of."""
    return f"resuming under {key} "


def crash_retake_comment(crashed_version: str, key: str, n: int, cap: int) -> str:
    """The text after the `RETAKEN_AFTER_CRASH` prefix of the comment a crash retake
    posts: the version the run raised under, the key it resumes under, and which retake
    of `cap` this is."""
    return f"run raised under {crashed_version}; {_resuming_fragment(key)}(retake {n} of {cap})"


def crash_retake_counts(bodies: Sequence[str], key: str, prefix: str) -> tuple[int, int]:
    """`(at_version, total)` over comment bodies: those starting with `prefix`, and among
    them those that resumed under `key`. The caller hands over only the bodies after the
    last non-system comment, so a human comment resets both."""
    ours = [b[len(prefix) :] for b in bodies if b.startswith(prefix)]
    fragment = _resuming_fragment(key)
    return sum(1 for b in ours if fragment in b), len(ours)


def interrupted_run(
    raw: TaskState,
    lock: LockFacts | None,
    prior_retakes: int,
    crash_prior_at_version: int = 0,
    crash_prior_total: int = 0,
    version_key: str | None = None,
) -> RetakeVerdict:
    """Whether the poller retakes an open task whose run is gone (GH-176, GH-308):

    - `none`: there is no lock record (`lock is None`), or the document has a truthy
      `closed_at`, `escalated` or `pull_request`, or its `status` is `ready_for_review`,
      `discarded` or `waiting_on_human` -- this is not an interrupted run at all;
    - `human`: the document has no `status` key at all (not one this rule can read);
    - `retake_crash`: the record carries a crash mark (`lock.crashed_version`), checked
      before `alive` because the mark proves the process ended, `version_key` is not
      `None`, and both `crash_prior_at_version` (retakes under this key) and
      `crash_prior_total` are under `CRASH_RETAKE_PER_VERSION_MAX` and
      `CRASH_RETAKE_TOTAL_MAX`. With a mark and a spent cap or no key, a record of
      another boot falls to the reboot rule below, anything else is the `human`'s;
    - `running`: `lock.alive`, the process still runs;
    - `retake`: `lock.other_boot is True` and `prior_retakes` is under
      `RETAKE_AFTER_REBOOT_MAX` -- a reboot killed it;
    - `human` in every other case: the same boot (a human killed it), an unknown boot
      (never retaken), or the cap spent."""
    if lock is None:
        return "none"
    if raw.get("closed_at") or raw.get("escalated") or raw.get("pull_request"):
        return "none"
    if "status" not in raw:
        return "human"
    if raw["status"] in _NOT_INTERRUPTED_STATUSES:
        return "none"
    if lock.crashed_version is not None:
        if (
            version_key is not None
            and crash_prior_at_version < CRASH_RETAKE_PER_VERSION_MAX
            and crash_prior_total < CRASH_RETAKE_TOTAL_MAX
        ):
            return "retake_crash"
        if lock.other_boot is True and prior_retakes < RETAKE_AFTER_REBOOT_MAX:
            return "retake"
        return "human"
    if lock.alive:
        return "running"
    if lock.other_boot is True and prior_retakes < RETAKE_AFTER_REBOOT_MAX:
        return "retake"
    return "human"


# Spend-paced concurrency (GH-177, ADR-0039 addendum): another run launches only while
# the account's trailing spend is under a sustainable pace. Everything below is pure:
# the caller reads the documents and the clock and hands over plain values.
TRAILING_SPEND_WINDOW = timedelta(hours=5)
# The shortest span a trailing figure is divided by: a ten-minute burst of ten dollars is
# 20 USD/h over the floor, not the 60 USD/h its own ten minutes would suggest.
SPEND_PACE_FLOOR_S = 1800.0


class SpendRun(NamedTuple):
    """One run's cost over the span it ran, both ends timezone-aware UTC."""

    started_at: datetime
    ended_at: datetime
    cost_usd: float


def spend_runs(runs: Sequence[Mapping[str, object]]) -> tuple[list[SpendRun], int]:
    """The `SpendRun`s of a task document's `runs`, and how many it skipped.

    Reads `RoleRun.cost_usd` only: `subagent_cost_usd` is not additive (it is a share of
    what `cost_usd` already holds) and `failed_cost_usd` is already inside `cost_usd`,
    so summing either would count the same dollars twice. A run is skipped (and counted)
    when its `ended_at` is missing, unparseable or naive, or its `cost_usd` is not a
    finite non-negative number. A missing, unparseable or naive `started_at`, or one
    later than `ended_at`, makes the run zero-length at `ended_at`. Aware values are
    converted to UTC."""
    out: list[SpendRun] = []
    skipped = 0
    for run in runs:
        end = _instant(run.get("ended_at"))
        cost = run.get("cost_usd")
        if (
            end is None
            or isinstance(cost, bool)
            or not isinstance(cost, int | float)
            or not math.isfinite(cost)
            or cost < 0
        ):
            skipped += 1
            continue
        end = end.astimezone(UTC)
        start = _instant(run.get("started_at"))
        start = end if start is None else min(start.astimezone(UTC), end)
        out.append(SpendRun(start, end, float(cost)))
    return out, skipped


def merged_spend(
    published: Sequence[SpendRun], fresh: Sequence[SpendRun], now: datetime
) -> list[SpendRun]:
    """What a poller publishes: `published` and `fresh` keyed on `(started_at,
    ended_at)`, the fresh entry winning over a published one with the same key (a cost
    that changed counts once, at its new value), a published entry absent from `fresh`
    kept (archiving or closing a document never lowers the trailing spend), and every
    entry that ended before `now - TRAILING_SPEND_WINDOW` dropped -- the only pruning.
    Sorted, so the same facts always serialise the same way."""
    by_key: dict[tuple[datetime, datetime], SpendRun] = {}
    for entry in (*published, *fresh):
        by_key[(entry.started_at, entry.ended_at)] = entry
    horizon = now - TRAILING_SPEND_WINDOW
    return sorted(e for e in by_key.values() if e.ended_at >= horizon)


def trailing_spend(
    runs: Sequence[SpendRun], now: datetime, window: timedelta = TRAILING_SPEND_WINDOW
) -> tuple[float, float]:
    """`(prorated USD, covered seconds)` of `runs` over `[now - window, now]`.

    A run counts by the share of its own duration that overlaps the window, its end
    clamped to `now`; a zero-length run counts whole when it ended inside the window.
    Covered seconds is the span from the earliest overlapping start to `now`, capped at
    the window and floored at `SPEND_PACE_FLOOR_S`; with nothing in the window it is the
    floor."""
    horizon = now - window
    spent = 0.0
    earliest: datetime | None = None
    for r in runs:
        if r.ended_at < horizon:
            continue
        end = min(r.ended_at, now)
        start = min(max(r.started_at, horizon), end)
        duration = (r.ended_at - r.started_at).total_seconds()
        if duration <= 0:
            spent += r.cost_usd
        else:
            overlap = (end - start).total_seconds()
            if overlap <= 0:
                continue
            spent += r.cost_usd * overlap / duration
        earliest = start if earliest is None else min(earliest, start)
    if earliest is None:
        return 0.0, SPEND_PACE_FLOOR_S
    covered = min(window.total_seconds(), (now - earliest).total_seconds())
    return spent, max(covered, SPEND_PACE_FLOOR_S)


def paced_launch_fits(
    *,
    spent_usd: float,
    covered_s: float,
    live_runs: int,
    ceiling: int,
    pace_usd_per_h: float | None,
) -> bool:
    """Whether one more run may launch: with no pace, only while `live_runs < ceiling`
    (today's rule); with no run live, whenever the ceiling allows any (the first run is
    never held back by the pace); otherwise also only while the trailing spend per hour
    is strictly under `pace_usd_per_h`."""
    if live_runs == 0:
        return ceiling > 0
    if live_runs >= ceiling:
        return False
    if pace_usd_per_h is None:
        return True
    return spent_usd / (max(covered_s, SPEND_PACE_FLOOR_S) / 3600) < pace_usd_per_h


@dataclass(frozen=True)
class SpendPace:
    """What `poll_plan` needs to gate a launch by spend: the machine's trailing figure
    (`spent_usd` over `covered_s`), `live_runs` alive before this round, and the pace."""

    spent_usd: float
    covered_s: float
    live_runs: int
    pace_usd_per_h: float


PollActionKind = Literal["close", "retake", "resume", "launch", "merge", "land"]


@dataclass(frozen=True)
class PollAction:
    """One action `rules.poll_plan` orders into this round: which issue, and whether
    the poller's child process is `agentic close|resume|run|merge` (the kind names the verb,
    the caller knows the mapping -- domain never names a CLI command)."""

    kind: PollActionKind
    issue: int
    # Why a `retake` was ordered: the run a reboot cut, or one that raised under the graph
    # (the caller writes a different comment for each). Other kinds leave the default.
    reason: Literal["reboot", "crash"] = "reboot"


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
    # The whole-chase count (a delivered run never resets it, only a human comment
    # does) `ACCOUNT_LIMIT_CHASE_MAX` bounds, and whether an interview's ceiling is
    # spent: appended with defaults so a candidate built without them stays valid.
    chase_prior: int = 0
    ceiling_spent: bool = False
    # An `account_limit` pause's stated reset (ADR-0039, addendum GH-381): the latest
    # `resets_at` of the current epoch's failures, and whether a retake was already made
    # after the `Escalated:` comment that followed it. Read only by `account_limit_due`.
    resets_at: datetime | None = None
    retaken: bool = False


def account_limit_due(
    since: datetime,
    prior: int,
    limit: int,
    now: datetime,
    *,
    schedule: tuple[int, ...],
    resets_at: datetime | None,
    retaken: bool,
) -> datetime | None:
    """The one place an `account_limit` pause's due time is decided (ADR-0039, addendum
    GH-381), only once due like `next_attempt_at`: the stated reset `resets_at` for the
    first retake after the `Escalated:` comment that followed it (never before it, with no
    margin, and whatever `prior` and `limit` say: the slots before the reset are neither
    run nor counted); `next_attempt_at(since, prior, limit, now, schedule)` otherwise --
    no stated reset, or a retake already made (`retaken`) -- so a later retake keeps its
    spacing and its exhaustion. Reads no clock: `now` arrives as an argument."""
    if resets_at is not None and not retaken:
        return resets_at if resets_at <= now else None
    return next_attempt_at(since, prior, limit, now, schedule=schedule)


def _governing_reset(r: ResumeCandidate) -> datetime | None:
    """The stated reset that decides `r`'s due time, `None` when none does: only an
    `account_limit` pause that is not yet retaken and has not spent the persisted week
    (`account_limit_persisted` escalates it, whatever the hint says)."""
    if r.reason != "account_limit" or r.retaken or account_limit_persisted(r):
        return None
    return r.resets_at


def retake_calendar(
    reason: FailureReason, prior: int, chase_prior: int, limit: int
) -> tuple[tuple[int, ...], int, int]:
    """`(schedule, prior, limit)` a retake of `reason` is measured on: its reason's own
    calendar, and for an `account_limit` past the chase max the hourly one, indexed by
    the retakes made since that max with all of it as the limit. The one place that
    mapping is applied: the poller's candidates (`_calendar`) and the landing's diagnoser
    retry (`domain.landing.diagnose_retry`) both ask it."""
    schedule = schedule_for(reason, chase_prior)
    if schedule is ACCOUNT_LIMIT_HOURLY:
        return schedule, chase_prior - ACCOUNT_LIMIT_CHASE_MAX, len(schedule)
    return schedule, prior, limit


def _calendar(r: ResumeCandidate) -> tuple[tuple[int, ...], int, int]:
    """`retake_calendar` of a resume candidate's own fields."""
    return retake_calendar(r.reason, r.prior, r.chase_prior, r.limit)


def retake_due(r: ResumeCandidate, now: datetime) -> datetime | None:
    """The candidate's next automatic attempt, only once due (`next_attempt_at` on the
    calendar `_calendar` names)."""
    schedule, prior, limit = _calendar(r)
    return account_limit_due(
        r.since,
        prior,
        limit,
        now,
        schedule=schedule,
        resets_at=_governing_reset(r),
        retaken=r.retaken,
    )


def resume_exhausted(r: ResumeCandidate) -> bool:
    """Whether a resume candidate needs a human now: its own calendar is spent
    (`limit` counts window slots only, `0` is just an empty calendar; an `account_limit`
    past `ACCOUNT_LIMIT_CHASE_MAX` has the hourly week) or an interview's ceiling is
    spent. The one exhaustion decision `poll_plan` takes over a resume. A pause a stated
    reset governs (`_governing_reset`) is not exhausted by the calendar it skips."""
    schedule, prior, limit = _calendar(r)
    if _governing_reset(r) is not None:
        return r.ceiling_spent
    return _schedule_exhausted(prior, limit, schedule=schedule) or r.ceiling_spent


def account_limit_persisted(r: ResumeCandidate) -> bool:
    """Whether `r` is an `account_limit` pause that spent the whole hourly week: the
    one exhaustion the system escalates itself (`account_limit_persisted_patch`)."""
    return r.reason == "account_limit" and r.chase_prior >= ACCOUNT_LIMIT_CHASE_MAX + len(
        ACCOUNT_LIMIT_HOURLY
    )


def account_limit_persisted_patch() -> dict[str, Any]:
    """The escalation of an account limit that outlasted the hourly week: class
    `failure`, with a failure entry of reason `other` in the log so `auto_resumable`
    (every failure external) no longer finds the pause eligible -- the escalation is
    written once, not each tick."""
    failure = Failure(
        role="Poller",
        attempt=0,
        error=ACCOUNT_LIMIT_PERSISTED,
        cost_usd=0.0,
        session_id="",
        reason="other",
    )
    return logged({**escalation("failure", ACCOUNT_LIMIT_PERSISTED)}, [failure])


def needs_a_human(state: TaskState) -> bool:
    """Whether a pause or escalation adds the `needs-human` label (GH-381): the one
    decision every site that labels calls. False only for a pause the poller retakes on
    its own because the account limit alone caused it (`auto_resumable` reads
    `account_limit`) and that has not spent the hourly week (`ACCOUNT_LIMIT_PERSISTED`,
    the escalation that is left to the human). Everything else keeps the label: an
    `upstream` or `network` pause, a mixed set of external and own failures, an
    escalation that is not a failure."""
    if state.get("escalated") == ACCOUNT_LIMIT_PERSISTED:
        return True
    return auto_resumable(state) != "account_limit"


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


RunKind = Literal["role", "merge", "land_checks", "land_model"]
"""What a registered child of the poller is: `role` (`run`, `resume`), `merge` (the
merge child), and the two kinds a `land` child registers under: `land_checks` (a step
that only syncs cleanly, reruns checks or files follow-ups, never a model pass) and
`land_model` (any step that can spend one, and the default)."""


def counts_against_cap(kind: RunKind) -> bool:
    """Whether a live child of this kind occupies a slot of `AGENTIC_MAX_CONCURRENT_RUNS`:
    the one place that decides it. Only the children that can spend a model session do."""
    match kind:
        case "role" | "land_model":
            return True
        case "merge" | "land_checks":
            return False
        case _:
            assert_never(kind)


LAND_HEADROOM = 1
"""The reserved slot (ADR-0045 addendum, GH-270; glossary: Cupo reservado para aterrizaje): a
`land_model` land may start while the counted live children are fewer than the cap plus this.
Only a land reads it -- launches, resumes and retakes keep the plain cap -- and the land and
the resume it spawns still count, which keeps the reserve from growing past one."""


@dataclass(frozen=True)
class LandCandidate:
    """One issue whose refusal has a landing step to take, and the `RunKind` its child
    registers under (`landing.run_kind_of`): the kind decides whether it takes a slot."""

    issue: int
    kind: RunKind


@dataclass(frozen=True)
class Deferred:
    """A merge or land `poll_plan` held back this round, and why: `no_slot` (a `land_model`
    child needs a slot and none is left, headroom included), `exempt_busy` (the one exempt
    child the machine allows is already live, or this round already starts one),
    `pause_waiting` (a `land_model` land while this product has an `account_limit` pause
    still waiting out its calendar: the land would spend the session the pause protects) or
    `land_queued` (a `land_model` land with a slot to start in, yielding to another product's
    land whose wait began earlier: first come, first landed, GH-362)."""

    kind: Literal["merge", "land"]
    issue: int
    reason: Literal["no_slot", "land_queued", "exempt_busy", "pause_waiting"]


def holds_launches(deferred: Deferred) -> bool:
    """Whether this deferral opens an Espera de aterrizaje (ADR-0045 addendum, GH-362): the
    product's poller publishes it, and the other products hold every counted start below a
    land while it lasts. Only a `land` that waits does, for a slot (`no_slot`) or for an earlier
    wait of another product (`land_queued`): `exempt_busy` is the serialisation of the children
    that take no slot, and `pause_waiting` waits on the
    account's calendar, which another product's start does not lengthen."""
    match deferred.reason:
        case "no_slot" | "land_queued":
            return deferred.kind == "land"
        case "exempt_busy" | "pause_waiting":
            return False
        case _:
            assert_never(deferred.reason)


LAND_WAIT_TTL = timedelta(minutes=15)
"""How long a published Espera de aterrizaje holds the other products after its writer last
rewrote it: a poller that died never holds anyone for longer (GH-362). A poller rewrites it
every round while its land waits, so a live one never lets it expire."""


@dataclass(frozen=True)
class LandWait:
    """The fact one product's poller publishes while a land of its own waits for a slot:
    `root_key` names the tasks root of the writer (`root_key`), `since` when the wait began
    and `issues` the lands that wait."""

    product: str
    root_key: str
    written_at: datetime
    since: datetime
    issues: tuple[int, ...]


def root_key(root: str) -> str:
    """The twelve hex digits that name one poller's tasks root (the resolved path) in the
    registry's per-product files. The one place the key is computed, so a poller recognises
    its own record."""
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]


def land_queued_behind(
    own_since: datetime | None, own_key: str, others: Sequence[LandWait]
) -> bool:
    """Whether this product's land yields to another product's published wait (GH-362, first
    come, first landed): some record of `others` (the fresh records of the other roots) began
    earlier than this product's own wait (`own_since`), a tie going to the lower `root_key`.
    A product with no wait of its own yet (`None`) is behind any fresh record. The oldest
    wait is behind none, so it always starts first once a slot is free."""
    if own_since is None:
        return bool(others)
    return any((w.since, w.root_key) < (own_since, own_key) for w in others)


def land_wait_since(previous: datetime | None, waiting: bool, now: datetime) -> datetime | None:
    """When the wait this round publishes began: `None` when no land waits (the record is
    removed), the `since` of the poller's own previous, still fresh record when it had one,
    otherwise `now`."""
    if not waiting:
        return None
    return now if previous is None else previous


@dataclass(frozen=True)
class PollPlan:
    """What `poll_plan` decides: the `actions` in the order to carry them out, and what it
    `deferred`, which is the only source of a poll line about a waiting merge or land."""

    actions: list[PollAction]
    deferred: list[Deferred]


def account_paused(resumes: Sequence[ResumeCandidate], now: datetime) -> bool:
    """Whether this product has an `account_limit` pause still waiting out its calendar:
    pending (`retake_due` is `None`), not exhausted and not persisted. It reads this
    product's own pause, never the account's: another product's 429 on the same account is
    invisible here. `upstream` and `network` pauses never count."""
    return any(
        r.reason == "account_limit"
        and not resume_exhausted(r)
        and not account_limit_persisted(r)
        and retake_due(r, now) is None
        for r in resumes
    )


SCRATCH_VOLUME = "scratch"
TMP_VOLUME = "/tmp"


def space_holds(scratch: SpaceReading, tmp: SpaceReading, floor_bytes: int) -> tuple[str, ...]:
    """The names (`SCRATCH_VOLUME`, `TMP_VOLUME`) of the volumes under the floor (GH-391): fewer
    free bytes than `floor_bytes`, or fewer free inodes than `INODE_FLOOR`. Empty means the round
    may start children; anything else is what `poll_plan` takes as `held`."""
    return tuple(
        name
        for name, reading in ((SCRATCH_VOLUME, scratch), (TMP_VOLUME, tmp))
        if reading.free_bytes < floor_bytes or reading.free_inodes < INODE_FLOOR
    )


def poll_plan(
    *,
    closes: Sequence[int],
    resumes: Sequence[ResumeCandidate],
    launches: Sequence[LaunchCandidate],
    available_slots: int,
    now: datetime,
    retakes: Sequence[int] = (),
    merges: Sequence[int] = (),
    pace: SpendPace | None = None,
    lands: Sequence[LandCandidate] = (),
    live_exempt: int = 0,
    land_slots: int | None = None,
    crash_retakes: Sequence[int] = (),
    closeness: Mapping[int, int] = {},
    others_landing: bool = False,
    earlier_land_wait: bool = False,
    held: Sequence[str] = (),
) -> PollPlan:
    """This round's actions, in the order the Tech Lead plan fixes: every close first
    (merging freed the slot it used, if any, so closing never spends one); then what
    counts against the cap, nearest to merge first (`closeness`, below): every `retakes`
    and `crash_retakes` issue (a run a reboot interrupted, `interrupted_run`, or one that
    raised) and the due resumes, together, while `available_slots` lasts; at most one launch,
    the lowest-numbered issue with every blocker resolved and its own wait (if any) elapsed
    -- and only when no eligible pause anywhere in the
    product is still waiting out its own calendar (its own next slot has not arrived
    yet, but one is still scheduled), whether or not a slot is free for it. A pause
    whose calendar is spent (`resume_exhausted`) does not count as "still waiting": it
    needs a human now, same as any other escalation, and must not freeze every launch
    in the product until one acts. A retake spends a slot exactly as a resume does, and
    never changes the rule above: a waiting resume still blocks every launch.
    `available_slots` already reflects the machine-wide cap
    (`AGENTIC_MAX_CONCURRENT_RUNS`) minus every run alive right now, counted by the
    caller; this function never reads the registry itself.

    `merges` (GH-178) are the issues `rules.auto_merge_decision` found ready to merge or
    to check. A merge child runs no model, so `counts_against_cap` is false for it: it takes
    no slot, and a round with every slot taken still starts it (GH-263, ADR-0045 addendum).

    `closeness` (GH-362, Cercanía) maps an issue to its `rules.closeness` rank; a missing
    key is rank 0 and an empty mapping makes every rank tie. Retakes and due resumes are
    started in `(-rank, kind, issue)` order, a retake before a resume at a tie, and the
    land competes at the rank of `CLOSENESS` "land", so it goes first in `actions`.

    `others_landing` (GH-362, Espera de aterrizaje) is true while another product has a fresh
    published wait for a land: every start ranked below a land is held, that is every launch,
    resume and retake, whatever its phase. Lands, closes and the children that take no slot
    are untouched, and nothing held leaves a deferral.

    `earlier_land_wait` (GH-362, `land_queued_behind`) is true while another product's wait
    for a land began earlier than this product's own: the `land_model` land that would start
    is `Deferred(land_queued)` instead and takes no slot, and the next one waits for a slot as
    ever. The `account_paused` row wins (`pause_waiting`), and the exempt children are untouched.

    `lands` (GH-239) are the issues whose refusal has a built landing step not yet taken on
    their head (`landing_step`; `wait` and `none` are never among them), each with the kind
    its child registers under. A `land_model` land can spend a model, so it counts: at most
    one, the lowest, goes before the launch (GH-270), and it may use the reserved slot
    (`LAND_HEADROOM`). `land_slots` is how many counted children fit with the headroom
    (`None`: `available_slots`, no headroom). With `A = available_slots`, `H = land_slots`
    and `n` the retakes and due resumes that could start:

    | case | land | retakes and resumes, in rank order | the launch sees |
    |---|---|---|---|
    | `account_paused` | `Deferred(pause_waiting)` | `min(n, A)`, regular | `A - started` |
    | `H <= 0` | `Deferred(no_slot)` | none | 0 |
    | `H > 0`, `A > n` | regular slot | all `n`, regular | `A - n - 1` |
    | `H > 0`, `A <= n`, `H > A` | headroom | `A`, regular | 0 |
    | `H > 0`, `A <= n`, `H == A` | regular slot | `A - 1`, regular | 0 |

    (`H <= 0` takes no slot at all, so only an `H` of 0 with an `A` of 0 reaches it in
    production: `run_once` passes `H = A + 1`, or `H = A = 0`.) Retakes and resumes never
    use the headroom, and at most one land starts per round. While `account_paused` holds,
    every `land_model` land is `Deferred(pause_waiting)` instead; pacing never gates a land.
    A `land_checks` land counts as the merge does not.

    The children that do not count (`merge`, `land_checks`) are bounded together instead:
    `live_exempt` is how many are live on the machine right now, and at most one is live
    and at most one starts per round -- the lowest merge if there is one, otherwise the
    lowest `land_checks` land. They come last in `actions`, after every action that takes a
    slot, so the wait for a child to register never delays a role action.

    Everything held back is in `PollPlan.deferred`: a `land_model` land with no slot is
    `no_slot`, one held by an account-limit pause is `pause_waiting`; every merge or
    `land_checks` land that did not start is `exempt_busy`.

    `pace` (GH-177, `None` is today's behaviour) gates only the launch: it goes out only
    if `paced_launch_fits` says so, counting this round's retakes and resumes as live
    runs (a launch after one on an empty machine is the second run) against a ceiling of
    `pace.live_runs` plus the `available_slots` received. Closes, retakes and resumes
    are never gated, and a waiting pause still blocks every launch.

    `held` (GH-391, `space_holds`) names the volumes under the free-space floor. When it is
    not empty the plan is the closes alone: no launch, resume, retake, land or merge starts,
    because a start would write scratch to a disk that is almost full. It defers nothing, so
    it opens no Espera de aterrizaje."""
    actions = [PollAction("close", issue) for issue in sorted(closes)]
    if held:
        return PollPlan(actions, [])
    received_slots = available_slots

    due_resumes = [r for r in resumes if not resume_exhausted(r) and retake_due(r, now) is not None]
    model_lands = sorted(c.issue for c in lands if counts_against_cap(c.kind))
    paused = account_paused(resumes, now)
    land_total = received_slots if land_slots is None else land_slots
    land_ready = bool(model_lands) and not paused and land_total > 0
    land_queued = land_ready and earlier_land_wait
    land_starts = land_ready and not land_queued

    # Rank order: the nearest to merge first; at a tie a retake goes before a resume, then
    # the lowest issue (crash before reboot only when the same issue is both).
    starts: list[tuple[tuple[int, int, int, str], PollAction]] = [
        (
            (-closeness.get(issue, 0), 0, issue, why),
            PollAction("retake", issue, reason="crash" if why == "crash" else "reboot"),
        )
        for issue, why in [(i, "reboot") for i in retakes] + [(i, "crash") for i in crash_retakes]
    ]
    starts += [
        ((-closeness.get(r.issue, 0), 1, r.issue, ""), PollAction("resume", r.issue))
        for r in due_resumes
    ]
    if others_landing:
        starts = []
    # The land takes a plain slot unless the starts below it would use them all; then it
    # takes the headroom if there is one. Retakes and resumes never use the headroom.
    land_on_headroom = land_starts and len(starts) >= received_slots and land_total > received_slots
    land_plain = land_starts and not land_on_headroom
    if land_starts:
        starts.append(
            ((-CLOSENESS.index("land"), 2, model_lands[0], ""), PollAction("land", model_lands[0]))
        )
    plain_for_starts = max(received_slots - (1 if land_plain else 0), 0)
    started = 0
    for _, action in sorted(starts, key=lambda start: start[0]):
        if action.kind == "land":
            actions.append(action)
        elif started < plain_for_starts:
            actions.append(action)
            started += 1
    slots_left = max(received_slots - started - (1 if land_plain else 0), 0)

    deferred: list[Deferred] = []
    if paused:
        deferred.extend(Deferred("land", issue, "pause_waiting") for issue in model_lands)
        model_lands = []
    elif land_starts:
        model_lands = model_lands[1:]
    elif land_queued:
        deferred.append(Deferred("land", model_lands[0], "land_queued"))
        model_lands = model_lands[1:]
    deferred.extend(Deferred("land", issue, "no_slot") for issue in model_lands)

    some_resume_still_waiting = any(
        not resume_exhausted(r) and retake_due(r, now) is None for r in resumes
    )
    if slots_left > 0 and not some_resume_still_waiting and not others_landing:
        ready = sorted(
            (
                c
                for c in launches
                if not c.blocked
                and (
                    c.since is None
                    or next_attempt_at(c.since, c.prior, c.limit, now, schedule=LAUNCH_SCHEDULE)
                    is not None
                )
            ),
            key=lambda c: c.issue,
        )
        if ready and (
            pace is None
            or paced_launch_fits(
                spent_usd=pace.spent_usd,
                covered_s=pace.covered_s,
                live_runs=pace.live_runs + started,
                ceiling=pace.live_runs + received_slots,
                pace_usd_per_h=pace.pace_usd_per_h,
            )
        ):
            actions.append(PollAction("launch", ready[0].issue))
            slots_left -= 1

    exempt_merges = sorted(merges)
    exempt_lands = sorted(c.issue for c in lands if not counts_against_cap(c.kind))
    if live_exempt == 0 and exempt_merges:
        actions.append(PollAction("merge", exempt_merges[0]))
        exempt_merges = exempt_merges[1:]
    elif live_exempt == 0 and exempt_lands:
        actions.append(PollAction("land", exempt_lands[0]))
        exempt_lands = exempt_lands[1:]
    deferred.extend(Deferred("merge", issue, "exempt_busy") for issue in exempt_merges)
    deferred.extend(Deferred("land", issue, "exempt_busy") for issue in exempt_lands)
    return PollPlan(actions, deferred)


# How many times the checks run on one pull request head before the poller leaves it to
# the human (GH-178, ADR-0045): a busy master moves under an approved PR, and each move
# asks for another full run.
MERGE_ATTEMPTS_MAX = 3

# How many failed `Tracker.merge` calls one head may cost (GH-196): it bounds merge calls,
# while `MERGE_ATTEMPTS_MAX` bounds check runs. A merge that fails with the pull request
# still open changes nothing else the decision reads, so without this bound every poll
# round would call `gh pr merge` again.
MERGE_CALLS_MAX = 3

MergeFailure = Literal["no_merge_method", "gh_unavailable", "gh_unreadable", "gh_rejected"]
"""Why a `Tracker.merge` call failed, a closed set decided where the error is raised (the
adapter, never by reading `gh`'s stderr): `no_merge_method` the repository allows none,
`gh_unavailable` the CLI is not installed, `gh_unreadable` its answer about the
repository's settings could not be read, `gh_rejected` `gh pr merge` itself failed."""

# A class that repeating cannot change costs one call, not `MERGE_CALLS_MAX`.
TERMINAL_MERGE_FAILURES: frozenset[MergeFailure] = frozenset({"no_merge_method"})

MERGE_FAILURE_ADVICE: dict[MergeFailure, str] = {
    "gh_rejected": (
        "GitHub refused the merge; check branch protection, required reviews and the "
        "token's permissions, then merge by hand."
    ),
    "no_merge_method": (
        "The repository allows no merge method; enable one in its settings or merge by hand."
    ),
    "gh_unavailable": "`gh` is not installed where the poller runs.",
    "gh_unreadable": "`gh` could not read the repository's merge settings.",
}

# For a document whose stored class is missing or invalid.
MERGE_FAILURE_UNKNOWN_ADVICE = "The recorded failure class is missing; read the merge verb's log."

# Commit ids (GH-199, ADR-0045 addendum): a full id is 40 hex (SHA-1) or 64 (SHA-256); an
# abbreviation older than the change that records full ids counts as a prefix from here.
MIN_SHA_PREFIX = 7
FULL_SHA_LENGTHS = (40, 64)

_HEX = re.compile(r"[0-9a-fA-F]+")


def full_commit_id(commit: str | None) -> bool:
    """`commit`, stripped, is a whole object id: hex of a length in `FULL_SHA_LENGTHS`."""
    if commit is None:
        return False
    text = commit.strip()
    return len(text) in FULL_SHA_LENGTHS and _HEX.fullmatch(text) is not None


def same_commit(a: str | None, b: str | None) -> bool:
    """Whether two ids name the same commit, whatever length each was recorded at. The one
    comparison of a `reviewed_head` or a recorded `base`; no other prefix test of ids
    exists. An empty or missing id matches nothing; a prefix counts only between hex ids
    with the shorter at least `MIN_SHA_PREFIX` long."""
    if a is None or b is None:
        return False
    left, right = a.strip().lower(), b.strip().lower()
    if not left or not right:
        return False
    # Equal ids match whatever they are: the opaque ids of `tests/fakes.py` ("base",
    # "headN") keep matching themselves.
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    return (
        len(short) >= MIN_SHA_PREFIX
        and _HEX.fullmatch(short) is not None
        and _HEX.fullmatch(long) is not None
        and long.startswith(short)
    )


MergeRefusal = Literal[
    "not_ready",
    "pr_not_open",
    "adr_number_taken",
    "not_approved",
    "head_not_reviewed",
    "perf_unverified",
    "criteria_over_cap",
    "criteria_unreadable",
    "open_findings_blocking",
    "open_findings",
    "eval_set",
    "eval_set_unreadable",
    "attempts_exhausted",
    "checks_failed",
    "merge_conflict",
    "master_moved",
    "merge_failed",
    "ci_failed",
    "ci_missing",
    "ci_unreadable",
]
"""Why `auto_merge_decision` refuses, one reason per condition:

- `not_ready`: the task is not `ready_for_review`.
- `pr_not_open`: the pull request is merged, closed or unreadable. Second, so that a pull
  request that is not open is never reported as a review or findings problem.
- `adr_number_taken` (GH-229): an ADR the branch's own commits added has a number master
  already holds under another file name, or the rewrite that would fix it is ambiguous
  (`MergeFacts.adr_collision`). Third, so that no review, fix pass or check run is spent on
  a head that is about to change.
- `not_approved`: the reviewed unit (`reviewed_unit`) is not approved.
- `head_not_reviewed`: the pull request head is not the one Tech Lead reviewed, or either
  is unknown.
- `perf_unverified` (GH-318, ADR-0060): the plan convened QA Performance and the latest
  QA Performance run is not `approved`, or it was not measured on the pull request's own head
  (a missing or short sha fails closed). Right after `head_not_reviewed`, so a head nobody
  reviewed is never reported as a performance problem. The landing leaves it to the human.
- `criteria_over_cap` (GH-334): the head puts a criteria document past `CRITERIA_MAX_LINES`
  while making it longer than the merge base had it (`MergeFacts.criteria_regressed`, the
  messages naming file and count). The state master already holds is not refused, only what
  the pull request adds. Right after `perf_unverified`, before any finding. Left to the human.
- `criteria_unreadable` (GH-334): the criteria documents could not be read at the merge base
  or the head (`criteria_regressed is None`), so nothing proves the head adds no excess. Fails
  closed, same place and same landing step.
- `open_findings_blocking`: a `blocking` finding is still open. Checked before
  `open_findings`: a blocking one is never filed as a follow-up (GH-239).
- `open_findings`: an `important` finding of the task is still open with neither the final
  important pass done (`important_pass_done`) nor the follow-up filed (`followups_filed`), or
  an `outside` one is open with no follow-up filed; the landing fixes or files it and lands
  the pull request (GH-239, GH-294).
- `eval_set`: the issue is in the evaluation set; its merge is the human's verdict.
- `eval_set_unreadable`: the evaluation set could not be read, so nothing proves the
  issue is outside it.
- `merge_conflict`: the head definitely conflicts with the current master (GH-255). Refused
  first among the merge-side reasons, after `eval_set` and before `merge_failed`, so landing
  syncs master: a head that cannot merge makes the merge-call budget and the CI result mean
  nothing.
- `attempts_exhausted`: the checks already ran `MERGE_ATTEMPTS_MAX` times on this head
  and the master moved again.
- `checks_failed`: the checks ran on this head merged with the current master and failed.
- `master_moved`: master moved between the checks and the merge. Only the merge verb
  gives it, after its last fetch; the decision cannot see a fact that did not exist yet.
- `merge_failed`: `Tracker.merge` already failed `MERGE_CALLS_MAX` times on this head.
- `ci_failed`: the product declares its CI and a declared check is red on this head.
- `ci_missing`: a declared check was still pending, or never reported, `ci.CI_WAIT_MAX_S`
  after the push.
- `ci_unreadable`: the CI report could not be read for `ci.CI_WAIT_MAX_S` after the push
  (`gh` missing or its answer unreadable), which says nothing about any product check.
"""


@dataclass(frozen=True)
class MergeFacts:
    """Every fact `auto_merge_decision` reads, gathered by the caller from the product
    checkout's documents, the tracker and the upstream sha -- never from the pull
    request's own copy of the task document (GH-178). `in_eval_set` is `None` when the
    set was unreadable; `touched_paths` is `None` when not yet known (the poller decides
    before any worktree exists, the verb after); `current_master` is `None` when the
    upstream sha could not be read. `merge_failures` counts the failed merge calls on this
    head and `merge_failure` is the last one's class, which only the refusal comment reads
    (GH-196). The `ci_*` facts (GH-216) are read only when `ci_required`, the check names
    the product declares as its merge gate, is not empty: `ci_head` is the commit the CI
    report was read for, `ci_verdict` its classification (`ci.ci_state`), `ci_unreadable`
    the closed cause when it could not be read, `ci_fresh` whether it ran on a tree holding
    the current master (None: not known) and `ci_waited_s` the seconds since the wait's
    anchor (None: no anchor). `open_blocking`, `open_important` and `open_outside` split the
    open findings (GH-239, GH-294): `open_important` counts the task's own `important` rows
    (`important_partition`'s `owned` and `plan`), `open_outside` the `outside` ones;
    `important_pass_done` says the final important pass was resolved and `followups_filed`
    that the landing filed or commented the follow-up for the `outside` ones. `conflicts`
    (GH-255) is whether the head conflicts with the current master: `None` unread, `False`
    clean, `True` a definite conflict. The `perf_*` facts (GH-318, ADR-0060) come only from
    `perf_merge_facts`: whether the plan convened QA Performance, the latest QA Performance
    run's verdict and the full sha it measured; a `MergeFacts` built without them is not
    convened, and decides as it always did. `criteria_regressed` (GH-334) is the messages of the
    criteria documents the head pushes past their cap (`application.preflight.criteria_regressed`
    against the merge base): `()` none, which is also what a `MergeFacts` built without it
    reads, and `None` unreadable. `ci_required` holds the stack's names joined with the plan's
    (`ci_gate_names`, GH-371) and `ci_plan_only` the plan's that the stack does not declare: a
    green CI that covers only those never decides `merge`, the local checks do.
    `ci_red_on_base` (GH-371) is the failing required names that are also red on the base
    branch's latest CI report (`ci.red_on_base`): `()` when none, when that report was not read,
    or in a `MergeFacts` built without it; only the landing reads it.
    `ci_untrusted` (GH-239) is `None` when `touched_paths` is unknown and `True` when the
    diff touches the CI's own definition (`CI_DEFINITION_PREFIX`): a CI whose definition the
    change itself could have edited proves nothing, so it never decides `merge`.
    `failed_step` is the step of the recorded `merge_checks` that failed, which only the
    landing reads. `adr_collision` (GH-229) is what `application.adr_collision` found: the
    `Renaming` that fixes an own ADR whose number master took, an `Ambiguous` one, or `None`
    (no collision, or the diff touches no `docs/adr/` path so nothing was read).
    `masters_covered` (GH-353, ADR-0045 addendum) is `master_gain.masters_covered`'s verdict on
    the masters the recorded check rows ran on: `True` every one is the current master or
    only behind by an inert gain, `False` something is in doubt, and `None` (what a
    `MergeFacts` built without it reads, the poller's) leaves the recorded `checks_master`
    to be compared with `current_master`, exactly as before."""

    task_id: str
    status: str | None
    reviewed_unit_approved: bool
    reviewed_head: str | None
    open_blocking: int
    open_important: int
    pr_state: str | None
    pr_head: str | None
    in_eval_set: bool | None
    touched_paths: tuple[str, ...] | None
    attempts: int
    checks_head: str | None
    checks_master: str | None
    checks_passed: bool | None
    current_master: str | None
    merge_failures: int = 0
    merge_failure: MergeFailure | None = None
    ci_required: tuple[str, ...] = ()
    ci_head: str | None = None
    ci_verdict: ci.CiVerdict | None = None
    ci_unreadable: ci.CiUnreadable | None = None
    ci_fresh: bool | None = None
    ci_waited_s: float | None = None
    ci_untrusted: bool | None = None
    followups_filed: bool = False
    failed_step: presubmit.FailedStep | None = None
    adr_collision: adr_numbers.Renaming | adr_numbers.Ambiguous | None = None
    masters_covered: bool | None = None
    conflicts: bool | None = None
    open_outside: int = 0
    important_pass_done: bool = False
    perf_convened: bool = False
    perf_verdict: Literal["approved", "returned", "unverified"] | None = None
    perf_head: str | None = None
    criteria_regressed: tuple[str, ...] | None = ()
    ci_plan_only: tuple[str, ...] = ()
    ci_red_on_base: tuple[str, ...] = ()


@dataclass(frozen=True)
class CiGate:
    """The check names that gate a merge: `names` the stack's declared ones first, then the
    plan's, without duplicates; `plan_only` the plan's that the stack does not declare."""

    names: tuple[str, ...]
    plan_only: tuple[str, ...]


def ci_gate_names(declared: tuple[str, ...], plan_required: Sequence[str]) -> CiGate:
    """The one place the names that gate a merge are decided (GH-371, ADR-0045 addendum):
    every `MergeFacts.ci_required`, `MergeFacts.ci_plan_only` and the review's CI read takes
    them from here. The plan's names are joined to the stack's, never replacing them."""
    names = tuple(dict.fromkeys(declared))
    plan_only = tuple(name for name in dict.fromkeys(plan_required) if name not in names)
    return CiGate((*names, *plan_only), plan_only)


def required_paths_missing(required: Sequence[str], touched: Sequence[str]) -> tuple[str, ...]:
    """The plan's `required_paths` when the integrated diff touches none of them, else the
    empty tuple (also when nothing is required)."""
    if not required or any(path in touched for path in required):
        return ()
    return tuple(required)


@dataclass(frozen=True)
class MergeDecision:
    """`merge`: call `Tracker.merge`. `run_checks`: the recorded checks do not cover this
    head on this master and attempts remain. `wait`: the CI is not decidable yet; nothing
    is refused, counted or written, and the next round asks again. `refuse`: leave it,
    `reason` says why."""

    kind: Literal["merge", "run_checks", "wait", "refuse"]
    reason: MergeRefusal | None


# The section QA Performance's runs carry: its role label, closed in ADR-0008 (GH-318).
_QA_PERFORMANCE_SECTION = "QA Performance"


def perf_merge_facts(
    state: TaskState,
) -> tuple[bool, Literal["approved", "returned", "unverified"] | None, str | None]:
    """`(convened, verdict, head)` for `MergeFacts`, the only reader of them (GH-318,
    ADR-0060). `convened` is the plan's own `performance` being non-empty -- never the
    branch's stack. `verdict` and `head` are the latest QA Performance run's `perf_verdict`
    and `perf_head`; `None` for both when the task has no such run, or its latest one carries
    none. A task whose plan did not convene reads `(False, None, None)` whatever its runs say."""
    if not state.get("performance"):
        return (False, None, None)
    latest = None
    for run in state.get("runs", []):
        if run.get("section", run["role"]) == _QA_PERFORMANCE_SECTION:
            latest = run
    if latest is None:
        return (True, None, None)
    return (True, latest.get("perf_verdict"), latest.get("perf_head"))


def reviewed_unit(state: TaskState) -> Subtask | None:
    """The subtask whose approval stands for the whole task: the `integration` one when
    the plan had several, else the only one. `None` for several subtasks with no
    integration yet, or none at all."""
    subtasks = state.get("subtasks", {})
    if INTEGRATION in subtasks:
        return subtasks[INTEGRATION]
    if len(subtasks) == 1:
        return next(iter(subtasks.values()))
    return None


CI_DEFINITION_PREFIX = ".github/"


def guarded_path(path: str, task_id: str) -> bool:
    """Whether `path` is the system's own record and configuration (`.agentic/**`, but the
    task's own document, which every delivery writes), the standards or the workflows.
    Since GH-239 it no longer keeps a pull request from merging: the landing records the
    guarded change in the pull request body instead."""
    if path == f".agentic/tasks/{task_id}.md":
        return False
    return path.startswith((".agentic/", CI_DEFINITION_PREFIX)) or path == "CODING_STANDARDS.md"


def ci_untrusted(touched_paths: Sequence[str] | None) -> bool | None:
    """Whether the CI's verdict may be a product of the change itself: `None` when the
    paths are unknown, `True` when any is under `CI_DEFINITION_PREFIX`. Fail closed: only
    `False` lets a green CI decide a `merge`."""
    if touched_paths is None:
        return None
    return any(path.startswith(CI_DEFINITION_PREFIX) for path in touched_paths)


def auto_merge_decision(facts: MergeFacts) -> MergeDecision:
    """The one place the conditions of an automatic merge live (GH-178, ADR-0045); no
    adapter, CLI or poller re-derives any of them. Checked in this order and refused at the
    first that fails: `not_ready`, `pr_not_open`, `adr_number_taken`, `not_approved`,
    `head_not_reviewed`, `perf_unverified` (only when `perf_convened`), `criteria_unreadable`
    (`criteria_regressed is None`), `criteria_over_cap` (it is not empty), `open_findings_blocking`,
    `open_findings`, `eval_set_unreadable`, `eval_set`, `merge_conflict` (only when
    `conflicts is True`; unread or clean change nothing), `merge_failed`, then, only when the
    product declares its CI or the plan requires checks (`ci_required`): `ci_unreadable`, a `wait`
    for a head that is not the reviewed one, `ci_failed` and
    `ci_missing`, a green and fresh CI being the `merge` itself when it covers a name the stack
    declares (a green stale one, or one that covers only the plan's names -- `ci_plan_only` --
    goes on to the checks, GH-371);
    then `checks_failed` (only when the recorded checks cover this head on this master;
    otherwise the next check) and
    `attempts_exhausted`. A CI that cannot be decided yet is a `wait` until
    `ci.CI_WAIT_MAX_S` have passed since its anchor, a refusal after. `master_moved` is not
    here: the merge verb gives it, after its last fetch. A recorded pass covers the current
    master when the recorded `checks_master` is it (`masters_covered` None) or when
    `masters_covered` says every row's master is only behind by an inert gain (GH-353); a
    `False` is not covered even if `checks_master` is current, since a rerun stamps it with
    the newest master. The checks last: they are the costly
    fact, so nothing is run for a pull request that is refused anyway, and a run is asked
    for only when the recorded one does not cover this head on this master. `attempts`
    bounds those runs per head (`MERGE_ATTEMPTS_MAX`), here and nowhere else; `merge_failures`
    bounds the failed merge calls per head (`MERGE_CALLS_MAX`), here and nowhere else, ahead
    of the checks so a spent head is not sent back to run them when master moves. The class
    of the last failure (`merge_failure`) is never read."""
    if facts.status != "ready_for_review":
        return MergeDecision("refuse", "not_ready")
    if facts.pr_state != "OPEN":
        return MergeDecision("refuse", "pr_not_open")
    if facts.adr_collision is not None:
        return MergeDecision("refuse", "adr_number_taken")
    if not facts.reviewed_unit_approved:
        return MergeDecision("refuse", "not_approved")
    if not (full_commit_id(facts.pr_head) and same_commit(facts.reviewed_head, facts.pr_head)):
        return MergeDecision("refuse", "head_not_reviewed")
    if facts.perf_convened and not (
        facts.perf_verdict == "approved"
        and full_commit_id(facts.perf_head)
        and same_commit(facts.perf_head, facts.pr_head)
    ):
        return MergeDecision("refuse", "perf_unverified")
    if facts.criteria_regressed is None:
        return MergeDecision("refuse", "criteria_unreadable")
    if facts.criteria_regressed:
        return MergeDecision("refuse", "criteria_over_cap")
    if facts.open_blocking > 0:
        return MergeDecision("refuse", "open_findings_blocking")
    if (
        facts.open_important > 0 and not facts.important_pass_done and not facts.followups_filed
    ) or (facts.open_outside > 0 and not facts.followups_filed):
        return MergeDecision("refuse", "open_findings")
    if facts.in_eval_set is None:
        return MergeDecision("refuse", "eval_set_unreadable")
    if facts.in_eval_set:
        return MergeDecision("refuse", "eval_set")
    if facts.conflicts is True:
        return MergeDecision("refuse", "merge_conflict")
    if facts.merge_failures >= MERGE_CALLS_MAX:
        return MergeDecision("refuse", "merge_failed")
    if facts.ci_required:
        gate = _ci_gate(facts)
        if gate is not None:
            return gate
    covers = (
        facts.checks_passed is not None
        and facts.checks_head == facts.pr_head
        and facts.current_master is not None
        and _master_covered(facts)
    )
    if covers:
        if facts.checks_passed:
            return MergeDecision("merge", None)
        return MergeDecision("refuse", "checks_failed")
    if facts.attempts >= MERGE_ATTEMPTS_MAX:
        return MergeDecision("refuse", "attempts_exhausted")
    return MergeDecision("run_checks", None)


def _master_covered(facts: MergeFacts) -> bool:
    """The master conjunct of a recorded pass covering the head: the verb's reading of the
    rows' masters when it made one, else the record's own `master` against the current one."""
    if facts.masters_covered is None:
        return facts.checks_master == facts.current_master
    return facts.masters_covered


def merge_conflicts_from(
    pull_request: PullRequest | None,
    pr_head: str | None,
    current_master: str | None,
    probe: bool | None,
) -> bool | None:
    """The only place GitHub's mergeability becomes a fact (GH-326): `MergeFacts.conflicts`
    from the two readings of it. GitHub's `CONFLICTING` counts, `True` whatever the probe
    says, only when it was read on the head (`head_oid`) and on the base tip (`base_oid`)
    this decision is made on (`pr_head`, `current_master`). Anything else -- `MERGEABLE`,
    `UNKNOWN` (still computing: read again on the next poll), no reading, a stale head, a
    different or absent base tip -- is the `probe`'s value, unchanged."""
    if (
        pull_request is not None
        and pull_request.mergeable == "CONFLICTING"
        and same_commit(pull_request.head_oid, pr_head)
        and same_commit(pull_request.base_oid, current_master)
    ):
        return True
    return probe


def conflict_probe_wanted(facts: MergeFacts) -> bool:
    """Whether the conflict probe is worth running for `facts`: the only answer to that
    question, derived from the order of `auto_merge_decision` and never from a list of
    refusals. It is worth it exactly when a definite conflict would be the decision, that is,
    when no refusal ahead of the conflict already rules the pull request out."""
    return auto_merge_decision(replace(facts, conflicts=True)) == MergeDecision(
        "refuse", "merge_conflict"
    )


def criteria_probe_wanted(facts: MergeFacts) -> bool:
    """Whether the criteria read is worth running for `facts` (GH-334): the only answer to
    that question, derived from the order of `auto_merge_decision`. It is worth it exactly
    when a regression would be the decision, that is, when no refusal ahead of
    `criteria_over_cap` already rules the pull request out."""
    return auto_merge_decision(replace(facts, criteria_regressed=("x",))) == MergeDecision(
        "refuse", "criteria_over_cap"
    )


def gain_probe_wanted(facts: MergeFacts) -> bool:
    """Whether reading what master gained is worth it for `facts` (GH-353): the only answer
    to that question, derived from the order of `auto_merge_decision`. It is worth it
    exactly when a covered verdict would change the decision, that is, when no refusal ahead
    of the checks and no CI that merges already settles the pull request, and the recorded
    pass or the attempts left leave it open."""
    return auto_merge_decision(replace(facts, masters_covered=True)) != auto_merge_decision(
        replace(facts, masters_covered=False)
    )


def adr_probe_wanted(facts: MergeFacts) -> bool:
    """Whether the ADR-collision read is worth running for `facts` (GH-262): the only answer
    to that question, derived from the order of `auto_merge_decision` and never from a list
    of refusals. It is worth it exactly when a collision would be the decision, that is,
    when no refusal ahead of `adr_number_taken` already rules the pull request out. The
    order is the decision's own, so a collision on an unapproved head is still read."""
    taken = adr_numbers.Renaming((("docs/adr/0000-x.md", "docs/adr/0001-x.md"),), {})
    return auto_merge_decision(replace(facts, adr_collision=taken)) == MergeDecision(
        "refuse", "adr_number_taken"
    )


def _ci_gate(facts: MergeFacts) -> MergeDecision | None:
    """The CI's part of `auto_merge_decision`: `None` means a green CI that is not fresh,
    which goes on to the local checks, as does any green CI whose definition is untrusted and
    any green CI that covers only names the plan required (`ci_plan_only`): those are a
    necessary condition of a merge, never a sufficient one (GH-371)."""
    spent = facts.ci_waited_s is not None and facts.ci_waited_s >= ci.CI_WAIT_MAX_S
    if facts.ci_unreadable is not None:
        return MergeDecision("refuse", "ci_unreadable") if spent else MergeDecision("wait", None)
    verdict = facts.ci_verdict
    if verdict is None or not (
        full_commit_id(facts.ci_head) and same_commit(facts.ci_head, facts.pr_head)
    ):
        return MergeDecision("wait", None)
    if verdict.state == "red":
        return MergeDecision("refuse", "ci_failed")
    if verdict.state == "pending":
        return MergeDecision("refuse", "ci_missing") if spent else MergeDecision("wait", None)
    declared = set(facts.ci_required) - set(facts.ci_plan_only)
    if declared and facts.ci_fresh is True and facts.ci_untrusted is False:
        return MergeDecision("merge", None)
    return None


def pr_abandoned(state: str | None) -> bool:
    """The merge-candidacy predicate: whether a pull request read as `state` was closed
    unmerged, which is the one state `gather_merges` stops reporting a task on. It decides
    only that, never whether a task is closed: `agentic close` and the poller's close
    sweep keep their own reads of the pull request. `None` (unreadable) is not abandoned."""
    return state == "CLOSED"


# Entrega con una rama remota que divergió (ADR-0011, addendum GH-204): the one place that
# decides whether delivery may replace what `origin` holds under the task branch. The
# facts -- the remote tip, whether HEAD or one of the task's own archives contains it, the
# pull requests on the branch -- are gathered by `deliver`, once and in that order; this
# only reads them.

PushRefusal = Literal["open_pull_request", "merged_pull_request", "unowned_divergence"]


class _BranchPullRequest(Protocol):
    """What `push_plan` reads of a pull request on the task branch: the application's
    `BranchPullRequest` satisfies it, so the domain imports nothing of it."""

    @property
    def number(self) -> int: ...

    @property
    def state(self) -> str: ...


@dataclass(frozen=True)
class PushPlan:
    """`kind` is what delivery does with the push. `old_sha` is the remote tip the plan
    was made against (`None` when `origin` has no such branch): it is the lease of a
    `replace` and, on a `refuse`, the sha the reason names; no other kind uses it."""

    kind: Literal["push", "replace", "refuse"]
    old_sha: str | None = None
    reason: PushRefusal | None = None


def push_plan(
    remote_tip: str | None,
    remote_in_head: bool,
    prs: Sequence[_BranchPullRequest],
    remote_in_own_archive: bool,
) -> PushPlan:
    """A remote branch that is absent, or that HEAD already contains, is a plain push. A
    diverged one is replaced only when it is certainly the system's own to replace: every
    pull request that was ever on it was closed unmerged, or there is none and the tip is
    in one of this task's own archives. An open or a merged pull request forbids a replace
    in every case."""
    if remote_tip is None or remote_in_head:
        return PushPlan("push", remote_tip)
    if any(pr.state == "OPEN" for pr in prs):
        return PushPlan("refuse", remote_tip, "open_pull_request")
    if any(pr.state == "MERGED" for pr in prs):
        return PushPlan("refuse", remote_tip, "merged_pull_request")
    if prs or remote_in_own_archive:
        return PushPlan("replace", remote_tip)
    return PushPlan("refuse", remote_tip, "unowned_divergence")


def replace_record(recorded: str | None, plan: PushPlan, recorded_in_remote: bool) -> str | None:
    """The `replaced_remote_tip` the task document holds from here on. A `replace` records
    its own sha. A `push` keeps the recorded one only when the remote does not contain it
    (`recorded_in_remote` False): that remote is the one our replace pushed, so the replace
    landed and a later step failed. Anything else -- a lost lease whose remote still is the
    old sha, a refusal -- clears it."""
    if plan.kind == "replace":
        return plan.old_sha
    if plan.kind == "push" and recorded and not recorded_in_remote:
        return recorded
    return None


def _numbers(prs: Sequence[_BranchPullRequest], state: str | None = None) -> str:
    return ", ".join(f"#{pr.number}" for pr in prs if state is None or pr.state == state)


def push_refused_reason(plan: PushPlan, prs: Sequence[_BranchPullRequest], branch: str) -> str:
    """Why delivery did not push, for the escalation: the branch, the remote sha it
    observed and the pull requests that decided it."""
    where = f"remote `{branch}` is at `{plan.old_sha}` and has diverged from the task branch"
    match plan.reason:
        case "open_pull_request":
            return (
                f"{where}; open pull request {_numbers(prs, 'OPEN')} is on it, so it is "
                f"never overwritten. Resolve that pull request, then resume to deliver again"
            )
        case "merged_pull_request":
            return (
                f"{where}; pull request {_numbers(prs, 'MERGED')} on it was merged, so it is "
                f"never overwritten. Decide what the remote branch should hold, then resume"
            )
        case _:
            return (
                f"{where}, with no pull request on it and not in any archive of this task, "
                f"so it is somebody else's work and is never overwritten. Decide what the "
                f"remote branch should hold, then resume"
            )


def replaced_note(sha: str, prs: Sequence[_BranchPullRequest], branch: str) -> str:
    """The line that makes a replace recoverable: the PR body's preface, the delivery
    comment and the escalation after it all carry it."""
    seen = ""
    if prs:
        label = "closed PR" if len(prs) == 1 else "closed PRs"
        seen = f"{label} {_numbers(prs)}; "
    return (
        f"Replaced remote `{branch}` at `{sha}` ({seen}recover with "
        f"`git push origin {sha}:refs/heads/recovered/{branch}`)"
    )


# The estimate (ADR-0054): the thresholds a plan must fit under before it builds, and the
# factor of the safety cap. They live here and nowhere else; every other module asks
# `estimate_fits` or `estimate_cap` and never repeats a number.
SPLIT_COST_USD = 150
SPLIT_FILES = 40
ESTIMATE_CAP_FACTOR = 2
# How far over the cost line a part of a split is still built whole without asking
# (ADR-0054, addendum): twice the line. A separate constant from the cap's factor, which
# answers another question.
PART_WHOLE_FACTOR = 2


def estimate_fits(cost_usd: float, files: int, reference_usd: float | None) -> bool:
    """Whether a plan's estimate is small enough to build as one task. It decides on the
    larger of the declared cost and the history's reference (the p90 of the bucket, when
    the bucket has one), never on the declared cost alone: the same model estimates and
    plans, so the estimate cannot be its own judge."""
    cost = cost_usd if reference_usd is None else max(cost_usd, reference_usd)
    return cost <= SPLIT_COST_USD and files <= SPLIT_FILES


# The marker every issue the partition opens carries (ADR-0054): an HTML comment, invisible
# once rendered, that names the task and the part. It is what a retry looks for among the
# open issues before opening one, and what tells a task it is itself a part.
_SPLIT_MARKER = re.compile(r"<!-- agentic-split: (\S+) part (\d+) -->")


def split_marker(task_id: str, part: int) -> str:
    """The marker of part `part` (1 is the first) of task `task_id`'s partition."""
    return f"<!-- agentic-split: {task_id} part {part} -->"


def split_part_of(body: str) -> tuple[str, int] | None:
    """The `(task id, part)` the body's marker names, or `None` when it has none: a body
    with a marker is a part of a partition, and a part never splits again."""
    match = _SPLIT_MARKER.search(body)
    return (match.group(1), int(match.group(2))) if match else None


def part_builds_whole(fit: FitDecision) -> bool:
    """Whether a part whose plan is over the size line is built whole without asking the
    human (ADR-0054, addendum): the larger of the declared cost and the history's reference
    is at most `PART_WHOLE_FACTOR` times the cost line. Raw figures, no rounding; the number
    of files never caps this rule. The one place that compares them against that limit."""
    return max(fit.cost_usd, fit.reference_usd or 0.0) <= PART_WHOLE_FACTOR * SPLIT_COST_USD


def part_whole_marker(task_id: str, fit: FitDecision) -> str:
    """The content marker of the line `part_whole_line` ends with: the task and the figures
    it quotes, so the system looks for it among the issue's comments before it posts."""
    return f"<!-- agentic-part-whole: {task_id} {fit.cost_usd:.2f} {fit.files} -->"


def part_whole_line(task_id: str, part_of: tuple[str, int], fit: FitDecision) -> str:
    """The one line the system posts on the issue when it builds a part whole without asking:
    which part, the figures the decision used, the rule with its limit, the caps that still
    apply, and the content marker. It has no prefix: `plan.PART_BUILT_WHOLE`, which
    `resume.SYSTEM_COMMENTS` registers, is prepended where it is posted, so a resume never
    reads the line as the human's decision."""
    origin, number = part_of
    reference = (
        f"the history's reference is {fit.reference_usd:.2f} USD"
        if fit.reference_usd is not None
        else "the history has no reference for this many subtasks"
    )
    limit = PART_WHOLE_FACTOR * SPLIT_COST_USD
    return (
        f"{task_id} is part {number} of {origin} and is built whole without asking. "
        f"Its plan declares {fit.cost_usd:.2f} USD and {fit.files} files, and {reference}. "
        f"A part is built whole up to {limit} USD on the larger of the two; above it, the "
        "system asks the human. The task cap and the estimate cap still apply.\n\n"
        f"{part_whole_marker(task_id, fit)}"
    )


def split_part_text(part_of: tuple[str, int] | None) -> str:
    """What the planning Tech Lead is told about its task being a part of a partition
    (ADR-0054): empty for a task that is not one. A part is never split again, so the text
    asks for subtasks only and states the real rule, with the figure taken from the
    constants: up to `PART_WHOLE_FACTOR` times the cost line, on the larger of the estimate
    and the history's reference, the system builds the part whole without asking."""
    if part_of is None:
        return ""
    task_id, part = part_of
    limit = PART_WHOLE_FACTOR * SPLIT_COST_USD
    return (
        f"This task is part {part} of task {task_id}. A part is never split again: write "
        "subtasks only and never `parts`. If your subtasks are over the size limit, write "
        f"them anyway: up to {limit} USD, on the larger of your estimate and the history's "
        "reference, the system builds the part whole without asking; above it, the system "
        "asks the human. In both cases estimate honestly; do not shrink the estimate or inflate "
        "it to pick a path."
    )


# The Tech Lead prompts' instructions to split an over-the-line plan into `parts`, kept
# here as the wording of a task that is not a part, so a part never reads them: a part
# is never split again (ADR-0054). Each is the exact text the prompt carried before it
# became a placeholder, so a task that is not a part renders it byte for byte.
_PARTS_REMEDY = (
    "as one task: write it as `parts` (see `### Parts`) instead of subtasks. Do not shrink the\n"
    "   estimate to make it fit; a plan that fits and carries `parts` is refused too."
)
# Where tracker work goes when a plan has parts (GH-350), the one text that states it: both
# Tech Lead prompts render it and the validator's refusal of `subtasks` next to `parts`
# appends it, so the three never drift. A multi-line passage, indented like the prompt line
# its placeholder sits on.
TRACKER_WITH_PARTS = (
    "When you split, tracker work for this task stays in `tracker_checklist` and the split\n"
    "   posts it on this issue. The system itself lists every open issue whose first line says\n"
    "   `Blocked by: #<this issue>`, with the part to re-point it to, so do not write those\n"
    "   items. Add only other tracker work, such as native blockers you know about. A part's own\n"
    "   tracker work goes in that part's body."
)
_PARTS_STEP = (
    "8. `### Parts`, only when the estimate does not fit: at least two chained parts, each a\n"
    "   small task of its own that lands and shows in the product before the next one starts.\n"
    "   Each part has a `title`, a `body` (its whole description: the person who builds it has\n"
    "   not seen this plan) and `depends_on`, the numbers of EARLIER parts (1 is the first).\n"
    "   Write no subtasks then, and `### Subtasks` is not needed. A part is never split again.\n"
    "   If the section `## Human` says the human decided to build this task whole, write\n"
    "   subtasks and no parts."
)
_PARTS_FIELD = (
    "- `parts`: one entry per part with `title`, `body` and `depends_on`, ONLY when the\n"
    "  estimate does not fit; then `subtasks` is empty. Empty when the plan fits."
)


def parts_remedy_text(part_of: tuple[str, int] | None) -> str:
    """The end of the prompt's sentence on a plan over the size limit: for a task that is
    not a part, to write `parts`; for a part, to write its subtasks anyway."""
    if part_of is None:
        return _PARTS_REMEDY
    return (
        "without the human's decision: write your subtasks anyway and do not shrink the\n"
        "   estimate to make it fit; the system asks the human whether to build it whole."
    )


def parts_step_text(part_of: tuple[str, int] | None) -> str:
    """The prompt's step on writing `### Parts`: empty for a part, which never has one."""
    return _PARTS_STEP if part_of is None else ""


def parts_field_text(part_of: tuple[str, int] | None) -> str:
    """The prompt's line on the `parts` output field: for a part, that it stays empty."""
    if part_of is None:
        return _PARTS_FIELD
    return "- `parts`: always empty, because this task is a part and a part is never split again."


SPLIT_TRACKER_ITEMS = 10


def last_part_numbers(parts: Sequence[PartRecord]) -> list[int]:
    """The parts no other part depends on (1 is the first), in order: where an issue that
    waited on the whole task has to wait instead, so it starts when the split is done."""
    needed = {dep for part in parts for dep in part["depends_on"]}
    return [n for n in range(1, len(parts) + 1) if n not in needed]


def split_tracker_items(
    task_issue: int | None,
    open_issues: Sequence[tuple[str, str]],
    part_urls: Sequence[str],
    last_parts: Sequence[int],
) -> list[str]:
    """The checklist items the system itself derives for a split (GH-350, ADR-0062): one per
    open issue, other than the parts, whose first line says `Blocked by:` the task's own
    issue -- the task ends `split` and never closes, so that issue would wait for ever.
    The human closes the task's issue or re-points the blocked one to the last part(s)
    (`last_parts`, 1-based numbers into `part_urls`); the system edits no issue.

    Pure over what the split already read. `[]` for a task with no issue. Ordered by issue
    number, never by URL; items use numbers only, one line each, so they pass the checks of
    a Tech Lead item. At most `SPLIT_TRACKER_ITEMS`, then one line counting the rest."""
    if task_issue is None:
        return []
    parts = set(part_urls)
    blocked = sorted(
        number
        for url, body in open_issues
        if url not in parts
        and task_issue in blockers_from_body(body)
        and (number := _url_issue_number(url)) is not None
    )
    targets = ", ".join(f"#{_url_issue_number(part_urls[n - 1])}" for n in last_parts)
    items = [
        f"#{n} is blocked by #{task_issue}, which this split does not close: "
        f"close #{task_issue} or re-point #{n} to {targets}"
        for n in blocked[:SPLIT_TRACKER_ITEMS]
    ]
    if len(blocked) > SPLIT_TRACKER_ITEMS:
        items.append(
            f"and {len(blocked) - SPLIT_TRACKER_ITEMS} more issues blocked by #{task_issue}: "
            f'run gh issue list --search "Blocked by: #{task_issue}"'
        )
    return items


def _url_issue_number(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def tracker_hash(items: Sequence[str]) -> str:
    """The first twelve hex digits of the sha256 of the stripped items joined by newlines:
    what tells one checklist from another (GH-336)."""
    text = "\n".join(item.strip() for item in items)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def tracker_marker(task_id: str, items: Sequence[str]) -> str:
    """The marker the checklist comment ends with: an HTML comment naming the task and the
    hash of the items, so "already posted" is never decided from the task id alone."""
    return f"<!-- agentic-tracker: {task_id} {tracker_hash(items)} -->"


def tracker_posted(raw: object) -> TrackerPosted | None:
    """The `tracker_checklist_posted` record, or `None` for "nothing posted yet" (GH-342).

    The graph channel is a reducer one, so it starts at `TrackerPosted()` == `{}` for every
    task, and a document from before GH-336 has no key at all: neither is a record. Only a
    dict whose `at` and `hash` are both strings is one. Stricter than `estimate_in_force`
    on purpose: truthiness lets a partial record through, and the caller then subscripts
    `["hash"]` on it. This is the one reader; no code subscripts the raw value."""
    if not isinstance(raw, dict):
        return None
    at, hash_ = raw.get("at"), raw.get("hash")
    if isinstance(at, str) and isinstance(hash_, str):
        return {"at": at, "hash": hash_}
    return None


TRACKER_FAILURE_ITEMS = 20
TRACKER_FAILURE_ITEM_CHARS = 200


def tracker_failure_block(items: Sequence[object], exc_class: str, relative: str) -> str:
    """The bounded list a delivery carries when the checklist comment could not be
    confirmed (GH-342, ADR-0062 addendum); `''` when no item has text.

    Total over any item value: each is coerced with `str()`, its whitespace collapsed so a
    line stays a line, empty ones skipped; at most `TRACKER_FAILURE_ITEMS` items of
    `TRACKER_FAILURE_ITEM_CHARS` characters, then a pointer to the task document. It names
    the exception class only, and carries nothing that could pass for the checklist
    comment: the `agentic-tracker:` marker text is defused in the items."""
    texts = [" ".join(str(item).split()) for item in items]
    texts = [_MARKER_TEXT.sub("agentic-tracker :", text) for text in texts if text]
    if not texts:
        return ""
    lines = [
        f"- [ ] {_cut(text, TRACKER_FAILURE_ITEM_CHARS)}" for text in texts[:TRACKER_FAILURE_ITEMS]
    ]
    block = (
        f"The tracker checklist comment could not be confirmed (`{exc_class}`); if it is not "
        "on the issue, this is your list:\n\n" + "\n".join(lines)
    )
    omitted = len(texts) - TRACKER_FAILURE_ITEMS
    if omitted > 0:
        block += f"\n\nand {omitted} more, in the task document at `{relative}`"
    return block


_MARKER_TEXT = re.compile(r"agentic-tracker:", re.IGNORECASE)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def estimate_cap(cost_usd: float) -> float:
    """The task's safety cap for an estimate in force: a multiple of it."""
    return cost_usd * ESTIMATE_CAP_FACTOR


def estimate_in_force(state: Mapping[str, Any]) -> float | None:
    """The cost estimate that governs the task now: the last revision a human decided
    on, else the plan's own estimate, else `None` -- a document from before estimates
    existed has none, which is "no estimate" and never zero."""
    revisions = state.get("estimate_revisions")
    if revisions:
        return float(revisions[-1]["cost_usd"])
    estimate = state.get("estimate")
    if estimate:
        return float(estimate["cost_usd"])
    return None


def estimate_cap_reached(spent_usd: float, state: Mapping[str, Any]) -> bool:
    """Whether what the task spent reached the safety cap of its estimate in force. A task
    with no estimate (a document from before them) has no such cap: it never stops here."""
    estimate = estimate_in_force(state)
    return estimate is not None and spent_usd >= estimate_cap(estimate)


def estimate_to_cover(spent_usd: float) -> float:
    """The estimate a task must be given to go on after spending `spent_usd`: anything
    above it has a safety cap beyond what was spent."""
    return spent_usd / ESTIMATE_CAP_FACTOR


def unfinished_subtasks(state: Mapping[str, Any]) -> list[str]:
    """The subtasks not approved yet: what is left to build and review."""
    subtasks = state.get("subtasks") or {}
    return [sid for sid, sub in subtasks.items() if sub.get("status") != "approved"]


def reestimate(state: Mapping[str, Any], history_median: float | None) -> float:
    """The estimate a task stopped at its estimate cap proposes: what it spent plus its
    unfinished subtasks, each at the mean cost of this task's approved ones (a subtask's
    cost is its Developer and review runs). With no approved subtask, the median of the
    history's bucket; with neither, twice what was spent."""
    spent = float(state.get("cost_usd", 0.0))
    subtasks = state.get("subtasks") or {}
    approved = [sid for sid, sub in subtasks.items() if sub.get("status") == "approved"]
    left = len(unfinished_subtasks(state))
    runs = state.get("runs") or []

    def cost_of(sid: str) -> float:
        sections = (developer_section(sid), review_section(sid))
        return float(sum(run["cost_usd"] for run in runs if run.get("section") in sections))

    if approved:
        each = sum(cost_of(sid) for sid in approved) / len(approved)
    elif history_median is not None:
        each = history_median
    else:
        return spent * 2
    return spent + left * each


def estimate_cap_reached_reason(
    role_name: str, spent_usd: float, cap_usd: float, left: int, proposed_usd: float
) -> str:
    """The escalation text of the estimate cap. Its own phrase, never `cap_reached`'s: the
    flat task cap (ADR-0004) keeps its text and the comment that raises it."""
    return (
        f"{role_name} stopped by the estimate cap: {spent_usd:.2f} of {cap_usd:.2f} USD spent, "
        f"{left} subtask(s) left, proposed estimate {proposed_usd:.2f} USD"
    )


def delivered_files(changed_files: int) -> dict[str, int]:
    """The patch that records how many files the pull request changed, the actual the
    estimate is compared with. The only place that names the key."""
    return {"actual_files": changed_files}


# GH-344 blocker-resolution: a closed task that landed through slices resolves a blocker
# with no merge commit of its own, and a stalled queue says which closed issues stall it.
# `poll` judges each blocker (git and `gh` are its I/O) and hands the verdicts here.

BlockerReason = Literal["no_merge_recorded", "merge_not_in_upstream", "slices_not_in_upstream"]

_BLOCKER_REASON_TEXT: dict[BlockerReason, str] = {
    "no_merge_recorded": "no merge recorded",
    "merge_not_in_upstream": "merge not in upstream",
    "slices_not_in_upstream": "slices not in upstream",
}


@dataclass(frozen=True)
class BlockerReport:
    """What one candidate's blockers came to: `open` ones still open, `closed_unresolved`
    ones closed but with nothing the system can rest a resolution on (and why), and
    `unknown` ones (or the candidate itself, when its blockers could not be read) whose
    judgement failed. The launch predicate is `bool(report)`: any field blocks."""

    open: tuple[int, ...] = ()
    closed_unresolved: tuple[tuple[int, BlockerReason], ...] = ()
    unknown: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.open or self.closed_unresolved or self.unknown)


def slices_landed_heads(document: TaskState) -> tuple[str, ...] | None:
    """The `reviewed_head` of every unit but the integration one, when the document is
    closed through slices (`closed_by_slices`, written by `landing.close_patch` only after
    each of these was found an ancestor of the upstream): the heads a blocker check
    re-verifies. `None` when nothing was recorded, there are no units, or a unit has no head."""
    if not document.get("closed_by_slices"):
        return None
    subtasks = document.get("subtasks", {})
    heads = [subtasks[name].get("reviewed_head") for name in subtasks if name != INTEGRATION]
    landed = tuple(head for head in heads if isinstance(head, str) and head)
    if not heads or len(landed) != len(heads):
        return None
    return landed


def queue_blocked_line(reports: Sequence[BlockerReport]) -> str | None:
    """The one line that replaces `nothing to do this round` when every candidate is
    blocked and some are blocked by a closed issue the system cannot resolve. Reason texts
    come from the codes, never from `gh`. A candidate with only `unknown` blockers adds no
    mention and does not suppress the line."""
    if not reports or not all(reports) or not any(r.closed_unresolved for r in reports):
        return None
    reasons: dict[int, BlockerReason] = {}
    for report in reports:
        for number, reason in report.closed_unresolved:
            reasons.setdefault(number, reason)
    named = ", ".join(f"#{n} ({_BLOCKER_REASON_TEXT[reasons[n]]})" for n in sorted(reasons))
    return f"agentic: poll: queue blocked by closed issues it cannot resolve: {named}"


# --- GH-344 close-after-merge ---
# The one place that says when a task's still-open issue is closed by the system
# (ADR-0062, addendum GH-344). Every other module asks `close_after_merge`.

_MERGED_OUTCOMES = frozenset({"merged", "merged_with_changes", "auto_merged"})


def close_after_merge(document: Mapping[str, object]) -> bool:
    """Whether the system closes this task's issue as completed. The caller already knows
    the issue is open. True for a merged outcome with a recorded merge commit, or for slices
    that closed the task (`closed_by_slices`). Never for `closed`, a missing outcome,
    `tracker_only` or a split: those either merged nothing or are the human's to close. It
    does not read `closed_at`, so a failed close is retried until the issue reads closed."""
    commit = document.get("merge_commit")
    if document.get("delivery_outcome") in _MERGED_OUTCOMES and isinstance(commit, str) and commit:
        return True
    slices = document.get("closed_by_slices")
    return isinstance(slices, list) and bool(slices)
