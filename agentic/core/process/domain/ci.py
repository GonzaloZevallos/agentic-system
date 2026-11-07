"""The product's GitHub CI as the merge gate (GH-216, ADR-0045 amendment): the one place
the CI verdict lives. Pure: it reads the structured enum fields `gh` reports and never a
stderr or a prose; the adapters fetch, the application decides what to do with the answer."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules
from .presubmit import FAILED_IDS_MAX, ID_FORBIDDEN

# Defined in `task` (the record that holds it is there); re-exported as the full tier's cause.
from .task import CiCause as CiCause
from .task import CiJobTiming, FlakeGate, FlakeRecord
from .task import CiTiming as CiTiming
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


# Where a dispatched job failed (GH-504): before the suite (`setup`, `checkout`) CI is not usable
# and the Developer's code was never run; `suite` is a real red.
FailedStage = Literal["setup", "checkout", "suite"]

_SETUP_STEP = "Set up job"
_CHECKOUT_STEP_PREFIX = "Run actions/checkout@"


def failed_stage(step_name: str) -> FailedStage:
    """The stage a failed step belongs to (GH-504): exactly `Set up job` is `setup`; a name that
    starts with `Run actions/checkout@` (any version) is `checkout`; every other name is `suite`,
    a `Post Run actions/checkout@...` or a step that merely mentions checkout included."""
    if step_name == _SETUP_STEP:
        return "setup"
    if step_name.startswith(_CHECKOUT_STEP_PREFIX):
        return "checkout"
    return "suite"


@dataclass(frozen=True)
class CiCheck:
    """One check run, or one commit status, as GraphQL reports it. `status` and
    `conclusion` hold the raw enum values. A `StatusContext` maps `context` to `name`,
    `state` to `conclusion` and `targetUrl` to `link`, with `status` `COMPLETED` or
    `PENDING`. `failed_stage` is where a dispatched job's first failing step sits (GH-504);
    its `None` default keeps the GraphQL path and every existing constructor unchanged, and
    `None` is also what a job with no readable failing step carries: it reads red."""

    name: str
    status: str
    conclusion: str | None
    link: str
    failed_stage: FailedStage | None = None
    # GitHub's own instants (ISO-8601 UTC) of a check run or a dispatched job; `None` for a
    # commit status, which carries none, and for a value GitHub did not give (GH-467).
    started_at: str | None = None
    completed_at: str | None = None


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
    known", and is always a wait (GH-485). `cancelled_runs` holds the run ids whose check suite
    concluded `CANCELLED` and `running_runs` those whose suite is not `COMPLETED` (GH-539); both
    empty when the source does not say, which keeps every verdict as it was."""

    head_sha: str
    base_ref: str | None
    base_sha: str | None
    opened_at: str | None
    checks: tuple[CiCheck, ...]
    runs_completed: bool | None = None
    cancelled_runs: frozenset[str] = frozenset()
    running_runs: frozenset[str] = frozenset()


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
    declared names the report never mentions; `cancelled` the red runs of a superseded or
    cancelled workflow run (`superseded_or_cancelled`) of the declared names that have nothing
    else to say: those names are pending, never failing (GH-539). `red` maps each declared name
    that has a red run to those runs (declared order, report order inside): what a finding or
    record that names a check by its identity uses (GH-535), `red_names` its names. Derived from
    `failing`, so it is left out of equality and its `()` default keeps every constructor that
    predates it."""

    state: Literal["green", "red", "pending"]
    failing: tuple[CiCheck, ...]
    missing: tuple[str, ...]
    cancelled: tuple[CiCheck, ...] = ()
    red: tuple[tuple[str, tuple[CiCheck, ...]], ...] = field(default=(), compare=False)

    @property
    def red_names(self) -> tuple[str, ...]:
        """The declared names that are red, in declared order."""
        return tuple(name for name, _ in self.red)


def _pending(check: CiCheck) -> bool:
    return check.status != "COMPLETED" or check.conclusion in _UNDECIDED


def _green(check: CiCheck) -> bool:
    return check.conclusion in _GREEN


def _is_exact(name: str, check_name: str) -> bool:
    return check_name == name


def _is_expansion(name: str, check_name: str) -> bool:
    return re.fullmatch(re.escape(name) + r" \(.+\)", check_name) is not None


def matches(name: str, check_name: str) -> bool:
    """Whether a check run called `check_name` can belong to the declared `name` (GH-535): the
    one comparison of a declared CI name with a run's name. True for the exact name, or for a
    matrix expansion `<name> (<values>)` (a space, a parenthesis, at least one character, the
    closing parenthesis, nothing after it). Never a bare prefix (`e2e` is not `e2e-shard (1)`),
    never case-folded, the name never a pattern. A pairwise answer: when several declared names
    compete for the same runs, `runs_of` decides."""
    return _is_exact(name, check_name) or _is_expansion(name, check_name)


def _claims(checks: Sequence[CiCheck], declared: Iterable[str]) -> dict[str, list[int]]:
    names = tuple(dict.fromkeys(declared))
    claimed: dict[str, list[int]] = {name: [] for name in names}
    taken: set[int] = set()
    for name in names:
        for index, check in enumerate(checks):
            if _is_exact(name, check.name):
                claimed[name].append(index)
                taken.add(index)
    for name in names:
        if claimed[name]:
            continue
        for index, check in enumerate(checks):
            if index not in taken and _is_expansion(name, check.name):
                claimed[name].append(index)
                taken.add(index)
    return claimed


def runs_of(checks: Iterable[CiCheck], declared: Iterable[str]) -> dict[str, tuple[CiCheck, ...]]:
    """The runs each declared name claims, a key per name (deduplicated, declared order), the
    runs in report order (GH-535). The whole declared set is resolved at once, by these rules:

    - an exact claim (`check.name == name`) comes first and leaves the pool before any
      expansion is looked at; a name with an exact run counts only its exact runs, so a red
      `check (3.12)` beside a green `check` does not touch `check`;
    - over what is left, a name with no exact run claims `<name> (<values>)` (see `matches`);
      never a bare prefix;
    - a run belongs to at most one name: where two names could claim it the earlier declared
      one does. A non-matrix job named `checks (fast)` is therefore `checks`' expansion when
      no exact `checks` run exists and `checks (fast)` is not itself declared; declared beside
      `checks`, it is that name's own exact run and `checks` is left with nothing;
    - a matrix with an `if:` per expansion would read as skipped as soon as one shard is
      skipped (`dispatch_verdict`); no product has one today (apostrophe's
      `.github/workflows/ci.yml` has only a job-level `if: needs.changes.outputs.pytest ==
      'true'` at l.108 and `... e2e == 'true'` at l.199, and `matrix.*` appears only in steps
      and artifacts, l.124-129 and 224-229), and changing that rule is a separate decision."""
    pool = tuple(checks)
    return {
        name: tuple(pool[index] for index in indexes)
        for name, indexes in _claims(pool, declared).items()
    }


def _expanded_only(name: str, runs: Sequence[CiCheck]) -> bool:
    """Whether `runs` reached `name` only through matrix expansion: none of them is exact."""
    return bool(runs) and not any(_is_exact(name, check.name) for check in runs)


def _incomplete(report: CiReport, name: str, runs: Sequence[CiCheck]) -> bool:
    """A name resolved only through expansion is never decided while the report cannot say
    every workflow run is done (`runs_completed` not `True`): a matrix still being expanded
    shows only the shards created so far (GH-535)."""
    return report.runs_completed is not True and _expanded_only(name, runs)


def _is_red(check: CiCheck) -> bool:
    return not _pending(check) and not _green(check)


def superseded_or_cancelled(report: CiReport, check: CiCheck) -> bool:
    """Whether `check`, a red one, belongs to a workflow run that was superseded or cancelled and
    so says nothing about the code (GH-539): the one place that is decided, from the run id in
    its link (`run_id_of_link`), the `CANCELLED` conclusions of the report's checks,
    `report.cancelled_runs` and nothing else, never a log or a prose. True when (a) its run has a
    `CANCELLED` check and the report holds a check of the same name in a run with a greater id
    (compared as integers), or (b) its run id is in `report.cancelled_runs`. A check that is
    green or pending, or whose run id cannot be read, is never discarded."""
    if not _is_red(check):
        return False
    run_id = run_id_of_link(check.link)
    if run_id is None:
        return False
    if run_id in report.cancelled_runs:
        return True
    ids = [(run_id_of_link(other.link), other) for other in report.checks]
    if not any(found == run_id and other.conclusion == "CANCELLED" for found, other in ids):
        return False
    return any(
        found is not None and other.name == check.name and int(found) > int(run_id)
        for found, other in ids
    )


def rerun_restarted(read: CiReport | CiUnreadable, run_id: str) -> bool:
    """Whether `read` shows the run `run_id`, the one rerun for a cancelled head (GH-539), outside
    `COMPLETED`: the rerun took hold. Only a `CiReport` can show it, from `running_runs`."""
    return isinstance(read, CiReport) and run_id in read.running_runs


def ci_state(report: CiReport, declared: tuple[str, ...]) -> CiVerdict:
    """The verdict of `report` over the `declared` check names, resolved by `runs_of`: a name
    claims its exact runs, or, with none, its matrix expansions `<name> (<values>)`; a check
    nobody declared is ignored. Every run of a declared name counts: any red makes the name red,
    else any pending makes it pending, else it is green. A name resolved only through expansion
    is pending, never green, while `report.runs_completed` is not `True` (a red expansion
    still makes it red). Red is every conclusion that is not green and not pending, an unknown
    one included, so a value GitHub adds later is never read as green. A declared name with no
    run is pending and listed in `missing`. The aggregate is red if any name is red, else
    pending if any is, else green. `CiVerdict.red` holds each red name's red runs. A red run that
    `superseded_or_cancelled` discards is not red: a name left with nothing else is pending and
    its discarded runs are in `cancelled` (GH-539)."""
    failing: list[CiCheck] = []
    red_by_name: list[tuple[str, tuple[CiCheck, ...]]] = []
    missing: list[str] = []
    cancelled: list[CiCheck] = []
    waiting = False
    for name, runs in runs_of(report.checks, declared).items():
        if not runs:
            missing.append(name)
            waiting = True
            continue
        dropped = [check for check in runs if superseded_or_cancelled(report, check)]
        red = tuple(check for check in runs if _is_red(check) and check not in dropped)
        if red:
            failing.extend(red)
            red_by_name.append((name, red))
        elif any(_pending(check) for check in runs) or _incomplete(report, name, runs):
            waiting = True
        elif dropped and not any(_green(check) for check in runs):
            cancelled.extend(dropped)
            waiting = True
    if failing:
        state: Literal["green", "red", "pending"] = "red"
    elif waiting:
        state = "pending"
    else:
        state = "green"
    return CiVerdict(state, tuple(failing), tuple(missing), tuple(cancelled), tuple(red_by_name))


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
    runs = runs_of(read.checks, (job,))[job]
    if not runs:
        return "uncovered"
    if any(check.conclusion == "SKIPPED" for check in runs):
        return "uncovered"
    if any(_is_red(check) for check in runs):
        return "red"
    if any(_pending(check) for check in runs) or _incomplete(read, job, runs):
        return "pending"
    return "covered"


def red_checks(report: CiReport) -> tuple[CiCheck, ...]:
    """Every run of `report` that is red whatever its name, in report order: decided and not
    green, with the reading of red `ci_state` has, a superseded or cancelled run (GH-539) left
    out. What a log is fetched for (GH-449)."""
    return tuple(
        check
        for check in report.checks
        if _is_red(check) and not superseded_or_cancelled(report, check)
    )


def pending_names(report: CiReport, declared: tuple[str, ...]) -> tuple[str, ...]:
    """The declared names that have a run and are still waiting on it (a name the report
    never mentions is in `CiVerdict.missing`, not here), in declared order. A name resolved only
    through matrix expansion on a report whose `runs_completed` is not `True` is waiting too."""
    return tuple(
        name
        for name, runs in runs_of(report.checks, declared).items()
        if any(_pending(check) for check in runs) or _incomplete(report, name, runs)
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
    name can still appear while a run is going. A matrix job id with expansions is carried, not
    unknown; matching is `runs_of`'s, as in `ci_state` (GH-535)."""
    if not confirmed or report.runs_completed is not True:
        return ()
    return tuple(name for name, runs in runs_of(report.checks, names).items() if not runs)


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
    on_head = set(ci_state(head, names).red_names)
    on_base = set(ci_state(base, names).red_names)
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
    """The link of the first red run claimed by the declared `name` in `report` (its status line
    when GitHub gave no link), `None` when no run of it is red."""
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
# The stages before the suite: a job that failed in one of them never ran the code under test.
_BEFORE_SUITE: frozenset[FailedStage | None] = frozenset({"setup", "checkout"})


