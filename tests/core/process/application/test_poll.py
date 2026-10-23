"""Issue #137: the poller's own scenarios (gather -> rules.poll_plan -> execute),
against in-memory ports and a fixed clock -- never a real `gh`, a real `Popen` or a
real clock. `run_once` is synchronous: no graph, no role, nothing to await."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import poll
from agentic.core.process.application.ports import (
    IssueComment,
    IssueState,
    LaunchAttempt,
    LockRecord,
    RunRegistration,
    TrackerTimedOut,
    TrackerUnreachable,
    Upstream,
)
from agentic.core.process.domain.roles import INTERVIEW_SOLUTION
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakeLauncher,
    FakeReaper,
    MemoryInbox,
    MemoryRunRegistry,
    MemoryTracker,
    memory_ports,
)

ESCALATED_AT = datetime(2026, 10, 2, 19, 30, tzinfo=UTC)


class _TrackerWithPullRequest(MemoryTracker):
    """`MemoryTracker`'s own shape, with `pull_request` answered from a canned map --
    the one verb this file's close scenario needs that `MemoryTracker` does not
    otherwise carry (the same gap `tests/test_cli.py`'s own `FakePullRequestTracker`
    fills for `close`)."""

    def __init__(self, prs: dict[str, PullRequest | None] | None = None):
        super().__init__()
        self.prs = prs or {}

    def pull_request(self, reference: str) -> PullRequest | None:
        pull_request_number(reference)  # the real adapter's parse, before anything else
        return self.prs.get(reference)


def test_a_bare_issue_number_fails_through_the_tracker_with_a_pull_request_map():
    tracker = _TrackerWithPullRequest({"9": None})
    for reference in ("9", "GH-9"):
        with pytest.raises(ValueError, match="not a pull request URL"):
            tracker.pull_request(reference)


def _cfg(tmp_path, **over):
    return Configuration(product_dir=tmp_path, **over)


def _paused(task_id, escalated, pause_log, **more):
    return {"id": task_id, "escalated": escalated, "pause_log": pause_log, **more}


class _Locks:
    """A `RunLock` for the poller's reads: `records` is what the lock files hold, `alive`
    the task ids whose process is still running. `acquire`, `release` and
    `keep_at_shutdown` raise: the poller must never take, drop or rewrite a lock."""

    def __init__(self, records: dict[str, LockRecord] | None = None, alive: set[str] | None = None):
        self.records = records or {}
        self.alive = alive or set()
        self.reads: list[str] = []
        self.kept_crashed: list[tuple[str, str]] = []

    def read(self, task_id):
        self.reads.append(task_id)
        return self.records.get(task_id)

    def refusal(self, task_id):
        return "held" if task_id in self.alive else None

    def acquire(self, task_id, command):
        raise AssertionError("the poller must never acquire a run's lock")

    def release(self, task_id):
        raise AssertionError("the poller must never release a run's lock")

    def keep_at_shutdown(self, task_id):
        raise AssertionError("the poller must never rewrite a run's lock")

    def keep_crashed(self, task_id, system_version):
        # Recorded, never acted on: a test asserts the poller left it empty.
        self.kept_crashed.append((task_id, system_version))


def _no_sleep(seconds: float) -> None:
    """The default `sleep` every test but the ones about the wait itself inject: a
    real `time.sleep` here would make a scenario that never registers block for the
    full `REGISTER_WAIT_S` (Human decision, issue #137)."""


def _round(cfg, ports, registry, launcher, now, **kw):
    kw.setdefault("sleep", _no_sleep)
    kw.setdefault("argv0", "agentic")
    kw.setdefault("lock", _Locks())
    kw.setdefault("current_boot", "boot-now")
    return poll.run_once(cfg, ports, registry, launcher, now, **kw)


def _seed_body(ports, number: int, description: str = "a plain description, no blockers") -> None:
    """GH-142: with native blockers empty or absent, `_blocker_numbers` reads the
    issue's own body -- so a scenario whose candidate has no native blockers must
    seed the inbox itself, never rely on the empty `MemoryInbox` the fixture starts
    with (that would hit `KeyError` by accident, not a scripted, declared outcome)."""
    ports.inbox.issues[str(number)] = {
        "id": f"GH-{number}",
        "title": "t",
        "description": description,
    }


def test_an_account_limit_resumes_once_its_own_wait_elapses_not_before(tmp_path):
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-02T19:30:00Z",
        )
    ]
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path)

    too_soon = ESCALATED_AT + timedelta(minutes=5)
    assert _round(cfg, ports, registry, launcher, too_soon) == [
        "agentic: poll: nothing to do this round"
    ]
    assert launcher.spawned == []

    due = ESCALATED_AT + timedelta(minutes=16)  # ACCOUNT_LIMIT_SCHEDULE[0] == 15
    lines = _round(cfg, ports, registry, launcher, due)
    assert lines == ["agentic: poll: resume #19 -> pid 4242"]
    assert launcher.spawned == [
        ["agentic", "resume", "--issue", "19", "--yes", "--detach", "--product-dir", str(tmp_path)]
    ]
    assert ports.tracker.comments == [("GH-19", "Auto-resumed: account limit")]


def test_a_questions_escalation_never_auto_resumes_however_long_it_waits(tmp_path):
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-20",
            "PM could not decide 1 question(s)",
            [{"kind": "escalation", "value": "questions"}],
        )
    )
    ports.tracker.timestamped["GH-20"] = [
        IssueComment(
            body="Escalated: PM could not decide 1 question(s)", created_at="2026-10-02T19:30:00Z"
        )
    ]
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    well_past = ESCALATED_AT + timedelta(days=1)
    assert _round(_cfg(tmp_path), ports, registry, launcher, well_past) == [
        "agentic: poll: nothing to do this round"
    ]
    assert launcher.spawned == []


