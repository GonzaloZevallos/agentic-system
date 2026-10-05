"""Issue #137: the poller's own scenarios (gather -> rules.poll_plan -> execute),
against in-memory ports and a fixed clock -- never a real `gh`, a real `Popen` or a
real clock. `run_once` is synchronous: no graph, no role, nothing to await."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic.core.process.application import poll
from agentic.core.process.application.ports import (
    IssueComment,
    IssueState,
    LaunchAttempt,
    RunRegistration,
    Upstream,
)
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryInbox, MemoryRunRegistry, MemoryTracker, memory_ports

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
    kw.setdefault("argv0", "agentic")
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
        _cfg(tmp_path), ports, registry, launcher, ESCALATED_AT, "agentic", sleep=counting_sleep
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
        cfg, ports, registry, launcher, ESCALATED_AT, "agentic", sleep=slow_register
    )
    assert first == ["agentic: poll: launch #5 -> pid 4242"]
    assert registered  # the wait polled until it showed up, not the full 60 s

    second = poll.run_once(
        cfg, ports, registry, launcher, ESCALATED_AT, "agentic", sleep=slow_register
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
        _cfg(tmp_path), ports, registry, launcher, due, "agentic", sleep=slept.append
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
    failure is the 6th and last the schedule (`AUTO_RESUME_SCHEDULE`, length 6) allows
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
    )
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert launcher.spawned[0][0] == "/venv/bin/agentic"


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