@dataclass(frozen=True)
class FullTierVerdict:
    """What the full tier concludes from the CI of the integrated head. `failing` is the
    red runs of the declared jobs in declared order, `pending` the declared names with no
    decided run, `cause` is set only for `unreachable`. `red` is `CiVerdict.red` on the verdicts
    that carry `failing` (`red`, `not_started`, `checkout_failed`) and `()` on every other one
    (GH-535); like there, derived and out of equality. `cancelled` is the state of a head whose
    every declared name is decided or left only by superseded or cancelled runs, with the runs
    completed (GH-539): never red, and `cancelled` holds those runs' checks (also carried by the
    verdicts that still wait or time out on a cancellation)."""

    state: Literal["green", "red", "waiting", "timed_out", "unreachable", "cancelled"]
    failing: tuple[CiCheck, ...]
    pending: tuple[str, ...]
    cause: CiCause | None
    unknown: tuple[str, ...] = ()
    cancelled: tuple[CiCheck, ...] = ()
    red: tuple[tuple[str, tuple[CiCheck, ...]], ...] = field(default=(), compare=False)

    @property
    def red_names(self) -> tuple[str, ...]:
        """The declared names that are red, in declared order."""
        return tuple(name for name, _ in self.red)


def _failed(
    state: Literal["red", "unreachable"],
    verdict: CiVerdict,
    pending: tuple[str, ...],
    cause: CiCause | None,
) -> FullTierVerdict:
    """The one builder of a verdict that carries `failing`: the declared-name mapping comes
    from `verdict`, nobody recomputes it from the runs (GH-535)."""
    return FullTierVerdict(state, verdict.failing, pending, cause, (), (), verdict.red)