def test_an_issue_with_an_open_blocker_does_not_launch(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("OPEN", "", None, None)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_an_issue_with_every_blocker_closed_launches(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("CLOSED", "NOT_PLANNED", None, None)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert launcher.spawned == [
        ["agentic", "run", "--issue", "5", "--detach", "--product-dir", str(tmp_path)]
    ]
    assert registry.attempts[("product", 5)].prior == 1


def test_a_blocker_whose_ancestor_check_raises_a_git_error_does_not_launch(tmp_path):
    """Tech Lead's blocking finding: `is_ancestor` can raise `GitError` for a real git
    failure (never for an unknown object, which answers `False` instead), and that must
    read as an unresolved blocker, not escape `run_once` and end the long-lived `_poll`
    loop, which has no handler of its own."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("CLOSED", "COMPLETED", None, "deadbeef")
    ports.workspace.ancestor_errors = {"deadbeef"}
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_a_blocker_whose_issue_state_raises_does_not_launch(tmp_path):
    """Tech Lead's blocking finding: a `gh` failure reading a blocker's own state (here,
    `ValueError`, the shape `GhTracker._gh` raises it in) counts as an unresolved
    blocker -- "ante la duda, no lanza" -- the same as the git failure above, not an
    exception `run_once` propagates."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.state_errors = {3}
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_empty_native_blockers_fall_back_to_the_body_and_block_on_an_open_one(tmp_path):
    """GH-142 (apostrophe #8): native blockers answer `()` -- GitHub's own shape for
    "no native dependency" -- so the body's own `Blocked by: #7` line is read, and #7
    is still OPEN: the candidate must not launch."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    ports.tracker.states[7] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5, "Blocked by: #7")
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_empty_native_blockers_with_no_body_line_launches(tmp_path):
    """The other side of the same fallback: native blockers are `()` and the body
    carries no `Blocked by:` line at all, so there is nothing to block on."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_non_empty_native_blockers_win_over_a_contradicting_body(tmp_path):
    """Non-empty native blockers never read the body at all (the plan's own
    invariant): #3 is `NOT_PLANNED` (resolved) and the body's own `Blocked by: #9`
    line would block if read, but it never is -- the candidate launches."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("CLOSED", "NOT_PLANNED", None, None)
    ports.tracker.states[9] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5, "Blocked by: #9")
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_an_unreadable_body_does_not_launch_and_records_no_attempt(tmp_path):
    """GH-142: native blockers are `()`, so the body is read -- and this candidate's
    body cannot be read at all (`MemoryInbox.task_errors`, the same declared-failure
    pattern `MemoryTracker.state_errors` already uses). `_blocked` treats the
    `ValueError` as blocked, "ante la duda, no lanza", and nothing is recorded in
    `launch_attempts` for a candidate that never actually launched."""
    ports = memory_ports(inbox=MemoryInbox(task_errors={5}))
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []
    assert registry.attempts == {}


def test_nothing_launches_when_the_machine_wide_cap_is_full(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry(
        entries=[RunRegistration(pid=1, host="h", product="other-product", task_id="GH-1")]
    )
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_the_same_candidate_launches_once_the_cap_has_a_free_slot(tmp_path):
    """The cap test's own twin: the same unblocked candidate (empty native blockers,
    a body with no `Blocked by:` line), with an empty registry instead of a full one,
    does launch -- so the cap test above is proven to fail for the cap, not because
    GH-142's own fallback silently swallowed the candidate."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_a_merged_pull_request_is_closed_only_once(tmp_path):
    merged = PullRequest(
        state="MERGED",
        created_at="2026-10-01T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        commits=(),
        merge_commit="c0ffee",
    )
    tracker = _TrackerWithPullRequest({"https://github.com/o/r/pull/7": merged})
    ports = memory_ports(tracker=tracker)
    ports.documents.save({"id": "GH-3", "pull_request": "https://github.com/o/r/pull/7"})
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path)

    lines = _round(cfg, ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: close #3 -> pid 4242"]
    assert launcher.spawned == [
        ["agentic", "close", "--issue", "3", "--product-dir", str(tmp_path)]
    ]

    # A second round, the document still unchanged (no real `close` child ran): since
    # this fake never flips `closed_at`, the real guarantee -- one close per merge -- is
    # the document's own `closed_at` once `close` runs, not this fixture's business to
    # simulate; what this file proves is that `gather_closes` reads the document, not
    # the pull request's state alone, and a document already carrying `closed_at` drops
    # out. Simulate that here directly.
    ports.documents.record_closure("GH-3", {"closed_at": "2026-10-02T00:05:00Z"})
    lines = _round(cfg, ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert len(launcher.spawned) == 1


def test_a_human_comment_after_the_last_system_comment_blocks_auto_resume(tmp_path):
    """Human decision 1 (issue #137): a comment that is not the system's own, posted
    after the system's last one, means a person is talking -- the poller leaves the
    task to the human, however long it has already waited."""
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-02T19:30:00Z",
        ),
        IssueComment(body="hold off, I'm looking into it", created_at="2026-10-02T19:35:00Z"),
    ]
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    well_past = ESCALATED_AT + timedelta(days=1)
    assert _round(_cfg(tmp_path), ports, registry, launcher, well_past) == [
        "agentic: poll: nothing to do this round"
    ]
    assert launcher.spawned == []
    assert ports.tracker.comments == []  # never posted Auto-resumed: either


def test_a_human_comment_before_the_last_auto_resumed_still_resumes(tmp_path):
    """The flip side: a human spoke, but the system (a previous `Auto-resumed:`) spoke
    again after -- the human's own comment is not the *last* one, so this is not the
    shape Human decision 1 names."""
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-02T19:30:00Z",
        ),
        IssueComment(body="try again please", created_at="2026-10-02T19:40:00Z"),
        IssueComment(body="Auto-resumed: account limit", created_at="2026-10-02T19:45:00Z"),
    ]
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    due = datetime(2026, 10, 2, 20, 10, tzinfo=UTC)  # 30 min after the 2nd auto-resume's own slot
    lines = _round(_cfg(tmp_path), ports, registry, launcher, due)
    assert lines == ["agentic: poll: resume #19 -> pid 4242"]


def test_the_wait_for_registration_gives_up_after_its_own_timeout_without_hanging(tmp_path):
    """Rejected input for Human decision 2: a child that never registers must not hang
    the round forever -- the wait gives up once `REGISTER_WAIT_S` of (faked) sleeping
    has passed, and the round still finishes."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    slept: list[float] = []

    def counting_sleep(seconds: float) -> None:
        slept.append(seconds)

    lines = poll.run_once(
        _cfg(tmp_path),
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "agentic",
        sleep=counting_sleep,
        lock=_Locks(),
        current_boot=None,
    )
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert sum(slept) == poll.REGISTER_WAIT_S  # it gave up, never registering
    assert registry.live() == []  # still nothing registered: the fake never adds one


def test_a_child_that_registers_while_waited_for_occupies_the_next_rounds_slot(tmp_path):
    """Human decision 2, the positive case: once the spawned child shows up in
    `registry.live()` (simulated here as a side effect of the injected `sleep`, as a
    slow-starting real child would), the very next round -- with the cap at 1 --
    neither launches nor resumes anything more."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    registered = False

    def slow_register(seconds: float) -> None:
        nonlocal registered
        if not registered:
            registry.entries.append(
                RunRegistration(pid=4242, host="h", product="product", task_id="GH-5")
            )
            registered = True

    cfg = _cfg(tmp_path, max_concurrent_runs=1)
    first = poll.run_once(
        cfg,
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "agentic",
        sleep=slow_register,
        lock=_Locks(),
        current_boot=None,
    )
    assert first == ["agentic: poll: launch #5 -> pid 4242"]
    assert registered  # the wait polled until it showed up, not the full 60 s

    second = poll.run_once(
        cfg,
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "agentic",
        sleep=slow_register,
        lock=_Locks(),
        current_boot=None,
    )
    assert second == ["agentic: poll: nothing to do this round"]
    assert len(launcher.spawned) == 1  # the second round launched nothing more


def _merged_pr(number: int) -> PullRequest:
    return PullRequest(
        state="MERGED",
        created_at="2026-10-01T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        commits=(),
        merge_commit=f"c0ffee{number}",
    )


def test_a_spawn_failure_does_not_stop_the_rounds_other_actions(tmp_path):
    """Acceptance (a): a `Launcher` that raises `FileNotFoundError` on action 1 of 3
    still runs actions 2 and 3, and action 1's own line says `not launched`."""
    prs = {f"https://github.com/o/r/pull/{n}": _merged_pr(n) for n in (7, 8, 9)}
    tracker = _TrackerWithPullRequest(prs)
    ports = memory_ports(tracker=tracker)
    for issue, pr in zip((3, 4, 5), (7, 8, 9), strict=True):
        ports.documents.save(
            {"id": f"GH-{issue}", "pull_request": f"https://github.com/o/r/pull/{pr}"}
        )
    registry = MemoryRunRegistry()
    launcher = FakeLauncher(fails={0})
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines[0].startswith("agentic: poll: close #3 -> not launched: ")
    assert lines[1] == "agentic: poll: close #4 -> pid 4242"
    assert lines[2] == "agentic: poll: close #5 -> pid 4242"
    assert launcher.spawned == [
        ["agentic", "close", "--issue", "4", "--product-dir", str(tmp_path)],
        ["agentic", "close", "--issue", "5", "--product-dir", str(tmp_path)],
    ]


