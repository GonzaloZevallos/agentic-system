"""The poller's first slice (issue #137, README.md 14): a long-lived process per
product that keeps the queue moving without a human watching it. Each round closes
every task whose pull request already merged, retakes every pause an external API cut
caused once its own wait elapsed, and launches at most one more unblocked issue -- never
past the machine-wide concurrency cap (`Configuration.max_concurrent_runs`). Every
action is a detached `agentic close|resume|run` child (`Launcher.spawn`); no role ever
runs in this process (the plan's own invariant).

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import assert_never, get_args

from agentic.shared.configuration import Configuration

from ..domain import ci, document, presubmit, rules
from ..domain import landing as landing_rules
from ..domain.adr_numbers import Ambiguous, Renaming
from ..domain.boot import boot_verdict
from ..domain.landing import LandingStepKind
from ..domain.task import PullRequest, TaskState, apply
from . import adr_collision, landing_plan, reflect_run
from . import escalate as escalate_module
from .checkouts import task_base
from .ports import (
    GitError,
    IssueComment,
    Launcher,
    LockRecord,
    Ports,
    RunLock,
    RunRegistry,
    Upstream,
)

# How long `execute` waits for a spawned `run`/`resume` child to register itself
# (Human decision, issue #137): a second round must never count a slot this round's
# own spawn has not registered into yet, which is what let a round exceed
# `AGENTIC_MAX_CONCURRENT_RUNS` before this. Polled, never slept once: a child that
# registers after the first step must not wait out the whole 60 s for nothing.
REGISTER_WAIT_S = 60.0
_REGISTER_POLL_INTERVAL_S = 1.0

# How the system's own auto-resume comment starts (issue #137): registered into
# `resume.SYSTEM_COMMENTS` by `resume.py` itself (a deferred import there, to avoid the
# cycle this module would otherwise have with it) -- never read as the human's decision
# on the next `agentic resume`.
AUTO_RESUMED = "Auto-resumed: "

# How the comment of a retake after a reboot starts (GH-176): registered into
# `resume.SYSTEM_COMMENTS` beside `AUTO_RESUMED`, posted only once the spawn returned a pid.
RETAKEN = "Retaken after reboot: "

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


def _prior_retakes(comments: Sequence[IssueComment]) -> int:
    """How many `Retaken after reboot:` comments followed the last comment that is not
    the system's own: the same shape as `_prior_auto_resumes`, so a human comment resets
    the count `rules.RETAKE_AFTER_REBOOT_MAX` bounds."""
    from .resume import SYSTEM_COMMENTS

    last_non_system = _last_non_system_index(comments, SYSTEM_COMMENTS)
    return sum(1 for comment in comments[last_non_system + 1 :] if comment.body.startswith(RETAKEN))


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
    return rules.MergeFacts(
        task_id=task_id,
        status=raw.get("status"),
        reviewed_unit_approved=unit is not None and unit.get("status") == "approved",
        reviewed_head=unit.get("reviewed_head") if unit is not None else None,
        open_blocking=sum(1 for row in rules.open_findings(raw) if row["severity"] == "blocking"),
        open_important=sum(1 for row in rules.open_findings(raw) if row["severity"] == "important"),
        pr_state=pull_request.state if pull_request is not None else None,
        pr_head=pr_head,
        in_eval_set=None if eval_set is None else number in eval_set,
        touched_paths=None,
        ci_untrusted=None,
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


def with_ci(
    facts: rules.MergeFacts,
    raw: TaskState,
    declared: tuple[str, ...],
    report: ci.CiReport | ci.CiUnreadable | None,
    fresh: bool | None = None,
    now: datetime | None = None,
) -> rules.MergeFacts:
    """`facts` plus the CI ones (GH-216), for the product that declares `declared` as its
    merge gate. `report` is None when none was read: the decision then waits, which is only
    ever asked after every earlier refusal has been ruled out. The classification is
    `ci.ci_state`'s and the freshness `ci.ci_fresh`'s: nothing here reads a check."""
    if not declared:
        return facts
    now = now or datetime.now(UTC)
    if report is None:
        return replace(facts, ci_required=declared)
    if isinstance(report, ci.CiUnreadable):
        anchor = ci.ci_wait_anchor(
            raw.get("pushed_head"), raw.get("pushed_at"), facts.pr_head, None
        )
        return replace(
            facts,
            ci_required=declared,
            ci_unreadable=report,
            ci_waited_s=_seconds_since(anchor, now),
        )
    anchor = ci.ci_wait_anchor(
        raw.get("pushed_head"), raw.get("pushed_at"), facts.pr_head, report.opened_at
    )
    return replace(
        facts,
        ci_required=declared,
        ci_head=report.head_sha,
        ci_verdict=ci.ci_state(report, declared),
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
    elif facts.ci_verdict is not None and facts.ci_verdict.missing:
        what = f"missing {', '.join(facts.ci_verdict.missing)}"
    elif facts.ci_verdict is not None and facts.ci_verdict.state == "pending":
        names = (
            ci.pending_names(report, facts.ci_required) if isinstance(report, ci.CiReport) else ()
        )
        what = f"pending {', '.join(names)}" if names else "pending"
    else:
        what = "pending head"
    bound = f"{waited}s of {ci.CI_WAIT_MAX_S}"
    return f"agentic: poll: auto-merge #{number} -> waiting: ci {what} ({bound})"


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


def assess_merge(
    ports: Ports,
    task_id: str,
    raw: TaskState,
    number: int,
    eval_set: frozenset[int] | None,
    current_master: str,
) -> MergeAssessment | None:
    """`rules.auto_merge_decision` over the facts the poller has, shared by `gather_merges`
    and `landing.land` so both decide the same way. `None` for a pull request closed
    unmerged; an unreadable one reads as not open; `StackUnreadable` for a base the stack
    cannot be read at. The pull request is read here, once."""
    reference = raw.get("pull_request")
    try:
        pull_request = ports.tracker.pull_request(reference) if reference else None
    except (ValueError, OSError):
        pull_request = None  # reads as not open: refused by the decision, with its line
    if pull_request is not None and rules.pr_abandoned(pull_request.state):
        return None
    facts = _merge_facts(task_id, raw, pull_request, eval_set, number, current_master)
    try:
        declared = ports.stack.ci(task_base(raw, ports))
    except ValueError as exc:
        raise StackUnreadable(str(exc)) from exc
    facts = replace(facts, adr_collision=_adr_collision(ports, raw, facts, current_master))
    decision = rules.auto_merge_decision(replace(facts, ci_required=declared))
    unread = False
    if (
        facts.pr_head is not None
        and not landing_plan.spent(raw, facts.pr_head, None).diagnosed
        and rules.conflict_probe_wanted(replace(facts, ci_required=declared))
    ):
        # GH-255: a head that does not merge with master is a conflict before it is a CI
        # fact. Git is asked only now, with every git-free refusal ahead of it ruled out.
        conflicts = ports.workspace.merge_conflicts(facts.pr_head, current_master)
        unread = conflicts is None
        facts = replace(facts, conflicts=conflicts)
        decision = rules.auto_merge_decision(replace(facts, ci_required=declared))
    report = None
    if declared and decision.kind != "refuse":
        # Every refusal ahead of the CI's is ruled out: only now is the report read.
        report = ports.tracker.ci_report(str(reference))
        facts = with_ci(facts, raw, declared, report)
        decision = rules.auto_merge_decision(facts)
    return MergeAssessment(facts, decision, report, pull_request, unread)


def _adr_collision(
    ports: Ports, raw: TaskState, facts: rules.MergeFacts, master: str
) -> Renaming | Ambiguous | None:
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


def gather_merges(
    ports: Ports,
    eval_set: frozenset[int] | None,
    current_master: str | None,
    lock: RunLock,
    lines: list[str] | None = None,
    lands: list[int] | None = None,
    live: frozenset[LandingStepKind] = landing_plan.LIVE_STEPS,
    now: datetime | None = None,
) -> list[int]:
    """Every delivered, unclosed task `rules.auto_merge_decision` finds worth a merge
    child -- `merge` or `run_checks` -- and a line appended to `lines` for each one it
    refuses, naming the reason (GH-178). A task whose run lock reads alive is skipped
    before anything else is read: a merge child already works on it, or a run does. An
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
    head. `wait` and `none` are never in `live`: they spawn nothing. A `not_ready` task whose
    reopened landing lost its resume child (`landing.resume_owed` at `now`, the round's time)
    is a `lands` candidate too (GH-242)."""
    out = lines if lines is not None else []
    now = now or datetime.now(UTC)
    if current_master is None:
        return []  # the round's own upstream note already says why
    found: list[int] = []
    for task_id in ports.documents.ids():
        number = ports.inbox.issue_of(task_id)
        if number is None:
            continue
        raw = ports.documents.load(task_id) or {}
        reference = raw.get("pull_request")
        if raw.get("closed_at") or not reference:
            continue
        if lock.refusal(task_id) is not None:
            continue
        try:
            assessed = assess_merge(ports, task_id, raw, number, eval_set, current_master)
        except StackUnreadable:
            # A base the stack cannot be read at (gone, or a malformed key) is this task's
            # problem, never the round's: every other candidate is still decided (GH-216).
            out.append(
                f"agentic: poll: auto-merge #{number} -> left to the human: stack_unreadable"
            )
            continue
        if assessed is None:
            continue  # closed unmerged: nothing to merge, nothing to say, nothing written
        facts, decision, report, pull_request = (
            assessed.facts,
            assessed.decision,
            assessed.report,
            assessed.pull_request,
        )
        if assessed.conflict_unread:
            head = pull_request.head_oid if pull_request is not None else None
            out.append(f"agentic: poll: auto-merge #{number} -> conflict unread ({head})")
        if decision.kind == "wait":
            out.append(ci_wait_line(number, facts, report))
        elif decision.kind == "refuse":
            line = f"agentic: poll: auto-merge #{number} -> left to the human: {decision.reason}"
            if decision.reason == "checks_failed":
                pr_head = pull_request.head_oid if pull_request is not None else None
                summary = presubmit.failed_checks_summary(_recorded(raw, "merge_checks"), pr_head)
                line += f" ({summary})" if summary else ""
            if decision.reason == "ci_failed" and facts.ci_verdict is not None:
                names = ", ".join(check.name for check in facts.ci_verdict.failing)
                line += f" ({names})"
            out.append(line)
            if lands is not None and decision.reason is not None:
                # Every refusal logged above asks `landing_step` what to do instead; only a
                # built step not yet dispatched on this head is worth a `land` child.
                if landing_plan.pending(raw, decision.reason, facts, live, now) is not None or (
                    decision.reason == "not_ready"
                    and (
                        landing_rules.resume_owed(raw, now)
                        or landing_plan.stranded_now(ports, task_id, raw)
                    )
                ):
                    # A `not_ready` task whose reopened pass lost its resume child owes
                    # one: `land` respawns it (GH-242). So does a stranded diagnose, which
                    # `land` frees and retries on the calendar (GH-257).
                    lands.append(number)
        else:
            found.append(number)
    return found


def gather_resumes(ports: Ports, now: datetime) -> list[rules.ResumeCandidate]:
    """Every paused task `rules.auto_resumable` finds eligible, with the `since`/
    `prior`/`limit` `rules.next_attempt_at` needs, and the `chase_prior`/`ceiling_spent`
    `rules.resume_exhausted` decides over. A task with no `Escalated:` comment
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
        if reason == "account_limit":
            prior, ceiling_spent = _account_limit_counts(raw, comments, chase)
        else:
            prior, ceiling_spent = chase, False
        limit = rules.auto_resume_limit(raw, reason)
        resumes.append(
            rules.ResumeCandidate(number, reason, since, prior, limit, chase, ceiling_spent)
        )
    return resumes


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
        state = apply(raw, rules.account_limit_persisted_patch())
        ports.documents.save(state)
        ports.tracker.label(task_id, escalate_module.NEEDS_HUMAN)
        ports.tracker.comment(task_id, escalate_module.comment(state, cfg, ports))
        lines.append(
            f"agentic: poll: #{r.issue} {rules.ACCOUNT_LIMIT_PERSISTED} -> left to the human"
        )
    return lines


def _human_line(
    number: int, record: LockRecord, other_boot: bool | None, verdict_cap_spent: bool
) -> str:
    """The one line a run left to the human says: what happened to its lock, whose it
    was, and the command that takes it up. It repeats every round (the poller keeps no
    state), which is how a human watching the output learns of it."""
    if verdict_cap_spent:
        what = "retake cap spent"
    elif record.stopped_at_shutdown:
        what = "stopped at shutdown"
    elif other_boot is None:
        what = "died (boot unknown)"
    else:
        what = "died in this boot"
    return (
        f"agentic: poll: #{number} {what} (lock pid {record.pid} since {record.started_at}), "
        f"left to the human: agentic resume --issue {number}"
    )


def gather_retakes(
    ports: Ports, lock: RunLock, current_boot: str | None
) -> tuple[list[int], list[str]]:
    """Every open task whose run a reboot interrupted (`rules.interrupted_run`), and the
    lines for the ones left to the human. The lock is only ever *read* here
    (`RunLock.read`, and `RunLock.refusal` for liveness, which never writes): taking it
    over is the retaken `resume`'s own first act, never the poller's, and a decision made
    over a record the poller had just rewritten would read its own pid. Liveness is
    asked of the lock itself, the one place that knows the boot id (`liveness.is_alive`),
    so this decision and `acquire` cannot disagree; a lock of another host reads as
    alive, as `acquire` reads it. The comments are fetched only for a task the first,
    cheap verdict did not already rule out."""
    retakes: list[int] = []
    lines: list[str] = []
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
        facts = rules.LockFacts(other_boot, alive=lock.refusal(task_id) is not None)
        first = rules.interrupted_run(raw, facts, 0)
        if first in ("none", "running"):
            continue
        prior = _prior_retakes(ports.tracker.timestamped_comments(task_id))
        decision = rules.interrupted_run(raw, facts, prior)
        if decision == "retake":
            retakes.append(number)
        elif decision == "human":
            cap_spent = first == "retake"  # only the count turned it into the human's
            lines.append(_human_line(number, record, other_boot, cap_spent))
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


def _blocker_resolved(
    cfg: Configuration, ports: Ports, blocker: int, upstream: Callable[[], Upstream]
) -> bool:
    state = ports.tracker.issue_state(blocker)
    if state.state != "CLOSED":
        return False
    merge_commit = _merge_commit_for(ports, blocker, state.closing_merge_commit)
    # The fetched tip, never local `HEAD` (GH-180): a merge on the remote that nobody
    # pulled is merged. `upstream` is asked only here, when a merge commit is to be
    # judged, and is memoised per round by `run_once`: at most one fetch.
    merged = merge_commit is not None and ports.workspace.is_ancestor(
        cfg.product_dir, merge_commit, ref=upstream().sha or "HEAD"
    )
    return rules.blocker_resolved(state.state_reason, merged)


def _blocked(
    cfg: Configuration, ports: Ports, number: int, upstream: Callable[[], Upstream]
) -> bool:
    """Whether `number` still has an unresolved blocker. Any doubt -- an unknown git
    object, a `gh` failure, a blocker whose own `stateReason` this function does not
    recognize, or a real git failure (`GitError` out of `Workspace.is_ancestor`,
    `ValueError`/`OSError` out of a `gh` call that is not merely "unknown field") --
    reads as blocked (Tech Lead plan: "ante la duda, no lanza"). A `GitError` escaping
    here would otherwise end the whole `_poll` loop, which has no handler of its own
    (the blocking finding this closes): one candidate's git trouble must cost that
    candidate's own launch, never the round, and never the process."""
    try:
        blockers = _blocker_numbers(ports, number)
        return not all(_blocker_resolved(cfg, ports, b, upstream) for b in blockers)
    except (ValueError, OSError, KeyError, GitError):
        return True


def gather_launches(
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    upstream: Callable[[], Upstream] | None = None,
) -> list[rules.LaunchCandidate]:
    """Every open `agentic`-labelled issue with no document yet (no run, so nothing
    delivered and no pull request either): `rules.poll_plan`'s own launch candidates,
    with each one's own launch-attempt schedule (`registry.launch_attempts`) so a
    launch that never registered a run does not relaunch every round. An issue that
    now has a document clears its own stale attempt record (`registry.clear_launch`):
    it is no longer a candidate at all."""
    upstream = upstream or functools.cache(ports.workspace.upstream)
    product = ports.stack.product()
    launches: list[rules.LaunchCandidate] = []
    for number in ports.tracker.queue():
        task_id = ports.inbox.task_id(number)
        if ports.documents.load(task_id) is not None:
            registry.clear_launch(product, number)
            continue
        blocked = _blocked(cfg, ports, number, upstream)
        attempt = registry.launch_attempts(product, number)
        since = attempt.since if attempt is not None else None
        prior = attempt.prior if attempt is not None else 0
        launches.append(
            rules.LaunchCandidate(number, blocked, since, prior, len(rules.LAUNCH_SCHEDULE))
        )
    return launches


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


def _await_registration(
    registry: RunRegistry, task_id: str, timeout_s: float, sleep: Callable[[float], None]
) -> bool:
    """Up to `timeout_s`, in `_REGISTER_POLL_INTERVAL_S` steps, until `task_id` shows up
    in `registry.live()` (Human decision, issue #137): so a second poll round never
    counts a slot this round's own spawn has not registered into yet, which is what let
    a round exceed `AGENTIC_MAX_CONCURRENT_RUNS` before this. Returns whether it showed
    up in time -- best-effort only, the same shape `cli._registered`'s own write
    already has: a child that never registers within the timeout is not retried here,
    and the round goes on rather than hang forever on one that crashed before it could."""
    waited = 0.0
    while True:
        if any(r.task_id == task_id for r in registry.live()):
            return True
        if waited >= timeout_s:
            return False
        sleep(_REGISTER_POLL_INTERVAL_S)
        waited += _REGISTER_POLL_INTERVAL_S


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
) -> list[str]:
    """What a `resume` and a `retake` share (GH-176): a detached `agentic resume --issue N
    --yes --detach` child, the system comment (`comment`, `AUTO_RESUMED` or `RETAKEN`
    plus its reason) posted only once `spawn` returned a pid, and the wait for the child
    to register. A retake that never registered says so, with which of its tries it was:
    it is the only case where the wait ends with the registry flock held for nothing.
    Neither kind records a launch attempt: that calendar belongs to `launch` alone."""
    number = action.issue
    task_id = ports.inbox.task_id(number)
    prior = (
        _prior_retakes(ports.tracker.timestamped_comments(task_id))
        if action.kind == "retake"
        else 0
    )
    argv = _argv(argv0, "resume", number, product_dir, "--yes", "--detach")
    try:
        pid = launcher.spawn(argv)
    except OSError as exc:
        return [f"agentic: poll: {action.kind} #{number} -> not launched (not counted): {exc}"]
    ports.tracker.comment(task_id, comment)
    registered = _await_registration(registry, task_id, register_timeout_s, sleep)
    lines = [f"agentic: poll: {action.kind} #{number} -> pid {pid}"]
    if action.kind == "retake" and not registered:
        lines.append(
            f"agentic: poll: retake #{number} -> not registered "
            f"(retake {prior + 1} of {rules.RETAKE_AFTER_REBOOT_MAX})"
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
    returns (`_await_registration`, Human decision): still under `run_once`'s own
    `registry.exclusive()`, so the next poller to decide -- this product's next round,
    or another product's on the same machine -- never reads a stale count."""
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
            argv = _argv(argv0, "merge", action.issue, product_dir)
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(f"agentic: poll: merge #{action.issue} -> not launched: {exc}")
                continue
            _await_registration(registry, task_id, register_timeout_s, sleep)
            lines.append(f"agentic: poll: merge #{action.issue} -> pid {pid}")
        elif action.kind == "land":
            argv = _argv(argv0, "land", action.issue, product_dir)
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(f"agentic: poll: land #{action.issue} -> not launched: {exc}")
                continue
            _await_registration(registry, task_id, register_timeout_s, sleep)
            lines.append(f"agentic: poll: land #{action.issue} -> pid {pid}")
        elif action.kind == "launch":
            registry.record_launch(product, action.issue, now)
            argv = _argv(argv0, "run", action.issue, product_dir, "--detach")
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
            _await_registration(registry, task_id, register_timeout_s, sleep)
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
    refuses every merge (`rules.auto_merge_decision`)."""
    closes = gather_closes(ports)
    resumes = gather_resumes(ports, now)
    retakes, human_lines = gather_retakes(ports, lock, current_boot)
    human_lines += escalate_persisted_limits(cfg, ports, resumes)
    # Outside the lock, and only with pacing on: pacing off reads and writes nothing.
    fresh, skipped = _recent_spend(ports, now) if cfg.spend_pacing else ([], 0)
    with registry.exclusive():
        live = len(registry.live())
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
        launches = gather_launches(cfg, ports, registry, upstream)
        merges: list[int] = []
        merge_lines: list[str] = []
        lands: list[int] = []
        if awaiting_merge(ports):
            merges = gather_merges(
                ports, eval_set, upstream().sha, lock, merge_lines, lands, live_steps, now
            )
        notes = [*spend_notes, *_upstream_notes(upstream.cache_info().currsize > 0, upstream)]
        actions = rules.poll_plan(
            closes=closes,
            resumes=resumes,
            launches=launches,
            available_slots=available,
            now=now,
            retakes=retakes,
            merges=merges,
            lands=lands,
            pace=pace,
        )
        lines = execute(
            actions, cfg, ports, registry, launcher, now, argv0, register_timeout_s, sleep
        )
    # Outside the registry's exclusive section: reflecting takes no slot of the cap and
    # touches no task.
    lines += reflect_lines(cfg, ports, launcher, argv0)
    lines = [*human_lines, *merge_lines, *lines]
    return [*notes, *(lines or ["agentic: poll: nothing to do this round"])]