def push_got_no_run(verdict: FullTierVerdict) -> bool:
    """Whether the push path ended with no run at all on the head (`no_run`, GH-531): the only
    end a dispatch could mend, and so the only one worth reading the upstream's declaration for."""
    return verdict.state == "unreachable" and verdict.cause == "no_run"


def dispatch_after_no_run(verdict: FullTierVerdict, dispatch_available: bool) -> bool:
    """Whether the head whose push ended in `verdict` is judged by the dispatched workflow
    instead of escalating (GH-531): only a push that got no run at all (`no_run`, after the
    republish) and only when the product's upstream declares what a dispatch needs. Any other
    end of the push path is a verdict, or a failure a dispatch does not mend."""
    return dispatch_available and push_got_no_run(verdict)


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
    planning defect, not a slow CI (GH-485). The classification of `superseded_or_cancelled`
    runs comes before `_NOT_STARTED` (GH-539): a declared name left only by such runs, with
    nothing pending and `runs_completed is True`, is `cancelled`; before that, `waiting`.
    `_NOT_STARTED` stays for `STARTUP_FAILURE` and a `CANCELLED` of a run that is neither."""
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
    cancelled = verdict.cancelled
    if verdict.failing:
        if all(check.conclusion in _NOT_STARTED for check in verdict.failing):
            return _failed("unreachable", verdict, pending, "not_started")
        return _failed("red", verdict, pending, None)
    if verdict.state == "green":
        return FullTierVerdict("green", (), (), None)
    if cancelled and not waiting_on and read.runs_completed is True:
        return FullTierVerdict("cancelled", (), (), None, (), cancelled)
    unknown = unreported(read, names, confirmed)
    if unknown:
        return FullTierVerdict("unreachable", (), pending, "unknown_names", unknown, cancelled)
    no_run = len(verdict.missing) == len(set(names))
    if no_run and (elapsed_s >= grace_s or at_bound):
        return FullTierVerdict("unreachable", (), pending, "no_run", (), cancelled)
    if not at_bound:
        return FullTierVerdict("waiting", (), pending, None, (), cancelled)
    return FullTierVerdict("timed_out", (), pending, None, (), cancelled)


# The margin subtracted from the local instant a workflow was dispatched to form the loose
# lower bound (`since`) of the run listing (GH-462). Hygiene only: it narrows what `gh` lists
# and never decides whether a run exists, which the event, the workflow and the sha do.
CI_DISPATCH_SKEW_S = 120


def _instant(text: str | None) -> datetime | None:
    """An ISO-8601 instant as an aware UTC datetime, `None` for a missing or unreadable one
    (an instant with no zone is read as UTC). Parsing only: this module never reads a clock."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def waited_seconds(timing: CiTiming, until: str | None = None) -> tuple[float | None, bool]:
    """The CI wait of `timing` (GH-467), the only place it is derived: `max(0, end - max(
    pushed_at, node_started_at))`, where `end` is `run_ended_at` or, when that is `None`, `until`.
    The flag `clamped` is true when the raw difference was negative. `(None, False)` when a bound
    is missing or unreadable: unknown, never 0.

    Two caveats. The machine's instants (`pushed_at`, `node_started_at`) and GitHub's
    (`run_ended_at`) are subtracted across two clocks, so a skew between them can show as a
    clamp. For a `no_verdict` timing, which has no `run_ended_at`, the only sanctioned `until` is
    the `at` of the `ci` escalation entry that ended the wait."""
    end = _instant(timing["run_ended_at"] if timing["run_ended_at"] is not None else until)
    pushed = _instant(timing["pushed_at"])
    began = _instant(timing["node_started_at"])
    if end is None or pushed is None or began is None:
        return None, False
    raw = (end - max(pushed, began)).total_seconds()
    return max(0.0, raw), raw < 0


