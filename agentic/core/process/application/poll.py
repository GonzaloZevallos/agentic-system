"""The poller's first slice (issue #137, README.md 14): a long-lived process per
product that keeps the queue moving without a human watching it. Each round closes
every task whose pull request already merged, retakes every pause an external API cut
caused once its own wait elapsed, and launches at most one more unblocked issue -- never
past the machine-wide concurrency cap (`Configuration.max_concurrent_runs`). Every
action is a detached `agentic close|resume|run|merge|land` child (`Launcher.spawn`); no
role ever runs in this process (the plan's own invariant). Only a child that can spend a
model session counts against that cap (`rules.counts_against_cap`): a merge child or a
checks-only land child takes no slot, so an approved pull request is never held behind
runs that last hours (GH-263); at most one such child is live per machine, and a merge
or land the plan holds back is said in the round's output, never silent.

`rules.poll_plan` decides the order and which issues qualify; this module only gathers
the facts it needs from the ports and carries out what it returns. `AUTO_RESUMED`,
`RETAKEN`, `AUTO_MERGE_REFUSED` and `AUTO_MERGED` are the comment prefixes the system
posts, all registered into `resume.SYSTEM_COMMENTS` so a resume reads them as the
system's own, never a human's answer. A retake (GH-176) is the poller taking up a run a
reboot interrupted: it reads the run's lock through `RunLock.read`, never `acquire`, and
decides in `rules.interrupted_run`."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, assert_never, get_args

from agentic.shared.configuration import Configuration

from ..domain import (
    ci,
    document,
    exempt_queue,
    experiment,
    landing_order,
    merge_ci,
    merge_conflict,
    presubmit,
    rules,
)
from ..domain import landing as landing_rules
from ..domain import retakes as retake_rules
from ..domain.adr_dated import Undated
from ..domain.adr_numbers import Ambiguous, Renaming
from ..domain.boot import boot_verdict
from ..domain.landing import LandingStep, LandingStepKind
from ..domain.landing_order import Turn
from ..domain.retakes import RETAKEN, RETAKEN_AFTER_CRASH, RETAKEN_ORPHANED
from ..domain.task import (
    INTEGRATION,
    HeadYield,
    PullRequest,
    TaskState,
    add_head_yield,
    apply,
)
from . import (
    adr_collision,
    board_sync,
    ci_ref_sweep,
    close_issues,
    landing_plan,
    preflight,
    reflect_run,
    release,
    scratch_sweep,
    tracker_only,
)
from . import escalate as escalate_module
from .checkouts import task_base
from .ports import (
    GitError,
    IssueComment,
    LaunchAttempt,
    Launcher,
    LockRecord,
    Ports,
    RunLock,
    RunRegistration,
    RunRegistry,
    TrackerCallFailed,
    TrackerTimedOut,
    Upstream,
)
from .registration import REGISTER_WAIT_S, await_registration

# How the system's own auto-resume comment starts (issue #137): registered into
# `resume.SYSTEM_COMMENTS` by `resume.py` itself (a deferred import there, to avoid the
# cycle this module would otherwise have with it) -- never read as the human's decision
# on the next `agentic resume`.
AUTO_RESUMED = "Auto-resumed: "

# How the comments of a retake start (GH-176, GH-308, GH-403): `RETAKEN` after a reboot,
# `RETAKEN_AFTER_CRASH` after the run raised under the graph, `RETAKEN_ORPHANED` for a run that
# left no lock. Defined in `domain/retakes.py`, the one place that counts them, and re-exported
# here for `resume.py`, which registers them in `resume.SYSTEM_COMMENTS`.

# The two comments the automatic merge posts (GH-178, ADR-0045), both registered into
# `resume.SYSTEM_COMMENTS` the same way: a refusal the human can act on, and the merge
# itself. Defined here, imported by `resume.py`, so neither is ever read as a human's answer.
AUTO_MERGE_REFUSED = "Auto-merge refused: "
AUTO_MERGED = "Auto-merged: "

# The two comments the landing posts (GH-239, ADR-0048), registered into
# `resume.SYSTEM_COMMENTS` the same way: the step it takes for a head, and the restart of
# an issue it gave up on.
LANDING = "Landing: "
RESTARTED_BY_LANDING = "Restarted by landing: "

# The per-product lock key `agentic poll` takes (Tech Lead plan): one poller per
# product, the same `FilesystemRunLock` under the product's own `.agentic/runs`
# `run`/`resume`/`close` already use, just keyed by this literal instead of a task id.
LOCK_KEY = "poll"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _since_last_escalated(comments: Sequence[IssueComment]) -> datetime | None:
    """`rules.next_attempt_at`'s own `since` for a retake: the last `Escalated:`
    comment's own `createdAt`, never a parsed "resets 7:30pm" (README.md, 14)."""
    from .escalate import ESCALATED

    last: IssueComment | None = None
    for comment in comments:
        if comment.body.startswith(ESCALATED):
            last = comment
    return _parse_timestamp(last.created_at) if last is not None else None


def _retaken_since_escalated(comments: Sequence[IssueComment]) -> bool:
    """Whether an `Auto-resumed:` comment follows the last `Escalated:` one: the pause's
    first retake after its stated reset (`rules.account_limit_due`) was already made."""
    from .escalate import ESCALATED

    last = -1
    for i, comment in enumerate(comments):
        if comment.body.startswith(ESCALATED):
            last = i
    return any(c.body.startswith(AUTO_RESUMED) for c in comments[last + 1 :])


def _last_non_system_index(
    comments: Sequence[IssueComment], system_comments: tuple[str, ...]
) -> int:
    last = -1
    for i, comment in enumerate(comments):
        if not comment.body.startswith(system_comments):
            last = i
    return last


def _last_system_index(comments: Sequence[IssueComment], system_comments: tuple[str, ...]) -> int:
    last = -1
    for i, comment in enumerate(comments):
        if comment.body.startswith(system_comments):
            last = i
    return last


def _prior_auto_resumes(comments: Sequence[IssueComment]) -> int:
    """How many `Auto-resumed:` comments followed the last comment that is not the
    system's own (`resume.SYSTEM_COMMENTS`, imported here only at call time to avoid
    the cycle `resume.py` importing `AUTO_RESUMED` from this module would otherwise
    make): a human comment resets the count, the same fact `resume.human_decision`
    already reads for a human's own answer."""
    from .resume import SYSTEM_COMMENTS

    last_non_system = _last_non_system_index(comments, SYSTEM_COMMENTS)
    return sum(
        1 for comment in comments[last_non_system + 1 :] if comment.body.startswith(AUTO_RESUMED)
    )


def _retake_budget(comments: Sequence[IssueComment]) -> retake_rules.Budget:
    """The task's retake budget (`retakes.budget`) over its comments: the system prefixes and
    the human's `Resumed: ` come from `resume`, imported here only at call time for the cycle
    `resume.py` importing from this module would otherwise make."""
    from .resume import RESUMED, SYSTEM_COMMENTS

    return retake_rules.budget(
        [(comment.body, _parse_timestamp(comment.created_at)) for comment in comments],
        SYSTEM_COMMENTS,
        RESUMED,
    )


def _retake_times(comments: Sequence[IssueComment]) -> list[datetime]:
    """The `createdAt` of the `Auto-resumed:` comments `_prior_auto_resumes` counts:
    the same set, with the time each one was posted."""
    from .resume import SYSTEM_COMMENTS

    last_non_system = _last_non_system_index(comments, SYSTEM_COMMENTS)
    return [
        _parse_timestamp(comment.created_at)
        for comment in comments[last_non_system + 1 :]
        if comment.body.startswith(AUTO_RESUMED)
    ]


def _interview_round_starts(raw: TaskState) -> list[datetime] | None:
    """When every round of every interview this epoch broke inside started, joined
    (a retake is productive if any of those interviews ran a round in its window);
    `None` when any start is missing or unreadable."""
    starts: list[datetime] = []
    for spec in rules.failed_interviews(raw):
        spec_starts = rules.round_starts(rules.interview_rounds(raw, spec))
        if spec_starts is None:
            return None
        starts.extend(spec_starts)
    return starts


def _account_limit_counts(
    raw: TaskState, comments: Sequence[IssueComment], chase: int
) -> tuple[int, bool]:
    """`(prior, ceiling_spent)` of an `account_limit` pause: `prior` counts only the
    retakes of the current account window (a delivered run proves the quota was back
    and opens a new one), falling back to the whole chase when any run's end is
    unreadable; the ceiling is spent once a retake ran an interview round. Pure reads
    of the document and the comments already fetched, never a new port call."""
    retakes = _retake_times(comments)
    ends = rules.run_ends(raw)
    prior = chase if ends is None else rules.window_probes(retakes, max(ends, default=None))
    productive = (
        rules.productive_retakes(retakes, _interview_round_starts(raw))
        if rules.failed_interviews(raw)
        else 0
    )
    return prior, rules.interview_ceiling_spent(raw, "account_limit", productive)


def _human_spoke_after_the_system(comments: Sequence[IssueComment]) -> bool:
    """Whether a comment that is not the system's own (`resume.SYSTEM_COMMENTS`)
    followed the system's own last one (Human decision, issue #137): a person is
    talking, and this pause is the human's from here on, exactly the fact
    `resume.human_decision` reads to build the decision itself -- this only needs
    whether there is one at all, never what it says."""
    from .resume import SYSTEM_COMMENTS

    last_system = _last_system_index(comments, SYSTEM_COMMENTS)
    return any(not c.body.startswith(SYSTEM_COMMENTS) for c in comments[last_system + 1 :])


def gather_closes(ports: Ports) -> list[int]:
    """Every task whose pull request already merged and whose document has not
    recorded a closure yet (`agentic close`'s own candidates, read the same facts
    `cli._close_all` already does): closed once, since the next round no longer finds
    a document without `closed_at`."""
    closes: list[int] = []
    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None:
            continue
        raw = ports.documents.load(task_id) or {}
        reference = raw.get("pull_request")
        if raw.get("closed_at") or not reference:
            continue
        try:
            pull_request = ports.tracker.pull_request(reference)
        except (ValueError, OSError):
            continue
        if pull_request is not None and pull_request.state == "MERGED":
            closes.append(number)
    return closes