def test_a_resume_whose_spawn_fails_posts_no_comment_and_never_waits(tmp_path):
    """Acceptance (b): the comment order is inverted from a plain `pid N` resume --
    `AUTO_RESUMED` only goes up once the spawn itself returned a pid, so a failed spawn
    leaves no comment and never awaits registration (a wait that would only ever time
    out, since nothing was launched to register)."""
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-02T19:30:00Z",
        )
    ]
    registry = MemoryRunRegistry()
    launcher = FakeLauncher(fails={0})
    slept: list[float] = []
    due = ESCALATED_AT + timedelta(minutes=16)
    lines = poll.run_once(
        _cfg(tmp_path),
        ports,
        registry,
        launcher,
        due,
        "agentic",
        sleep=slept.append,
        lock=_Locks(),
        current_boot=None,
    )
    assert len(lines) == 1
    assert lines[0].startswith("agentic: poll: resume #19 -> not launched (not counted): ")
    assert ports.tracker.comments == []  # never posted Auto-resumed:
    assert slept == []  # never awaited registration


def test_a_launch_whose_spawn_fails_still_counts_its_own_attempt(tmp_path):
    """Acceptance (c), first attempt: `record_launch` ran before the failed spawn, so
    the failure still spends an attempt against the issue's own calendar, and the line
    names it -- `attempt 1 of 6`, with the schedule not yet spent."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher(fails={0})
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert len(lines) == 1
    assert lines[0].startswith("agentic: poll: launch #5 -> not launched (attempt 1 of 6): ")
    assert "calendar spent" not in lines[0]
    assert registry.attempts[("product", 5)].prior == 1


def test_a_launch_whose_spawn_fails_on_the_last_attempt_says_the_calendar_is_spent(tmp_path):
    """Acceptance (c), last attempt: with 5 prior attempts already on record, this
    failure is the 6th and last the schedule (`LAUNCH_SCHEDULE`, length 6) allows
    -- the line says so, for a human to notice."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    registry.attempts[("product", 5)] = LaunchAttempt(
        since=ESCALATED_AT - timedelta(minutes=121), prior=5
    )
    launcher = FakeLauncher(fails={0})
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert len(lines) == 1
    assert lines[0].startswith("agentic: poll: launch #5 -> not launched (attempt 6 of 6)")
    assert "; calendar spent, needs a human: " in lines[0]
    assert registry.attempts[("product", 5)].prior == 6


