"""GH-178: the poller's automatic-merge slice -- `gather_merges` over the document, the
tracker and the upstream sha; `run_once`/`execute` spawning `agentic merge`; and the two
comment prefixes the system posts reading as its own, never as a human's decision. All on
in-memory ports and the `Launcher` fake: no `gh`, no `Popen`, no worktree."""

from __future__ import annotations

from typing import Literal

from agentic.core.process.application import poll, resume
from agentic.core.process.application.ports import (
    IssueComment,
    LockRecord,
    RunRegistration,
    Upstream,
)
from agentic.core.process.domain import rules
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


def test_a_closed_pull_request_is_refused():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: pr_not_open"]


def test_an_unreadable_pull_request_is_a_refusal_not_a_crash():
    ports = _ports(5)
    ports.tracker.prs[URL.format(5)] = None
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: head_not_reviewed"]


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


def test_with_no_slot_left_no_merge_is_spawned(tmp_path):
    ports = _ports(5)
    registry = MemoryRunRegistry(entries=[RunRegistration(1, "h", "other", "GH-1")])
    launcher = FakeLauncher()
    _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher, ESCALATED_AT)
    assert launcher.spawned == []


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
        raise ValueError("gh failed")

    ports = _ports(5)
    ports.tracker.pull_request = raising
    found, lines = _merges(ports)
    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: head_not_reviewed"]