def _recorded(raw: TaskState, key: str) -> dict[str, object]:
    """A record the merge verb wrote (`merge_checks`, `merge_attempts`, `merge_failures`),
    read with `.get`: absent on every document that never saw one, and anything that is not
    a mapping reads as absent too. `merge_checks` also holds `checks` (default `[]`) and
    `failed_step` (default None) since GH-212, read with `.get` and absent on older ones."""
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _merge_facts(
    task_id: str,
    raw: TaskState,
    pull_request: PullRequest | None,
    eval_set: frozenset[int] | None,
    number: int,
    current_master: str | None,
) -> rules.MergeFacts:
    """What the poller can know about a delivered task before any worktree exists, every
    fact off the product checkout's own document, the tracker and the upstream sha --
    never the pull request's copy of the document. `touched_paths` stays `None`: the diff
    is the verb's to read, in its worktree."""
    unit = rules.reviewed_unit(raw)
    pr_head = pull_request.head_oid if pull_request is not None else None
    checks = _recorded(raw, "merge_checks")
    attempts = _recorded(raw, "merge_attempts")
    failures = _recorded(raw, "merge_failures")
    passed = checks.get("passed")
    count = attempts.get("count")
    failed_count = failures.get("count")
    failed_here = failures.get("head") == pr_head
    failure = failures.get("failure")
    step = checks.get("failed_step")
    partition = rules.important_partition(raw)
    perf_convened, perf_verdict, perf_head = rules.perf_merge_facts(raw)
    return rules.MergeFacts(
        task_id=task_id,
        status=raw.get("status"),
        reviewed_unit_approved=unit is not None and unit.get("status") == "approved",
        reviewed_head=unit.get("reviewed_head") if unit is not None else None,
        close_docs=raw.get("close_docs"),
        open_blocking=sum(1 for row in rules.open_findings(raw) if row["severity"] == "blocking"),
        open_important=sum(len(rows) for rows in partition.owned.values()) + len(partition.plan),
        open_outside=len(partition.outside),
        important_pass_done=rules.important_pass_spent(raw, resolved_only=True),
        # GH-610: the system's own close of a body fix is not a `closed` result.
        pr_state=(
            None
            if pull_request is None
            else "OPEN"
            if rules.closed_for_body_fix(raw, pull_request.head_oid)
            else pull_request.state
        ),
        pr_head=pr_head,
        in_eval_set=None if eval_set is None else number in eval_set,
        touched_paths=None,
        ci_untrusted=None,
        merge_ci=tuple(raw.get("merge_ci") or ()),
        attempts=count if isinstance(count, int) and attempts.get("head") == pr_head else 0,
        checks_head=_text(checks.get("head")),
        checks_master=_text(checks.get("master")),
        checks_passed=passed if isinstance(passed, bool) else None,
        current_master=current_master,
        followups_filed=raw.get("followups_filed") is True,
        merge_failures=failed_count if isinstance(failed_count, int) and failed_here else 0,
        merge_failure=failure if failed_here and failure in get_args(rules.MergeFailure) else None,
        failed_step=(
            step
            if checks.get("head") == pr_head and step in get_args(presubmit.FailedStep)
            else None
        ),
        perf_convened=perf_convened,
        perf_verdict=perf_verdict,
        perf_head=perf_head,
        # GH-395: GitHub's reading of this head, off the pull request already read.
        github_conflicting=rules.github_conflicting(pull_request, pr_head),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _seconds_since(anchor: str | None, now: datetime) -> float | None:
    """Seconds from an ISO timestamp to `now`; None for no anchor or one that does not
    parse, which the decision reads as an unknown wait, never a crash."""
    if not anchor:
        return None
    try:
        then = datetime.fromisoformat(anchor)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()


def ci_gate(raw: TaskState, declared: tuple[str, ...]) -> rules.CiGate:
    """The check names that gate `raw`'s merge (GH-371): the stack's `declared` ones joined
    with the plan's `ci_required` by `rules.ci_gate_names`, the one place that decides it.
    Every caller that builds `MergeFacts` asks here, so none rebuilds the list its own way."""
    plan = raw.get("ci_required")
    return rules.ci_gate_names(declared, plan if isinstance(plan, list) else [])


def with_recorded_red(facts: rules.MergeFacts) -> rules.MergeFacts:
    """`facts` read the way `merge.with_ci_run` reads a decided red fresh run (GH-463), from the
    head's `merge_ci` pair instead of GitHub: `ci_subject` `tested`, the pair's `tested` sha as
    `ci_head` and a red verdict over the names the pair recorded as failing. Anything else (no
    red pair on the current master) leaves `facts` as they were."""
    pair = merge_ci.decided_red(facts.merge_ci, facts.pr_head, facts.current_master)
    if pair is None:
        return facts
    failing = tuple(
        ci.CiCheck(name, "COMPLETED", "FAILURE", "") for name in pair.get("failing", [])
    )
    claimed = ci.runs_of(failing, facts.ci_required)
    taken = {id(run) for runs in claimed.values() for run in runs}
    # The recorded names are the runs' own (`pytest (a)`): the declared name (`pytest`) is what
    # a record that names a check by its identity uses (GH-535). A recorded name no declared
    # name claims any more stays its own.
    red = tuple((name, runs) for name, runs in claimed.items() if runs) + tuple(
        (run.name, (run,)) for run in failing if id(run) not in taken
    )
    return replace(
        facts,
        ci_subject="tested",
        ci_head=pair["tested"],
        ci_verdict=ci.CiVerdict("red", failing, (), red=red),
        ci_unreadable=None,
        ci_fresh=True,
    )


def with_red_on_base(
    ports: Ports, facts: rules.MergeFacts, report: ci.CiReport | ci.CiUnreadable | None
) -> rules.MergeFacts:
    """`facts` plus `ci_red_on_base` (GH-371): when the head's CI is red, the base branch's
    latest report is read once and the failing names that are also red there are recorded.
    Anything else leaves `facts` as they were; an unreadable base report gives `()`."""
    verdict = facts.ci_verdict
    if verdict is None or verdict.state != "red" or not isinstance(report, ci.CiReport):
        return facts
    if not report.base_ref:
        return facts
    base = ports.tracker.branch_ci(report.base_ref)
    return replace(
        facts,
        ci_red_on_base=ci.red_names_on_base(base, facts.ci_required, report),
    )


def _landing_step(
    ports: Ports,
    cfg: Configuration | None,
    task_id: str,
    raw: TaskState,
    number: int,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    live: frozenset[LandingStepKind],
    now: datetime,
    turn: Turn,
) -> LandingStep | None:
    """The step worth a `land` child for a refused task (`landing_plan.pending`). An escalated
    document whose table step is the diagnose asks `landing_plan.release` (GH-328, GH-442; the
    tracker is read only for the CI shape or a failure with attempts); when it is released the
    step is the one `landing_plan.released_step` gives on the released view, the one `land`
    recomputes, so the child registers under the run kind of the step it will take."""
    if facts.pr_head is not None and landing_plan.open_fix(
        landing_plan.live_entries(raw), facts.pr_head
    ):
        # GH-610: a body fix left open (the pull request may be closed) is taken again.
        return landing_rules.LandingStep("fix_pr_body", refusal)
    non_code = landing_plan.non_code_state(ports, raw, refusal, facts)
    waiting = cfg is not None and landing_plan.release_asked(
        raw,
        landing_plan.step_for(raw, refusal, facts, turn=turn, non_code=non_code).kind,
        refusal=refusal,
        facts=facts,
    )
    outcome = (
        landing_plan.release(
            ports, cfg, task_id, raw, number, head=facts.pr_head, upstream=facts.current_master
        )
        if waiting and cfg is not None
        else None
    )
    if outcome is None or not outcome.released or cfg is None or facts.pr_head is None:
        return landing_plan.pending(
            raw, refusal, facts, live, now, False, turn=turn, non_code=non_code
        )
    at = now.isoformat()
    ancestry = (
        landing_plan.head_ancestry(ports, cfg, facts.pr_head, facts.current_master)
        if facts.current_master is not None
        else None
    )
    patch = landing_plan.release_patch(raw, outcome, facts.current_master, at)
    view = apply(raw, patch) if patch else raw
    step = landing_plan.released_step(
        raw,
        outcome,
        facts,
        refusal,
        at=at,
        ancestry=ancestry,
        turn=turn,
        non_code=landing_plan.non_code_state(ports, view, refusal, facts),
    )
    if step.kind == "wait" and patch and refusal == "ci_no_verdict":
        # GH-463: the release of a `ci_no_verdict` escalation is persisted by a `land` child.
        return step
    if not landing_plan.takes_action(step.kind, live) or landing_rules.already_dispatched(
        landing_plan.live_entries(view),
        facts.pr_head,
        step.kind,
        facts.current_master,
        names=step.names,
    ):
        return None
    return step


def _instant(text: object) -> datetime | None:
    """`text` as an aware instant, `None` when it is not an ISO time."""
    if not isinstance(text, str):
        return None
    try:
        then = datetime.fromisoformat(text)
    except ValueError:
        return None
    return then if then.tzinfo is not None else then.replace(tzinfo=UTC)


def _turn_anchor(anchor: str | None, raw: TaskState, pr_head: str | None) -> str | None:
    """`anchor` or, when the poller recorded the instant this very head took the first place
    of the landing order (`head_since_*`, GH-588), the later of the two: a pull request that
    waited its turn with a CI nobody asked for is not late for it. A record of another head, or
    one that does not read, leaves `anchor` as it was."""
    since = _instant(raw.get("head_since_at"))
    if since is None or not rules.same_commit(raw.get("head_since_head"), pr_head):
        return anchor
    then = _instant(anchor)
    return anchor if then is not None and then >= since else since.isoformat()


def with_ci(
    facts: rules.MergeFacts,
    raw: TaskState,
    declared: tuple[str, ...],
    report: ci.CiReport | ci.CiUnreadable | None,
    fresh: bool | None = None,
    now: datetime | None = None,
) -> rules.MergeFacts:
    """`facts` plus the CI ones (GH-216), for the product that declares `declared` as its
    merge gate and the plan that requires `raw`'s `ci_required` (`ci_gate`, GH-371).
    `report` is None when none was read: the decision then waits, which is only ever asked
    after every earlier refusal has been ruled out. The classification is `ci.ci_state`'s
    and the freshness `ci.ci_fresh`'s: nothing here reads a check."""
    gate = ci_gate(raw, declared)
    names = gate.names
    if not names:
        return facts
    facts = replace(facts, ci_plan_only=gate.plan_only)
    now = now or datetime.now(UTC)
    if report is None:
        return replace(facts, ci_required=names)
    if isinstance(report, ci.CiUnreadable):
        anchor = _turn_anchor(
            ci.ci_wait_anchor(raw.get("pushed_head"), raw.get("pushed_at"), facts.pr_head, None),
            raw,
            facts.pr_head,
        )
        return replace(
            facts,
            ci_required=names,
            ci_unreadable=report,
            ci_waited_s=_seconds_since(anchor, now),
        )
    anchor = _turn_anchor(
        ci.ci_wait_anchor(
            raw.get("pushed_head"), raw.get("pushed_at"), facts.pr_head, report.opened_at
        ),
        raw,
        facts.pr_head,
    )
    return replace(
        facts,
        ci_required=names,
        ci_head=report.head_sha,
        ci_verdict=ci.ci_state(report, names),
        ci_unreported=ci.unreported(report, names, True),
        ci_reported=ci.reported_names(report),
        ci_fresh=fresh,
        ci_waited_s=_seconds_since(anchor, now),
    )


def ci_wait_line(
    number: int, facts: rules.MergeFacts, report: ci.CiReport | ci.CiUnreadable | None = None
) -> str:
    """The poller's line for a CI that is not decidable yet: what it waits on and for how
    long (`?` when the wait has no anchor). A pending check is named from `report`."""
    waited = "?" if facts.ci_waited_s is None else str(int(facts.ci_waited_s))
    if facts.ci_unreadable is not None:
        what = f"unreadable {facts.ci_unreadable.cause}"
    elif facts.ci_unreported:
        # GH-485: `ci.unreported` first -- a name no check run carries is not pending; the gate
        # refuses it once the wait is confirmed, so a line that waits is that confirmation.
        what = (
            f"unreported {', '.join(facts.ci_unreported)}: not a check-run name on this head, "
            "confirming"
        )
    elif facts.ci_verdict is not None and facts.ci_verdict.missing:
        what = f"missing {', '.join(facts.ci_verdict.missing)}"
        if facts.ci_plan_only and isinstance(report, ci.CiReport):
            carried = ", ".join(ci.reported_names(report)) or "none"
            what += f", the report carried {carried}"
    elif facts.ci_verdict is not None and facts.ci_verdict.state == "pending":
        names = (
            ci.pending_names(report, facts.ci_required) if isinstance(report, ci.CiReport) else ()
        )
        what = f"pending {', '.join(names)}" if names else "pending"
    else:
        what = "pending head"
    bound = f"{waited}s of {ci.CI_WAIT_MAX_S}"
    return f"agentic: poll: auto-merge #{number} -> waiting: ci {what} ({bound})"


ConflictSource = Literal["github", "probe", "recorded"]


def read_conflicts(
    ports: Ports, pull_request: PullRequest | None, head: str, master: str, *, recorded: bool
) -> tuple[bool | None, ConflictSource]:
    """`MergeFacts.conflicts` for `head` on `master` (GH-326, GH-492), shared by the poller and
    the merge verb so both read it the same way: GitHub's reading, or the conflict git recorded
    for this exact pair (`recorded`), when `rules.merge_conflicts_from` takes it as a conflict
    -- git is then not asked -- and the merge-tree probe otherwise. The source is log text,
    nothing else."""
    if rules.merge_conflicts_from(pull_request, head, master, None, recorded=False) is True:
        return True, "github"
    if recorded:
        return True, "recorded"
    probe = ports.workspace.merge_conflicts(head, master)
    return rules.merge_conflicts_from(pull_request, head, master, probe, recorded=False), "probe"


def read_criteria(ports: Ports, head: str, master: str) -> tuple[str, ...] | None:
    """`MergeFacts.criteria_regressed` for `head` against `master` (GH-334), shared by the
    poller and the merge verb: what the head adds past the criteria caps compared with the
    merge base, a local git fact. `merge_base` raises when either sha is unavailable, so a
    document the base lacks (counts 0) is never mistaken for one that could not be read:
    any `GitError` is `None`, unreadable, which the decision refuses."""
    try:
        base = ports.workspace.merge_base(head, master)
        return tuple(preflight.criteria_regressed(ports, base, head))
    except GitError:
        return None


CRITERIA_JOIN = " | "


class StackUnreadable(Exception):
    """The task's base has no readable stack: this task's problem, never the round's."""


@dataclass(frozen=True)
class MergeAssessment:
    """What the poller can decide about one delivered task: the facts, the decision over
    them, the CI report it read (if any) and the pull request it read."""

    facts: rules.MergeFacts
    decision: rules.MergeDecision
    report: ci.CiReport | ci.CiUnreadable | None
    pull_request: PullRequest | None
    # The conflict probe was asked and read `None` (GH-255): the decision is today's.
    conflict_unread: bool = False
    # Where a `True` conflict was read (GH-326), for the log line only.
    conflict_source: ConflictSource | None = None


@dataclass(frozen=True)
class Abandoned:
    """A pull request read closed unmerged (`rules.pr_abandoned`): nothing to merge. The read
    it carries is the only one made for that task this round (GH-334)."""

    pull_request: PullRequest


def assess_merge(
    ports: Ports,
    task_id: str,
    raw: TaskState,
    number: int,
    eval_set: frozenset[int] | None,
    current_master: str,
) -> MergeAssessment | Abandoned:
    """`rules.auto_merge_decision` over the facts the poller has, shared by `gather_merges`
    and `landing.land` so both decide the same way. `Abandoned` for a pull request closed
    unmerged; an unreadable one reads as not open; `StackUnreadable` for a base the stack
    cannot be read at. The pull request is read here, once."""
    reference = raw.get("pull_request")
    try:
        pull_request = ports.tracker.pull_request(reference) if reference else None
    except (ValueError, OSError):
        pull_request = None  # reads as not open: refused by the decision, with its line
    if (
        pull_request is not None
        and rules.pr_abandoned(pull_request.state)
        and not rules.closed_for_body_fix(raw, pull_request.head_oid)
    ):
        return Abandoned(pull_request)
    facts = _merge_facts(task_id, raw, pull_request, eval_set, number, current_master)
    # GH-492: the base reading of the conflict is GitHub's and the one git recorded for this
    # exact pair, so a recorded conflict refuses `merge_conflict` here, unlandable pair or not.
    recorded = merge_conflict.applies(raw.get("merge_conflict"), facts.pr_head, current_master)
    base_conflicts = rules.merge_conflicts_from(
        pull_request, facts.pr_head, current_master, None, recorded=recorded
    )
    if base_conflicts is True:
        facts = replace(facts, conflicts=True)
    try:
        declared = ports.stack.ci(task_base(raw, ports))
    except ValueError as exc:
        raise StackUnreadable(str(exc)) from exc
    gate = ci_gate(raw, declared)
    try:
        dispatch_declared = ports.stack.ci_dispatch(task_base(raw, ports)) is not None
    except ValueError:
        dispatch_declared = False

    def gated(candidate: rules.MergeFacts) -> rules.MergeFacts:
        # What the decision reads before the CI is: the names, so a refusal ahead of the CI
        # is the same one, and no verdict, so the CI itself is a `wait`.
        return replace(
            candidate,
            ci_required=gate.names,
            ci_plan_only=gate.plan_only,
            ci_dispatch_declared=dispatch_declared,
        )

    collision = _adr_collision(ports, raw, facts, current_master)
    facts = replace(facts, adr_collision=collision, adr_naming=adr_collision.naming_of(collision))
    decision = rules.auto_merge_decision(gated(facts))
    if facts.pr_head is not None and rules.criteria_probe_wanted(gated(facts)):
        # GH-334: a head that grows a criteria document past its cap is refused before the
        # findings, the conflict and the CI. Git is asked only now, like the conflict probe.
        facts = replace(
            facts, criteria_regressed=read_criteria(ports, facts.pr_head, current_master)
        )
        decision = rules.auto_merge_decision(gated(facts))
    unread = False
    source: ConflictSource | None = None
    if (
        facts.pr_head is not None
        and not landing_plan.spent(raw, facts.pr_head, None).unlandable
        and rules.conflict_probe_wanted(gated(facts))
    ):
        # GH-255: a head that does not merge with master is a conflict before it is a CI
        # fact. Git is asked only now, with every git-free refusal ahead of it ruled out.
        conflicts, source = read_conflicts(
            ports, pull_request, facts.pr_head, current_master, recorded=recorded
        )
        unread = conflicts is None
        facts = replace(facts, conflicts=conflicts)
        decision = rules.auto_merge_decision(gated(facts))
    report = None
    if gate.names and decision.kind != "refuse":
        # Every refusal ahead of the CI's is ruled out: only now is the report read.
        report = ports.tracker.ci_report(str(reference))
        facts = with_red_on_base(ports, with_ci(facts, raw, declared, report), report)
        decision = rules.auto_merge_decision(facts)
        if rules.declares_ci(facts) and decision.kind != "refuse":
            # GH-463: the gate's fresh run, once it ended red, is the CI fact of this head;
            # the merge child refuses `ci_failed` on it, so the landing must see the same.
            facts = with_recorded_red(facts)
            decision = rules.auto_merge_decision(facts)
    return MergeAssessment(
        facts, decision, report, pull_request, unread, source if facts.conflicts else None
    )


def _abandoned_by_slices(
    ports: Ports,
    cfg: Configuration,
    task_id: str,
    raw: TaskState,
    number: int,
    reference: str,
    master: str,
    now: datetime,
    out: list[str],
    reruns: list[int] | None,
) -> None:
    """A task whose pull request closed unmerged and which landed slices (GH-334): git's
    ancestry of each unit's `reviewed_head` on `master` decides (`landing.abandoned_by_slices`).
    Every unit landed: the document closes (`closed_at`, `closed_by_slices`, never a
    `delivery_outcome`). Work left: the issue is a candidate for a fresh run. A task with no
    `landed_slices` record reads no git at all."""
    landed = raw.get("landed_slices")
    if not landed:
        return
    units = [name for name in raw.get("subtasks", {}) if name != INTEGRATION]
    heads = landing_plan.reviewed_heads(raw, units)
    ancestry: dict[str, bool | None] = {}
    for unit in units:
        if unit not in heads:
            ancestry[unit] = False
            continue
        try:
            ancestry[unit] = ports.workspace.is_ancestor(cfg.product_dir, heads[unit], master)
        except GitError:
            ancestry[unit] = None
    verdict = landing_rules.abandoned_by_slices(
        units,
        ancestry,
        pr_abandoned=True,
        status=raw.get("status"),
        escalated=raw.get("escalated"),
        pull_request=raw.get("pull_request"),
        abandoned_ref=reference,
    )
    if verdict == "close":
        refs = landing_rules.slice_refs(landed)
        patch: dict[str, object] = dict(landing_rules.slices_closed(refs, now.isoformat()))
        posted = False
        if items := raw.get("tracker_checklist"):
            # The checklist comes before the closure and its record rides in the same
            # write (GH-336): a comment that fails leaves the document open for the next tick.
            try:
                record = tracker_only.post_tracker_checklist(
                    ports, task_id, items, raw.get("tracker_checklist_posted")
                )
            except (ValueError, OSError) as exc:
                out.append(f"agentic: poll: #{number} -> checklist not posted: {exc}")
                return
            if record is not None:
                patch["tracker_checklist_posted"] = record
                posted = True
        try:
            ports.documents.record_closure(task_id, patch)
        except (ValueError, OSError) as exc:
            out.append(f"agentic: poll: #{number} -> not closed: {exc}")
            return
        labels = ", ".join(landing_rules.pull_request_label(ref) for ref in refs)
        out.append(
            f"agentic: poll: #{number} -> closed: every unit landed through slices ({labels})"
            + ("; tracker checklist posted" if posted else "")
        )
    elif verdict == "rerun" and reruns is not None:
        left = ", ".join(unit for unit in units if ancestry[unit] is False)
        reruns.append(number)
        out.append(f"agentic: poll: #{number} -> fresh run: slices left {left}")


def _adr_collision(
    ports: Ports, raw: TaskState, facts: rules.MergeFacts, master: str
) -> Renaming | Ambiguous | Undated | None:
    """The collision the landing has to see to route `adr_number_taken` (GH-229), read on
    the same master sha the merge verb uses. Whether it is read at all is
    `rules.adr_probe_wanted`'s, derived from the decision's own order (GH-262: a refactor of
    where the order lives, it moves no read).

    Per tick, for each task that is `ready_for_review` with an `OPEN` pull request: one local
    `cat-file -e` for the head (when it is absent locally: that one, one `fetch --quiet origin
    <sha>` bounded by `FETCH_TIMEOUT_S`, and a second `cat-file -e`), one `git config --get`
    for the task's base (`task_base`, read once more than the one `assess_merge` already
    makes for the stack's CI) and one `diff --name-only`. When the diff touches `docs/adr/`,
    one `added_adrs` log more, and what `adr_collision.adr_collision` reads after it. Bounded
    by the number of open pull requests, not by data size. A diff or a read that fails is no
    collision here: the merge verb reads again and is the one that stops."""
    head = facts.pr_head
    if head is None or not rules.adr_probe_wanted(facts):
        return None
    try:
        if not ports.workspace.fetch_commit(head):
            return None
        touched = ports.workspace.changed_paths(task_base(raw, ports), head)
        if not adr_collision.touches_adrs(touched):
            return None
        return adr_collision.adr_collision(ports, master, head)
    except GitError:
        return None


def awaiting_merge(ports: Ports) -> bool:
    """Whether any task has a pull request and no closure: the one cheap read, on exactly
    what `gather_merges` itself filters on, that decides if the round needs the upstream
    sha at all (a fetch). Whether such a task may merge is `rules.auto_merge_decision`'s."""
    for task_id in ports.documents.ids():
        raw = ports.documents.load(task_id) or {}
        if raw.get("pull_request") and not raw.get("closed_at"):
            return True
    return False


class OrderLock(Protocol):
    """The two reads of a run lock the landing order needs; a `RunLock` is one."""

    def read(self, task_id: str) -> LockRecord | None: ...

    def refusal(self, task_id: str) -> str | None: ...


@dataclass(frozen=True)
class OrderEntry:
    """One task of the round's first phase (GH-588): what was read of it and, when a pull request
    of it can lead the order, its `member`. `outcome` is `None` for a task a child already works
    on (busy or locked: read nothing), a `StackUnreadable`, an `Abandoned` or the assessment."""

    task_id: str
    number: int
    raw: TaskState
    outcome: MergeAssessment | Abandoned | StackUnreadable | None
    member: landing_order.Member | None
    turned: landing_order.Member | None = None
    """`member` after `landing_order.advance` (GH-621): what `landing_order.turn_patch` compares
    it with. `None` when it is not a member."""


@dataclass(frozen=True)
class LandingRound:
    """The first phase of `gather_merges`: every task with a pull request, in document order,
    the issue at the head of the product's landing order (`None`: nobody eligible) and the
    members that yielded to reach it (GH-621)."""

    entries: tuple[OrderEntry, ...]
    head: int | None
    yielded: tuple[landing_order.Yield, ...] = ()


def _stamp(raw: TaskState) -> datetime | None:
    return _instant(raw.get("landing_queued_at"))


def _needs_human(ports: Ports, number: int) -> bool:
    """Whether the issue carries `needs-human`: the one read of the issue per delivered task. An
    issue that cannot be read does not make its task a person's: the document says the rest."""
    try:
        labels = ports.tracker.issue_state(number).labels
    except (ValueError, OSError):
        return False
    return labels is not None and escalate_module.NEEDS_HUMAN in labels


def _released(
    ports: Ports,
    cfg: Configuration | None,
    task_id: str,
    number: int,
    raw: TaskState,
    assessed: MergeAssessment | None,
    stopped: bool,
) -> TaskState | None:
    """The document of an escalated task whose owner already answered, as the release `land`
    carries out for the head would leave it (`landing_plan.release_patch`), or `None`: the task
    is the system's again. The release is the one `_landing_step` asks, read the same way."""
    if cfg is None or assessed is None or stopped or not raw.get("escalated"):
        return None
    facts, decision = assessed.facts, assessed.decision
    refusal = decision.reason
    if decision.kind != "refuse" or refusal is None or facts.pr_head is None:
        return None
    if refusal in landing_rules.HUMAN_OWNED_REFUSALS:
        return None
    kind = landing_plan.step_for(raw, refusal, facts, turn="head").kind
    if not landing_plan.release_asked(raw, kind, refusal=refusal, facts=facts):
        return None
    outcome = landing_plan.release(
        ports, cfg, task_id, raw, number, head=facts.pr_head, upstream=facts.current_master
    )
    patch = landing_plan.release_patch(
        raw, outcome, facts.current_master, datetime.now(UTC).isoformat()
    )
    return apply(raw, patch) if patch else None


def _system_view(
    ports: Ports,
    cfg: Configuration | None,
    task_id: str,
    number: int,
    raw: TaskState,
    assessed: MergeAssessment | None,
    stopped: bool,
    stranded: bool,
) -> TaskState:
    """The document `waits_on_human` reads: `raw`, except that an escalation that is not a
    person's -- a stranded diagnose nobody was told of (GH-257, `land` retries it on the
    calendar), or one the owner released -- is read as not escalated. Everything else, the
    definition of a person's task included, is `waits_on_human`'s."""
    if stranded:
        return {key: value for key, value in raw.items() if key != "escalated"}
    return _released(ports, cfg, task_id, number, raw, assessed, stopped) or raw


def _seconds(value: object) -> float:
    """A recorded `head_charged_s`: a finite number `>= 0`, else nothing."""
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
    return float(value) if isinstance(value, int | float) and ok else 0.0


def _member(
    ports: Ports,
    cfg: Configuration | None,
    lock: OrderLock,
    task_id: str,
    number: int,
    raw: TaskState,
    assessed: MergeAssessment | None,
    *,
    busy: bool = False,
    now: datetime | None = None,
    resumes: Sequence[rules.ResumeCandidate] = (),
) -> landing_order.Member:
    """`task_id`'s place in the order. `assessed` is `None` for a task a child works on: its
    refusal is not known and nothing is read; the document alone says whether it is a person's.
    `busy` is a live child of the product on it (a live run lock says so too). The head-budget
    facts (GH-621) are read from the document and the assessment; nothing is derived from them
    here (`landing_order` does) and nothing is written."""
    now = now or datetime.now(UTC)
    live_pass = landing_rules.landing_pass_live(raw)
    facts = assessed.facts if assessed is not None else None
    decision = assessed.decision if assessed is not None else None
    pr_head = facts.pr_head if facts is not None else None
    record = lock.read(task_id) if assessed is not None else None
    stopped = record is not None and record.stopped_deliberately
    refusal = decision.reason if decision is not None and decision.kind == "refuse" else None
    stranded = assessed is not None and landing_plan.stranded_now(ports, task_id, raw)
    waits = landing_rules.waits_on_human(
        _system_view(ports, cfg, task_id, number, raw, assessed, stopped, stranded),
        pr_head,
        refusal,
        spent=landing_plan.spent(raw, pr_head, None),
        stopped=stopped,
        needs_human=assessed is not None and _needs_human(ports, number),
        stranded=False,
    )
    working = busy or lock.refusal(task_id) is not None
    report = assessed.report if assessed is not None else None
    ci_running = (
        decision is not None
        and decision.kind == "wait"
        and isinstance(report, ci.CiReport)
        and rules.same_commit(report.head_sha, pr_head)
        and bool(report.running_runs)
    )
    master = facts.current_master if facts is not None else None
    merge_due = (
        decision is not None
        and decision.kind in ("merge", "run_checks")
        and exempt_queue.backoff_ends(raw.get("merge_refusal"), pr_head, master) is None
    )
    turn_at = landing_order.instant(raw.get("head_turn_at"))
    yields = raw.get("head_yields")
    return landing_order.Member(
        number,
        _stamp(raw),
        live_pass,
        waits,
        turn_at=turn_at,
        charged_s=_seconds(raw.get("head_charged_s")),
        seen_at=landing_order.instant(raw.get("head_seen_at")),
        yielded_at=landing_order.instant(raw.get("head_yielded_at")),
        yields=len(yields) if isinstance(yields, list) else 0,
        steps_on_head=(
            landing_order.unproductive_steps(landing_plan.live_entries(raw), pr_head, turn_at)
            if pr_head is not None
            else 0
        ),
        busy=working,
        merge_due=merge_due,
        waits_on_ci=ci_running and not working,
        account_paused=rules.account_paused(resumes, now) and not working,
    )


def evaluate_landing_order(
    ports: Ports,
    eval_set: frozenset[int] | None,
    current_master: str,
    lock: OrderLock,
    busy: frozenset[str] = frozenset(),
    cfg: Configuration | None = None,
    now: datetime | None = None,
    resumes: Sequence[rules.ResumeCandidate] = (),
) -> LandingRound:
    """The first phase (GH-588): `assess_merge` over every task with a pull request and no
    `closed_at`, and the head of the landing order over them (`landing_order.advance`, the one
    place that sorts). A pull request closed unmerged is not a member; a busy or locked task is,
    since a child at work is the head if it is the oldest. The one function `gather_merges` and
    `agentic land` share, so there is no second ordering.

    Read-only (GH-621): the members are read as the documents hold them, `landing_order.resumed`
    is applied once, then `advance`. Each entry keeps its `member` as read and what `advance` made
    of it (`turned`); the writer (`_record_turn`) compares the two. `now` defaults to the clock;
    `resumes` are the product's paused tasks, for `account_paused`."""
    now = now or datetime.now(UTC)
    entries: list[OrderEntry] = []
    members: list[landing_order.Member] = []

    def add(
        task_id: str,
        number: int,
        raw: TaskState,
        outcome: MergeAssessment | Abandoned | StackUnreadable | None,
        member: landing_order.Member | None,
    ) -> None:
        entries.append(OrderEntry(task_id, number, raw, outcome, member))
        if member is not None:
            members.append(member)

    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None:
            continue
        raw = ports.documents.load(task_id) or {}
        if raw.get("closed_at") or not raw.get("pull_request"):
            continue
        working = task_id in busy
        if working or lock.refusal(task_id) is not None:
            member = _member(
                ports, cfg, lock, task_id, number, raw, None, busy=True, now=now, resumes=resumes
            )
            add(task_id, number, raw, None, member)
            continue
        try:
            assessed = assess_merge(ports, task_id, raw, number, eval_set, current_master)
        except StackUnreadable as unreadable:
            # Its pull request is open and recorded: still a member (an unreadable base is
            # no reason to let a younger pull request pass it).
            member = _member(ports, cfg, lock, task_id, number, raw, None, now=now, resumes=resumes)
            add(task_id, number, raw, unreadable, member)
            continue
        member = None
        if not isinstance(assessed, Abandoned):
            member = _member(
                ports, cfg, lock, task_id, number, raw, assessed, now=now, resumes=resumes
            )
        add(task_id, number, raw, assessed, member)
    result, turned = landing_order.advance(landing_order.resumed(members, now), now)
    after = iter(turned)
    return LandingRound(
        tuple(
            OrderEntry(e.task_id, e.number, e.raw, e.outcome, e.member, next(after))
            if e.member is not None
            else e
            for e in entries
        ),
        result.head,
        result.yielded,
    )


def _yield_record(
    entry: OrderEntry, round_: LandingRound, now: datetime
) -> tuple[landing_order.YieldReason, HeadYield] | None:
    """The `{at, head, reason}` this round appends to `head_yields` for `entry`, with its reason,
    or `None` when this turn already holds one for the same head (idempotent per turn)."""
    gave_up = next((y for y in round_.yielded if y.member.issue == entry.number), None)
    if gave_up is None or entry.member is None:
        return None
    outcome = entry.outcome
    pr_head = outcome.facts.pr_head if isinstance(outcome, MergeAssessment) else None
    previous = entry.raw.get("head_yields")
    last = previous[-1] if isinstance(previous, list) and previous else None
    if isinstance(last, dict):
        at = landing_order.instant(last.get("at"))
        turn = entry.member.turn_at
        if rules.same_commit(last.get("head"), pr_head) and at and turn and at >= turn:
            return None
    return gave_up.reason, {"at": now.isoformat(), "head": pr_head or "", "reason": gave_up.reason}


def _record_turn(
    ports: Ports, entry: OrderEntry, round_: LandingRound, now: datetime, out: list[str]
) -> bool:
    """The one writer of the head budget (GH-621) and of the head's anchor (`head_since_*`,
    GH-588, read by `with_ci`), through `record_closure`. What is written is
    `landing_order.turn_patch`'s: the transitions, and the head's sighting at most every
    `HEAD_SEEN_EVERY`. The head also gets its anchor when the document does not hold it for its
    pull request head; a yield clears it, so a pull request that returns with the same sha is
    anchored again. A yield appends `{at, head, reason}` to `head_yields` (the last
    `HEAD_YIELDS_MAX`) unless this turn already holds one, and says so in exactly one line, after
    the write succeeded. A write a concurrent `save` loses is written again next round with a
    later instant, which only counts less. True when written."""
    if entry.member is None or entry.turned is None:
        return False
    patch = landing_order.turn_patch(entry.member, entry.turned, now)
    outcome = entry.outcome
    pr_head = outcome.facts.pr_head if isinstance(outcome, MergeAssessment) else None
    line: str | None = None
    if patch.pop(landing_order.YIELD_MARK, None) is not None:
        patch["head_since_at"] = patch["head_since_head"] = None
        if (recorded := _yield_record(entry, round_, now)) is not None:
            reason, row = recorded
            previous = entry.raw.get("head_yields")
            patch["head_yields"] = add_head_yield(
                [e for e in previous if isinstance(e, dict)] if isinstance(previous, list) else [],
                row,
            )
            line = (
                f"agentic: poll: landing order #{entry.number} -> yielded ({reason}) "
                f"to #{round_.head}"
            )
    if (
        entry.number == round_.head
        and pr_head is not None
        and (
            "head_yielded_at" in patch
            or not rules.same_commit(entry.raw.get("head_since_head"), pr_head)
        )
    ):
        patch["head_since_at"], patch["head_since_head"] = now.isoformat(), pr_head
    if not patch:
        return False
    try:
        ports.documents.record_closure(entry.task_id, patch)
    except (ValueError, OSError):
        return False
    if line is not None:
        out.append(line)
    return True


def gather_merges(
    ports: Ports,
    eval_set: frozenset[int] | None,
    current_master: str | None,
    lock: RunLock,
    lines: list[str] | None = None,
    lands: list[rules.LandCandidate] | None = None,
    live: frozenset[LandingStepKind] = landing_plan.LIVE_STEPS,
    now: datetime | None = None,
    busy: frozenset[str] = frozenset(),
    cfg: Configuration | None = None,
    reruns: list[int] | None = None,
    backed_off: list[int] | None = None,
    backoff_ends: dict[int, datetime] | None = None,
    held: bool = False,
    resumes: Sequence[rules.ResumeCandidate] = (),
) -> list[int]:
    """Every delivered, unclosed task `rules.auto_merge_decision` finds worth a merge
    child -- `merge` or `run_checks` -- and a line appended to `lines` for each one it
    refuses, naming the reason (GH-178). A task whose run lock reads alive is skipped
    before anything else is read: a merge child already works on it, or a run does. So is a
    task in `busy`, the ids that have a live registered child of any kind for this product
    (GH-263): no second child for one task, whatever slots are free. An
    unreadable upstream sha leaves every task alone, since the checks could not be told
    from stale ones; an unreadable pull request reads as not open and is refused with its
    line, every round. A task whose pull request reads closed unmerged
    (`rules.pr_abandoned`) is skipped like one with no pull request, whatever its status:
    no candidate, no line, and nothing written to its document (only a human-run
    `agentic close` records a closure for it). This decides over the
    facts the poller has: the verb decides again, after its checks, with the diff's paths
    too, so a candidate here is a reason to look, never the merge. Every refusal it logs also
    asks `landing_step` (GH-239) what the system does instead; `lands`, when given, collects
    the issues whose step is in `live` and not yet in the document's `landing` list for this
    head, each as a `rules.LandCandidate` with the kind its child registers under
    (`landing.run_kind_of`). `wait` and `none` are never in `live`: they spawn nothing. A
    `not_ready` task whose reopened landing lost its resume child (`landing.resume_owed` at
    `now`, the round's time) is a `lands` candidate too (GH-242), as is a stranded diagnose;
    both have no step, so they are `land_model`. A task is in `found` or in `lands`, never
    both. A task whose pull request closed unmerged after slices landed for it (`cfg` given)
    is closed in its document when git says every unit landed, or its issue goes into
    `reruns` (when given) when work is left; each says so in one line (GH-334). A candidate
    whose `merge_refusal` still holds on its head and master (`exempt_queue.backed_off`, GH-500)
    goes into `backed_off` (when given) instead of the result: `rules.poll_plan` defers it. Its
    backoff's end, for any candidate of that pair, goes into `backoff_ends` (when given): the
    starvation clock starts there (`exempt_queue.backoff_ends`). A `held` round (GH-621) observes
    nothing: no turn is charged, seen or yielded and no yield is said (`_record_turn` is not
    called); it still decides and says its order lines."""
    out = lines if lines is not None else []
    now = now or datetime.now(UTC)
    if current_master is None:
        return []  # the round's own upstream note already says why
    found: list[int] = []
    round_ = evaluate_landing_order(ports, eval_set, current_master, lock, busy, cfg, now, resumes)
    for entry in round_.entries:
        task_id, number, raw = entry.task_id, entry.number, entry.raw
        assessed = entry.outcome
        if not held:
            _record_turn(ports, entry, round_, now, out)
        if assessed is None:
            continue  # busy or locked: a child already works on it
        if isinstance(assessed, StackUnreadable):
            # A base the stack cannot be read at (gone, or a malformed key) is this task's
            # problem, never the round's: every other candidate is still decided (GH-216).
            out.append(
                f"agentic: poll: auto-merge #{number} -> left to the human: stack_unreadable"
            )
            continue
        reference = raw.get("pull_request")
        if isinstance(assessed, Abandoned):
            # Closed unmerged: nothing to merge. Silent and unwritten unless slices landed
            # for it (GH-334), which this very read decides, without asking the tracker again.
            if cfg is not None:
                _abandoned_by_slices(
                    ports,
                    cfg,
                    task_id,
                    raw,
                    number,
                    str(reference),
                    current_master,
                    now,
                    out,
                    reruns,
                )
            continue
        turn = landing_order.turn_of(number, round_.head)
        facts, decision, report, pull_request = (
            assessed.facts,
            assessed.decision,
            assessed.report,
            assessed.pull_request,
        )
        owned = (
            entry.member is not None and entry.member.waits_on_human and decision.kind == "refuse"
        )
        if turn == "waiting" and not owned:
            # GH-588: a pull request that is not the head of the order is left as it is,
            # approved: a clean merge-tree needs no pass, a conflict none until its turn.
            reading = (
                "unread" if facts.conflicts is None else "conflicts" if facts.conflicts else "clean"
            )
            behind = f"behind #{round_.head}" if round_.head is not None else "with no head"
            out.append(f"agentic: poll: auto-merge #{number} -> waiting ({reading}) {behind}")
            continue
        if assessed.conflict_unread:
            head = pull_request.head_oid if pull_request is not None else None
            out.append(f"agentic: poll: auto-merge #{number} -> conflict unread ({head})")
        if decision.kind == "wait":
            out.append(ci_wait_line(number, facts, report))
        elif decision.kind == "refuse":
            line = f"agentic: poll: auto-merge #{number} -> left to the human: {decision.reason}"
            if decision.reason == "merge_conflict" and assessed.conflict_source is not None:
                line += f" ({assessed.conflict_source})"
            if decision.reason == "checks_failed":
                pr_head = pull_request.head_oid if pull_request is not None else None
                summary = presubmit.failed_checks_summary(_recorded(raw, "merge_checks"), pr_head)
                line += f" ({summary})" if summary else ""
            if decision.reason == "criteria_over_cap":
                line += f" ({CRITERIA_JOIN.join(facts.criteria_regressed or ())})"
            if decision.reason == "ci_failed" and facts.ci_verdict is not None:
                names = ", ".join(check.name for check in facts.ci_verdict.failing)
                line += f" ({names})"
            out.append(line)
            if lands is not None and decision.reason is not None and turn == "head":
                # Every refusal logged above asks `landing_step` what to do instead; only a
                # built step not yet dispatched on this head is worth a `land` child.
                step = _landing_step(
                    ports, cfg, task_id, raw, number, decision.reason, facts, live, now, turn
                )
                if step is not None:
                    kind = landing_rules.run_kind_of(step.kind, decision.reason)
                    lands.append(rules.LandCandidate(number, kind))
                elif decision.reason == "not_ready" and (
                    landing_rules.resume_owed(raw, now)
                    or landing_rules.landing_pass(raw) == "unstarted"
                    or landing_plan.stranded_now(ports, task_id, raw)
                ):
                    # A `not_ready` task whose reopened pass lost its resume child owes
                    # one: `land` respawns it (GH-242). So does a stranded diagnose, which
                    # `land` frees and retries on the calendar (GH-257), and a pass recorded
                    # whose executor never reopened anything (`unstarted`, GH-450): `land`
                    # runs it on the same entry. No step: it can spend a model, so it counts.
                    lands.append(rules.LandCandidate(number, landing_rules.run_kind_of(None, None)))
        else:
            head = pull_request.head_oid if pull_request is not None else None
            refusal = raw.get("merge_refusal")
            ends = exempt_queue.backoff_ends(refusal, head, current_master)
            if backoff_ends is not None and ends is not None:
                backoff_ends[number] = ends
            if backed_off is not None and exempt_queue.backed_off(
                refusal, head, current_master, now
            ):
                backed_off.append(number)
            else:
                found.append(number)
    return found


def gather_resumes(ports: Ports, now: datetime) -> list[rules.ResumeCandidate]:
    """Every paused task `rules.auto_resumable` finds eligible, with the `since`/
    `prior`/`limit` `rules.next_attempt_at` needs, and the `chase_prior`/`ceiling_spent`
    `rules.resume_exhausted` decides over, and for an `account_limit` pause the stated reset
    (`rules.stated_reset`) and whether it was retaken since (`rules.account_limit_due`'s
    inputs; ADR-0039, addendum GH-381). A task with no `Escalated:` comment
    at all (its document disagrees with the issue, or it has no issue) is skipped: there
    is no wait to measure `since` from."""
    resumes: list[rules.ResumeCandidate] = []
    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None:
            continue
        raw = ports.documents.load(task_id) or {}
        if raw.get("closed_at") or not raw.get("escalated"):
            continue
        reason = rules.auto_resumable(raw)
        if reason is None:
            continue
        comments = ports.tracker.timestamped_comments(task_id)
        if landing_plan.stranded(raw, comments):
            continue  # `land` owns it (GH-257): `resume` would refuse a task that is not paused
        since = _since_last_escalated(comments)
        if since is None:
            continue
        if _human_spoke_after_the_system(comments):
            # A person is talking (Human decision, issue #137): the pause is the
            # human's from here on, whatever the calendar says -- never retaken on
            # top of a comment `agentic resume`'s own `--yes` would otherwise run over.
            continue
        chase = _prior_auto_resumes(comments)
        resets_at = None
        if reason == "account_limit":
            prior, ceiling_spent = _account_limit_counts(raw, comments, chase)
            resets_at = rules.stated_reset(raw)
        else:
            prior, ceiling_spent = chase, False
        limit = rules.auto_resume_limit(raw, reason)
        resumes.append(
            rules.ResumeCandidate(
                number,
                reason,
                since,
                prior,
                limit,
                chase,
                ceiling_spent,
                resets_at,
                _retaken_since_escalated(comments),
            )
        )
    return resumes


def gather_closeness(ports: Ports, issues: Iterable[int]) -> dict[int, int]:
    """The `rules.closeness` rank of each issue's task document, read once, for the order
    `rules.poll_plan` starts retakes and resumes in (GH-362). An issue with no document is
    left out: `poll_plan` ranks a missing key 0."""
    ranks: dict[int, int] = {}
    for issue in set(issues):
        raw = ports.documents.load(ports.inbox.task_id(issue))
        if raw is not None:
            ranks[issue] = rules.closeness(raw)
    return ranks


def escalate_persisted_limits(
    cfg: Configuration, ports: Ports, resumes: Sequence[rules.ResumeCandidate]
) -> list[str]:
    """The week of hourly retakes is spent (`rules.account_limit_persisted`): the task
    escalates as a failure, once -- the patch leaves a failure entry that `rules.
    auto_resumable` no longer retakes, so the next round does not find it again. The one
    place the poller writes a task document, and only for this escalation."""
    lines: list[str] = []
    for r in resumes:
        if not rules.account_limit_persisted(r):
            continue
        task_id = ports.inbox.task_id(r.issue)
        raw = ports.documents.load(task_id)
        if raw is None:
            continue
        state = apply(raw, rules.account_limit_persisted_patch(at=ports.now()))
        ports.documents.save(state)
        if rules.needs_a_human(state):
            ports.tracker.label(task_id, escalate_module.NEEDS_HUMAN)
        ports.tracker.comment(task_id, escalate_module.comment(state, cfg, ports))
        lines.append(
            f"agentic: poll: #{r.issue} {rules.ACCOUNT_LIMIT_PERSISTED} -> left to the human"
        )
    return lines


@dataclass(frozen=True)
class CrashRetake:
    """What `gather_retakes` read once about a run that raised under the graph, handed to
    `execute` so it never recounts: the retakes already made, the version the run recorded when
    it raised, and the key (the current version, without `+dirty`) this retake resumes under."""

    prior: int
    crashed_version: str
    key: str


@dataclass(frozen=True)
class Seen:
    """What `gather_retakes` decided about one task with a lock record (GH-616): the verdict of
    `rules.interrupted_run`, and whether the record carries the `agentic stop` mark. The round
    reads it to word the one line an owed task gets when nothing else names it."""

    verdict: rules.RetakeVerdict
    stopped: bool


@dataclass(frozen=True)
class RetakesSpent:
    """What `gather_retakes` or `gather_orphans` read once about a task whose retake budget is
    spent (or whose dead lock went stale) and that nobody was told about yet, handed to `execute`
    so it never rereads: the `shape` of what happened, the retakes `used`, the `createdAt` of the
    first and the last of them, and the dead run's lock (`pid`, `started_at`, `boot_id`; `None`
    for an orphan, which left none). `reap` says the run died in this boot, so its own scope is
    reaped when the notice is written (ADR-0052)."""

    issue: int
    shape: retake_rules.RetakeShape
    used: int
    first_at: datetime | None
    last_at: datetime | None
    pid: int | None = None
    started_at: str | None = None
    boot_id: str | None = None
    reap: bool = False


def _human_line(number: int, record: LockRecord, other_boot: bool | None) -> str:
    """The one line a run left to the human says when its document is one the poller cannot
    read (no `status`): what happened to its lock, whose it was, and the command that takes it
    up. A spent budget or a stale lock is not this: it is announced once, on the issue
    (`RetakesSpent`)."""
    if record.stopped_at_shutdown:
        what = "stopped at shutdown"
    elif other_boot is None:
        what = "died (boot unknown)"
    else:
        what = "died in this boot"
    return (
        f"agentic: poll: #{number} {what} (lock pid {record.pid} since {record.started_at}), "
        f"left to the human: agentic resume --issue {number}"
    )


def _notice_pending(comments: Sequence[IssueComment]) -> bool:
    """Whether the spent budget was not announced yet (`retakes.notice_posted` over the comments
    already fetched)."""
    from .resume import RESUMED, SYSTEM_COMMENTS

    return not retake_rules.notice_posted([c.body for c in comments], SYSTEM_COMMENTS, RESUMED)


def _lock_shape(
    decision: Literal["spent", "stale"], record: LockRecord, other_boot: bool | None
) -> retake_rules.RetakeShape:
    """What killed the run, as the notice names it."""
    if decision == "stale":
        return "stale"
    if record.crashed_version is not None:
        return "crash"
    return "reboot" if other_boot else "same_boot"


def gather_retakes(
    ports: Ports,
    lock: RunLock,
    current_boot: str | None,
    *,
    system_version: Callable[[], str | None] = lambda: None,
    crash: dict[int, CrashRetake] | None = None,
    now: datetime | None = None,
    spent: dict[int, RetakesSpent] | None = None,
    seen: dict[int, Seen] | None = None,
    reported: set[int] | None = None,
) -> tuple[list[int], list[str]]:
    """Every open task in the tray whose run is gone (`rules.interrupted_run`, one budget for
    every shape, GH-466), and the lines for the ones left to the human. A task whose budget is
    spent, or whose dead lock went stale, is a `RetakesSpent` fact under its issue in `spent`
    (filled here like `crash`) unless its issue already carries the notice; this function writes
    nothing: the tracker, the lock and the documents are `execute`'s to change. A run that
    raised under the graph (its record carries `crashed_version`, GH-308) is retaken the same way,
    but is returned apart: its issue is a key of `crash` (the mapping the caller hands in, filled
    here, the same shape as `gather_merges`' `lands`) with the count `execute` needs, and is not in
    the list. `system_version` is read only when some record carries the mark, at most once per
    round, and names the version the crash retake resumes under; the count comes from the
    comments after the reset boundary. The tray (`Tracker.queue`) and the documents written
    inside `retakes.STALE_AFTER` are read at most once per round, when a record needs them. The
    lock is
    only ever *read* here
    (`RunLock.read`, and `RunLock.refusal` for liveness, which never writes): taking it
    over is the retaken `resume`'s own first act, never the poller's, and a decision made
    over a record the poller had just rewritten would read its own pid. Liveness is
    asked of the lock itself, the one place that knows the boot id (`liveness.is_alive`),
    so this decision and `acquire` cannot disagree; a lock of another host reads as
    alive, as `acquire` reads it. The comments are fetched only for a task the first,
    cheap verdict did not already rule out. `seen` (GH-616) is filled with the verdict of every
    task that has a lock record, and `reported` with the issues this function printed a line
    for, so the round can say why an owed task was left alone (`rules.owed_a_line`)."""
    retakes: list[int] = []
    lines: list[str] = []
    crashes = crash if crash is not None else {}
    notices = spent if spent is not None else {}
    verdicts = seen if seen is not None else {}
    said = reported if reported is not None else set()
    read_version = functools.cache(system_version)
    moment = now if now is not None else datetime.now(UTC)
    tray = functools.cache(lambda: frozenset(ports.tracker.queue()))
    fresh = functools.cache(
        lambda: frozenset(ports.documents.ids_touched_since(moment - retake_rules.STALE_AFTER))
    )
    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None:
            continue
        record = lock.read(task_id)
        if record is None:
            continue
        raw = ports.documents.load(task_id) or {}
        verdict = boot_verdict(record.boot_id, current_boot)
        other_boot = None if verdict == "unknown" else verdict == "other_boot"
        facts = rules.LockFacts(
            other_boot,
            alive=lock.refusal(task_id) is not None,
            crashed_version=record.crashed_version,
            stopped_deliberately=record.stopped_deliberately,
        )
        # The document's write time is only known as "inside the stale window or not".
        written_at = moment if task_id in fresh() else moment - retake_rules.STALE_AFTER
        in_tray = number in tray()
        first = rules.interrupted_run(
            raw,
            facts,
            retake_rules.Budget(0, None, None),
            moment,
            in_tray=in_tray,
            document_written_at=written_at,
        )
        verdicts[number] = Seen(first, record.stopped_deliberately)
        if first in ("none", "running"):
            continue
        comments = ports.tracker.timestamped_comments(task_id)
        used = _retake_budget(comments)
        decision = rules.interrupted_run(
            raw, facts, used, moment, in_tray=in_tray, document_written_at=written_at
        )
        verdicts[number] = Seen(decision, record.stopped_deliberately)
        match decision:
            case "retake":
                retakes.append(number)
            case "retake_crash":
                assert record.crashed_version is not None  # the rule's own
                key = rules.crash_version_key(read_version()) or "unknown"
                crashes[number] = CrashRetake(used.used, record.crashed_version, key)
            case "calendar":
                # A retake is made, and the next one is only waiting for its time: said, not silent.
                due = retake_rules.due(used, written_at, timedelta(0)).astimezone(UTC)
                lines.append(
                    rules.orphaned_line(number, "retake_calendar", due.strftime("%H:%M UTC"))
                )
                said.add(number)
            case "human":
                lines.append(_human_line(number, record, other_boot))
                said.add(number)
            case "spent" | "stale":
                # Told to the human on the issue (now or by an earlier round): accounted for.
                said.add(number)
                if _notice_pending(comments):
                    notices[number] = RetakesSpent(
                        number,
                        _lock_shape(decision, record, other_boot),
                        used.used,
                        used.first_at,
                        used.last_at,
                        record.pid,
                        record.started_at,
                        record.boot_id,
                        reap=other_boot is False,
                    )
            case "none" | "running":
                continue
            case _:
                assert_never(decision)
    return retakes, lines


def _blocker_numbers(ports: Ports, number: int) -> tuple[int, ...]:
    state = ports.tracker.issue_state(number)
    return rules.blocker_numbers(state.blocked_by, lambda: ports.inbox.task(number)["description"])


def _merge_commit_for(ports: Ports, blocker: int, closing_merge_commit: str | None) -> str | None:
    """The document's own `merge_commit` wins over `gh`'s (Tech Lead plan): a document
    this product already wrote is the more trustworthy record of what actually landed."""
    task_id = ports.inbox.task_id(blocker)
    document = ports.documents.load(task_id)
    if document and document.get("merge_commit"):
        return str(document["merge_commit"])
    return closing_merge_commit


_Verdict = Literal["resolved", "open", "unknown"] | rules.BlockerReason


def _blocker_verdict(
    cfg: Configuration, ports: Ports, blocker: int, upstream: Callable[[], Upstream]
) -> _Verdict:
    """How one blocker stands. Every resolution path sits behind `state == "CLOSED"`: an
    open issue never resolves. A `COMPLETED` issue resolves on a merge commit in the
    upstream, or (GH-344, no merge commit of its own) on a status the system recorded:
    `closed_by_slices` with every unit's `reviewed_head` an ancestor of the upstream, or
    `tracker_only`, where there was no pull request and nothing to merge. Never prose."""
    state = ports.tracker.issue_state(blocker)
    if state.state != "CLOSED":
        return "open"
    if state.state_reason not in ("COMPLETED", "NOT_PLANNED"):
        return "unknown"
    if state.state_reason == "NOT_PLANNED":
        return "resolved"
    # The fetched tip, never local `HEAD` (GH-180): a merge on the remote that nobody
    # pulled is merged. `upstream` is asked only here, when a merge commit is to be
    # judged, and is memoised per round by `run_once`: at most one fetch.
    merge_commit = _merge_commit_for(ports, blocker, state.closing_merge_commit)
    if merge_commit is not None:
        tip = upstream().sha
        merged = ports.workspace.is_ancestor(cfg.product_dir, merge_commit, ref=tip or "HEAD")
        if merged:
            return "resolved"
        # With no fetched tip the judgement fell back to local `HEAD`, and the round already
        # carries the upstream note: that is doubt, not a verdict on the merge.
        return "merge_not_in_upstream" if tip else "unknown"
    document = ports.documents.load(ports.inbox.task_id(blocker)) or {}
    if document.get("status") == "tracker_only":
        return "resolved"  # COMPLETED here: the stateReason triage above already ran
    heads = rules.slices_landed_heads(document)
    if heads is None:
        return "no_merge_recorded"
    tip = upstream().sha
    if all(ports.workspace.is_ancestor(cfg.product_dir, h, ref=tip or "HEAD") for h in heads):
        return "resolved"
    return "slices_not_in_upstream" if tip else "unknown"


def _blocked(
    cfg: Configuration, ports: Ports, number: int, upstream: Callable[[], Upstream]
) -> rules.BlockerReport:
    """What `number`'s blockers come to; the launch predicate is `bool(report)`. Any
    doubt -- an unknown git object, a `gh` failure, a blocker whose own `stateReason` this
    function does not recognize, or a real git failure (`GitError` out of
    `Workspace.is_ancestor`, `ValueError`/`OSError` out of a `gh` call that is not merely
    "unknown field") -- puts that blocker in `unknown`, which blocks (Tech Lead plan: "ante
    la duda, no lanza"). A `GitError` escaping here would otherwise end the whole `_poll`
    loop, which has no handler of its own (the blocking finding this closes): one
    candidate's git trouble must cost that candidate's own launch, never the round, and
    never the process. When the blockers themselves cannot be read the candidate is its
    own `unknown` entry, so the report still blocks."""
    try:
        blockers = _blocker_numbers(ports, number)
    except (ValueError, OSError, KeyError, GitError):
        return rules.BlockerReport(unknown=(number,))
    open_: list[int] = []
    closed: list[tuple[int, rules.BlockerReason]] = []
    unknown: list[int] = []
    for blocker in blockers:
        try:
            verdict = _blocker_verdict(cfg, ports, blocker, upstream)
        except (ValueError, OSError, KeyError, GitError):
            verdict = "unknown"
        match verdict:
            case "resolved":
                pass
            case "open":
                open_.append(blocker)
            case "unknown":
                unknown.append(blocker)
            case reason:
                closed.append((blocker, reason))
    return rules.BlockerReport(tuple(open_), tuple(closed), tuple(unknown))


# A budget with nothing used: what the cheap, fields-only verdicts are asked with.
_NO_RETAKES = retake_rules.Budget(0, None, None)


def queue_documents(ports: Ports) -> dict[int, TaskState | None]:
    """The document of every issue in the bandeja (`Tracker.queue`: open and labelled
    `agentic`), in queue order, `None` for one with none yet. Loaded once per round and
    shared by the launch pass (an issue with no document) and the orphan pass (one with a
    pre-delivery document), so neither reads a file the other already did."""
    return {
        number: ports.documents.load(ports.inbox.task_id(number))
        for number in ports.tracker.queue()
    }


def gather_launches(
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    upstream: Callable[[], Upstream] | None = None,
    reports: list[rules.BlockerReport] | None = None,
    by_issue: dict[int, rules.BlockerReport] | None = None,
    documents: Mapping[int, TaskState | None] | None = None,
    notes: list[str] | None = None,
) -> list[rules.LaunchCandidate]:
    """Every open `agentic`-labelled issue with no document yet (no run, so nothing
    delivered and no pull request either): `rules.poll_plan`'s own launch candidates,
    with each one's own launch-attempt schedule (`registry.launch_attempts`) so a
    launch that never registered a run does not relaunch every round. An issue that
    now has a document clears its own stale attempt record (`registry.clear_launch`):
    it is no longer a candidate at all. Each candidate's `BlockerReport` is appended to
    `reports` when given (GH-344), for the queue-blocked line, and set in `by_issue` under
    its issue number when given (GH-375), for the board mirror. `documents` (GH-403) is
    `queue_documents`' answer, shared with the orphan pass so each queue document is loaded
    once per round; without it this function reads it itself. When the product declares an
    experiment (GH-470, `ports.experiment`), each launch candidate that can launch carries the
    arm `experiment.assign` picked for it, or the one its launch record already has; an invalid
    declaration, or an `.env` switch an arm names, adds one line to `notes` and leaves every
    candidate unassigned -- it never stops the queue. With no declaration this reads and counts
    nothing more than it did before."""
    upstream = upstream or functools.cache(ports.workspace.upstream)
    product = ports.stack.product()
    launches: list[rules.LaunchCandidate] = []
    queued = documents if documents is not None else queue_documents(ports)
    declaration = _declared_experiment(cfg, ports, notes)
    counts = _arm_counts(declaration, ports, registry, product, queued) if declaration else {}
    for number, loaded in queued.items():
        if loaded is not None:
            registry.clear_launch(product, number)
            continue
        report = _blocked(cfg, ports, number, upstream)
        if reports is not None:
            reports.append(report)
        if by_issue is not None:
            by_issue[number] = report
        attempt = registry.launch_attempts(product, number)
        since = attempt.since if attempt is not None else None
        prior = attempt.prior if attempt is not None else 0
        arm = None
        if declaration is not None and not report:
            arm = _arm_for(declaration, number, attempt, counts)
        launches.append(
            rules.LaunchCandidate(
                number, bool(report), since, prior, len(rules.LAUNCH_SCHEDULE), arm
            )
        )
    return launches


EXPERIMENT_FILE = ".agentic/experiment.yaml"


def _declared_experiment(
    cfg: Configuration, ports: Ports, notes: list[str] | None
) -> experiment.Experiment | None:
    """The product's valid experiment for this round, or `None` -- no declaration, an invalid one,
    or one the environment contradicts -- the last two adding their one line to `notes`."""
    if ports.experiment is None:
        return None
    try:
        data = ports.experiment.read()
        if data is None:
            return None
        declaration = experiment.parse_experiment(data, EXPERIMENT_FILE)
    except (ValueError, experiment.InvalidExperiment) as exc:
        _note(notes, f"agentic: poll: experiment ignored, launching unassigned: {exc}")
        return None
    if clash := experiment.conflicts(declaration, cfg):
        _note(
            notes,
            f"agentic: poll: experiment {declaration.id} ignored, launching unassigned: "
            f".env sets {', '.join(clash)}, which an arm also sets",
        )
        return None
    return declaration


def _note(notes: list[str] | None, line: str) -> None:
    if notes is not None:
        notes.append(line)


def _arm_counts(
    declaration: experiment.Experiment,
    ports: Ports,
    registry: RunRegistry,
    product: str,
    queued: Mapping[int, TaskState | None],
) -> dict[str, int]:
    """Tasks per arm name, each counted once: the documents under this experiment's id (the
    queue's own were loaded already) and the launch records with an arm whose issue has no
    document yet."""
    counts: dict[str, int] = {}
    loaded = {ports.inbox.task_id(n): doc for n, doc in queued.items() if doc is not None}
    for task_id in ports.documents.ids():
        doc = loaded[task_id] if task_id in loaded else ports.documents.load(task_id)
        given = (doc or {}).get("assignment")
        if given is not None and given.get("experiment") == declaration.id:
            counts[given["arm"]] = counts.get(given["arm"], 0) + 1
    for number, doc in queued.items():
        attempt = registry.launch_attempts(product, number) if doc is None else None
        if attempt is not None and attempt.arm is not None:
            counts[attempt.arm] = counts.get(attempt.arm, 0) + 1
    return counts


def _arm_for(
    declaration: experiment.Experiment,
    number: int,
    attempt: LaunchAttempt | None,
    counts: dict[str, int],
) -> str:
    """The arm of a launch candidate: the one its record already has (counted already, and kept
    while the declaration still names it), else the next `experiment.assign` picks, which
    counts for the candidates after it in this round."""
    if attempt is not None and attempt.arm in {a.name for a in declaration.arms}:
        assert attempt.arm is not None
        return attempt.arm
    chosen = experiment.assign(declaration, number, counts).name
    counts[chosen] = counts.get(chosen, 0) + 1
    return chosen


def orphaned_comment(counted: int) -> str:
    """The comment an orphan retake posts: `RETAKEN_ORPHANED` and which retake of the budget it
    is, `counted` being the ones already made. The one function that writes it."""
    return f"{RETAKEN_ORPHANED}retake {counted + 1} of {retake_rules.RETAKE_MAX}"


def gather_orphans(
    cfg: Configuration,
    ports: Ports,
    lock: RunLock,
    documents: Mapping[int, TaskState | None],
    busy: Collection[str],
    taken: Collection[int],
    now: datetime,
    upstream: Callable[[], Upstream],
    reports: list[rules.BlockerReport],
    by_issue: dict[int, rules.BlockerReport],
    spent: dict[int, RetakesSpent] | None = None,
    reported: set[int] | None = None,
) -> tuple[dict[int, int], list[str]]:
    """The orphan retakes of this round (GH-403, `rules.orphaned_run`) and the lines for the
    orphans it cannot retake now. `documents` is `queue_documents`' answer, so only an issue in
    the bandeja is ever judged; `taken` are the issues this round already acts on. Each task goes
    through the cheapest check first, and stops at the first that rules it out: its document's
    own fields (the rule itself, with nothing else known), a live run in `busy` or on its
    lock (`RunLock.refusal`), a lock record (`RunLock.read`, an unreadable one is a line, never
    the round's end), its blockers (`_blocked`, once per issue; the report goes to `reports`
    and `by_issue` whether it blocks or not, and one that blocks holds the orphan silently --
    no slot, no comment, no attempt, no line -- as it holds a fresh launch), whether the
    document was written inside `rules.ORPHANED_QUIET` (one `ids_touched_since` for the whole
    round) and, only for what is left, the issue's comments.
    Returns `{issue: counted}` in issue order, `counted` being the retakes of the shared budget
    already made (`retakes.budget`). A spent budget is a `RetakesSpent` fact of shape `orphan`
    in `spent` unless the notice is already on the issue; it adds no line here. `reported`
    (GH-616) gets the issue of every line this function returns."""
    orphans: dict[int, int] = {}
    notices = spent if spent is not None else {}
    lines: list[str] = []
    said = reported if reported is not None else set()
    touched: set[str] | None = None
    for number in sorted(documents):
        raw = documents[number]
        if raw is None or number in taken:
            continue
        task_id = ports.inbox.task_id(number)
        fields_only = rules.orphaned_run(
            raw, live=False, has_lock=False, quiet=True, used=_NO_RETAKES, now=now
        )
        if fields_only == "none":
            continue
        if task_id in busy or lock.refusal(task_id) is not None:
            continue
        try:
            record = lock.read(task_id)
        except (ValueError, OSError, KeyError) as exc:
            lines.append(
                rules.orphaned_line(number, "lock_unreadable", str(exc) or type(exc).__name__)
            )
            said.add(number)
            continue
        if record is not None:
            continue
        report = _blocked(cfg, ports, number, upstream)
        reports.append(report)
        by_issue[number] = report
        if report:
            continue  # held by an open blocker, as a fresh launch is: no slot, comment or line
        if touched is None:
            touched = set(ports.documents.ids_touched_since(now - rules.ORPHANED_QUIET))
        if task_id in touched:
            minutes = int(rules.ORPHANED_QUIET.total_seconds() // 60)
            lines.append(rules.orphaned_line(number, "quiet", f"{minutes} min"))
            said.add(number)
            continue
        comments = ports.tracker.timestamped_comments(task_id)
        used = _retake_budget(comments)
        verdict = rules.orphaned_run(
            raw,
            live=False,
            has_lock=False,
            quiet=True,
            used=used,
            now=now,
        )
        match verdict:
            case "retake":
                orphans[number] = used.used
            case "calendar":
                due = retake_rules.due(used, now, rules.ORPHANED_QUIET).astimezone(UTC)
                lines.append(rules.orphaned_line(number, "calendar", due.strftime("%H:%M UTC")))
                said.add(number)
            case "cap_spent":
                if _notice_pending(comments):
                    notices[number] = RetakesSpent(
                        number, "orphan", used.used, used.first_at, used.last_at
                    )
            case "none" | "quiet":
                continue
            case _:
                assert_never(verdict)
    return orphans, lines


def unaccounted_lines(
    ports: Ports,
    lock: RunLock,
    documents: Mapping[int, TaskState | None],
    busy: Collection[str],
    taken: Collection[int],
    reported: Collection[int],
    seen: Mapping[int, Seen],
    by_issue: Mapping[int, rules.BlockerReport],
) -> list[str]:
    """The one line each task the round owes a run (`rules.owed_a_line`, GH-616, the owner's
    rule) gets when nothing else named it: `stopped` when its lock record carries the `agentic
    stop` mark, `unaccounted` otherwise, with the verdict `gather_retakes` reached (`no_lock` for
    a task with no lock record). `taken` are the issues the round starts something for,
    `reported` those another line already names; blockers are the ones the round read."""
    lines: list[str] = []
    for number in sorted(documents):
        raw = documents[number]
        if raw is None or not rules.owed_a_run(raw):  # cheapest first: no lock read for the rest
            continue
        task_id = ports.inbox.task_id(number)
        report = by_issue.get(number)
        if not rules.owed_a_line(
            raw,
            in_tray=True,  # `documents` is the bandeja
            blocked=bool(report),
            alive=task_id in busy or lock.refusal(task_id) is not None,
            taken=number in taken,
            already_reported=number in reported,
        ):
            continue
        fact = seen.get(number)
        if fact is not None and fact.stopped:
            lines.append(rules.orphaned_line(number, "stopped"))
        else:
            lines.append(
                rules.orphaned_line(number, "unaccounted", fact.verdict if fact else "no_lock")
            )
    return lines


def reflect_lines(cfg: Configuration, ports: Ports, launcher: Launcher, argv0: str) -> list[str]:
    """Launches `agentic reflect --all` (ADR-0043) -- a detached child, like every other
    action here -- only when the product has closed tasks nobody reflected on yet. It reads
    and spawns and nothing else: no task state, no document, no lock of a task. Any doubt
    (a `gh` or git failure, an unreadable record) reads as nothing to reflect on, and a
    failed spawn is a line, never the round's end. The child takes the `playbook` run lock
    itself, so a second spawn while one is working refuses and exits."""
    try:
        due = reflect_run.pending(cfg, ports)
    except (ValueError, OSError, GitError, KeyError, TypeError):
        # a malformed `gh` item must never fail the poll round (invariant 12)
        return []
    if not due:
        return []
    argv = [argv0, "reflect", "--all", "--product-dir", str(cfg.product_dir)]
    try:
        pid = launcher.spawn(argv)
    except OSError as exc:
        return [f"agentic: poll: reflect -> not launched: {exc}"]
    return [f"agentic: poll: reflect {len(due)} task(s) -> pid {pid}"]


def _argv(argv0: str, command: str, number: int, product_dir: str, *extra: str) -> list[str]:
    return [argv0, command, "--issue", str(number), *extra, "--product-dir", product_dir]


def _spawn_resume(
    action: rules.PollAction,
    ports: Ports,
    registry: RunRegistry,
    launcher: Launcher,
    argv0: str,
    product_dir: str,
    comment: str,
    register_timeout_s: float,
    sleep: Callable[[float], None],
    crash: CrashRetake | None = None,
    orphan_prior: int | None = None,
) -> list[str]:
    """What a `resume` and a `retake` share (GH-176): a detached `agentic resume --issue N
    --yes --detach` child (plus `--from-document` for an orphan retake, GH-403: `orphan_prior`
    is the retakes of it that ran nothing, counted by `gather_orphans`, and the one place
    that argv is built), the system comment (`comment`, `AUTO_RESUMED` or `RETAKEN`
    plus its reason) posted only once `spawn` returned a pid, and the wait for the child
    to register. A retake that never registered says so, with which of its tries it was:
    it is the only case where the wait ends with the registry flock held for nothing.
    Neither kind records a launch attempt: that calendar belongs to `launch` alone. A crash
    retake (`crash`, counted once by `gather_retakes`) names its own count and cap there."""
    number = action.issue
    task_id = ports.inbox.task_id(number)
    if crash is not None:
        prior = crash.prior
    elif orphan_prior is not None:
        prior = orphan_prior
    else:
        prior = (
            _retake_budget(ports.tracker.timestamped_comments(task_id)).used
            if action.kind == "retake"
            else 0
        )
    cap = retake_rules.RETAKE_MAX
    document = ("--from-document",) if orphan_prior is not None else ()
    # Every resume the poller spawns says so: the resume it starts must not reset the budget.
    argv = _argv(
        argv0, "resume", number, product_dir, "--yes", "--detach", "--by-poller", *document
    )
    try:
        pid = launcher.spawn(argv)
    except OSError as exc:
        return [f"agentic: poll: {action.kind} #{number} -> not launched (not counted): {exc}"]
    lines = [f"agentic: poll: {action.kind} #{number} -> pid {pid}"]
    try:
        ports.tracker.comment(task_id, comment)
    except TrackerCallFailed as exc:  # the child runs on: only the comment is lost
        what = "timed out" if isinstance(exc, TrackerTimedOut) else f"failed: {exc}"
        lines.append(f"agentic: poll: {action.kind} #{number} -> comment {what}")
    registered = await_registration(registry, task_id, register_timeout_s, sleep)
    if action.kind == "retake" and not registered:
        lines.append(
            f"agentic: poll: retake #{number} -> not registered (retake {prior + 1} of {cap})"
        )
    return lines


_SHAPE_TEXT: Mapping[retake_rules.RetakeShape, str] = {
    "same_boot": "crashed: the run died in this boot",
    "reboot": "crashed: the run died after a reboot",
    "crash": "crashed: the run raised under the graph",
    "orphan": "crashed: the run left no lock",
    "stale": "stale: untouched 24 h",
}


def _span(notice: RetakesSpent) -> str:
    if notice.first_at is None or notice.last_at is None:
        return "none made"
    stamp = "%Y-%m-%d %H:%M UTC"
    first = notice.first_at.astimezone(UTC).strftime(stamp)
    last = notice.last_at.astimezone(UTC).strftime(stamp)
    return f"over {first}..{last}"


def spent_comment(notice: RetakesSpent) -> str:
    """The one comment a spent budget leaves on the issue: what crashed, the dead run's lock, how
    many retakes were made and over what span, and the command that takes the task up."""
    lock = (
        f" (lock pid {notice.pid} since {notice.started_at}, boot {notice.boot_id or 'unknown'})"
        if notice.pid is not None
        else ""
    )
    document = " --from-document" if notice.shape == "orphan" else ""
    return (
        f"{retake_rules.RETAKES_SPENT}{_SHAPE_TEXT[notice.shape]}{lock}; "
        f"{notice.used} of {retake_rules.RETAKE_MAX} retakes, {_span(notice)}; "
        f"resume it with `agentic resume --issue {notice.issue}{document}`."
    )


def _announce_spent(ports: Ports, cfg: Configuration, notice: RetakesSpent) -> list[str]:
    """Tell the owner a task's retake budget is spent: the `needs-human` label first, then one
    comment. Both are best effort, a failure is a line and never the end of the round; a comment
    that did not land is retried next round, because the marker is still absent. The poll line
    is printed only in the round the comment landed."""
    number = notice.issue
    task_id = ports.inbox.task_id(number)
    lines: list[str] = []
    if notice.reap and notice.pid is not None:
        # A run dead in THIS boot may have left processes alive (GH-282, ADR-0052): its own
        # scope only, never the task's.
        reaped = release.summary(release.release_processes(ports, task_id, run=notice.pid))
        if reaped:
            lines.append(f"agentic: poll: #{number} {reaped}")
    try:
        ports.tracker.label(task_id, escalate_module.NEEDS_HUMAN)
    except ValueError as exc:  # a TrackerCallFailed is one
        lines.append(
            f"agentic: poll: #{number} -> label {escalate_module.NEEDS_HUMAN} failed: {exc}"
        )
    if ports.tracker.note(task_id, spent_comment(notice)) is not None:
        lines.append(
            f"agentic: poll: #{number} -> retakes-spent comment failed, retried next round"
        )
        return lines
    document = " --from-document" if notice.shape == "orphan" else ""
    lines.append(
        f"agentic: poll: #{number} retake budget spent ({_SHAPE_TEXT[notice.shape]}; "
        f"{notice.used} of {retake_rules.RETAKE_MAX}), left to the human: "
        f"agentic resume --issue {number}{document}"
    )
    return lines


def execute(
    actions: Sequence[rules.PollAction],
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    launcher: Launcher,
    now: datetime,
    argv0: str,
    register_timeout_s: float = REGISTER_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    land_kinds: Mapping[int, rules.RunKind] | None = None,
    crash_retakes: Mapping[int, CrashRetake] | None = None,
    reruns: frozenset[int] = frozenset(),
    orphan_retakes: Mapping[int, int] | None = None,
    spent_notices: Mapping[int, RetakesSpent] | None = None,
) -> list[str]:
    """Carries out this round's actions, in the order `rules.poll_plan` already fixed:
    a detached `agentic close|resume|run` child each, through `launcher.spawn` alone --
    never a role in this process. `launcher.spawn` is the one call this function wraps
    in `except OSError` (GH-140): the first real poller died on a `FileNotFoundError`
    out of a bare `Popen`, and nothing here may let a launch failure end the round, let
    alone the process. A resume posts `AUTO_RESUMED` only once its own spawn has
    returned a pid -- never before, since a comment on an issue nothing actually
    launched would claim a retake that never happened and burn the one retake
    `auto_resume_limit` allows an interview failure. A launch records its own attempt
    *before* the spawn, so a child that never launches at all still counts against its
    own calendar next round, exactly as one that launched but never registered already
    does. A spawned `run`/`resume` (never `close`, which never registers at all, and
    never a failed spawn either) is awaited into `registry.live()` before this round
    returns (`await_registration`, Human decision): still under `run_once`'s own
    `registry.exclusive()`, so the next poller to decide -- this product's next round,
    or another product's on the same machine -- never reads a stale count. A `land` child is
    told the kind it registers under, `--run-kind`, from `land_kinds` (the classified
    candidates of this round; an issue not in it is `land_model`, the kind that counts).
    A `retake` whose `reason` is `crash` (GH-308) posts `RETAKEN_AFTER_CRASH` with the text
    `rules.crash_retake_comment` formats from `crash_retakes[issue]`, counted by
    `gather_retakes` and never here; a reboot retake is as it was. A `launch` of an issue in
    `reruns` (a task whose slices landed with work left, GH-334) is a fresh run, `run
    --fresh`: the same archive-and-start-over the landing's restart spawns, on the launch's
    own calendar. A `retake` whose `reason` is `orphaned` (GH-403) resumes `--from-document` and
    posts `RETAKEN_ORPHANED` with `orphaned_comment`, from the count `orphan_retakes[issue]`
    that `gather_orphans` made. A `retakes_spent` action announces the spent budget of
    `spent_notices[issue]` (`_announce_spent`), spawning nothing."""
    product = ports.stack.product()
    product_dir = str(cfg.product_dir)
    total_attempts = len(rules.LAUNCH_SCHEDULE)
    lines: list[str] = []
    for action in actions:
        task_id = ports.inbox.task_id(action.issue)
        if action.kind == "close":
            argv = _argv(argv0, "close", action.issue, product_dir)
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(f"agentic: poll: close #{action.issue} -> not launched: {exc}")
                continue
            lines.append(f"agentic: poll: close #{action.issue} -> pid {pid}")
        elif action.kind == "retakes_spent":
            lines += _announce_spent(ports, cfg, (spent_notices or {})[action.issue])
        elif action.kind == "resume":
            raw = ports.documents.load(task_id) or {}
            reason = rules.auto_resumable(raw) or "other"
            lines += _spawn_resume(
                action,
                ports,
                registry,
                launcher,
                argv0,
                product_dir,
                f"{AUTO_RESUMED}{reason.replace('_', ' ')}",
                register_timeout_s,
                sleep,
            )
        elif action.kind == "retake" and action.reason == "crash":
            crash = (crash_retakes or {})[action.issue]
            text = rules.crash_retake_comment(
                crash.crashed_version,
                crash.key,
                crash.prior + 1,
                retake_rules.RETAKE_MAX,
            )
            lines += _spawn_resume(
                action,
                ports,
                registry,
                launcher,
                argv0,
                product_dir,
                f"{RETAKEN_AFTER_CRASH}{text}",
                register_timeout_s,
                sleep,
                crash,
            )
        elif action.kind == "retake" and action.reason == "orphaned":
            counted = (orphan_retakes or {})[action.issue]
            lines += _spawn_resume(
                action,
                ports,
                registry,
                launcher,
                argv0,
                product_dir,
                orphaned_comment(counted),
                register_timeout_s,
                sleep,
                orphan_prior=counted,
            )
        elif action.kind == "retake":
            lines += _spawn_resume(
                action,
                ports,
                registry,
                launcher,
                argv0,
                product_dir,
                f"{RETAKEN}previous boot",
                register_timeout_s,
                sleep,
            )
        elif action.kind == "merge":
            registry.record_exempt_attempt(product, action.issue, "merge", now)
            argv = _argv(argv0, "merge", action.issue, product_dir)
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(f"agentic: poll: merge #{action.issue} -> not launched: {exc}")
                continue
            await_registration(registry, task_id, register_timeout_s, sleep)
            lines.append(f"agentic: poll: merge #{action.issue} -> pid {pid}")
        elif action.kind == "land":
            kind = (land_kinds or {}).get(action.issue, "land_model")
            if not rules.counts_against_cap(kind):
                registry.record_exempt_attempt(product, action.issue, "land", now)
            argv = _argv(argv0, "land", action.issue, product_dir, "--run-kind", kind)
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(f"agentic: poll: land #{action.issue} -> not launched: {exc}")
                continue
            await_registration(registry, task_id, register_timeout_s, sleep)
            lines.append(f"agentic: poll: land #{action.issue} -> pid {pid}")
        elif action.kind == "launch":
            registry.record_launch(product, action.issue, now, action.arm)
            fresh = ("--fresh",) if action.issue in reruns else ()
            arm = (experiment.ARM_FLAG, action.arm) if action.arm is not None else ()
            argv = _argv(argv0, "run", action.issue, product_dir, *fresh, *arm, "--detach")
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                attempt = registry.launch_attempts(product, action.issue)
                k = attempt.prior if attempt is not None else 0
                spent = "; calendar spent, needs a human" if k >= total_attempts else ""
                lines.append(
                    f"agentic: poll: launch #{action.issue} -> not launched "
                    f"(attempt {k} of {total_attempts}){spent}: {exc}"
                )
                continue
            await_registration(registry, task_id, register_timeout_s, sleep)
            lines.append(f"agentic: poll: launch #{action.issue} -> pid {pid}")
        else:
            assert_never(action.kind)
    return lines


def _recent_spend(ports: Ports, now: datetime) -> tuple[list[rules.SpendRun], int]:
    """This product's own runs that may still be inside the trailing window, as
    `SpendRun`s, and how many it skipped for lack of usable timestamps (GH-177). Only the
    documents `ids_touched_since` names are loaded -- a `stat` per document, a parse only
    of what changed in the window -- and only this product's: never another's directory."""
    fresh: list[rules.SpendRun] = []
    skipped = 0
    for task_id in ports.documents.ids_touched_since(now - rules.TRAILING_SPEND_WINDOW):
        raw = ports.documents.load(task_id)
        if raw is None:
            continue
        found, count = rules.spend_runs(raw.get("runs", []))
        fresh.extend(found)
        skipped += count
    return fresh, skipped


def _upstream_notes(asked: bool, upstream: Callable[[], Upstream]) -> list[str]:
    """The one line a round says about its upstream, when it asked for one at all and the
    answer was not the plain fetched tip (GH-180): the poller's output is where a human
    watching it learns the fetch failed or local commits were left out."""
    note = upstream().note if asked else None
    return [f"agentic: poll: upstream: {note}"] if note else []


def deferred_line(deferred: rules.Deferred) -> str:
    """The poll line for a merge or land `rules.poll_plan` held back: built from the plan's
    own `Deferred`, never from a second slot count (GH-263). The reason is printed as it is:
    `no_slot`, `exempt_busy`, `pause_waiting` for a land held by an account-limit pause
    (GH-270), `land_queued` for one yielding to an earlier wait of another product
    (GH-362) or `backoff` for a merge whose refusal still holds on its head and master
    (GH-500)."""
    return f"agentic: poll: {deferred.kind} #{deferred.issue} waits: {deferred.reason}"


_LIVE_ACTIONS: frozenset[rules.PollActionKind] = frozenset(
    {"launch", "resume", "retake", "merge", "land"}
)


def _live_issues(
    ports: Ports, registry: RunRegistry, actions: Sequence[rules.PollAction]
) -> frozenset[int]:
    """The issues with a live process for the board mirror (GH-375): this product's
    registered runs, read fresh after `execute`, and the issues a run, resume, retake, merge
    or land was just ordered for, whose child may not have registered yet. A `close` is not
    one. Nothing is read when no board is configured."""
    if ports.board is None:
        return frozenset()
    product = ports.stack.product()
    running = {
        issue
        for r in registry.live()
        if r.product == product and (issue := ports.inbox.issue_of(r.task_id)) is not None
    }
    return frozenset(running | {a.issue for a in actions if a.kind in _LIVE_ACTIONS})


def _exempt_candidates(
    product: str,
    key: str,
    merges: Sequence[int],
    lands: Sequence[rules.LandCandidate],
    attempts: Mapping[tuple[int, exempt_queue.ExemptKind], exempt_queue.ExemptAttempt],
) -> list[exempt_queue.ExemptCandidate]:
    """This product's merges and exempt (`land_checks`) lands as `exempt_queue` candidates,
    with the instants its attempt records hold (GH-500)."""
    wanted: list[tuple[int, exempt_queue.ExemptKind]] = [(n, "merge") for n in merges] + [
        (c.issue, "land") for c in lands if not rules.counts_against_cap(c.kind)
    ]
    found = []
    for issue, kind in wanted:
        known = attempts.get((issue, kind)) or exempt_queue.ExemptAttempt()
        found.append(
            exempt_queue.ExemptCandidate(
                kind, product, key, issue, known.first_seen, known.last_attempt
            )
        )
    return found


def _sweep_exempt_attempts(
    ports: Ports,
    registry: RunRegistry,
    product: str,
    attempts: dict[tuple[int, exempt_queue.ExemptKind], exempt_queue.ExemptAttempt],
) -> None:
    """Delete the attempt records a fact has made stale (`exempt_queue.stale_keys`): the facts
    are the task document's `closed_at` and `pull_request`, read once per record's issue, with
    no call to the tracker. A task absent from this round's candidates keeps its record."""
    facts: dict[tuple[str, int], exempt_queue.ExemptFacts] = {}
    for issue in {issue for issue, _ in attempts}:
        raw = ports.documents.load(ports.inbox.task_id(issue))
        if raw is not None:
            reference = raw.get("pull_request")
            facts[(product, issue)] = exempt_queue.ExemptFacts(
                bool(raw.get("closed_at")), str(reference) if reference else None, None
            )
    records = [(product, issue, kind) for issue, kind in attempts]
    for _, issue, kind in exempt_queue.stale_keys(records, facts):
        registry.clear_exempt_attempt(product, issue, kind)
        attempts.pop((issue, kind), None)


def _exempt_holder(
    running: Sequence[RunRegistration],
    yielding: Sequence[exempt_queue.ExemptWait],
    started: str | None,
) -> str:
    """Who holds the exempt slot, in closed fields (GH-500): `yielding to product P` while this
    product waits for another product's older wait, else the live exempt child (`product P task
    T kind K`), else the child this very round starts."""
    if yielding:
        first = min(yielding, key=lambda w: (w.since, w.root_key))
        return f"yielding to product {first.product}"
    for r in running:
        if not rules.counts_against_cap(r.kind):
            return f"product {r.product} task {r.task_id} kind {r.kind}"
    return started or "the child started this round"


def _starvation_lines(
    plan: rules.PollPlan,
    registry: RunRegistry,
    product: str,
    records: Mapping[tuple[int, exempt_queue.ExemptKind], exempt_queue.ExemptAttempt],
    now: datetime,
    holder: Callable[[], str],
    backoff_ends: Mapping[int, datetime] | None = None,
) -> list[str]:
    """One line per candidate that waited `exempt_busy` (a live child, a yield, or the child
    started this round) for longer than `exempt_queue.EXEMPT_STARVED_AFTER` since its last
    attempt or first sighting and was not reported yet; the record is then marked, so it is said
    once. Backoff rounds are `backoff`, never `exempt_busy`, so they do not count: a merge whose
    refusal backoff ran out waits from that instant (`backoff_ends`), not from its last attempt.
    Closed fields only, never a metric (GH-500)."""
    found: list[str] = []
    for deferred in plan.deferred:
        if deferred.kind == "orphan" or deferred.reason != "exempt_busy":
            continue
        record = records.get((deferred.issue, deferred.kind))
        since = (record.last_attempt or record.first_seen) if record else None
        ended = (backoff_ends or {}).get(deferred.issue) if deferred.kind == "merge" else None
        if since is not None and ended is not None:
            since = max(since, ended)
        if record is None or since is None or record.starved_reported:
            continue
        if now - since < exempt_queue.EXEMPT_STARVED_AFTER:
            continue
        minutes = int((now - since).total_seconds() // 60)
        found.append(
            f"agentic: poll: {deferred.kind} #{deferred.issue} starved: "
            f"{minutes} min behind {holder()}"
        )
        registry.mark_exempt_starved(product, deferred.issue, deferred.kind)
    return found


def run_once(
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    launcher: Launcher,
    now: datetime,
    argv0: str,
    register_timeout_s: float = REGISTER_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    *,
    lock: RunLock,
    current_boot: str | None,
    eval_set: frozenset[int] | None = frozenset(),
    live_steps: frozenset[LandingStepKind] = landing_plan.LIVE_STEPS,
    system_version: Callable[[], str | None] = lambda: None,
) -> list[str]:
    """One round: gather, decide (`rules.poll_plan`), carry out. The slot count and the
    launch candidates are read under `registry.exclusive()` together (Tech Lead plan):
    deciding how many launches fit is atomic across every poller on this machine, not
    only within one product's, since the account's own 429 is the machine's, not a
    product's. `execute`'s own wait for a spawned child to register also runs inside
    this same `exclusive()` block, for the same reason. The retakes (GH-176) are gathered
    here too, before anything spawns and from the lock records as the dead runs left
    them; the lines for the runs left to the human come first in the round's output.
    The merges (GH-178) are gathered inside the same exclusive section, from the same
    memoised upstream, and only when some delivered task awaits one: `eval_set` is the
    issues whose merge is the human's verdict, `None` when it could not be read, which
    refuses every merge (`rules.auto_merge_decision`). The slots are `cfg.max_concurrent_runs`
    minus the live children `rules.counts_against_cap` says count; the others (`merge`,
    `land_checks`) are passed to `poll_plan` as `live_exempt`, from the same `live()` read,
    and the tasks that have any live child of this product are `busy`: no merge or land
    candidate is gathered for them (GH-263). A `land_model` land may use one slot above the
    cap (`rules.LAND_HEADROOM`, GH-270): `land_slots` is computed here from the same `live`
    and nothing else. One line per `PollPlan.deferred` item ends the
    round's output, and `nothing to do this round` is printed only when there is nothing
    at all to say. The GH-375 board mirror (`board_sync.mirror`) runs last, outside the
    exclusive section, and its notes join the others; with no board in `ports` it makes no
    call and says nothing. `system_version` (GH-308) is the non-memoising read of the stage0 version
    that keys the count of crash retakes, injected by `cli` and called only when some lock
    record carries a crash mark. The GH-344 issue sweep (`close_issues.sweep`) runs first,
    right after the closes are gathered and outside `registry.exclusive()`: its `issue
    closed` lines open the round's output, and a failed close or unreadable queue says
    nothing (the next round retries)."""
    closes = gather_closes(ports)
    # GH-348: before the launches, so a dependent sees its blocker CLOSED this same round;
    # outside the exclusive section, since it takes no slot. Only the closes are said.
    swept = close_issues.sweep(ports, report_failures=False)
    # GH-391: first the scratch of closed tasks and dead owners goes, then both volumes are
    # read, so a round that frees room starts its children in the same round.
    swept = [*scratch_sweep.sweep(ports, now), *swept]
    # GH-376: the CI refs of ended tasks with no live run, the only place they are deleted.
    swept = [*ci_ref_sweep.sweep(ports, {r.task_id for r in registry.live()}), *swept]
    held, hold_lines = scratch_sweep.read_holds(cfg, ports)
    resumes = gather_resumes(ports, now)
    crash_retakes: dict[int, CrashRetake] = {}
    spent_notices: dict[int, RetakesSpent] = {}
    seen: dict[int, Seen] = {}
    reported: set[int] = set()
    retakes, human_lines = gather_retakes(
        ports,
        lock,
        current_boot,
        system_version=system_version,
        crash=crash_retakes,
        now=now,
        spent=spent_notices,
        seen=seen,
        reported=reported,
    )
    human_lines += escalate_persisted_limits(cfg, ports, resumes)
    ranks = gather_closeness(ports, [*retakes, *crash_retakes, *(r.issue for r in resumes)])
    # Outside the lock, and only with pacing on: pacing off reads and writes nothing.
    fresh, skipped = _recent_spend(ports, now) if cfg.spend_pacing else ([], 0)
    with registry.exclusive():
        running = registry.live()
        # `rules.counts_against_cap` decides what takes a slot; the children that run no
        # model (`merge`, `land_checks`) are counted apart, for the planning to bound.
        live = sum(1 for r in running if rules.counts_against_cap(r.kind))
        exempt_live = len(running) - live
        product = ports.stack.product()
        busy = frozenset(r.task_id for r in running if r.product == product)
        available = max(cfg.max_concurrent_runs - live, 0)
        pace: rules.SpendPace | None = None
        spend_notes: list[str] = []
        if cfg.spend_pacing:
            root = cfg.product_dir / document.TASKS_SUBDIR
            spend_notes = registry.publish_spend(root, ports.stack.product(), fresh, now)
            window = now - rules.TRAILING_SPEND_WINDOW
            machine, read_notes = registry.spend(window)
            spend_notes += read_notes
            if skipped:
                spend_notes.append(f"agentic: poll: {skipped} runs without timestamps not counted")
            spent, covered = rules.trailing_spend(machine, now)
            pace = rules.SpendPace(spent, covered, live, cfg.spend_pace_usd_per_h)
        upstream = functools.cache(ports.workspace.upstream)
        blocker_reports: list[rules.BlockerReport] = []
        blockers_by_issue: dict[int, rules.BlockerReport] = {}
        queued = queue_documents(ports)
        experiment_notes: list[str] = []
        launches = gather_launches(
            cfg,
            ports,
            registry,
            upstream,
            blocker_reports,
            blockers_by_issue,
            queued,
            experiment_notes,
        )
        merges: list[int] = []
        backed_off_merges: list[int] = []
        backoff_ends: dict[int, datetime] = {}
        merge_lines: list[str] = []
        lands: list[rules.LandCandidate] = []
        reruns: list[int] = []
        if awaiting_merge(ports):
            merges = gather_merges(
                ports,
                eval_set,
                upstream().sha,
                lock,
                merge_lines,
                lands,
                live_steps,
                now,
                busy,
                cfg,
                reruns,
                backed_off_merges,
                backoff_ends,
                bool(held),
                resumes,
            )
        for number in reruns:
            blocker_reports.append(rules.BlockerReport())
            # A fresh run is a launch of an issue that already has a document: the launch
            # calendar is what keeps a child that never registered from restarting every round.
            attempt = registry.launch_attempts(product, number)
            launches.append(
                rules.LaunchCandidate(
                    number,
                    False,
                    attempt.since if attempt is not None else None,
                    attempt.prior if attempt is not None else 0,
                    len(rules.LAUNCH_SCHEDULE),
                )
            )
        # GH-403: after every other gatherer, so an issue they already act on is left out; a held
        # round starts nothing, and asking the tracker for its comments would be for nothing.
        orphans: dict[int, int] = {}
        orphan_lines: list[str] = []
        if not held:
            acting = {
                *retakes,
                *crash_retakes,
                *(r.issue for r in resumes),
                *merges,
                *backed_off_merges,
                *(c.issue for c in lands),
                *reruns,
            }
            orphans, orphan_lines = gather_orphans(
                cfg,
                ports,
                lock,
                queued,
                busy,
                acting,
                now,
                upstream,
                blocker_reports,
                blockers_by_issue,
                spent_notices,
                reported,
            )
            ranks.update({n: rules.closeness(raw) for n in orphans if (raw := queued[n])})
        tasks_root = (cfg.product_dir / document.TASKS_SUBDIR).resolve()
        own_key = rules.root_key(str(tasks_root))
        # GH-500: the exempt queue. The records of tasks a fact closed go first; every candidate
        # is noted the first time it is seen; this product yields to an older wait of another root.
        exempt_records = registry.exempt_attempts(product)
        _sweep_exempt_attempts(ports, registry, product, exempt_records)
        for candidate in _exempt_candidates(product, own_key, merges, lands, exempt_records):
            if candidate.first_seen is None:
                registry.record_exempt_seen(product, candidate.issue, candidate.kind, now)
                exempt_records[(candidate.issue, candidate.kind)] = exempt_queue.ExemptAttempt(
                    now, candidate.last_attempt
                )
        exempt_head = next(
            iter(
                exempt_queue.ordered(
                    _exempt_candidates(product, own_key, merges, lands, exempt_records)
                )
            ),
            None,
        )
        exempt_waits, exempt_wait_notes = registry.exempt_waits(now)
        exempt_others = [w for w in exempt_waits if w.root_key != own_key]
        waits, wait_notes = registry.land_waits(now)
        # A product never holds itself: only another root's fresh record holds this round.
        others = [w for w in waits if w.root_key != own_key]
        others_landing = bool(others)
        previous = next((w.since for w in waits if w.root_key == own_key), None)
        notes = [
            *experiment_notes,
            *spend_notes,
            *wait_notes,
            *exempt_wait_notes,
            *_upstream_notes(upstream.cache_info().currsize > 0, upstream),
        ]
        plan_yielded = exempt_queue.behind(
            exempt_queue.rank(exempt_head) if exempt_head else None, exempt_others
        )
        plan = rules.poll_plan(
            closes=closes,
            resumes=resumes,
            launches=launches,
            available_slots=available,
            now=now,
            retakes=retakes,
            crash_retakes=sorted(crash_retakes),
            merges=merges,
            backed_off_merges=backed_off_merges,
            lands=lands,
            live_exempt=exempt_live,
            pace=pace,
            land_slots=max(cfg.max_concurrent_runs + rules.LAND_HEADROOM - live, 0),
            closeness=ranks,
            others_landing=others_landing,
            earlier_land_wait=rules.land_queued_behind(previous, own_key, others),
            held=held,
            orphan_retakes=sorted(orphans),
            spent_retakes=sorted(spent_notices),
            exempt_attempts=exempt_records,
            exempt_yield=plan_yielded,
        )
        # GH-500: the head of this product's queue is published only while it could start. A
        # round that starts it, or that has no such head, removes the record: the child that
        # runs is the claim, and a record of the old `since` would hold the others for nothing.
        started = {(a.issue, a.kind) for a in plan.actions}
        own_exempt_live = any(
            r.product == product and not rules.counts_against_cap(r.kind) for r in running
        )
        could_start = exempt_head is not None and exempt_queue.startable(
            exempt_head,
            backed_off=exempt_head.kind == "merge" and exempt_head.issue in backed_off_merges,
            busy=exempt_live > 0,
            held=bool(held),
            own_live_exempt=own_exempt_live,
        )
        registry.publish_exempt_wait(
            tasks_root,
            product,
            exempt_head
            if could_start and exempt_head and (exempt_head.issue, exempt_head.kind) not in started
            else None,
            now,
        )
        starting = next((a for a in plan.actions if a.kind in ("merge", "land")), None)
        starvation = _starvation_lines(
            plan,
            registry,
            product,
            exempt_records,
            now,
            lambda: _exempt_holder(
                running,
                exempt_others if exempt_head and plan_yielded else [],
                f"product {product} task {ports.inbox.task_id(starting.issue)} kind {starting.kind}"
                if starting
                else None,
            ),
            backoff_ends,
        )
        # Right after the decision and before `execute`, so no exit path leaves the previous
        # round's record in place: a round with no waiting land removes it.
        waiting_lands = sorted(d.issue for d in plan.deferred if rules.holds_launches(d))
        registry.publish_land_wait(
            tasks_root,
            product,
            waiting_lands,
            rules.land_wait_since(previous, bool(waiting_lands), now),
            now,
        )
        lines = execute(
            plan.actions,
            cfg,
            ports,
            registry,
            launcher,
            now,
            argv0,
            register_timeout_s,
            sleep,
            {c.issue: c.kind for c in lands},
            crash_retakes,
            frozenset(reruns),
            orphans,
            spent_notices,
        )
        deferral_lines = [deferred_line(d) for d in plan.deferred if d.kind != "orphan"]
        deferral_lines += starvation
        orphan_lines += [
            rules.orphaned_line(d.issue, "no_slot") for d in plan.deferred if d.kind == "orphan"
        ]
        reported |= {d.issue for d in plan.deferred if d.kind == "orphan"}
        if not held:  # a held round starts nothing and its hold lines already say why
            orphan_lines += unaccounted_lines(
                ports,
                lock,
                queued,
                busy,
                {a.issue for a in plan.actions},
                reported,
                seen,
                blockers_by_issue,
            )
    # GH-375: the board mirror, outside the exclusive section (it takes no slot and a Project
    # call may be slow). Its notes go with the others, so `nothing to do` is unaffected.
    notes += board_sync.mirror(
        ports,
        now,
        live_issues=_live_issues(ports, registry, plan.actions),
        blockers=blockers_by_issue,
        eval_set=eval_set,
    )
    # Outside the registry's exclusive section: reflecting takes no slot of the cap and
    # touches no task.
    # A held round starts no child at all, and the reflect child is one.
    if not held:
        lines += reflect_lines(cfg, ports, launcher, argv0)
    lines = [*swept, *hold_lines, *human_lines, *merge_lines, *lines, *deferral_lines]
    blocked = rules.queue_blocked_line(blocker_reports)
    if lines:
        return [*notes, *lines, *orphan_lines]
    if orphan_lines:  # GH-403: never `nothing to do` beside an orphan; the blocked queue stays
        return [*notes, *orphan_lines, *([blocked] if blocked else [])]
    return [*notes, blocked or "agentic: poll: nothing to do this round"]
