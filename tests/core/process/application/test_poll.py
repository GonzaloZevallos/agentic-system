"""Issue #137: the poller's own scenarios (gather -> rules.poll_plan -> execute),
against in-memory ports and a fixed clock -- never a real `gh`, a real `Popen` or a
real clock. `run_once` is synchronous: no graph, no role, nothing to await."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueComment, IssueState, RunRegistration
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryTracker, memory_ports

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
        return self.prs.get(reference)


def _cfg(tmp_path, **over):
    return Configuration(product_dir=tmp_path, **over)


def _paused(task_id, escalated, pause_log, **more):
    return {"id": task_id, "escalated": escalated, "pause_log": pause_log, **more}


def _no_sleep(seconds: float) -> None:
    """The default `sleep` every test but the ones about the wait itself inject: a
    real `time.sleep` here would make a scenario that never registers block for the
    full `REGISTER_WAIT_S` (Human decision, issue #137)."""


def _round(cfg, ports, registry, launcher, now, **kw):
    kw.setdefault("sleep", _no_sleep)
    return poll.run_once(cfg, ports, registry, launcher, now, **kw)


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

    due = ESCALATED_AT + timedelta(minutes=16)  # AUTO_RESUME_SCHEDULE[0] == 15
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


def test_nothing_launches_when_the_machine_wide_cap_is_full(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    registry = MemoryRunRegistry(
        entries=[RunRegistration(pid=1, host="h", product="other-product", task_id="GH-1")]
    )
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


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
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    slept: list[float] = []

    def counting_sleep(seconds: float) -> None:
        slept.append(seconds)

    lines = poll.run_once(
        _cfg(tmp_path), ports, registry, launcher, ESCALATED_AT, sleep=counting_sleep
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
    first = poll.run_once(cfg, ports, registry, launcher, ESCALATED_AT, sleep=slow_register)
    assert first == ["agentic: poll: launch #5 -> pid 4242"]
    assert registered  # the wait polled until it showed up, not the full 60 s

    second = poll.run_once(cfg, ports, registry, launcher, ESCALATED_AT, sleep=slow_register)
    assert second == ["agentic: poll: nothing to do this round"]
    assert len(launcher.spawned) == 1  # the second round launched nothing more
