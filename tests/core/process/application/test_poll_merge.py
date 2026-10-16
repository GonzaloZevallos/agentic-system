"""GH-178: the poller's automatic-merge slice -- `gather_merges` over the document, the
tracker and the upstream sha; `run_once`/`execute` spawning `agentic merge`; and the two
comment prefixes the system posts reading as its own, never as a human's decision. All on
in-memory ports and the `Launcher` fake: no `gh`, no `Popen`, no worktree."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, get_args

from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import landing, landing_sync, merge, poll, resume
from agentic.core.process.application.ports import (
    IssueComment,
    LockRecord,
    RunRegistration,
    Upstream,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.landing import LandingStepKind
from agentic.core.process.domain.task import PullRequest
from tests.core.process.application.test_poll import (
    ESCALATED_AT,
    _cfg,
    _Locks,
    _no_sleep,
    _paused,
    _round,
)
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryTracker, memory_ports

HEAD = "a" * 40
MASTER = "b" * 40
URL = "https://github.com/o/r/pull/{}"


def _pr(state: Literal["OPEN", "MERGED", "CLOSED"] = "OPEN") -> PullRequest:
    return PullRequest(
        state=state,
        created_at="2026-10-05T00:00:00Z",
        merged_at=None,
        commits=(),
        head_oid=HEAD,
    )


def _ports(*numbers: int, checks: bool = True, **more):
    """One approved, delivered task per number with its open pull request, upstream at
    `MASTER`, and (unless `checks` is False) the checks recorded green on that head and
    master: every one of them a `merge` for `gather_merges`."""
    ports = memory_ports(tracker=MemoryTracker())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    # GH-255: a head that merges cleanly with master, as every test here assumed before the
    # probe existed; a test of the probe scripts its own answer (`None` is unread).
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    for number in numbers:
        _ready(ports, number, checks=checks, **more)
    return ports


def _ready(ports, number: int, checks: bool = True, **more) -> None:
    url = URL.format(number)
    ports.tracker.prs[url] = _pr()
    document = {
        "id": f"GH-{number}",
        "status": "ready_for_review",
        "pull_request": url,
        "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
        **more,
    }
    if checks:
        document["merge_checks"] = {"head": HEAD, "master": MASTER, "passed": True}
    ports.documents.save(document)


def _merges(ports, eval_set=frozenset(), master: str | None = MASTER, lock=None):
    lines: list[str] = []
    found = poll.gather_merges(ports, eval_set, master, lock or _Locks(), lines)
    return found, lines


def _merge_argv(number: int, tmp_path) -> list[str]:
    return ["agentic", "merge", "--issue", str(number), "--product-dir", str(tmp_path)]


def _finding(severity: str) -> dict[str, str]:
    return {
        "location": "a.py:1",
        "claim": "wrong",
        "severity": severity,
        "status": "open",
        "scope": "subtask",
        "subtask": "only",
    }


def test_a_ready_task_with_green_checks_is_a_candidate():
    assert _merges(_ports(5)) == ([5], [])


def test_a_ready_task_with_no_checks_yet_is_a_candidate_to_check():
    assert _merges(_ports(5, checks=False))[0] == [5]


def test_the_pr_head_and_the_reviewed_head_must_agree():
    ports = _ports(5)
    ports.tracker.head_oid = "c" * 40
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: head_not_reviewed"]


def test_a_refusal_names_its_reason_in_one_line_per_issue():
    ports = _ports(5, 6)
    ports.documents.record_closure("GH-6", {"status": "developing"})
    found, lines = _merges(ports)
    assert found == [5]
    assert lines == ["agentic: poll: auto-merge #6 -> left to the human: not_ready"]


def test_an_open_important_finding_refuses():
    ports = _ports(5)
    ports.documents.record_closure("GH-5", {"findings": [_finding("important")]})
    assert _merges(ports)[1] == ["agentic: poll: auto-merge #5 -> left to the human: open_findings"]


def test_an_open_blocking_finding_refuses_and_an_observation_does_not():
    blocked = _ports(5)
    blocked.documents.record_closure("GH-5", {"findings": [_finding("blocking")]})
    assert _merges(blocked)[0] == []
    noted = _ports(5)
    noted.documents.record_closure("GH-5", {"findings": [_finding("observation")]})
    assert _merges(noted)[0] == [5]


def test_a_task_without_a_pull_request_or_already_closed_is_skipped_without_a_line():
    ports = _ports(5, 6)
    ports.documents.record_closure("GH-6", {"closed_at": "2026-10-05T00:00:00Z"})
    ports.documents.save({"id": "GH-7", "status": "developing"})
    assert _merges(ports) == ([5], [])


def test_a_merged_pull_request_is_refused_as_not_open():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("MERGED")
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: pr_not_open"]


def test_a_closed_pull_request_is_no_candidate_and_prints_no_line():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert _merges(ports) == ([], [])


def test_a_closed_pull_request_with_an_open_blocking_finding_prints_no_open_findings():
    ports = _ports(5)
    ports.documents.record_closure("GH-5", {"findings": [_finding("blocking")]})
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert _merges(ports) == ([], [])


def test_a_closed_pull_request_is_skipped_whatever_the_status_but_an_open_one_is_not_ready():
    closed = _ports(5, status="waiting_on_human")
    closed.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert _merges(closed) == ([], [])
    open_ = _ports(5, status="waiting_on_human")
    assert _merges(open_) == (
        [],
        ["agentic: poll: auto-merge #5 -> left to the human: not_ready"],
    )


def test_a_reopened_pull_request_is_a_candidate_again_on_the_next_read():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert _merges(ports) == ([], [])
    ports.tracker.prs[URL.format(5)] = _pr("OPEN")
    assert _merges(ports) == ([5], [])


def test_the_poller_writes_nothing_to_the_document_of_a_closed_pull_request():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    before = ports.documents.load("GH-5")
    _merges(ports)
    assert ports.documents.load("GH-5") == before


def test_the_close_sweep_does_not_list_a_task_whose_pull_request_closed_unmerged():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert poll.gather_closes(ports) == []


def test_an_unreadable_pull_request_is_refused_as_not_open_not_a_crash():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = None
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: pr_not_open"]


def test_a_pull_request_read_that_raises_oserror_still_prints_the_not_open_line():
    def raising(reference):
        pull_request_number(reference)
        raise OSError("gh missing")

    ports = _ports(5)
    ports.tracker.pull_request = raising
    assert _merges(ports) == (
        [],
        ["agentic: poll: auto-merge #5 -> left to the human: pr_not_open"],
    )


def test_the_verb_on_a_closed_pull_request_with_an_open_blocking_finding_refuses_it_as_not_open(
    tmp_path,
):
    ports = _ports(5)
    ports.documents.record_closure("GH-5", {"findings": [_finding("blocking")]})
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    logged: list[str] = []
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        logged.append,
    )
    assert result == merge.MergeResult("refused", "pr_not_open")
    assert ports.tracker.comments == []
    assert ports.workspace.detached == []
    assert ports.commands.ran == []
    assert logged == []


def test_an_issue_in_the_eval_set_is_never_a_candidate():
    found, lines = _merges(_ports(5, 6), eval_set=frozenset({5}))
    assert found == [6]
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: eval_set"]


def test_an_unreadable_eval_set_refuses_every_issue():
    found, lines = _merges(_ports(5, 6), eval_set=None)
    assert found == []
    assert [line.rsplit(": ", 1)[1] for line in lines] == ["eval_set_unreadable"] * 2


def test_a_live_lock_skips_the_issue_without_a_line():
    assert _merges(_ports(5, 6), lock=_Locks(alive={"GH-5"})) == ([6], [])


def test_attempts_count_only_for_the_current_head():
    stale = {"merge_checks": {"head": HEAD, "master": "d" * 40, "passed": True}}
    spent = {"head": HEAD, "count": rules.MERGE_ATTEMPTS_MAX}
    ports = _ports(5, checks=False, **stale, merge_attempts=spent)
    assert _merges(ports)[1] == [
        "agentic: poll: auto-merge #5 -> left to the human: attempts_exhausted"
    ]
    other = _ports(5, checks=False, **stale, merge_attempts={**spent, "head": "c" * 40})
    assert _merges(other)[0] == [5]


def _facts_of(raw):
    return poll._merge_facts("GH-5", raw, _pr(), frozenset(), 5, MASTER)


def test_merge_failures_read_as_zero_when_absent_or_for_another_head():
    assert _facts_of({}).merge_failures == 0
    assert _facts_of({}).merge_failure is None
    other = {"merge_failures": {"head": "c" * 40, "count": 2, "failure": "gh_rejected"}}
    assert (_facts_of(other).merge_failures, _facts_of(other).merge_failure) == (0, None)
    mine = {"merge_failures": {"head": HEAD, "count": 2, "failure": "gh_rejected"}}
    assert (_facts_of(mine).merge_failures, _facts_of(mine).merge_failure) == (2, "gh_rejected")


def test_an_invalid_failure_class_or_count_reads_as_none_and_zero():
    invalid = {"merge_failures": {"head": HEAD, "count": 2, "failure": "boom: stderr text"}}
    assert (_facts_of(invalid).merge_failures, _facts_of(invalid).merge_failure) == (2, None)
    odd = {"merge_failures": {"head": HEAD, "count": "2", "failure": "gh_rejected"}}
    assert _facts_of(odd).merge_failures == 0
    assert _facts_of({"merge_failures": "x"}).merge_failures == 0


def test_a_spent_merge_budget_for_the_current_head_proposes_no_merge():
    spent = {"head": HEAD, "count": rules.MERGE_CALLS_MAX, "failure": "gh_rejected"}
    found, lines = _merges(_ports(5, merge_failures=spent))
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: merge_failed"]
    again = _ports(5, merge_failures={**spent, "head": "c" * 40})
    assert _merges(again)[0] == [5]


def test_an_unknown_upstream_leaves_every_issue_alone():
    assert _merges(_ports(5), master=None) == ([], [])


def test_a_round_spawns_the_merge_child_and_waits_for_it_to_register(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()
    registry = MemoryRunRegistry()
    lines = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    assert launcher.spawned == [_merge_argv(5, tmp_path)]
    assert lines == ["agentic: poll: merge #5 -> pid 4242"]
    assert registry.attempts == {}  # no `record_launch`: that calendar is the launch's
    assert ports.tracker.comments == []


def test_two_rounds_with_the_merge_childs_lock_alive_spawn_exactly_once(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()
    registry = MemoryRunRegistry()
    cfg = _cfg(tmp_path)
    _round(cfg, ports, registry, launcher, ESCALATED_AT)
    locks = _Locks(alive={"GH-5"})
    lines = _round(cfg, ports, registry, launcher, ESCALATED_AT, lock=locks)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert len(launcher.spawned) == 1


def test_with_no_slot_left_the_merge_is_still_spawned(tmp_path):
    # Was `test_with_no_slot_left_no_merge_is_spawned` (GH-263): the merge child runs no
    # model, so a full cap does not hold it.
    ports = _ports(5)
    registry = MemoryRunRegistry(entries=[RunRegistration(1, "h", "other", "GH-1")])
    launcher = FakeLauncher()
    _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert launcher.spawned == [_merge_argv(5, tmp_path)]


def test_a_live_exempt_child_of_another_product_holds_the_merge_back(tmp_path):
    # Invariant 7 (GH-263): one exempt child live on the machine, whatever its product.
    ports = _ports(5)
    live = RunRegistration(1, "h", "other", "GH-1", kind="merge")
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(entries=[live]), launcher, ESCALATED_AT)
    assert launcher.spawned == []
    assert lines != ["agentic: poll: merge #5 -> pid 4242"]


def test_a_live_role_child_does_not_hold_the_merge_back_as_an_exempt_one(tmp_path):
    ports = _ports(5)
    live = RunRegistration(1, "h", "other", "GH-1", kind="role")
    launcher = FakeLauncher()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(entries=[live]), launcher, ESCALATED_AT)
    assert launcher.spawned == [_merge_argv(5, tmp_path)]


def test_an_eval_set_issue_is_never_spawned(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()
    _round(
        _cfg(tmp_path),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        eval_set=frozenset({5}),
    )
    assert launcher.spawned == []


def test_an_unreadable_eval_set_spawns_nothing(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, eval_set=None)
    assert launcher.spawned == []


def test_a_round_lists_every_refusal_it_left_to_the_human(tmp_path):
    ports = _ports(5, 6)
    ports.documents.record_closure("GH-6", {"status": "developing"})
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert lines == [
        "agentic: poll: auto-merge #6 -> left to the human: not_ready",
        "agentic: poll: merge #5 -> pid 4242",
    ]


def test_a_round_with_no_task_waiting_never_asks_for_the_upstream(tmp_path):
    ports = memory_ports()
    ports.documents.save({"id": "GH-5", "status": "developing"})
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert ports.workspace.upstream_calls == 0


def test_one_merge_per_round_the_lowest_issue(tmp_path):
    ports = _ports(7, 5)
    launcher = FakeLauncher()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)
    assert launcher.spawned == [_merge_argv(5, tmp_path)]


def test_a_merge_whose_spawn_fails_is_a_line_and_not_the_end_of_the_round(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher(fails={0})
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT)
    assert lines == ["agentic: poll: merge #5 -> not launched: [Errno 2] No such file or directory"]
    assert ports.tracker.comments == []


def test_execute_spawns_the_merge_verb_and_never_comments(tmp_path):
    ports = memory_ports()
    launcher = FakeLauncher()
    lines = poll.execute(
        [rules.PollAction("merge", 5)],
        _cfg(tmp_path),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        "agentic",
        register_timeout_s=3.0,
        sleep=_no_sleep,
    )
    assert launcher.spawned == [_merge_argv(5, tmp_path)]
    assert lines == ["agentic: poll: merge #5 -> pid 4242"]
    assert ports.tracker.comments == []


# --- the two comment prefixes ----------------------------------------------------------


def test_both_prefixes_start_the_way_the_plan_says_and_are_system_comments():
    assert poll.AUTO_MERGE_REFUSED == "Auto-merge refused: "
    assert poll.AUTO_MERGED == "Auto-merged: "
    assert poll.AUTO_MERGE_REFUSED in resume.SYSTEM_COMMENTS
    assert poll.AUTO_MERGED in resume.SYSTEM_COMMENTS


def test_human_decision_reads_past_a_refusal_and_a_merge_comment():
    comments = [
        "Escalated: Developer failed",
        "Auto-merge refused: open_findings",
        "Auto-merged: squash",
        "do it again",
    ]
    assert resume.human_decision(comments) == "do it again"
    assert resume.human_decision(comments[:3]) is None


def _stamped(body: str, minute: int) -> IssueComment:
    return IssueComment(body=body, created_at=f"2026-10-02T19:{minute}:00Z")


def _account_limit_pause():
    ports = memory_ports()
    ports.documents.save(
        _paused(
            "GH-19",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    return ports


ESCALATION = _stamped("Escalated: Developer failed 2 times: API Error: 429", 30)


def test_a_refusal_comment_is_not_a_human_voice_so_the_pause_still_resumes():
    ports = _account_limit_pause()
    ports.tracker.timestamped["GH-19"] = [ESCALATION, _stamped("Auto-merge refused: eval_set", 31)]
    assert [c.issue for c in poll.gather_resumes(ports, ESCALATED_AT.replace(day=3))] == [19]


def test_a_human_comment_in_the_same_place_does_block_the_resume():
    ports = _account_limit_pause()
    ports.tracker.timestamped["GH-19"] = [ESCALATION, _stamped("wait, I'm looking", 31)]
    assert poll.gather_resumes(ports, ESCALATED_AT.replace(day=3)) == []


def test_a_task_waiting_for_its_merge_is_neither_retaken_nor_resumed():
    ports = _ports(5)
    ports.tracker.timestamped["GH-5"] = [_stamped("Auto-merge refused: eval_set", 31)]
    assert poll.gather_resumes(ports, ESCALATED_AT.replace(day=3)) == []
    record = LockRecord(1, "h", "2026-10-05T08:00:00Z", "agentic merge", "boot-old")
    assert poll.gather_retakes(ports, _Locks({"GH-5": record}), "boot-now") == ([], [])


def test_a_ready_task_with_an_open_pull_request_reaches_the_decision_and_the_upstream(tmp_path):
    ports = _ports(5)
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert ports.workspace.upstream_calls == 1
    assert lines == ["agentic: poll: merge #5 -> pid 4242"]


def test_a_task_not_ready_still_reaches_the_decision_which_refuses_it_by_name(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure("GH-5", {"status": "developing"})
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), FakeLauncher(), ESCALATED_AT)
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: not_ready"]


def test_a_tracker_that_raises_reading_the_pull_request_is_a_refusal_with_its_line():
    def raising(reference):
        pull_request_number(reference)
        raise ValueError("gh failed")

    ports = _ports(5)
    ports.tracker.pull_request = raising
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: pr_not_open"]


def test_a_short_reviewed_head_against_the_full_head_oid_is_a_merge_candidate():
    ports = _ports(
        7, subtasks={"only": {"id": "only", "status": "approved", "reviewed_head": HEAD[:7]}}
    )
    found, lines = _merges(ports)
    assert found == [7]
    assert lines == []


def test_a_reviewed_head_of_another_commit_is_not_a_merge_candidate():
    ports = _ports(
        7, subtasks={"only": {"id": "only", "status": "approved", "reviewed_head": "c" * 7}}
    )
    found, lines = _merges(ports)
    assert found == []
    assert any("head_not_reviewed" in line for line in lines)


# -- the landing (GH-239): `land` and the poller's `land` action ---------------------


class _HeldLocks:
    """The `RunLock` `land` takes: acquire and release are recorded, nothing is alive."""

    def __init__(self):
        self.taken: list[str] = []
        self.released: list[str] = []

    def acquire(self, task_id, command):
        self.taken.append(task_id)

    def release(self, task_id):
        self.released.append(task_id)


def _unapproved(ports, number: int) -> None:
    """A delivered task whose reviewed unit is not approved: `not_approved`, whose step is
    `re_review`."""
    _ready(
        ports,
        number,
        subtasks={"only": {"id": "only", "status": "delivered", "reviewed_head": HEAD}},
    )


class _LandingLauncher(FakeLauncher):
    """Runs `land` in-process for each `agentic land` argv it is asked to spawn, with a
    stub in place of the executor, so a second tick sees what the first one wrote."""

    def __init__(self, ports, tmp_path, live):
        super().__init__()
        self.ports, self.tmp_path, self.live = ports, tmp_path, live
        self.results = []

    def spawn(self, argv):
        pid = super().spawn(argv)
        if argv[1] == "land":
            self.results.append(
                landing.land(
                    f"GH-{argv[3]}",
                    _cfg(self.tmp_path),
                    self.ports,
                    _HeldLocks(),
                    Upstream("origin/master", MASTER, None),
                    frozenset(),
                    lambda line: None,
                    clock=lambda: "2026-10-14T00:00:00+00:00",
                    live=self.live,
                )
            )
        return pid


def test_two_ticks_with_a_non_inert_dispatcher_spawn_land_once_and_record_one_entry(
    tmp_path, monkeypatch
):
    dispatched = []

    def re_review(context):
        dispatched.append(context)
        landing_sync.spawn_and_mark(context)  # what the real step does once it reopened

    monkeypatch.setattr(landing_sync, "re_review", re_review)
    live = frozenset({"re_review"})
    ports = _ports()
    _unapproved(ports, 5)
    launcher = _LandingLauncher(ports, tmp_path, live)
    registry = MemoryRunRegistry()

    first = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT, live_steps=live)
    second = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT, live_steps=live)

    assert first[-1] == "agentic: poll: land #5 -> pid 4242"
    assert "land #5" not in "".join(second)
    assert [argv[1] for argv in launcher.spawned] == ["land"]
    [entry] = (ports.documents.load("GH-5") or {})["landing"]
    assert entry == {
        "head": HEAD,
        "step": "re_review",
        "refusal": "not_approved",
        "at": "2026-10-14T00:00:00+00:00",
    }
    assert len(dispatched) == 1 and dispatched[0].step.kind == "re_review"


def test_an_inert_step_is_neither_recorded_nor_spawned_and_the_log_is_unchanged(tmp_path):
    ports = _ports()
    _unapproved(ports, 5)
    launcher = FakeLauncher()
    lines = _round(
        _cfg(tmp_path),
        ports,
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        live_steps=frozenset(),
    )
    assert launcher.spawned == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: not_approved"]
    assert "landing" not in (ports.documents.load("GH-5") or {})


def test_wait_and_none_spawn_nothing_even_when_every_kind_is_live(tmp_path):
    ports = _ports()
    _ready(ports, 5, status="merged")  # not_ready: step none
    launcher = FakeLauncher()
    everything = frozenset(get_args(LandingStepKind))
    _round(
        _cfg(tmp_path), ports, MemoryRunRegistry(), launcher, ESCALATED_AT, live_steps=everything
    )
    assert launcher.spawned == []


def test_a_head_that_moved_between_the_decision_and_the_dispatch_writes_nothing(
    tmp_path, monkeypatch
):
    dispatched = []

    def re_review(context):
        dispatched.append(context)
        landing_sync.spawn_and_mark(context)  # what the real step does once it reopened

    monkeypatch.setattr(landing_sync, "re_review", re_review)

    class Moving(MemoryTracker):
        reads = 0

        def pull_request(self, reference):
            pull_request_number(reference)
            self.reads += 1
            if self.reads > 1:
                self.head_oid = "c" * 40  # pushed after the first read
            return super().pull_request(reference)

    ports = memory_ports(tracker=Moving())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    _unapproved(ports, 5)
    before = dict(ports.documents.load("GH-5") or {})
    logged: list[str] = []

    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        logged.append,
        live=frozenset({"re_review"}),
    )

    assert result == landing.LandResult("skipped", "head_moved", "re_review")
    assert ports.documents.load("GH-5") == before
    assert dispatched == [] and any("head moved" in line for line in logged)


def test_land_takes_the_run_lock_and_releases_it_even_when_it_stops(tmp_path):
    ports = _ports()
    locks = _HeldLocks()
    result = landing.land(
        "GH-9", _cfg(tmp_path), ports, locks, Upstream("o", MASTER, None), frozenset(), print
    )
    assert result.kind == "stopped" and result.reason == "no_pull_request"
    assert locks.taken == ["GH-9"] and locks.released == ["GH-9"]


def test_a_second_land_of_the_same_head_and_step_is_skipped_without_a_write(tmp_path, monkeypatch):
    dispatched = []

    def re_review(context):
        dispatched.append(context)
        landing_sync.spawn_and_mark(context)  # what the real step does once it reopened

    monkeypatch.setattr(landing_sync, "re_review", re_review)
    ports = _ports()
    _unapproved(ports, 5)
    args = (
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("o", MASTER, None),
        frozenset(),
        lambda line: None,
    )
    live = frozenset({"re_review"})
    assert landing.land(*args, live=live, resume=lambda number: 4242).kind == "dispatched"
    again = landing.land(*args, live=live, resume=lambda number: 4242)
    assert again == landing.LandResult("skipped", "already_dispatched", "re_review")
    assert len(dispatched) == 1 and len((ports.documents.load("GH-5") or {})["landing"]) == 1


def test_poll_plan_orders_at_most_one_model_land_into_a_spare_slot_before_the_merge():
    # Was `...into_a_spare_slot_after_the_merge` (GH-263): the merge is exempt and goes
    # last; a land that can spend a model still needs a slot of its own.
    now = ESCALATED_AT
    lands = [rules.LandCandidate(9, "land_model"), rules.LandCandidate(4, "land_model")]
    plan = rules.poll_plan(
        closes=[], resumes=[], launches=[], available_slots=2, now=now, merges=[7], lands=lands
    ).actions
    assert plan == [rules.PollAction("land", 4), rules.PollAction("merge", 7)]
    tight = rules.poll_plan(
        closes=[],
        resumes=[],
        launches=[],
        available_slots=0,
        now=now,
        merges=[7],
        lands=lands[1:],
    )
    assert tight.actions == [rules.PollAction("merge", 7)]
    assert tight.deferred == [rules.Deferred("land", 4, "no_slot")]


# ---- a reopened landing that lost its resume child (GH-242) ---------------------------------

REOPENED = {"head": HEAD, "step": "re_review", "refusal": "head_not_reviewed", "at": "t"}


def _stuck(**entry):
    """Task 5 as GH-227 left it: `developing`, its unit back at its review, the landing
    entry on disk and no child running."""
    return _ports(
        5,
        status="developing",
        landing=[{**REOPENED, **entry}],
        subtasks={"only": {"id": "only", "status": "delivered", "reviewed_head": HEAD}},
    )


def _lands(ports, now):
    lands: list[rules.LandCandidate] = []
    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands, now=now)
    return [c.issue for c in lands]


def test_a_not_ready_task_whose_reopened_landing_owes_a_resume_is_a_land_candidate():
    assert _lands(_stuck(), datetime(2026, 10, 14, tzinfo=UTC)) == [5]


def test_a_not_ready_task_whose_resume_was_spawned_a_moment_ago_is_not_a_land_candidate():
    ports = _stuck(resume_spawns=1, resume_spawned_at="2026-10-14T00:00:00+00:00")
    assert _lands(ports, datetime(2026, 10, 14, 0, 1, tzinfo=UTC)) == []
    assert _lands(ports, datetime(2026, 10, 14, 0, 6, tzinfo=UTC)) == [5]


def test_a_not_ready_task_with_no_open_landing_is_not_a_land_candidate():
    ports = _ports(5, status="developing")
    assert _lands(ports, datetime(2026, 10, 14, tzinfo=UTC)) == []


def test_a_closed_pull_request_with_an_owed_resume_is_skipped_before_the_candidate_check():
    ports = _stuck()
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    assert _lands(ports, datetime(2026, 10, 14, tzinfo=UTC)) == []
