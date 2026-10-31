"""GH-216: the merge verb and the poller's gather when the product declares its CI,
through `merge_task` and `gather_merges` on in-memory ports and a fake tracker."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.presubmit import CiCover
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _merges, _ports
from tests.fakes import MemoryStack


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    """These tests pin the merge verb's refusal evidence, not the landing: no step is live,
    so a refusal stays the single comment it was. A test that wants one patches it itself."""
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


RUN = "https://github.com/o/r/actions/runs/{}"


def _ago(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _report(*checks: CiCheck, head: str = HEAD, base: str = MASTER, opened: str | None = None):
    return CiReport(head, "master", base, opened if opened is not None else _ago(60), checks)


def _check(name="web", conclusion: str | None = "SUCCESS", run=1) -> CiCheck:
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    return CiCheck(name, status, conclusion, RUN.format(run))


def _world(*reports, declared=("web",), **document):
    ports = dataclasses.replace(
        _ports(5, checks=False, **document),
        stack=MemoryStack(checks={"tests": "run tests"}, ci=declared),
    )
    ports.tracker.ci_reports = list(reports)
    return ports


def _run(ports, tmp_path):
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
    return result, logged


def _doc(ports):
    return ports.documents.load("GH-5") or {}


def test_a_green_fresh_ci_merges_without_a_worktree_a_setup_or_a_local_check(tmp_path):
    ports = _world(_report(_check()))

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert ports.workspace.detached == [] and ports.commands.ran == []
    document = _doc(ports)
    assert document["auto_merge_via"] == "ci"
    assert document["auto_merge_ci"] == [{"name": "web", "link": RUN.format(1)}]
    assert "merge_checks" not in document and "merge_attempts" not in document
    [(_, comment)] = ports.tracker.comments
    assert comment.startswith("Auto-merged: ") and RUN.format(1) in comment


def test_the_merge_comment_lists_the_task_important_rows_accepted_without_a_fix(tmp_path):
    row = {
        "location": "a.py:1",
        "claim": "wrong",
        "severity": "important",
        "status": "open",
        "scope": "subtask",
        "subtask": "only",
        "category": "standard-other",
    }
    pass_done = [{"step": "fix_important", "resolved_at": "2026-10-14T00:00:00+00:00"}]
    ports = _world(_report(_check()), findings=[row], landing=pass_done)

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    [(_, comment)] = ports.tracker.comments
    assert comment.startswith("Auto-merged: ")
    assert "Accepted without a fix:\n- `a.py:1`: wrong" in comment


def test_the_merge_comment_has_no_accepted_section_without_open_important_rows(tmp_path):
    outside = {
        "location": "a.py:1",
        "claim": "wrong",
        "severity": "important",
        "status": "open",
        "scope": "outside",
        "subtask": "only",
        "category": "standard-other",
    }
    ports = _world(_report(_check()), findings=[outside], followups_filed=True)

    _run(ports, tmp_path)

    [(_, comment)] = ports.tracker.comments
    assert "Accepted without a fix" not in comment


def test_a_green_ci_on_an_older_master_runs_the_local_checks_once_when_contains_says_no(tmp_path):
    ports = _world(_report(_check(), base="c" * 40))
    ports.workspace.contains_answer = "no"

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert len(ports.workspace.detached) == 1
    assert [command for _, command in ports.commands.ran] == ["run tests"]
    assert _doc(ports)["auto_merge_via"] == "local"
    assert "auto_merge_ci" not in _doc(ports)
    assert ports.workspace.contains_calls == [(HEAD, MASTER)]


def test_a_green_ci_on_an_older_master_that_the_head_contains_merges_on_the_ci(tmp_path):
    ports = _world(_report(_check(), base="c" * 40))
    ports.workspace.contains_answer = "yes"

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged" and ports.workspace.detached == []
    assert _doc(ports)["auto_merge_via"] == "ci"


def test_a_contains_that_cannot_be_read_counts_as_stale(tmp_path):
    ports = _world(_report(_check(), base="c" * 40))
    ports.workspace.contains_answer = "unknown"

    _run(ports, tmp_path)

    assert len(ports.workspace.detached) == 1


def test_a_red_ci_is_refused_naming_the_job_and_its_link_and_is_commented_once(tmp_path):
    red = _report(_check("web", "FAILURE", run=7), _check("e2e", "SUCCESS", run=8))
    ports = _world(red, declared=("web", "e2e"))

    result, _ = _run(ports, tmp_path)
    _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_failed")
    assert ports.workspace.detached == [] and ports.tracker.merge_calls == []
    [(_, comment)] = ports.tracker.comments
    lines = comment.splitlines()
    assert lines[0] == f"Auto-merge refused: {HEAD} ci_failed"
    assert f"web FAILURE {RUN.format(7)}" in comment and "e2e" not in comment
    assert "```" not in comment


def test_a_pending_ci_waits_with_no_write_no_comment_and_no_counter(tmp_path):
    ports = _world(_report(_check("web", None)))
    before = dict(_doc(ports))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("stopped", "ci_wait")
    assert _doc(ports) == before
    assert ports.tracker.comments == [] and ports.workspace.detached == []


def test_a_head_that_is_not_the_one_ci_ran_on_waits(tmp_path):
    ports = _world(_report(_check(), head="c" * 40))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("stopped", "ci_wait")
    assert ports.tracker.comments == [] and ports.tracker.merge_calls == []


def test_an_unreadable_report_for_several_rounds_then_green_merges(tmp_path):
    ports = _world(CiUnreadable("gh_unavailable"), CiUnreadable("gh_unreadable"), _report(_check()))
    before = dict(_doc(ports))

    first, _ = _run(ports, tmp_path)
    second, _ = _run(ports, tmp_path)
    assert (first.reason, second.reason) == ("ci_wait", "ci_wait")
    assert _doc(ports) == before and ports.tracker.comments == []

    third, _ = _run(ports, tmp_path)
    assert third.kind == "merged"


def test_an_unreadable_report_after_the_bound_names_gh_and_the_cause_never_a_check(tmp_path):
    ports = _world(CiUnreadable("gh_unreadable"), pushed_head=HEAD, pushed_at=_ago(4000))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_unreadable")
    [(_, comment)] = ports.tracker.comments
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} ci_unreadable"
    assert "gh" in comment and "gh_unreadable" in comment and "web" not in comment


def test_a_declared_check_never_reported_is_named_after_the_bound(tmp_path):
    ports = _world(
        _report(_check("other")), declared=("web", "e2e"), pushed_head=HEAD, pushed_at=_ago(4000)
    )

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_missing")
    [(_, comment)] = ports.tracker.comments
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} ci_missing"
    assert "web" in comment and "e2e" in comment and "other" not in comment


def test_the_wait_is_measured_from_the_push_of_this_head_not_from_another(tmp_path):
    old_opening = _ago(10_000)
    pending = _report(_check("web", None), opened=old_opening)
    pushed_other = _world(pending, pushed_head="d" * 40, pushed_at=_ago(10))
    assert _run(pushed_other, tmp_path)[0].kind == "refused"  # the opening's age counts
    pushed_this = _world(pending, pushed_head=HEAD, pushed_at=_ago(10))
    assert _run(pushed_this, tmp_path)[0] == merge.MergeResult("stopped", "ci_wait")


def test_a_product_that_declares_no_ci_writes_neither_key_and_never_asks_for_a_report(tmp_path):
    ports = _world(declared=())

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert "auto_merge_via" not in _doc(ports) and "auto_merge_ci" not in _doc(ports)
    assert ports.tracker.ci_reads == [] and ports.workspace.contains_calls == []
    assert len(ports.workspace.detached) == 1


def test_the_poller_spawns_for_a_green_ci_and_says_nothing(tmp_path):
    ports = _world(_report(_check()))

    assert _merges(ports) == ([5], [])
    assert ports.workspace.contains_calls == []  # the poller never asks git


def test_the_poller_names_what_it_waits_on_and_how_long_of_the_bound():
    pending = _world(_report(_check("web", None)))
    assert _merges(pending) == (
        [],
        ["agentic: poll: auto-merge #5 -> waiting: ci pending web (60s of 3600)"],
    )
    two = _world(
        _report(_check("web", None), _check("api", None, run=2), _check("e2e", run=3)),
        declared=("web", "api", "e2e"),
    )
    assert _merges(two)[1] == [
        "agentic: poll: auto-merge #5 -> waiting: ci pending web, api (60s of 3600)"
    ]
    missing = _world(_report(_check("other")), declared=("web",))
    assert _merges(missing)[1] == [
        "agentic: poll: auto-merge #5 -> waiting: ci missing web (60s of 3600)"
    ]
    unreadable = _world(CiUnreadable("gh_unavailable"), pushed_head=HEAD, pushed_at=_ago(5))
    assert _merges(unreadable)[1] == [
        "agentic: poll: auto-merge #5 -> waiting: ci unreadable gh_unavailable (5s of 3600)"
    ]


def test_the_poller_prints_a_question_mark_when_the_wait_has_no_anchor():
    ports = _world(CiUnreadable("gh_unavailable"))

    found, lines = _merges(ports)

    assert found == []
    assert lines == [
        "agentic: poll: auto-merge #5 -> waiting: ci unreadable gh_unavailable (?s of 3600)"
    ]


def test_the_poller_leaves_a_red_ci_with_the_failing_names_and_a_spent_wait_with_its_reason():
    red = _world(_report(_check("web", "FAILURE")))
    found, lines = _merges(red)
    assert found == [] and lines == [
        "agentic: poll: auto-merge #5 -> left to the human: ci_failed (web)"
    ]
    spent = _world(_report(_check("web", None), opened=_ago(5000)))
    assert _merges(spent)[1] == ["agentic: poll: auto-merge #5 -> left to the human: ci_missing"]


def test_the_poller_reads_no_report_for_a_task_an_earlier_reason_already_refuses():
    ports = _world(_report(_check()), status="developing")

    found, lines = _merges(ports)

    assert found == [] and lines == ["agentic: poll: auto-merge #5 -> left to the human: not_ready"]
    assert ports.tracker.ci_reads == []


def test_a_poll_round_waiting_on_ci_writes_nothing_and_comments_nothing():
    ports = _world(_report(_check("web", None)))
    before = dict(_doc(ports))

    _merges(ports)

    assert _doc(ports) == before and ports.tracker.comments == []


def test_a_stack_the_poller_cannot_read_leaves_that_task_and_never_aborts_the_round():
    ports = _world(_report(_check()))
    ports.stack.ci_error = "no commit 'gone' in product"

    found, lines = _merges(ports)

    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: stack_unreadable"]
    assert ports.tracker.ci_reads == []


def test_a_refusal_whose_comment_fails_is_recorded_once_and_still_refuses(tmp_path):
    # GH-389: the merge child has no state owner; the row goes through the state's reducer
    # and `record_closure`, and repeated rounds stay deduplicated.
    red = _report(_check("web", "FAILURE", run=7), _check("e2e", "SUCCESS", run=8))
    ports = _world(red, declared=("web", "e2e"))
    ports.tracker.comment_failures = 10

    first, _ = _run(ports, tmp_path)
    second, _ = _run(ports, tmp_path)

    assert first == second == merge.MergeResult("refused", "ci_failed")
    assert ports.tracker.comments == []
    rows = ports.documents.closures["GH-5"]["tracker_failures"]
    assert [(r["target"], r["cls"]) for r in rows] == [("comment", "refused")]
    assert "reason" not in rows[0] and set(rows[0]) == {"target", "cls", "at"}


def test_a_merge_whose_notice_fails_is_still_merged_and_records_the_failure(tmp_path):
    ports = _world(_report(_check()))
    ports.tracker.comment_failures = 1

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert ports.tracker.merge_calls  # the merge happened, only its notice did not
    rows = ports.documents.closures["GH-5"]["tracker_failures"]
    assert [(r["target"], r["cls"]) for r in rows] == [("comment", "refused")]


# GH-453: a `full_checks` record that deferred a check to a CI job.
E2E = CiCover("e2e", "✘")


def _deferring(*reports, record_head=HEAD, covers=None, **document):
    record = {
        "version": 1,
        "head": record_head,
        "passed": True,
        "duration_s": 1.0,
        "declared": {"web-e2e": "playwright test"},
        "results": [{"name": "web-e2e", "passed": False, "deferred": True}],
        "witness": None,
        "deferred": ["web-e2e"],
    }
    ports = _world(*reports, full_checks=record, **document)
    ports.stack.declared_ci_covers = {"web-e2e": E2E} if covers is None else covers
    return ports


def test_a_deferral_whose_job_is_green_on_the_head_merges_on_the_ci(tmp_path):
    ports = _deferring(_report(_check(), _check("e2e", run=2)))

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged" and ports.commands.ran == []


def test_a_deferral_whose_job_is_red_is_a_red_ci_check_and_no_merge(tmp_path):
    ports = _deferring(_report(_check(), _check("e2e", "FAILURE", run=2)))

    result, _ = _run(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "ci_failed")
    [(_, comment)] = ports.tracker.comments
    assert "e2e FAILURE " + RUN.format(2) in comment
    assert ports.commands.ran == []


def test_a_deferral_whose_job_is_pending_waits_and_runs_nothing_locally(tmp_path):
    ports = _deferring(_report(_check(), _check("e2e", None, run=2)))

    result, _ = _run(ports, tmp_path)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert ports.commands.ran == [] and ports.workspace.detached == []


@pytest.mark.parametrize("jobs", [(_check("e2e", "SKIPPED", run=2),), ()])
def test_a_deferral_no_job_covers_runs_the_checks_here_and_never_merges_on_the_ci(tmp_path, jobs):
    ports = _deferring(_report(_check(), *jobs))

    result, _ = _run(ports, tmp_path)

    assert ports.commands.ran, "the merge gate ran the checks locally"
    assert _doc(ports).get("auto_merge_via") != "ci"


def test_a_deferral_of_another_head_imposes_nothing(tmp_path):
    ports = _deferring(_report(_check()), record_head="b" * 40)

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged" and ports.commands.ran == []


def test_a_deferred_check_the_stack_no_longer_covers_is_uncovered(tmp_path):
    ports = _deferring(_report(_check(), _check("e2e", run=2)), covers={"x": E2E})

    _run(ports, tmp_path)

    assert ports.commands.ran