def timing(
    pushed_at: str,
    node_started_at: str,
    verdict: Literal["green", "red", "no_verdict"],
    checks: Iterable[CiCheck],
    names: Iterable[str],
) -> CiTiming:
    """The `CiTiming` of one decided wait (GH-467), pure: built after the verdict is made and
    read by no verdict. A check enters it only when a declared name of `names` claims it
    (`runs_of`, so the expansions of a matrix id too) and its `started_at` is no earlier than
    `pushed_at - CI_DISPATCH_SKEW_S`, so a run of an older push never counts. `run_started_at`
    is the earliest start, `run_ended_at` the latest end among the checks that finished, both
    `None` when none gave one, always for a `no_verdict`. `jobs` lists each finished check's
    duration, and is omitted when empty. An unreadable
    `pushed_at` admits no check."""
    result = CiTiming(
        pushed_at=pushed_at,
        node_started_at=node_started_at,
        run_started_at=None,
        run_ended_at=None,
        verdict=verdict,
    )
    floor = _instant(pushed_at)
    if verdict == "no_verdict" or floor is None:
        return result
    pool = tuple(checks)
    claimed = {index for indexes in _claims(pool, names).values() for index in indexes}
    cutoff = floor - timedelta(seconds=CI_DISPATCH_SKEW_S)
    starts: list[tuple[datetime, str]] = []
    ends: list[tuple[datetime, str]] = []
    jobs: list[CiJobTiming] = []
    for index, check in enumerate(pool):
        started = _instant(check.started_at)
        if index not in claimed or started is None or started < cutoff:
            continue
        starts.append((started, check.started_at or ""))
        ended = _instant(check.completed_at)
        if ended is not None:
            ends.append((ended, check.completed_at or ""))
            jobs.append({"name": check.name, "duration_s": (ended - started).total_seconds()})
    if starts:
        result["run_started_at"] = min(starts)[1]
    if ends:
        result["run_ended_at"] = max(ends)[1]
    if jobs:
        result["jobs"] = jobs
    return result


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
    declared job that never started alone is `unreachable/not_started`, and one whose every
    failing declared job failed at `setup` or `checkout` is `unreachable/checkout_failed`
    (GH-504; a `suite` or unknown stage anywhere among them is red). A declared name that is a
    matrix id is skipped when any of its expansions is (`runs_of`, GH-535). Then `ci_state`
    decides: undeclared jobs are ignored. No clock of this machine is compared with a GitHub one."""
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
    skipped = any(
        job.conclusion == "SKIPPED" for runs in runs_of(run.jobs, declared).values() for job in runs
    )
    if verdict.missing or skipped:
        return FullTierVerdict("unreachable", (), pending, "jobs_missing")
    if verdict.failing:
        if all(check.conclusion in _NOT_STARTED for check in verdict.failing):
            return _failed("unreachable", verdict, pending, "not_started")
        if all(check.failed_stage in _BEFORE_SUITE for check in verdict.failing):
            return _failed("unreachable", verdict, pending, "checkout_failed")
        return _failed("red", verdict, pending, None)
    if verdict.state == "green":
        return FullTierVerdict("green", (), (), None)
    if elapsed_s >= timeout_s:
        return FullTierVerdict("timed_out", (), pending, None)
    return FullTierVerdict("waiting", (), pending, None)


def _red_runs(report: CiReport, name: str) -> tuple[CiCheck, ...]:
    """The red runs the declared `name` claims in `report`, report order."""
    return tuple(check for check in runs_of(report.checks, (name,))[name] if _is_red(check))


def _first_red(report: CiReport, name: str) -> CiCheck | None:
    red = tuple(
        check for check in _red_runs(report, name) if not superseded_or_cancelled(report, check)
    )
    return red[0] if red else None


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
    evidence adds nothing. The finding names the declared check (`CI: pytest`), never an
    expansion: one per declared name, its evidence the first red run's link, and for a matrix
    id the claim says how many of its expansions are red (GH-535)."""
    on_master = master if isinstance(master, CiReport) else None
    extended = list(findings)
    claimed = runs_of(head.checks, declared)
    for name, reds in ci_state(head, declared).red:
        red = reds[0]
        evidence = _reference(red)
        if _quoted(red.link, extended):
            continue
        red_on_master = None if on_master is None else _first_red(on_master, name)
        expansions = ""
        if _expanded_only(name, claimed[name]):
            expansions = f" ({len(reds)} of {len(claimed[name])} expansions red)"
        if red_on_master is not None:
            claim = (
                f"the declared CI check `{name}` is also red on the base branch's latest "
                f"commit, so it is pre-existing{expansions}"
            )
            # Never a second finding that quotes a run link another one already holds.
            if not _quoted(red_on_master.link, extended):
                evidence = f"{evidence} (head), {_reference(red_on_master)} (base branch)"
            severity = "observation"
        else:
            claim = f"the declared CI check `{name}` is red on the head under review{expansions}"
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


