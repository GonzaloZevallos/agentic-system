"""GH-239, subtask landing-fix: the executors of `fix`, `rerun` and `file_followups_and_land`,
driven through `landing.land` (the verb the poller spawns) on in-memory ports and the fake
launcher: each refusal ends in a filed issue, a reopen with a resume spawn or a rerun, with
no human step."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import landing, landing_fix, landing_plan, poll
from agentic.core.process.application.landing_sync import reopen
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.task import apply
from tests.core.process.application.test_merge_ci import _check, _report
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _finding,
    _HeldLocks,
    _ports,
)
from tests.fakes import FakeLauncher, MemoryStack

NOW = "2026-10-14T00:00:00+00:00"


def _land(ports, tmp_path, launcher=None):
    lines: list[str] = []
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: NOW,
        launcher=launcher,
        argv0="agentic",
    )
    return result, lines


def _doc(ports):
    return ports.documents.load("GH-5") or {}


def _candidates(ports) -> list[int]:
    from tests.core.process.application.test_poll import _Locks

    return poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [])


def test_an_important_finding_files_one_agentic_follow_up_and_the_next_decision_merges(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5", {"findings": [{**_finding("important"), "category": "standard-other"}]}
    )
    assert _candidates(ports) == []

    result, _ = _land(ports, tmp_path)

    assert result.kind == "dispatched" and result.step == "file_followups_and_land"
    [(title, body)] = ports.tracker.opened_issues
    assert title == "Follow-up of #5: open important findings"
    assert ports.tracker.opened_labels == ["agentic"]
    assert body.splitlines()[0] == landing_rules.followup_marker("GH-5", 1)
    assert "a.py:1" in body and "wrong" in body
    assert _doc(ports)["followups_filed"] is True
    assert _candidates(ports) == [5]


def test_a_follow_up_already_open_for_the_task_is_not_filed_twice(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5", {"findings": [{**_finding("important"), "category": "standard-other"}]}
    )
    ports.inbox._open_issues = [("u", landing_rules.followup_marker("GH-5", 1) + "\nold")]

    _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    assert _doc(ports)["followups_filed"] is True


def test_a_task_that_is_itself_a_follow_up_files_nothing_and_writes_the_rows_in_a_comment(tmp_path):
    ports = _ports(5, description=landing_rules.followup_marker("GH-5", 1) + "\nfrom a task")
    ports.documents.record_closure(
        "GH-5", {"findings": [{**_finding("important"), "category": "standard-other"}]}
    )

    _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    [(_, comment)] = ports.tracker.comments
    assert "a.py:1" in comment


def _sibling(ports) -> None:
    """Two subtasks and the integration: `a` owns the blocking finding, the integration
    carries the approval."""
    ports.documents.save(
        {
            "id": "GH-5",
            "title": "t",
            "description": "d",
            "status": "ready_for_review",
            "pull_request": URL.format(5),
            "changes_requested": {"x": 1},
            "subtasks": {
                "a": {"id": "a", "status": "approved", "reviewed_head": HEAD},
                "integration": {"id": "integration", "status": "approved", "reviewed_head": HEAD},
            },
            "findings": [{**_finding("blocking"), "subtask": "a", "category": "spec-behaviour"}],
        }
    )


def test_a_blocking_finding_owned_by_a_sibling_reopens_that_unit_and_resumes_the_task(tmp_path):
    ports = _ports()
    ports.tracker.prs[URL.format(5)] = ports.tracker.prs.get(URL.format(5)) or _pr_open()
    _sibling(ports)
    launcher = FakeLauncher()

    result, _ = _land(ports, tmp_path, launcher)

    assert result.step == "fix"
    document = _doc(ports)
    assert document["subtasks"]["a"]["status"] == "returned"
    assert "a.py:1: wrong" in document["subtasks"]["a"]["reason"]
    assert document["subtasks"]["a"]["change_category"] == "spec-behaviour"
    assert document["subtasks"]["integration"]["status"] == "pending"
    [entry] = document["change_history"]
    assert entry["by"] == "landing"
    assert document["changes_requested"] == {"x": 1}  # the pair's counter is never touched
    [argv] = launcher.spawned
    assert argv[:6] == ["agentic", "resume", "--issue", "5", "--yes", "--product-dir"]


def _pr_open():
    from tests.core.process.application.test_poll_merge import _pr

    return _pr()


def test_a_red_ci_is_rerun_once_then_the_second_red_is_a_fix(tmp_path):
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=("web",))
    )
    ports.tracker.failed_runs = ["https://github.com/o/r/actions/runs/1"]
    ports.tracker.ci_reports = [_report(_check(conclusion="FAILURE"))] * 3

    first, _ = _land(ports, tmp_path)
    assert first.step == "rerun"
    assert ports.tracker.rerun_calls == [(URL.format(5), HEAD)]

    launcher = FakeLauncher()
    second, _ = _land(ports, tmp_path, launcher)
    assert second.step == "fix"
    assert len(ports.tracker.rerun_calls) == 1  # once per head
    assert _doc(ports)["subtasks"]["only"]["status"] == "returned"
    assert "web" in _doc(ports)["subtasks"]["only"]["reason"]
    assert len(launcher.spawned) == 1


def test_a_local_check_that_failed_after_a_rerun_is_a_fix_with_its_tail(tmp_path):
    ports = _ports(5, checks=False)
    ports.documents.record_closure(
        "GH-5",
        {
            "merge_checks": {
                "head": HEAD,
                "master": MASTER,
                "passed": False,
                "failed_step": "checks",
                "checks": [{"name": "tests", "passed": False, "tail": ["boom"]}],
            },
            "landing": [{"head": HEAD, "step": "rerun", "refusal": "checks_failed", "at": NOW}],
        },
    )

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix"
    reason = _doc(ports)["subtasks"]["only"]["reason"]
    assert "tests" in reason and "boom" in reason
    assert _doc(ports)["subtasks"]["only"]["status"] == "returned"


def test_the_executors_that_are_built_are_the_live_steps():
    assert landing_plan.LIVE_STEPS == {"fix", "rerun", "file_followups_and_land", "diagnose"}


def test_reopen_rejects_a_name_that_is_not_a_subtask_by_reopening_the_reviewed_unit():
    state = {
        "id": "GH-5",
        "title": "t",
        "description": "d",
        "subtasks": {"only": {"id": "only", "status": "approved"}},
    }
    patch = reopen(state, ("ghost",), "why", "spec-behaviour", "returned")
    assert list(patch["subtasks"]) == ["only"]
    assert apply(state, patch)["subtasks"]["only"]["status"] == "returned"
    assert "changes_requested" not in patch


def test_land_without_a_launcher_logs_it_and_still_reopens(tmp_path):
    ports = _ports()
    _sibling(ports)
    ports.tracker.prs[URL.format(5)] = _pr_open()

    _, lines = _land(ports, tmp_path, None)

    assert any("no way to spawn the resume" in line for line in lines)
    assert _doc(ports)["subtasks"]["a"]["status"] == "returned"


@pytest.mark.parametrize("kind", ["fix", "rerun", "file_followups_and_land", "diagnose"])
def test_every_live_step_has_an_executor(kind):
    assert callable(getattr(landing_fix, kind))


# --- diagnose: the terminal path (restart at depth 0, escalation from depth 1) -------------


def _diagnosed(ports, tmp_path, launcher, **document):
    from agentic.core.process.application.ports import RawResult
    from tests.fakes import ScriptedRunner

    spent = {"head": HEAD, "count": 3, "failure": "gh_rejected"}
    ports.documents.record_closure(
        "GH-5", {"merge_failures": spent, "title": "t", "description": "d", **document}
    )
    ports = dataclasses.replace(
        ports,
        runner=ScriptedRunner(
            landing_diagnoser=[
                RawResult(
                    {"markdown": "### Why it does not land\n\nstale premise", "reason": "stale"},
                    0.4,
                    3,
                    "s",
                )
            ]
        ),
    )
    return ports, _land(ports, tmp_path, launcher)


def test_merge_failed_diagnoses_once_closes_the_pull_request_and_restarts_fresh(tmp_path):
    spans: list[dict] = []

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def finish(self, patch):
            spans.append(patch)

    launcher = FakeLauncher()
    ports = _ports(5)
    ports.documents.record_closure("GH-5", {"cost_usd": 1.5, "title": "t", "description": "d"})
    from agentic.core.process.application.ports import RawResult
    from tests.fakes import ScriptedRunner

    spent = {"head": HEAD, "count": 3, "failure": "gh_rejected"}
    ports.documents.record_closure("GH-5", {"merge_failures": spent})
    ports = dataclasses.replace(
        ports,
        runner=ScriptedRunner(
            landing_diagnoser=[
                RawResult({"markdown": "### Why\n\nstale", "reason": "stale premise"}, 0.4, 3, "s")
            ]
        ),
    )
    lines: list[str] = []
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: NOW,
        launcher=launcher,
        argv0="agentic",
        role_span=lambda role: _Span(),
    )

    assert result.step == "diagnose"
    assert ports.tracker.closed_pull_requests == [URL.format(5)]
    assert ports.tracker.comments == [("GH-5", "Restarted by landing: stale premise")]
    assert launcher.spawned[0][:6] == ["agentic", "run", "--issue", "5", "--fresh", "--detach"]
    closure = ports.documents.closures["GH-5"]
    assert closure["landing_restarts"] == 1
    assert closure["landing_cost_carried"] == pytest.approx(1.9)
    assert [e["step"] for e in spans[0]["landing"]] == ["diagnose"]
    assert spans[0]["landing"][-1]["refusal"] == "merge_failed"
    assert ports.tracker.opened_issues == []


def test_a_second_diagnosis_escalates_as_failure_and_files_nothing(tmp_path):
    launcher = FakeLauncher()
    ports, (result, _) = _diagnosed(_ports(5), tmp_path, launcher, landing_restarts=1)

    assert result.step == "diagnose"
    assert launcher.spawned == []
    assert ports.tracker.closed_pull_requests == []
    assert ports.tracker.opened_issues == [] and ports.tracker.comments == []
    saved = ports.documents.saved[-1]
    assert saved["escalation_class"] == "failure" and saved["escalated"] == "stale"
