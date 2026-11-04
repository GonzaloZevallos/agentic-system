"""The product's GitHub CI as the merge gate (GH-216, ADR-0045 amendment): the one place
the CI verdict lives. Pure: it reads the structured enum fields `gh` reports and never a
stderr or a prose; the adapters fetch, the application decides what to do with the answer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules

# Defined in `task` (the record that holds it is there); re-exported as the full tier's cause.
from .task import CiCause as CiCause
from .task import FallbackCause as FallbackCause
from .task import JobLogCause as JobLogCause

# How long a pending, missing or unreadable CI is waited for before the merge gate refuses.
# A declared value, not a measured one: nobody has measured a product's CI duration yet.
CI_WAIT_MAX_S = 3600

Containment = Literal["yes", "no", "unknown"]

_GREEN = frozenset({"SUCCESS", "NEUTRAL"})
# A conclusion that says the check has not decided yet: the `StatusContext` states that
# are not final, whatever `status` says.
_UNDECIDED = frozenset({"PENDING", "EXPECTED"})


@dataclass(frozen=True)
class CiCheck:
    """One check run, or one commit status, as GraphQL reports it. `status` and
    `conclusion` hold the raw enum values. A `StatusContext` maps `context` to `name`,
    `state` to `conclusion` and `targetUrl` to `link`, with `status` `COMPLETED` or
    `PENDING`."""

    name: str
    status: str
    conclusion: str | None
    link: str


_RUN_SEGMENT = re.compile(r"/actions/runs/(\d+)(?=[/?#]|$)")


def run_id_of_link(link: str) -> str | None:
    """The workflow run id in a check run's `detailsUrl` (the `/actions/runs/<id>` segment), or
    `None` when the link has none or it is not all digits: a commit status of another system,
    an empty link, a malformed url (GH-463). The merge gate stores it to rerun that run."""
    found = _RUN_SEGMENT.search(link)
    return found.group(1) if found else None


@dataclass(frozen=True)
class CiReport:
    """What one read of a commit's CI says. `head_sha` is the commit the checks belong
    to, read in the same query as `checks`. `base_ref`, `base_sha` and `opened_at` (an ISO
    UTC string) are None for a branch's report, which has no pull request. `runs_completed` is
    True only when the source says every workflow run of the commit is completed (the tracker
    adapter's check-suite read, the dispatched-run builder from `run.status`); None is "not
    known", and is always a wait (GH-485)."""

    head_sha: str
    base_ref: str | None
    base_sha: str | None
    opened_at: str | None
    checks: tuple[CiCheck, ...]
    runs_completed: bool | None = None


@dataclass(frozen=True)
class CiUnreadable:
    """The CI could not be read, which is not the same as a report that lacks a check:
    `gh` is missing or failed (`gh_unavailable`), or answered something this code cannot
    read (`gh_unreadable`). A workflow dispatch `gh` refused is `dispatch_refused` (GH-462)."""

    cause: Literal["gh_unavailable", "gh_unreadable", "dispatch_refused"]


@dataclass(frozen=True)
class JobLog:
    """What one read of a red job's log gave (GH-449). `cause` is the closed answer: `read`
    (`text` holds the log, at most the byte cap), `unreadable` (the tool or its answer
    failed), `empty`, `other_head` (the job belongs to another commit than the one being
    judged: nothing was downloaded) or `timed_out`. `bytes` is what `text` holds in bytes;
    `truncated_bytes` what was left unread past the cap and not retained. Never persisted: the
    text goes to one prompt and nowhere else (invariant 4 of the plan)."""

    job: str
    link: str
    text: str
    bytes: int
    cause: JobLogCause
    truncated_bytes: int


@dataclass(frozen=True)
class CiVerdict:
    """`failing` is the red runs of the declared checks, in declared order; `missing` the
    declared names the report never mentions."""

    state: Literal["green", "red", "pending"]
    failing: tuple[CiCheck, ...]
    missing: tuple[str, ...]


def _pending(check: CiCheck) -> bool:
    return check.status != "COMPLETED" or check.conclusion in _UNDECIDED


def _green(check: CiCheck) -> bool:
    return check.conclusion in _GREEN


def ci_state(report: CiReport, declared: tuple[str, ...]) -> CiVerdict:
    """The verdict of `report` over the `declared` check names, matched exactly: a matrix
    job `check (3.12)` is not `check`, and a check nobody declared is ignored. Every run
    of a declared name counts: any red makes the name red, else any pending makes it
    pending, else it is green. Red is every conclusion that is not green and not pending,
    an unknown one included, so a value GitHub adds later is never read as green. A
    declared name with no run is pending and listed in `missing`. The aggregate is red if
    any name is red, else pending if any is, else green."""
    failing: list[CiCheck] = []
    missing: list[str] = []
    waiting = False
    for name in dict.fromkeys(declared):
        runs = [check for check in report.checks if check.name == name]
        if not runs:
            missing.append(name)
            waiting = True
            continue
        red = [check for check in runs if not _pending(check) and not _green(check)]
        failing.extend(red)
        if not red and any(_pending(check) for check in runs):
            waiting = True
    if failing:
        state: Literal["green", "red", "pending"] = "red"
    elif waiting:
        state = "pending"
    else:
        state = "green"
    return CiVerdict(state, tuple(failing), tuple(missing))


CoveringState = Literal["covered", "red", "pending", "uncovered"]


def covering_state(read: CiReport | CiUnreadable | None, job: str, head: str) -> CoveringState:
    """What the CI says about the `job` that covers a deferred check at `head` (GH-453):
    `covered` when every run of `job` on `head` (by `same_commit`) is green; `red` when one is
    decided and red, `SKIPPED` aside; `pending` when one is not decided yet and none is red;
    `uncovered` when the job did not really run on this head: skipped, missing, a report of
    another commit, or one that is unreadable or absent. Only `uncovered` and `covered` end
    the wait. A separate reading from `ci_state`, which keeps `SKIPPED` red."""
    if not isinstance(read, CiReport) or not rules.same_commit(read.head_sha, head):
        return "uncovered"
    runs = [check for check in read.checks if check.name == job]
    if not runs:
        return "uncovered"
    if any(check.conclusion == "SKIPPED" for check in runs):
        return "uncovered"
    if any(not _pending(check) and not _green(check) for check in runs):
        return "red"
    if any(_pending(check) for check in runs):
        return "pending"
    return "covered"


def red_checks(report: CiReport) -> tuple[CiCheck, ...]:
    """Every run of `report` that is red whatever its name, in report order: decided and not
    green, with the reading of red `ci_state` has. What a log is fetched for (GH-449)."""
    return tuple(check for check in report.checks if not _pending(check) and not _green(check))


def pending_names(report: CiReport, declared: tuple[str, ...]) -> tuple[str, ...]:
    """The declared names that have a run and are still waiting on it (a name the report
    never mentions is in `CiVerdict.missing`, not here), in declared order."""
    return tuple(
        name
        for name in dict.fromkeys(declared)
        if any(check.name == name and _pending(check) for check in report.checks)
    )


def reported_names(report: CiReport) -> tuple[str, ...]:
    """The distinct check names `report` carries, in report order: what a refusal for a
    required name that never reported shows next to the names that were missing (GH-371)."""
    return tuple(dict.fromkeys(check.name for check in report.checks))


def unreported(report: CiReport, names: tuple[str, ...], confirmed: bool) -> tuple[str, ...]:
    """The `names`, in declared order and deduplicated, that no check run of `report` carries
    (GH-485): a wrong name (a matrix job id, a job whose `name:` differs from its id), not a
    slow CI. The one place that decision is made. Only when `confirmed` (the caller saw the same
    complete answer twice) and `report.runs_completed is True`; otherwise `()`, because a
    name can still appear while a run is going. Matching is exact, as in `ci_state`."""
    if not confirmed or report.runs_completed is not True:
        return ()
    carried = {check.name for check in report.checks}
    return tuple(name for name in dict.fromkeys(names) if name not in carried)


def unreported_text(
    unknown: tuple[str, ...], reported: tuple[str, ...], head: str, plan_only: tuple[str, ...]
) -> str:
    """The planning-defect text for `unknown` names (GH-485), the one wording the full tier, the
    merge gate and the landing share: which names, on which head, what did report, and where each
    is fixed. A plan-only name lives in the task document's `ci_required`, a stack `ci:` name in
    `.agentic/stack.yaml` on master."""
    shown = ", ".join(f"`{r}`" for r in reported) or "none"
    lines = [
        f"no check run named `{name}` on this head `{head}` "
        f"(workflow runs completed); reported: {shown}; either the name is wrong or its "
        "workflow does not run for this event."
        for name in unknown
    ]
    fixes: list[str] = []
    if owned := [name for name in unknown if name in plan_only]:
        fixes.append(
            f"Edit `ci_required` in the task document's frontmatter ({', '.join(owned)}), "
            "then `agentic resume`."
        )
    if stack := [name for name in unknown if name not in plan_only]:
        fixes.append(
            f"Change `ci:` in `.agentic/stack.yaml` on master ({', '.join(stack)}), then "
            "`agentic resume`."
        )
    return " ".join(lines) + " " + " ".join(fixes) + " Resume re-enters on the same head."


def red_on_base(head: CiReport, base: CiReport | None, names: tuple[str, ...]) -> tuple[str, ...]:
    """The `names` that fail on `head` and also fail on `base`, the base branch's latest
    report, in `names` order (GH-371). `()` without a base report: a base that was not read
    proves nothing. Red on both says only that both are red, not that it is the same failure."""
    if base is None:
        return ()
    on_head = {check.name for check in ci_state(head, names).failing}
    on_base = {check.name for check in ci_state(base, names).failing}
    return tuple(name for name in dict.fromkeys(names) if name in on_head and name in on_base)


def red_names_on_base(
    base: CiReport | CiUnreadable | None, names: tuple[str, ...], head: CiReport | None = None
) -> tuple[str, ...]:
    """The `names` that are red on the `base` branch's report, in `names` order (GH-442): the
    one comparison of its kind, over `red_on_base`. With a `head` report only the names that
    also fail there are kept (GH-371's question); without one, the names red on the base alone.
    `()` for a base that was not read or could not be: it proves nothing, red or green (see
    `base_green`)."""
    if not isinstance(base, CiReport):
        return ()
    return red_on_base(head or base, base, names)


def base_green(base: CiReport | CiUnreadable | None, names: tuple[str, ...]) -> bool:
    """Whether the `base` report says every one of `names` is green (GH-442): readable, every
    name reported and decided, none red. A running or incomplete report, an unreadable one and
    no names at all are not green, so a base that cannot be judged is a wait, never a release."""
    if not isinstance(base, CiReport) or not names:
        return False
    return not red_names_on_base(base, names) and ci_state(base, names).state == "green"


def run_link(report: CiReport, name: str) -> str | None:
    """The link of the first red run of `name` in `report` (its status line when GitHub gave
    no link), `None` when no run of it is red."""
    red = _first_red(report, name)
    return None if red is None else _reference(red)


def ci_fresh(
    base_sha: str | None, current_master: str | None, contains: Containment | None
) -> bool | None:
    """Whether the CI ran on a base that is still master's: True when both shas are known
    and name the same commit, or when `contains` says the head already has master. False
    when `contains` is `no` or `unknown`: a containment that cannot be read counts as
    stale, never fresh. None when nothing is known yet, the poller's case."""
    if rules.same_commit(base_sha, current_master) or contains == "yes":
        return True
    if contains in ("no", "unknown"):
        return False
    return None


def ci_wait_anchor(
    pushed_head: str | None,
    pushed_at: str | None,
    pr_head: str | None,
    opened_at: str | None,
) -> str | None:
    """The instant the CI wait bound is counted from: the push time when the head pushed
    is the pull request's head, else the pull request's creation. Never a commit
    timestamp, which says when the code was written and not when CI could have started."""
    if pushed_at is not None and rules.same_commit(pushed_head, pr_head):
        return pushed_at
    return opened_at


CI_CATEGORY = "process-suite-red"

# How often the full tier reads the CI of the integrated head while it waits (GH-376).
CI_POLL_S = 30

# A conclusion of a job that never ran any code: GitHub could not start it or it was
# cancelled. Every red run of this kind reads as "CI not usable", not as a failing suite.
_NOT_STARTED = frozenset({"STARTUP_FAILURE", "CANCELLED"})


@dataclass(frozen=True)
class FullTierVerdict:
    """What the full tier concludes from the CI of the integrated head. `failing` is the
    red runs of the declared jobs in declared order, `pending` the declared names with no
    decided run, `cause` is set only for `unreachable`."""

    state: Literal["green", "red", "waiting", "timed_out", "unreachable"]
    failing: tuple[CiCheck, ...]
    pending: tuple[str, ...]
    cause: CiCause | None
    unknown: tuple[str, ...] = ()


def full_tier_verdict(
    read: CiReport | CiUnreadable,
    head: str,
    names: tuple[str, ...],
    elapsed_s: float,
    timeout_s: float,
    grace_s: float,
    confirmed: bool = False,
) -> FullTierVerdict:
    """The verdict of one read of the CI over the declared job `names`, `elapsed_s` into
    a wait bounded by `timeout_s`. A report of another commit than `head` counts as no run
    on `head` (the only place that comparison lives for the full tier). With no run on `head`
    at all (that, or a report with none of the declared jobs) the wait is bounded by the
    shorter `grace_s` (GH-462): a CI that never starts is told in minutes; once any declared
    job has a run on `head`, queued included, only `timeout_s` bounds it, and an unreadable
    CI keeps `timeout_s` too. Red wins over the bound; a job that never started, alone, says
    the CI is unusable, not that the suite failed; nothing is inferred from a skip (`SKIPPED`
    is red, as in `ci_state`). Red still wins; then a declared name that `unreported` finds (with
    `confirmed` by the caller) is `unreachable/unknown_names` at once, before either bound: a
    planning defect, not a slow CI (GH-485)."""
    at_bound = elapsed_s >= timeout_s
    if isinstance(read, CiUnreadable):
        if at_bound:
            return FullTierVerdict("unreachable", (), (), read.cause)
        return FullTierVerdict("waiting", (), tuple(dict.fromkeys(names)), None)
    if not rules.same_commit(read.head_sha, head):
        if elapsed_s >= grace_s or at_bound:
            return FullTierVerdict("unreachable", (), (), "no_run")
        return FullTierVerdict("waiting", (), tuple(dict.fromkeys(names)), None)
    verdict = ci_state(read, names)
    waiting_on = set(verdict.missing) | set(pending_names(read, names))
    pending = tuple(name for name in dict.fromkeys(names) if name in waiting_on)
    if verdict.failing:
        if all(check.conclusion in _NOT_STARTED for check in verdict.failing):
            return FullTierVerdict("unreachable", verdict.failing, pending, "not_started")
        return FullTierVerdict("red", verdict.failing, pending, None)
    if verdict.state == "green":
        return FullTierVerdict("green", (), (), None)
    unknown = unreported(read, names, confirmed)
    if unknown:
        return FullTierVerdict("unreachable", (), pending, "unknown_names", unknown)
    no_run = len(verdict.missing) == len(set(names))
    if no_run and (elapsed_s >= grace_s or at_bound):
        return FullTierVerdict("unreachable", (), pending, "no_run")
    if not at_bound:
        return FullTierVerdict("waiting", (), pending, None)
    return FullTierVerdict("timed_out", (), pending, None)


# The margin subtracted from the local instant a workflow was dispatched to form the loose
# lower bound (`since`) of the run listing (GH-462). Hygiene only: it narrows what `gh` lists
# and never decides whether a run exists, which the event, the workflow and the sha do.
CI_DISPATCH_SKEW_S = 120

# How many times a workflow is dispatched for one head when no run of it ever shows up: the
# first dispatch and one more after a grace period without a run (GH-462).
CI_DISPATCH_ATTEMPTS = 2


# How many pairs (a head merged with a master) of the merge gate's fresh GitHub run may end
# `stale` per head -- master overtook a decided run with a gain that is not inert -- before the
# gate refuses `ci_stale_exhausted` and the landing syncs master instead (GH-463).
CI_STALE_PAIRS_MAX = 3

# How many pairs may end `no_verdict` per head (counted since the last release) before the gate
# refuses `ci_no_verdict`: a pair counts once its one republish or second dispatch was spent
# and GitHub still gave no verdict (GH-463).
CI_NO_VERDICT_PAIRS_MAX = 1


@dataclass(frozen=True)
class DispatchedRun:
    """One workflow run as the port reports it (GH-462): `event` and `display_title` are what
    identify a dispatched run of a head, `status` is the run's own (`completed` or not),
    `created_at` the ISO UTC instant GitHub gave, and `jobs` its jobs as checks."""

    run_id: int
    event: str
    display_title: str
    status: str
    created_at: str
    link: str
    jobs: tuple[CiCheck, ...]


def chosen_run(
    runs: list[DispatchedRun] | tuple[DispatchedRun, ...], head: str
) -> DispatchedRun | None:
    """The dispatched run that judges `head` (GH-462): the newest by `created_at` among those
    whose event is `workflow_dispatch` and whose title holds the full 40-character `head`."""
    matching = [
        run
        for run in runs
        if run.event == "workflow_dispatch" and len(head) == 40 and head in run.display_title
    ]
    return max(matching, key=lambda candidate: candidate.created_at) if matching else None


def dispatch_verdict(
    runs: list[DispatchedRun] | tuple[DispatchedRun, ...],
    head: str,
    names: tuple[str, ...],
    elapsed_s: float,
    grace_s: float,
    timeout_s: float,
) -> FullTierVerdict:
    """The verdict of the dispatched runs listed for the base's workflow, over the declared job
    `names`, `elapsed_s` into the wait (GH-462). A run counts only when its event is
    `workflow_dispatch` and its title holds the full 40-character `head`; of those the newest by
    `created_at` (both GitHub's own, an ISO UTC string) is judged, whatever the list order. No
    run is `waiting` until `grace_s` and `unreachable/no_run` from then on; a run that is not
    completed is `waiting` until `timeout_s`, then `timed_out`. A completed run that lacks a
    declared job or skipped it is `unreachable/jobs_missing`, before red or the bound; a
    declared job that never started alone is `unreachable/not_started`. Then `ci_state` decides:
    undeclared jobs are ignored. No clock of this machine is compared with a GitHub one."""
    declared = tuple(dict.fromkeys(names))
    run = chosen_run(runs, head)
    if run is None:
        if elapsed_s >= grace_s:
            return FullTierVerdict("unreachable", (), (), "no_run")
        return FullTierVerdict("waiting", (), declared, None)
    report = CiReport(head, None, None, None, run.jobs, run.status == "completed")
    verdict = ci_state(report, declared)
    waiting_on = set(verdict.missing) | set(pending_names(report, declared))
    pending = tuple(name for name in declared if name in waiting_on)
    if run.status != "completed":
        if elapsed_s >= timeout_s:
            return FullTierVerdict("timed_out", (), pending, None)
        return FullTierVerdict("waiting", (), pending, None)
    skipped = {job.name for job in run.jobs if job.conclusion == "SKIPPED"}
    if verdict.missing or skipped & set(declared):
        return FullTierVerdict("unreachable", (), pending, "jobs_missing")
    if verdict.failing:
        if all(check.conclusion in _NOT_STARTED for check in verdict.failing):
            return FullTierVerdict("unreachable", verdict.failing, pending, "not_started")
        return FullTierVerdict("red", verdict.failing, pending, None)
    if verdict.state == "green":
        return FullTierVerdict("green", (), (), None)
    if elapsed_s >= timeout_s:
        return FullTierVerdict("timed_out", (), pending, None)
    return FullTierVerdict("waiting", (), pending, None)


def _first_red(report: CiReport, name: str) -> CiCheck | None:
    for check in report.checks:
        if check.name == name and not _pending(check) and not _green(check):
            return check
    return None


def _reference(check: CiCheck) -> str:
    """What backs a finding about `check`: its run link, or, when GitHub gave none, what
    it reported, so a blocking finding never lacks evidence."""
    return check.link or f"{check.name}: {check.status}/{check.conclusion}"


def _quoted(link: str | None, findings: list[dict[str, Any]]) -> bool:
    """Whether a finding's evidence already holds `link` as a whole link: `.../job/100`
    does not quote `.../job/10`."""
    if not link:
        return False
    pattern = re.compile(re.escape(link) + r"(?![\w/-])")
    return any(pattern.search(item.get("evidence", "")) for item in findings)


def ci_findings(
    head: CiReport,
    master: CiReport | CiUnreadable | None,
    declared: tuple[str, ...],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """`findings` plus one finding per declared check that is red on the head under
    review, written by the system so an approval cannot sit over red CI. Blocking, unless
    the same name is red on the base branch's latest commit: then it is pre-existing, an
    observation with both links. A check whose run link a finding already quotes in its
    evidence adds nothing."""
    on_master = master if isinstance(master, CiReport) else None
    extended = list(findings)
    for name in dict.fromkeys(check.name for check in ci_state(head, declared).failing):
        red = _first_red(head, name)
        if red is None:
            continue
        evidence = _reference(red)
        if _quoted(red.link, extended):
            continue
        red_on_master = None if on_master is None else _first_red(on_master, name)
        if red_on_master is not None:
            claim = (
                f"the declared CI check `{name}` is also red on the base branch's latest "
                "commit, so it is pre-existing"
            )
            # Never a second finding that quotes a run link another one already holds.
            if not _quoted(red_on_master.link, extended):
                evidence = f"{evidence} (head), {_reference(red_on_master)} (base branch)"
            severity = "observation"
        else:
            claim = f"the declared CI check `{name}` is red on the head under review"
            severity = "blocking"
        extended.append(
            {
                "location": f"CI: {name}",
                "claim": claim,
                "evidence": evidence,
                "severity": severity,
                "scope": "subtask",
                "category": CI_CATEGORY,
            }
        )
    return extended