# The line a CI job prints for the flakes its own reruns found (GH-464): `agentic-flakes: ` and a
# JSON object `{"v":1,"check":"<name>","tests":["<id>",...]}`. The adapter that reads the log
# strips gh's job and step columns and the timestamp and hands over only lines that start with
# this prefix; the parser below does not strip anything and ignores a line that does not.
FLAKE_BLOCK_PREFIX = "agentic-flakes: "
FLAKE_BLOCK_VERSION = 1
FLAKE_ID_MAX_CHARS = 200


@dataclass(frozen=True)
class CiFlakeBlock:
    """What one read of the CI log of a head's jobs gave (GH-464): per job name, in log order,
    the lines that start with `FLAKE_BLOCK_PREFIX` (gh's columns and timestamp already
    stripped). Evidence from the head under test, not a verdict: `parse_flake_blocks` is the
    one reader."""

    job_lines: Mapping[str, tuple[str, ...]]


def clean_flake_ids(ids: Iterable[str]) -> list[str]:
    """The ids that may go in a flake block: at most `FLAKE_ID_MAX_CHARS` characters and none of
    `ID_FORBIDDEN` (shared with `presubmit.expandable`: an id lands in a tracker comment and a
    marker), without duplicates (in order), at most `FAILED_IDS_MAX`. The one
    copy of the rule: the parser reads blocks with it and `scripts/ci_flakes.py` writes them."""
    clean = [
        item
        for item in ids
        if len(item) <= FLAKE_ID_MAX_CHARS and not any(bad in item for bad in ID_FORBIDDEN)
    ]
    return list(dict.fromkeys(clean))[:FAILED_IDS_MAX]