def test_every_launched_action_uses_the_argv0_passed_in(tmp_path):
    """Acceptance (d): every spawned child's own argv starts with the `argv0` this
    call passed explicitly -- never a literal `"agentic"` `poll.py` picked on its own,
    which is the whole point of this issue's fix."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = poll.run_once(
        _cfg(tmp_path),
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "/venv/bin/agentic",
        sleep=_no_sleep,
        lock=_Locks(),
        current_boot=None,
    )
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert launcher.spawned[0][0] == "/venv/bin/agentic"


# ---- `agentic reflect --all` (ADR-0043) ---------------------------------------------------


def _reflect_ports(tmp_path, started_at: str, closed: bool):
    from agentic.core.process.domain import playbook_candidates as pc

    ports = memory_ports()
    if started_at:
        ports.workspace.files[("HEAD", pc.LEDGER_FILE)] = pc.render_ledger(
            pc.Ledger(started_at=started_at)
        )
    if closed:
        ports.documents.save({"id": "GH-9", "title": "t", "description": "d"})
        ports.documents.record_closure(
            "GH-9", {"closed_at": "2026-10-06T09:00:00.000+00:00", "delivery_outcome": "merged"}
        )
    return ports


def test_the_poller_launches_reflect_only_when_a_kept_closed_task_awaits(tmp_path):
    ports = _reflect_ports(tmp_path, "2026-10-05T00:00:00+00:00", closed=True)
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)
    assert launcher.spawned == [["agentic", "reflect", "--all", "--product-dir", str(tmp_path)]]
    assert lines == ["agentic: poll: reflect 1 task(s) -> pid 4242"]


def test_the_poller_does_not_launch_reflect_with_nothing_eligible(tmp_path):
    # No clock yet, a closed task before it, and no closed task at all: never a launch.
    for started_at, closed in (("", True), ("2026-10-07T00:00:00+00:00", True), ("x", False)):
        ports = _reflect_ports(tmp_path, started_at, closed)
        launcher = FakeLauncher()
        lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)
        assert launcher.spawned == []
        assert lines == ["agentic: poll: nothing to do this round"]


def test_a_malformed_pull_request_listing_never_fails_the_poll_round(tmp_path, monkeypatch):
    from agentic.core.process.application import reflect_run

    def malformed(cfg, ports):
        raise KeyError("mergeCommit")

    monkeypatch.setattr(reflect_run, "pending", malformed)
    ports = _reflect_ports(tmp_path, "2026-10-05T00:00:00+00:00", closed=True)
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)
    assert launcher.spawned == []
    assert lines == ["agentic: poll: nothing to do this round"]


def _two_candidates_with_merged_blockers(ports) -> None:
    ports.tracker._queue = [5, 6]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[6] = IssueState("OPEN", "", (4,), None)
    ports.tracker.states[3] = IssueState("CLOSED", "COMPLETED", None, "aaa111")
    ports.tracker.states[4] = IssueState("CLOSED", "COMPLETED", None, "bbb222")


def test_an_empty_queue_never_asks_for_the_upstream(tmp_path):
    ports = memory_ports()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert ports.workspace.upstream_calls == 0


def test_candidates_with_no_blockers_never_ask_for_the_upstream(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5, 6]
    for number in (5, 6):
        ports.tracker.states[number] = IssueState("OPEN", "", (), None)
        _seed_body(ports, number)
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert ports.workspace.upstream_calls == 0


def test_two_candidates_with_merged_blockers_fetch_once_and_judge_against_that_tip(tmp_path):
    ports = memory_ports()
    _two_candidates_with_merged_blockers(ports)
    ports.workspace.upstream_answer = Upstream("origin/master", "f00dcafe", None)
    ports.workspace.ancestors = {"aaa111", "bbb222"}
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert ports.workspace.upstream_calls == 1
    assert [ref for _, _, ref in ports.workspace.ancestor_checks] == ["f00dcafe", "f00dcafe"]


def test_an_upstream_with_no_sha_judges_against_head_and_keeps_both_blocked(tmp_path):
    ports = memory_ports()
    _two_candidates_with_merged_blockers(ports)
    ports.workspace.upstream_answer = Upstream("HEAD", None, "HEAD is unborn")
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert ports.workspace.upstream_calls == 1
    assert {ref for _, _, ref in ports.workspace.ancestor_checks} == {"HEAD"}
    assert launcher.spawned == []
    assert lines == [
        "agentic: poll: upstream: HEAD is unborn",
        "agentic: poll: nothing to do this round",
    ]


def test_the_upstream_note_is_one_line_of_the_round_however_many_blockers_judged(tmp_path):
    ports = memory_ports()
    _two_candidates_with_merged_blockers(ports)
    ports.workspace.upstream_answer = Upstream("origin/master", "f00dcafe", "fetch failed")
    ports.workspace.ancestors = {"aaa111", "bbb222"}
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert lines.count("agentic: poll: upstream: fetch failed") == 1


# GH-172: an account-limit pause is retaken on a flat 15-minute calendar counted per
# account window, and an interview keeps its ceiling for retakes that ran a round.


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_time(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _chase_comments(
    probes: int, first: datetime = ESCALATED_AT, period_min: int = 16
) -> list[IssueComment]:
    """One `Escalated:`, then `probes` pairs of `Auto-resumed:` + `Escalated:`, a
    `period_min` apart: the shape a chase that kept hitting the same 429 leaves."""
    comments = [IssueComment(body="Escalated: cut", created_at=_stamp(first))]
    for k in range(1, probes + 1):
        at = first + timedelta(minutes=period_min * k)
        comments.append(IssueComment(body="Auto-resumed: account limit", created_at=_stamp(at)))
        comments.append(
            IssueComment(body="Escalated: cut", created_at=_stamp(at + timedelta(minutes=1)))
        )
    return comments


def _account_limit_ports(section: str, comments: list[IssueComment], runs: list[dict]):
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": section},
                {"kind": "escalation", "value": "failure"},
            ],
            runs=runs,
        )
    )
    ports.tracker.timestamped["GH-19"] = comments
    return ports


def _retaken(ports, now: datetime, tmp_path) -> bool:
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, now)
    return lines == ["agentic: poll: resume #19 -> pid 4242"]


def test_an_account_limit_pause_gets_a_fresh_window_once_a_run_delivered_after_its_probes(
    tmp_path,
):
    comments = _chase_comments(probes=24)
    last_probe = ESCALATED_AT + timedelta(minutes=16 * 24)
    now = last_probe + timedelta(minutes=1 + 16)
    delivered = [
        {
            "role": "Developer",
            "section": "Developer",
            "started_at": _run_time(last_probe + timedelta(seconds=10)),
            "ended_at": _run_time(last_probe + timedelta(seconds=40)),
        }
    ]
    # Rejected input: with no delivered run the same 24 probes are one window, spent.
    spent = _account_limit_ports("Developer", comments, runs=[])
    assert not _retaken(spent, now, tmp_path)
    # A delivered run after the last probe: the next 429 is a new window, retaken 15
    # minutes after its own `Escalated:` though earlier probes exist.
    fresh = _account_limit_ports("Developer", comments, runs=delivered)
    assert not _retaken(fresh, last_probe + timedelta(minutes=1 + 14), tmp_path)
    assert _retaken(fresh, now, tmp_path)


def test_an_account_limit_pause_whose_run_ends_are_unreadable_counts_the_whole_chase(tmp_path):
    comments = _chase_comments(probes=24)
    now = ESCALATED_AT + timedelta(minutes=16 * 24 + 1 + 16)
    runs = [{"role": "Developer", "section": "Developer", "ended_at": "not a time"}]
    assert not _retaken(_account_limit_ports("Developer", comments, runs), now, tmp_path)


def _delivered_at(moment: datetime) -> list[dict]:
    return [
        {
            "role": "Developer",
            "section": "Developer",
            "started_at": _run_time(moment),
            "ended_at": _run_time(moment + timedelta(seconds=30)),
        }
    ]


def test_past_the_chase_max_an_account_limit_pause_is_retaken_hourly_not_left_to_a_human(tmp_path):
    # GH-239 edits the case that asserted the chase backstop handed the pause to a human.
    # A cheap role keeps delivering (a fresh window each time) while another keeps
    # hitting a 429: the whole-chase count, which a delivered run never resets, moves the
    # pause from the 15-minute calendar to the hourly one.
    last_probe_at = ESCALATED_AT + timedelta(minutes=16 * 96)
    escalated_at = last_probe_at + timedelta(minutes=1)
    over = _account_limit_ports(
        "Developer", _chase_comments(probes=96), _delivered_at(last_probe_at)
    )
    assert not _retaken(over, escalated_at + timedelta(minutes=59), tmp_path)
    assert _retaken(over, escalated_at + timedelta(minutes=61), tmp_path)
    under_at = ESCALATED_AT + timedelta(minutes=16 * 95)
    under = _account_limit_ports("Developer", _chase_comments(probes=95), _delivered_at(under_at))
    assert _retaken(under, under_at + timedelta(minutes=1 + 16), tmp_path)
    # Rejected input: the same 96 probes of an upstream pause never get the hourly slot.
    assert not _retaken(
        _upstream_ports(_chase_comments(probes=96)), escalated_at + timedelta(minutes=61), tmp_path
    )


def _upstream_ports(comments: list[IssueComment]):
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: 503",
            [
                {"kind": "failure", "value": "upstream", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped["GH-19"] = comments
    return ports


def test_a_spent_hourly_week_escalates_the_pause_as_a_failure_once(tmp_path):
    probes = 96 + 168
    comments = _chase_comments(probes=probes, period_min=60)
    now = ESCALATED_AT + timedelta(minutes=60 * probes + 90)
    ports = _account_limit_ports("Developer", comments, runs=[])
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), now)
    assert lines == ["agentic: poll: #19 account limit persisted 7 days -> left to the human"]
    saved = ports.documents.load("GH-19")
    assert saved["escalated"] == "account limit persisted 7 days"
    assert saved["escalation_class"] == "failure"
    assert saved["pause_log"][-2:] == [
        {"kind": "failure", "value": "other", "section": ""},
        {"kind": "escalation", "value": "failure"},
    ]
    assert ("GH-19", "needs-human") in ports.tracker.labels
    ((_, body),) = ports.tracker.comments
    assert body.startswith("Escalated: account limit persisted 7 days")
    # Written once: the next round finds nothing to retake and nothing to say.
    again = _round(
        _cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), now + timedelta(hours=2)
    )
    assert again == ["agentic: poll: nothing to do this round"]
    assert len(ports.tracker.comments) == 1


def test_a_hundred_and_sixty_seven_hourly_probes_still_retake(tmp_path):
    probes = 96 + 167
    comments = _chase_comments(probes=probes, period_min=60)
    now = ESCALATED_AT + timedelta(minutes=60 * probes + 1 + 61)
    assert _retaken(_account_limit_ports("Developer", comments, runs=[]), now, tmp_path)


def test_an_account_limit_interview_pause_with_a_productive_retake_goes_to_a_human(tmp_path):
    section = INTERVIEW_SOLUTION.section
    comments = _chase_comments(probes=1)
    probe = ESCALATED_AT + timedelta(minutes=16)
    now = probe + timedelta(minutes=1 + 16)
    round_run = {
        "role": INTERVIEW_SOLUTION.interviewer.name,
        "section": section,
        "started_at": _run_time(probe + timedelta(seconds=20)),
        "ended_at": _run_time(probe + timedelta(minutes=5)),
    }
    # The retake ran an interview round: the ceiling is spent, nobody retakes it.
    productive = _account_limit_ports(section, comments, [round_run])
    assert not _retaken(productive, now, tmp_path)
    # Control: the retake's 429 arrived on turn 1 and ran no round, so the ceiling
    # still stands and the next slot is retaken.
    unproductive = _account_limit_ports(section, comments, [])
    assert _retaken(unproductive, now, tmp_path)
    # Fail closed: a round whose start is unreadable counts as spent.
    unreadable = _account_limit_ports(section, comments, [{**round_run, "started_at": "?"}])
    assert not _retaken(unreadable, now, tmp_path)


def test_an_upstream_interview_pause_keeps_its_single_retake_and_never_reads_the_runs(tmp_path):
    section = INTERVIEW_SOLUTION.section
    malformed = [
        {
            "role": INTERVIEW_SOLUTION.interviewer.name,
            "section": section,
            "started_at": "garbage",
            "ended_at": "garbage",
        }
    ]

    def _ports(comments: list[IssueComment]):
        ports = memory_ports()
        ports.documents.save(
            _paused(
                "GH-19",
                "Tech Lead failed 2 times: 503",
                [
                    {"kind": "failure", "value": "upstream", "section": section},
                    {"kind": "escalation", "value": "failure"},
                ],
                runs=malformed,
            )
        )
        ports.tracker.timestamped["GH-19"] = comments
        return ports

    assert _retaken(
        _ports(_chase_comments(probes=0)), ESCALATED_AT + timedelta(minutes=16), tmp_path
    )
    after_one = _chase_comments(probes=1)
    now = ESCALATED_AT + timedelta(days=1)
    assert not _retaken(_ports(after_one), now, tmp_path)


# --- GH-176: a run a reboot interrupted is retaken -----------------------------------


def _dead_lock(task_id: str, boot_id: str | None = "boot-old", **more) -> dict[str, LockRecord]:
    return {task_id: LockRecord(777, "h", "2026-10-05T08:00:00Z", "agentic run", boot_id, **more)}


def _open_task(ports, task_id: str = "GH-30", status: str = "developing", **more) -> None:
    ports.documents.save({"id": task_id, "status": status, **more})


def test_gather_retakes_retakes_a_run_whose_lock_is_from_a_previous_boot():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30"))
    assert poll.gather_retakes(ports, locks, "boot-now") == ([30], [])
    assert locks.reads == ["GH-30"]  # read, never acquired: `_Locks.acquire` raises


def test_gather_retakes_leaves_a_same_boot_death_to_the_human_with_a_line():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30", boot_id="boot-now"))
    assert poll.gather_retakes(ports, locks, "boot-now") == (
        [],
        [
            "agentic: poll: #30 died in this boot (lock pid 777 since 2026-10-05T08:00:00Z), "
            "left to the human: agentic resume --issue 30"
        ],
    )


def test_gather_retakes_says_stopped_at_shutdown_for_a_marked_record_of_this_boot():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30", boot_id="boot-now", stopped_at_shutdown=True))
    _, lines = poll.gather_retakes(ports, locks, "boot-now")
    assert "#30 stopped at shutdown (lock pid 777" in lines[0]


def test_gather_retakes_never_retakes_a_record_without_a_boot_id():
    ports = memory_ports()
    _open_task(ports)
    retakes, lines = poll.gather_retakes(ports, _Locks(_dead_lock("GH-30", boot_id=None)), "b")
    assert retakes == []
    assert "#30 died (boot unknown)" in lines[0]


def test_gather_retakes_leaves_a_live_lock_alone_and_says_nothing():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30"), alive={"GH-30"})
    assert poll.gather_retakes(ports, locks, "boot-now") == ([], [])


def test_gather_retakes_skips_closed_escalated_and_delivered_tasks():
    ports = memory_ports()
    _open_task(ports, "GH-31", closed_at="2026-10-05")
    _open_task(ports, "GH-32", escalated="Developer failed")
    _open_task(ports, "GH-33", pull_request="https://x/pull/3")
    _open_task(ports, "GH-34", status="ready_for_review")
    locks = _Locks({t: r for n in (31, 32, 33, 34) for t, r in _dead_lock(f"GH-{n}").items()})
    assert poll.gather_retakes(ports, locks, "boot-now") == ([], [])


def test_gather_retakes_stops_at_the_cap_after_three_retakes_and_says_so():
    ports = memory_ports()
    _open_task(ports)
    retaken = IssueComment(
        body="Retaken after reboot: previous boot", created_at="2026-10-05T09:00:00Z"
    )
    ports.tracker.timestamped["GH-30"] = [
        IssueComment(body="a human decision", created_at="2026-10-05T07:00:00Z"),
        retaken,
        retaken,
    ]
    locks = _Locks(_dead_lock("GH-30"))
    assert poll.gather_retakes(ports, locks, "boot-now") == ([30], [])  # two of three spent
    ports.tracker.timestamped["GH-30"].append(retaken)
    retakes, lines = poll.gather_retakes(ports, locks, "boot-now")
    assert retakes == []
    assert "#30 retake cap spent" in lines[0]


def test_a_human_comment_resets_the_retake_count():
    ports = memory_ports()
    _open_task(ports)
    retaken = IssueComment(
        body="Retaken after reboot: previous boot", created_at="2026-10-05T09:00:00Z"
    )
    ports.tracker.timestamped["GH-30"] = [
        retaken,
        retaken,
        retaken,
        IssueComment(body="try again", created_at="2026-10-05T10:00:00Z"),
    ]
    assert poll.gather_retakes(ports, _Locks(_dead_lock("GH-30")), "boot-now")[0] == [30]


def test_retaken_is_a_system_comment_a_resume_reads_past():
    from agentic.core.process.application import resume

    assert poll.RETAKEN in resume.SYSTEM_COMMENTS


def test_execute_runs_a_retake_as_a_detached_resume_and_never_records_a_launch():
    from agentic.core.process.domain import rules

    ports = memory_ports()
    _open_task(ports)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = poll.execute(
        [rules.PollAction("retake", 30)],
        _cfg("/p"),
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "agentic",
        register_timeout_s=0.0,
        sleep=_no_sleep,
    )
    assert launcher.spawned == [
        ["agentic", "resume", "--issue", "30", "--yes", "--detach", "--product-dir", "/p"]
    ]
    assert registry.attempts == {}  # `record_launch` was never called
    assert ports.tracker.comments == [("GH-30", "Retaken after reboot: previous boot")]
    assert lines == [
        "agentic: poll: retake #30 -> pid 4242",
        "agentic: poll: retake #30 -> not registered (retake 1 of 3)",
    ]


def test_a_retake_whose_spawn_fails_posts_no_comment():
    from agentic.core.process.domain import rules

    ports = memory_ports()
    launcher = FakeLauncher(fails={0})
    lines = poll.execute(
        [rules.PollAction("retake", 30)],
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        "agentic",
        register_timeout_s=0.0,
        sleep=_no_sleep,
    )
    assert ports.tracker.comments == []
    assert lines[0].startswith("agentic: poll: retake #30 -> not launched")


def test_execute_has_no_catch_all_launch_for_an_unknown_kind():
    """The rejected input: an action kind no branch knows must raise, never launch."""
    import pytest

    from agentic.core.process.domain import rules

    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    with pytest.raises(AssertionError):
        poll.execute(
            [rules.PollAction("bogus", 30)],  # type: ignore[arg-type]
            _cfg("/p"),
            memory_ports(),
            registry,
            launcher,
            ESCALATED_AT,
            "agentic",
        )
    assert launcher.spawned == [] and registry.attempts == {}


def test_a_round_retakes_before_anything_spawns_from_the_untouched_lock_record():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30"))
    before = dict(locks.records)
    launcher = FakeLauncher()
    lines = _round(_cfg("/p"), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, lock=locks)
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"
    assert locks.records == before


def test_retakes_count_against_the_machine_cap():
    ports = memory_ports()
    _open_task(ports, "GH-30")
    _open_task(ports, "GH-31")
    locks = _Locks({**_dead_lock("GH-30"), **_dead_lock("GH-31")})
    registry = MemoryRunRegistry([RunRegistration(pid=1, host="h", product="p", task_id="GH-9")])
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p", max_concurrent_runs=2), ports, registry, launcher, ESCALATED_AT, lock=locks
    )
    assert [a[3] for a in launcher.spawned] == ["30"]
    assert len(launcher.spawned) == 1  # one slot free: the second retake waits
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"


def test_a_round_lists_a_run_left_to_the_human_every_round():
    ports = memory_ports()
    _open_task(ports)
    locks = _Locks(_dead_lock("GH-30", boot_id="boot-now"))
    launcher = FakeLauncher()
    first = _round(_cfg("/p"), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, lock=locks)
    second = _round(_cfg("/p"), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, lock=locks)
    assert first == second and "#30 died in this boot" in first[0]
    assert launcher.spawned == []


def _retaken_comments(count: int) -> list[IssueComment]:
    retaken = IssueComment(
        body="Retaken after reboot: previous boot", created_at="2026-10-05T09:00:00Z"
    )
    human = IssueComment(body="a human decision", created_at="2026-10-05T07:00:00Z")
    return [human, *[retaken] * count]


def test_a_round_leaves_a_run_to_the_human_once_its_three_retakes_are_spent():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _retaken_comments(poll.rules.RETAKE_AFTER_REBOOT_MAX)
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_dead_lock("GH-30")),
    )
    assert launcher.spawned == []
    assert ports.tracker.comments == []
    assert len(lines) == 1
    assert lines[0].startswith("agentic: poll: #30 retake cap spent (lock pid 777")
    assert lines[0].endswith("left to the human: agentic resume --issue 30")


def test_a_round_still_retakes_a_run_with_two_of_its_three_retakes_spent():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _retaken_comments(2)
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_dead_lock("GH-30")),
    )
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"
    assert "retake 3 of 3" in lines[1]
    assert len(launcher.spawned) == 1


# -- spend pacing (GH-177) ---------------------------------------------------------------

PACE_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _spent_run(ended_ago_min: int, cost: float, length_min: int = 60, **more) -> dict:
    end = PACE_NOW - timedelta(minutes=ended_ago_min)
    return {
        "started_at": (end - timedelta(minutes=length_min)).isoformat(),
        "ended_at": end.isoformat(),
        "cost_usd": cost,
        **more,
    }


def _paced_ports(runs: list[dict]):
    """A queue with one launchable issue (#5) and a closed-off task whose `runs` carry
    the spend."""
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    ports.documents.save({"id": "GH-1", "title": "t", "description": "d", "runs": runs})
    return ports


def _one_live() -> list[RunRegistration]:
    return [RunRegistration(pid=1, host="h", product="other-product", task_id="GH-1")]


def test_with_pacing_off_nothing_is_published_or_read_and_the_actions_are_todays(tmp_path):
    ports = _paced_ports([_spent_run(10, 500.0)])
    registry = MemoryRunRegistry(entries=_one_live())
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2)
    lines = _round(cfg, ports, registry, launcher, PACE_NOW)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert registry.publish_calls == [] and registry.spend_calls == []


def test_pacing_on_with_spend_over_pace_and_a_live_run_holds_the_launch_back(tmp_path):
    ports = _paced_ports([_spent_run(10, 200.0)])
    registry = MemoryRunRegistry(entries=_one_live())
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, launcher, PACE_NOW)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []
    assert len(registry.publish_calls) == 1 and len(registry.spend_calls) == 1


def test_pacing_on_with_spend_under_pace_and_a_live_run_launches(tmp_path):
    ports = _paced_ports([_spent_run(10, 5.0)])
    registry = MemoryRunRegistry(entries=_one_live())
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, launcher, PACE_NOW)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_pacing_on_with_an_empty_machine_launches_even_over_pace(tmp_path):
    ports = _paced_ports([_spent_run(10, 500.0)])
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, launcher, PACE_NOW)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_pacing_publishes_this_products_runs_under_its_own_document_dir_and_name(tmp_path):
    ports = _paced_ports([_spent_run(10, 5.0)])
    ports.stack.declared_product = "apostrophe"
    registry = MemoryRunRegistry()
    cfg = _cfg(tmp_path, max_concurrent_runs=1, spend_pacing=True)
    _round(cfg, ports, registry, FakeLauncher(), PACE_NOW)
    [(root, product, runs, now)] = registry.publish_calls
    assert root == tmp_path / ".agentic/tasks" and product == "apostrophe" and now == PACE_NOW
    assert [r.cost_usd for r in runs] == [5.0]
    assert registry.spend_calls == [PACE_NOW - timedelta(hours=5)]


def test_spend_that_another_product_published_counts_toward_the_pace(tmp_path):
    ports = _paced_ports([])
    registry = MemoryRunRegistry(entries=_one_live())
    registry.publish_spend(
        tmp_path / "elsewhere",
        "gym",
        [
            rules_spend_run(
                PACE_NOW - timedelta(minutes=70), PACE_NOW - timedelta(minutes=10), 300.0
            )
        ],
        PACE_NOW,
    )
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    assert _round(cfg, ports, registry, launcher, PACE_NOW) == [
        "agentic: poll: nothing to do this round"
    ]
    assert launcher.spawned == []


def rules_spend_run(start: datetime, end: datetime, cost: float):
    from agentic.core.process.domain.rules import SpendRun

    return SpendRun(start, end, cost)


def test_a_run_with_no_ended_at_is_counted_in_a_note_and_never_raises(tmp_path):
    ports = _paced_ports(
        [
            {"started_at": PACE_NOW.isoformat(), "cost_usd": 4.0},
            {
                "started_at": PACE_NOW.isoformat(),
                "ended_at": "2026-10-06T11:00:00",
                "cost_usd": 1.0,
            },
            _spent_run(10, 2.0),
        ]
    )
    registry = MemoryRunRegistry()
    cfg = _cfg(tmp_path, max_concurrent_runs=1, spend_pacing=True)
    lines = _round(cfg, ports, registry, FakeLauncher(), PACE_NOW)
    assert lines == [
        "agentic: poll: 2 runs without timestamps not counted",
        "agentic: poll: launch #5 -> pid 4242",
    ]
    assert [r.cost_usd for r in registry.publish_calls[0][2]] == [2.0]


def test_registry_notes_come_before_the_skip_note(tmp_path):
    ports = _paced_ports([{"cost_usd": 4.0}])
    registry = MemoryRunRegistry()
    registry.spend_notes = ["agentic: poll: spend file abc.json unreadable, skipped"]
    cfg = _cfg(tmp_path, max_concurrent_runs=1, spend_pacing=True)
    lines = _round(cfg, ports, registry, FakeLauncher(), PACE_NOW)
    assert lines[:2] == [
        "agentic: poll: spend file abc.json unreadable, skipped",
        "agentic: poll: 1 runs without timestamps not counted",
    ]


def test_only_cost_usd_is_summed_never_the_subagent_or_failed_share(tmp_path):
    ports = _paced_ports(
        [_spent_run(10, 1.0, length_min=30, subagent_cost_usd=900.0, failed_cost_usd=900.0)]
    )
    registry = MemoryRunRegistry(entries=_one_live())
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, FakeLauncher(), PACE_NOW)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]


def test_the_pace_never_holds_back_a_close_while_it_holds_the_launch(tmp_path):
    merged = PullRequest(
        state="MERGED",
        created_at="2026-10-01T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        commits=(),
        merge_commit="c0ffee",
    )
    ports = memory_ports(tracker=_TrackerWithPullRequest({"https://github.com/o/r/pull/7": merged}))
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    ports.documents.save(
        {
            "id": "GH-3",
            "pull_request": "https://github.com/o/r/pull/7",
            "runs": [_spent_run(10, 500.0)],
        }
    )
    registry = MemoryRunRegistry(entries=_one_live())
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, FakeLauncher(), PACE_NOW)
    assert lines == ["agentic: poll: close #3 -> pid 4242"]


# -- what counts against the cap (RunKind) --------------------------------------------------


def _live(kind: str, pid: int = 1) -> RunRegistration:
    return RunRegistration(pid=pid, host="h", product="other-product", task_id="GH-1", kind=kind)


def _launchable(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    _seed_body(ports, 5)
    return ports


def test_a_live_merge_or_checks_land_does_not_take_a_slot_of_the_cap(tmp_path):
    for exempt in ("merge", "land_checks"):
        ports = _launchable(tmp_path)
        registry = MemoryRunRegistry(entries=[_live("role"), _live(exempt, pid=2)])
        launcher = FakeLauncher()
        lines = _round(
            _cfg(tmp_path, max_concurrent_runs=2), ports, registry, launcher, ESCALATED_AT
        )
        assert lines == ["agentic: poll: launch #5 -> pid 4242"], exempt


def test_a_live_model_land_and_a_role_fill_the_cap_but_a_merge_beside_them_changes_nothing(
    tmp_path,
):
    """The rejected input: counted children (`role`, `land_model`) alone fill the cap, and
    a merge beside them neither frees a slot nor takes one."""
    for entries in (
        [_live("role"), _live("role", pid=2)],
        [_live("role"), _live("land_model", pid=2)],
        [_live("role"), _live("land_model", pid=2), _live("merge", pid=3)],
    ):
        ports = _launchable(tmp_path)
        launcher = FakeLauncher()
        lines = _round(
            _cfg(tmp_path, max_concurrent_runs=2),
            ports,
            MemoryRunRegistry(entries=entries),
            launcher,
            ESCALATED_AT,
        )
        assert lines == ["agentic: poll: nothing to do this round"]
        assert launcher.spawned == []


def test_a_record_that_names_no_kind_still_takes_a_slot(tmp_path):
    ports = _launchable(tmp_path)
    registry = MemoryRunRegistry(
        entries=[RunRegistration(pid=1, host="h", product="p", task_id="GH-1")]
    )
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]


def test_the_pace_sees_only_the_counted_runs_so_a_live_merge_is_an_empty_machine(tmp_path):
    ports = _paced_ports([_spent_run(10, 500.0)])
    registry = MemoryRunRegistry(entries=[_live("merge")])
    launcher = FakeLauncher()
    cfg = _cfg(tmp_path, max_concurrent_runs=2, spend_pacing=True)
    lines = _round(cfg, ports, registry, launcher, PACE_NOW)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    held = _paced_ports([_spent_run(10, 500.0)])
    lines = _round(cfg, held, MemoryRunRegistry(entries=[_live("role")]), FakeLauncher(), PACE_NOW)
    assert lines == ["agentic: poll: nothing to do this round"]


# -- a stalled gh call (GH-266) ------------------------------------------------------------


class _CommentStalls(MemoryTracker):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def comment(self, task_id, text):
        raise self.error


def _retake(tracker):
    from agentic.core.process.domain import rules

    ports = memory_ports(tracker=tracker)
    _open_task(ports)
    return poll.execute(
        [rules.PollAction("retake", 30)],
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        FakeLauncher(),
        ESCALATED_AT,
        "agentic",
        register_timeout_s=0.0,
        sleep=_no_sleep,
    )


def test_a_comment_that_times_out_after_a_spawn_keeps_the_spawn_line_and_still_waits():
    lines = _retake(_CommentStalls(TrackerTimedOut("gh issue comment timed out after 60 s")))
    assert lines == [
        "agentic: poll: retake #30 -> pid 4242",
        "agentic: poll: retake #30 -> comment timed out",
        "agentic: poll: retake #30 -> not registered (retake 1 of 3)",
    ]


def test_a_comment_that_cannot_reach_github_after_a_spawn_keeps_the_spawn_and_names_the_error():
    error = TrackerUnreachable("gh issue comment could not reach GitHub (error connecting to)")
    tracker = _CommentStalls(error)
    ports = memory_ports(tracker=tracker)
    _open_task(ports)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    from agentic.core.process.domain import rules

    waits: list[float] = []
    lines = poll.execute(
        [rules.PollAction("retake", 30)],
        _cfg("/p"),
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        "agentic",
        register_timeout_s=0.5,
        sleep=waits.append,
    )
    assert lines == [
        "agentic: poll: retake #30 -> pid 4242",
        "agentic: poll: retake #30 -> comment failed: "
        "gh issue comment could not reach GitHub (error connecting to)",
        "agentic: poll: retake #30 -> not registered (retake 1 of 3)",
    ]
    assert len(launcher.spawned) == 1 and waits  # the child runs on; the wait still ran
    assert registry.registered_kinds == []


def test_a_comment_failing_with_any_other_value_error_still_escapes():
    with pytest.raises(ValueError, match="scripted") as raised:
        _retake(_CommentStalls(ValueError("scripted")))
    assert not isinstance(raised.value, TrackerTimedOut)


class _ReadsStall(MemoryTracker):
    def queue(self):
        raise TrackerTimedOut("gh issue list timed out after 60 s")

    def list_pull_requests(self):
        raise TrackerTimedOut("gh pr list timed out after 60 s")


def test_a_round_whose_reads_time_out_releases_the_registry_lock_and_acts_on_nothing(tmp_path):
    import fcntl

    from agentic.core.process.adapters.filesystem import FilesystemRunRegistry

    ports = memory_ports(tracker=_ReadsStall())
    _open_task(ports)
    registry = FilesystemRunRegistry(tmp_path / "runs")
    launcher = FakeLauncher()
    with pytest.raises(TrackerTimedOut):
        _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    with open(tmp_path / "runs" / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
    assert launcher.spawned == [] and ports.tracker.comments == []
    assert [path for path in (tmp_path / "runs").iterdir() if path.name != ".lock"] == []


# --- GH-282: the processes a run left are reaped by that run's own scope -------------

_LEFT = ({"pid": 5, "command": "uv run sample_app.py", "outcome": "kill"},)


def test_gather_retakes_reaps_a_same_boot_death_by_its_pid_and_says_so_on_its_line():
    reaper = FakeReaper(reaped=_LEFT, reaped_runs=_LEFT)
    ports = memory_ports(reaper=reaper)
    _open_task(ports)

    _, lines = poll.gather_retakes(
        ports, _Locks(_dead_lock("GH-30", boot_id="boot-now")), "boot-now"
    )

    assert reaper.calls == [
        ("run", "GH-30", 777, None),
        ("run", "check", 777, None),
    ]  # never `reap_task`
    assert lines[0].endswith(
        "; reaped 2 processes: `uv run sample_app.py` (kill), `uv run sample_app.py` (kill)"
    )


def test_gather_retakes_says_nothing_extra_when_the_reap_found_nothing():
    reaper = FakeReaper()
    ports = memory_ports(reaper=reaper)
    _open_task(ports)

    _, lines = poll.gather_retakes(
        ports, _Locks(_dead_lock("GH-30", boot_id="boot-now")), "boot-now"
    )

    assert reaper.calls == [("run", "GH-30", 777, None), ("run", "check", 777, None)]
    assert "reaped" not in lines[0]


@pytest.mark.parametrize("boot_id", ["boot-old", None])
def test_gather_retakes_reaps_nothing_for_an_other_boot_or_unknown_boot_record(boot_id):
    reaper = FakeReaper(reaped=_LEFT, reaped_runs=_LEFT)
    ports = memory_ports(reaper=reaper)
    _open_task(ports)

    poll.gather_retakes(ports, _Locks(_dead_lock("GH-30", boot_id=boot_id)), "boot-now")

    assert reaper.calls == []


def test_gather_retakes_reaps_nothing_for_a_live_lock():
    reaper = FakeReaper(reaped=_LEFT, reaped_runs=_LEFT)
    ports = memory_ports(reaper=reaper)
    _open_task(ports)

    poll.gather_retakes(
        ports, _Locks(_dead_lock("GH-30", boot_id="boot-now"), alive={"GH-30"}), "boot-now"
    )

    assert reaper.calls == []


def test_the_poller_neither_launches_nor_retakes_nor_resumes_nor_closes_a_split_task(tmp_path):
    ports = memory_ports()
    ports.documents.save({"id": "GH-53", "status": "split", "parts": [{"title": "t"}]})
    ports.tracker._queue = {53}
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)

    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


# --- GH-308: a run that raised under the graph is retaken by the poller --------------


def _crashed_lock(task_id: str = "GH-30", version: str = "abc123") -> dict[str, LockRecord]:
    return _dead_lock(task_id, boot_id="boot-now", crashed_version=version)


def _crash_comments(*keys: str) -> list[IssueComment]:
    """A human comment, then one `Retaken after crash:` comment per key, as `execute` writes."""
    from agentic.core.process.domain import rules

    human = IssueComment(body="a human decision", created_at="2026-10-05T07:00:00Z")
    retaken = [
        IssueComment(
            body=poll.RETAKEN_AFTER_CRASH + rules.crash_retake_comment(key, key, n, 2),
            created_at="2026-10-05T09:00:00Z",
        )
        for n, key in enumerate(keys, start=1)
    ]
    return [human, *retaken]


def _gather_crash(ports, locks, version="abc123"):
    crash: dict[int, poll.CrashRetake] = {}
    result = poll.gather_retakes(
        ports, locks, "boot-now", system_version=lambda: version, crash=crash
    )
    return result, crash


def test_retaken_after_crash_is_a_system_comment_a_resume_reads_past():
    from agentic.core.process.application import resume

    assert poll.RETAKEN_AFTER_CRASH in resume.SYSTEM_COMMENTS


def test_gather_retakes_returns_a_crashed_same_boot_run_apart_with_its_counts():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _crash_comments("abc123")
    (retakes, lines), crash = _gather_crash(ports, _Locks(_crashed_lock("GH-30", "abc123+dirty")))
    assert (retakes, lines) == ([], [])
    assert crash == {30: poll.CrashRetake(1, "abc123+dirty", "abc123")}


def test_the_system_version_is_read_only_for_a_crash_mark_and_once_per_round():
    ports = memory_ports()
    _open_task(ports, "GH-30")
    _open_task(ports, "GH-31")
    _open_task(ports, "GH-32")
    calls: list[int] = []

    def version():
        calls.append(1)
        return "abc123"

    plain = _Locks(_dead_lock("GH-32"))
    poll.gather_retakes(ports, plain, "boot-now", system_version=version)
    assert calls == []
    both = _Locks({**_crashed_lock("GH-30"), **_crashed_lock("GH-31")})
    poll.gather_retakes(ports, both, "boot-now", system_version=version, crash={})
    assert calls == [1]


def test_a_crash_retake_is_spawned_with_its_own_comment_and_counted_line():
    ports = memory_ports()
    _open_task(ports)
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_crashed_lock("GH-30", "abc123+dirty")),
        system_version=lambda: "abc123",
    )
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"
    assert lines[1] == "agentic: poll: retake #30 -> not registered (retake 1 of 2)"
    assert ports.tracker.comments == [
        (
            "GH-30",
            "Retaken after crash: run raised under abc123+dirty; resuming under abc123 "
            "(retake 1 of 2)",
        )
    ]
    assert len(launcher.spawned) == 1


def test_a_round_leaves_a_crash_to_the_human_once_its_version_cap_is_spent_and_says_it_each_round():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _crash_comments("abc123", "abc123")
    launcher = FakeLauncher()
    locks = _Locks(_crashed_lock("GH-30"))
    kw = {"lock": locks, "system_version": lambda: "abc123+dirty"}  # same key as the clean sha
    first = _round(_cfg("/p"), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, **kw)
    second = _round(_cfg("/p"), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, **kw)
    assert first == second
    assert len(first) == 1
    assert first[0].startswith(
        "agentic: poll: #30 crashed, crash retake cap spent for this system version (lock pid 777"
    )
    assert first[0].endswith("left to the human: agentic resume --issue 30")
    assert launcher.spawned == [] and ports.tracker.comments == []


def test_the_same_comments_under_a_new_version_retake_again():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _crash_comments("abc123", "abc123")
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_crashed_lock("GH-30", "abc123")),
        system_version=lambda: "def456",
    )
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"
    assert "resuming under def456 (retake 1 of 2)" in ports.tracker.comments[0][1]


def test_a_total_of_six_crash_retakes_across_versions_goes_to_the_human():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _crash_comments("a1", "a1", "b2", "b2", "c3", "c3")
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_crashed_lock("GH-30")),
        system_version=lambda: "d4",
    )
    assert len(lines) == 1
    assert lines[0].startswith("agentic: poll: #30 crashed, crash retake cap spent (lock pid 777")
    assert launcher.spawned == [] and ports.tracker.comments == []


def test_a_human_comment_resets_both_crash_counts():
    ports = memory_ports()
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = [
        *_crash_comments("abc123", "abc123"),
        IssueComment(body="try again", created_at="2026-10-05T10:00:00Z"),
    ]
    (_, _), crash = _gather_crash(ports, _Locks(_crashed_lock("GH-30")))
    assert crash == {30: poll.CrashRetake(0, "abc123", "abc123")}


@pytest.mark.parametrize("version", ["unknown", "unknown+dirty", None, ""])
def test_a_crash_under_an_unknown_system_version_goes_to_the_human_with_no_comment(version):
    ports = memory_ports()
    _open_task(ports)
    launcher = FakeLauncher()
    lines = _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=_Locks(_crashed_lock("GH-30")),
        system_version=lambda: version,
    )
    assert len(lines) == 1 and "#30 crashed, system version unknown (lock pid 777" in lines[0]
    assert launcher.spawned == [] and ports.tracker.comments == []


def test_a_crashed_record_of_another_boot_with_an_unknown_version_falls_to_the_reboot_retake():
    ports = memory_ports()
    _open_task(ports)
    launcher = FakeLauncher()
    locks = _Locks(_dead_lock("GH-30", boot_id="boot-old", crashed_version="abc123"))
    _round(
        _cfg("/p"),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        lock=locks,
        system_version=lambda: "unknown",
    )
    assert ports.tracker.comments == [("GH-30", "Retaken after reboot: previous boot")]


def test_gather_retakes_reaps_a_same_boot_crash_left_to_the_human():
    reaper = FakeReaper(reaped=_LEFT, reaped_runs=_LEFT)
    ports = memory_ports(reaper=reaper)
    _open_task(ports)
    ports.tracker.timestamped["GH-30"] = _crash_comments("abc123", "abc123")
    (_, lines), _ = _gather_crash(ports, _Locks(_crashed_lock("GH-30")))
    assert reaper.calls == [("run", "GH-30", 777, None), ("run", "check", 777, None)]
    assert lines[0].endswith(
        "; reaped 2 processes: `uv run sample_app.py` (kill), `uv run sample_app.py` (kill)"
    )


def test_a_crash_mark_on_a_task_that_is_not_interrupted_retakes_nothing():
    ports = memory_ports()
    _open_task(ports, "GH-30", status="ready_for_review")
    _open_task(ports, "GH-31", escalated="Developer failed")
    locks = _Locks({**_crashed_lock("GH-30"), **_crashed_lock("GH-31")})
    (result, crash) = _gather_crash(ports, locks)
    assert result == ([], []) and crash == {}
