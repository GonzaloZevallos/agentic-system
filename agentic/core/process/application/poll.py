"""The poller's first slice (issue #137, README.md 14): a long-lived process per
product that keeps the queue moving without a human watching it. Each round closes
every task whose pull request already merged, retakes every pause an external API cut
caused once its own wait elapsed, and launches at most one more unblocked issue -- never
past the machine-wide concurrency cap (`Configuration.max_concurrent_runs`). Every
action is a detached `agentic close|resume|run` child (`Launcher.spawn`); no role ever
runs in this process (the plan's own invariant).

`rules.poll_plan` decides the order and which issues qualify; this module only gathers
the facts it needs from the ports and carries out what it returns. `AUTO_RESUMED` is the
one comment prefix this module posts, registered into `resume.SYSTEM_COMMENTS` in the
same diff so a resume reads it as the system's own, never a human's answer."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import TaskState
from . import reflect_run
from .ports import GitError, IssueComment, Launcher, Ports, RunRegistry, Upstream

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
        elif action.kind == "resume":
            argv = _argv(argv0, "resume", action.issue, product_dir, "--yes", "--detach")
            try:
                pid = launcher.spawn(argv)
            except OSError as exc:
                lines.append(
                    f"agentic: poll: resume #{action.issue} -> not launched (not counted): {exc}"
                )
                continue
            raw = ports.documents.load(task_id) or {}
            reason = rules.auto_resumable(raw) or "other"
            ports.tracker.comment(task_id, f"{AUTO_RESUMED}{reason.replace('_', ' ')}")
            _await_registration(registry, task_id, register_timeout_s, sleep)
        else:
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
        lines.append(f"agentic: poll: {action.kind} #{action.issue} -> pid {pid}")
    return lines


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
) -> list[str]:
    """One round: gather, decide (`rules.poll_plan`), carry out. The slot count and the
    launch candidates are read under `registry.exclusive()` together (Tech Lead plan):
    deciding how many launches fit is atomic across every poller on this machine, not
    only within one product's, since the account's own 429 is the machine's, not a
    product's. `execute`'s own wait for a spawned child to register also runs inside
    this same `exclusive()` block, for the same reason."""
    closes = gather_closes(ports)
    resumes = gather_resumes(ports, now)
    with registry.exclusive():
        available = max(cfg.max_concurrent_runs - len(registry.live()), 0)
        upstream = functools.cache(ports.workspace.upstream)
        launches = gather_launches(cfg, ports, registry, upstream)
        notes = _upstream_notes(upstream.cache_info().currsize > 0, upstream)
        actions = rules.poll_plan(
            closes=closes, resumes=resumes, launches=launches, available_slots=available, now=now
        )
        lines = execute(
            actions, cfg, ports, registry, launcher, now, argv0, register_timeout_s, sleep
        )
    # Outside the registry's exclusive section: reflecting takes no slot of the cap and
    # touches no task.
    lines += reflect_lines(cfg, ports, launcher, argv0)
    return [*notes, *(lines or ["agentic: poll: nothing to do this round"])]
