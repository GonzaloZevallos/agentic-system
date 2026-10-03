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
    assert poll.run_once(cfg, ports, registry, launcher, too_soon) == [
        "agentic: poll: nothing to do this round"
    ]
    assert launcher.spawned == []

    due = ESCALATED_AT + timedelta(minutes=16)  # AUTO_RESUME_SCHEDULE[0] == 15
    lines = poll.run_once(cfg, ports, registry, launcher, due)
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
    assert poll.run_once(_cfg(tmp_path), ports, registry, launcher, well_past) == [
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
    lines = poll.run_once(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == []


def test_an_issue_with_every_blocker_closed_launches(tmp_path):
    ports = memory_ports()
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("CLOSED", "NOT_PLANNED", None, None)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = poll.run_once(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
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
    lines = poll.run_once(
        _cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT
    )
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

    lines = poll.run_once(cfg, ports, registry, launcher, ESCALATED_AT)
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
    lines = poll.run_once(cfg, ports, registry, launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert len(launcher.spawned) == 1
