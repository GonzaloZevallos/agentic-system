"""Rules of the process that the use cases apply (README.md, 3 and 6).

Pure functions over the task state, so each one is a unit test and not a graph run.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from . import presubmit
from .document import HUMAN, demote
from .roles import (
    DEVELOPER,
    FINDINGS,
    PAIR_PM_PA,
    PM,
    PRODUCT_ANALYST,
    SOLUTION_INTERVIEW,
    TECH_LEAD,
    Pair,
    Role,
)
from .task import (
    INTEGRATION,
    WAITING_ON_HUMAN,
    ChangeCategory,
    ChangesRequested,
    EscalationClass,
    FindingCategory,
    FindingRecord,
    ReviewKind,
    RoleRun,
    Status,
    Subtask,
    TaskState,
)

# The category the system stamps on PM's returns of the definition (ADR-0016).
DEFINITION_RETURN: ChangeCategory = "definition"


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


def change_category(findings: list[dict]) -> FindingCategory | None:
    """The verdict of a review, derived (ADR-0016): the category of the return, the
    first blocking finding's since the reviewer lists them most severe first, or None
    when nothing blocks and the review approves."""
    return next((f["category"] for f in findings if f["severity"] == "blocking"), None)


def lower_unbacked_blocks(findings: list[dict]) -> tuple[list[dict], list[str]]:
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


def finding_records(subtask_id: str, version: int, findings: list[dict]) -> list[FindingRecord]:
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


def escalation(cls: EscalationClass, reason: str) -> TaskState:
    """The patch that escalates the task: `escalated`, the text for the human, and
    `escalation_class`, the closed reason `agentic.task.paused` groups pauses by. The
    only place either key is written, so a patch cannot carry one without the other."""
    return {"escalated": reason, "escalation_class": cls}


def unmergeable(subtask_id: str, dependency: str, conflict: str) -> TaskState:
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


def setup_unprepared(role: Role, branch: str, step: str, command: str, output: str) -> TaskState:
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


# Subtasks (ADR-0011): each in its own branch, sections named after it.


def developer_section(subtask_id: str) -> str:
    return f"Developer: {subtask_id}"


REVIEW_PREFIX = f"{TECH_LEAD.name}: review "


def review_section(subtask_id: str) -> str:
    return f"{REVIEW_PREFIX}{subtask_id}"


def task_branch(task_id: str) -> str:
    return f"agentic/{task_id}"


def subtask_branch(task_id: str, subtask_id: str) -> str:
    # A dot, not a slash: git cannot hold `agentic/GH-12` and `agentic/GH-12/api` at once.
    return f"{task_branch(task_id)}.{subtask_id}"


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
    before it exists: the cap has to cover it from the start."""
    real = [sid for sid in state.get("subtasks", {}) if sid != INTEGRATION]
    return len(real) + (1 if len(real) > 1 else 0)


def task_cap(state: TaskState, base: float, per_unit: float, ceiling: float) -> float:
    """What the run may spend: the base until there is a plan, then the base plus a share
    per unit, never over the ceiling."""
    return min(ceiling, base + per_unit * work_units(state))


def cap_reached(role_name: str, spent_usd: float, cap_usd: float) -> str:
    return f"{role_name} stopped by the task cap: {spent_usd:.2f} of {cap_usd:.2f} USD spent"


def stopped_by_cap(reason: str | None) -> bool:
    return " stopped by the task cap: " in (reason or "")


def suggested_budget(state: TaskState, per_unit: float) -> int:
    """The `--task-budget` an escalation by the cap proposes: what was spent plus one more
    unit of work, in whole dollars. A starting point for the human, not an estimate."""
    return math.ceil(state.get("cost_usd", 0.0) + per_unit)


PHASES = ("definition", "plan", "development", "review")


def phase_of(section: str) -> str:
    """The phase a run's section belongs to. PM's consultations during the plan write
    PM's section, so they count as definition."""
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
    validated, an approval to the plan, unless PM ruled the behaviour expected, which
    ends a bug discarded (README.md, 3; issue #65)."""
    if state.get("escalated"):
        return None
    status = state.get("status", "")
    if status == "returned":
        return FIRST_NODE.get(state.get("type", ""), "define")
    return {"definition_ready": "plan", "discarded": "discard"}.get(status)


def after_plan(state: TaskState) -> str | None:
    return None if state.get("escalated") else "integrate"


def after_develop(state: TaskState) -> str | None:
    """Inside a branch of the fan-out. An escalation ends the branch and nothing else:
    who tells the human is `integrate`, once, after every branch came back."""
    return None if state.get("escalated") else "review"


def after_integrate(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    if dispatched_subtasks(state):
        return "pipeline"
    return "deliver" if all_approved(state) else None


def after_deliver(state: TaskState) -> str | None:
    return None


def after_discard(state: TaskState) -> str | None:
    return None


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
    history = sum(1 for h in state.get("change_history", []) if h["category"] == DEFINITION_RETURN)
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


def human_section(state: TaskState, reason: str | None, decision: str) -> str:
    """The `Human` section with one more decision: every answer stays, numbered, after
    the escalation it answers."""
    previous = state.get("sections", {}).get(HUMAN, "")
    number = previous.count("### Decision ") + 1
    escalation = f"Escalated: {reason}\n\n" if reason else ""
    entry = f"### Decision {number}\n\n{escalation}{demote(decision.strip())}"
    return f"{previous}\n\n{entry}".strip()


def resumed(state: TaskState, decision: str | None) -> TaskState:
    """The patch that resumes a paused task: no escalation, the status it had, the pair
    that hit its limit back at zero, and the human's decision as a section the roles read.

    Nothing about the integration: a resumed task goes through the fan-in, which adds it
    from the merges it just did, and only that one knows what conflicted."""
    reason = state.get("escalated")
    patch: TaskState = {
        "escalated": None,
        "escalation_class": None,
        "status": resumed_status(state),
        "questions_for_human": [],
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
    # `discarded` is an end like `pull_request`: nothing left to run.
    return {"defined": "validate", "definition_ready": "plan"}.get(status)