def _flake_ids(value: object) -> list[str] | None:
    """The clean, bounded ids of a block's `tests`, `None` when it is not a list of strings."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return clean_flake_ids(value)


def _flake_block(line: str) -> tuple[str, list[str]] | None:
    """`(check, ids)` of one block line, `None` for anything malformed: not the prefix, bad
    JSON, another version, a non-string check or ids that are not a list of strings."""
    if not line.startswith(FLAKE_BLOCK_PREFIX):
        return None
    try:
        data = json.loads(line[len(FLAKE_BLOCK_PREFIX) :])
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("v") != FLAKE_BLOCK_VERSION:
        return None
    check, ids = data.get("check"), _flake_ids(data.get("tests"))
    if not isinstance(check, str) or ids is None:
        return None
    return check, ids


def parse_flake_blocks(
    job_lines: Mapping[str, Sequence[str]], declared: frozenset[str], head: str, gate: FlakeGate
) -> list[FlakeRecord]:
    """The flake records of the `agentic-flakes:` blocks in `job_lines` (job name to its lines,
    already reduced to the prefixed ones by the adapter), for `head` seen by `gate`. Per
    `(job, check)` the last block wins; different checks of one job add up; a check not in
    `declared` (the stack's names at the task's base) is dropped, and so is every malformed line,
    whole. Ids are cleaned and bounded in `_flake_ids`. Records carry no `at`: the application
    stamps it. Only records, never a verdict (invariant 4 of GH-464)."""
    records: list[FlakeRecord] = []
    for lines in job_lines.values():
        last: dict[str, list[str]] = {}
        for line in lines:
            block = _flake_block(line)
            if block is not None and block[0] in declared:
                last[block[0]] = block[1]
        records += [
            {"check": check, "head": head, "gate": gate, "tests": ids}
            for check, ids in last.items()
        ]
    return records
