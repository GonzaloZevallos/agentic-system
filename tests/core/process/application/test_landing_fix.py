"""GH-239, subtask landing-fix: the executors of `fix`, `rerun` and `file_followups_and_land`,
driven through `landing.land` (the verb the poller spawns) on in-memory ports and the fake
launcher: each refusal ends in a filed issue, a reopen with a resume spawn or a rerun, with
no human step."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from agentic.core.process.application import landing, landing_fix, landing_plan, poll
from agentic.core.process.application.landing_sync import reopen
from agentic.core.process.application.ports import (
    AttemptFailed,
    IssueComment,
    RawResult,
    Upstream,
)
from agentic.core.process.domain import document, rules
from agentic.core.process.domain import landing as landing_rules
from tests.core.process.application.test_merge_ci import _check, _report
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _finding,
    _HeldLocks,
    _ports,
    _pr,
)
from tests.fakes import FakeLauncher, MemoryStack, ScriptedRunner
from tests.gh150_stranded import GH150_STRANDED

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


def test_a_follow_up_whose_marker_names_its_parent_files_nothing_and_comments_the_next_lineage(
    tmp_path,
):
    ports = _ports(5, description=landing_rules.followup_marker("GH-3", 1) + "\nfrom GH-3")
    ports.documents.record_closure(
        "GH-5", {"findings": [{**_finding("important"), "category": "standard-other"}]}
    )

    _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    [(_, comment)] = ports.tracker.comments
    assert landing_rules.marker_lineage(comment, "GH-5") == 2


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
    assert landing_plan.LIVE_STEPS == {
        "sync_master",
        "re_review",
        "fix",
        "rerun",
        "file_followups_and_land",
        "renumber",
        "diagnose",
    }


def test_reopen_rejects_a_name_that_is_not_a_subtask_by_reopening_the_reviewed_unit():
    state = {
        "id": "GH-5",
        "title": "t",
        "description": "d",
        "subtasks": {"only": {"id": "only", "status": "approved"}},
    }
    reopened = reopen(state, ("ghost",), "why", "spec-behaviour", "returned")
    assert list(reopened["subtasks"]) == ["only"]
    assert reopened["subtasks"]["only"]["status"] == "returned"
    assert "changes_requested" not in reopened


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
    assert ports.tracker.opened_issues == []
    assert ports.tracker.labels == [("GH-5", "needs-human")] and len(ports.tracker.comments) == 1
    saved = ports.documents.saved[-1]
    assert saved["escalation_class"] == "failure" and saved["escalated"] == "stale"


# --- acceptance: every case through `poll.run_once`, in-process children, ending in a merge --


class _Driver(FakeLauncher):
    """Runs each child the poller spawns in-process: `land` (with itself as the launcher, so
    the resume and the fresh run it spawns are recorded) and `merge`; `on_resume` plays the
    Developer's re-delivery."""

    def __init__(self, ports, tmp_path, on_resume=None):
        super().__init__()
        self.ports, self.tmp_path, self.on_resume = ports, tmp_path, on_resume

    def spawn(self, argv):
        from agentic.core.process.application import merge

        pid = super().spawn(argv)
        upstream = Upstream("origin/master", MASTER, None)
        if argv[1] == "land":
            landing.land(
                f"GH-{argv[3]}",
                _cfg(self.tmp_path),
                self.ports,
                _HeldLocks(),
                upstream,
                frozenset(),
                lambda line: None,
                clock=lambda: NOW,
                launcher=self,
                argv0="agentic",
            )
        elif argv[1] == "merge":
            merge.merge_task(
                f"GH-{argv[3]}",
                int(argv[3]),
                _cfg(self.tmp_path),
                self.ports,
                upstream,
                frozenset(),
                lambda line: None,
                clock=lambda: NOW,
            )
        elif argv[1] == "resume" and self.on_resume is not None:
            self.on_resume(self.ports)
        return pid


def _tick(ports, tmp_path, driver):
    from tests.core.process.application.test_poll import ESCALATED_AT, _round
    from tests.fakes import MemoryRunRegistry

    return _round(_cfg(tmp_path), ports, MemoryRunRegistry(), driver, ESCALATED_AT)


NEW_HEAD = "d" * 40


def _redelivered(ports) -> None:
    """The Developer fixed it and pushed: the pull request is on a new head, every unit
    approved on it, the finding settled, the checks green."""
    document = ports.documents.load("GH-5") or {}
    ports.tracker.prs[URL.format(5)] = dataclasses.replace(
        ports.tracker.prs[URL.format(5)], head_oid=NEW_HEAD
    )
    ports.documents.record_closure(
        "GH-5",
        {
            "subtasks": {
                name: {"id": name, "status": "approved", "reviewed_head": NEW_HEAD}
                for name in document["subtasks"]
            },
            "status": "ready_for_review",
            "findings": [],
            "merge_checks": {"head": NEW_HEAD, "master": MASTER, "passed": True},
            # `deliver` closes the landing entry it re-delivered for.
            "landing": landing_rules.resolve_open(
                landing_plan.entries(document), NOW, document.get("cost_usd", 0.0)
            ),
        },
    )


def test_acceptance_an_important_finding_files_the_follow_up_then_the_task_merges(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5", {"findings": [{**_finding("important"), "category": "standard-other"}]}
    )
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)
    assert ports.tracker.merge_calls == [] and len(ports.tracker.opened_issues) == 1
    _tick(ports, tmp_path, driver)

    assert ports.tracker.merge_calls == [(URL.format(5), HEAD)]
    assert [a[1] for a in driver.spawned] == ["land", "merge"]


def test_acceptance_a_sibling_blocking_finding_is_fixed_and_the_task_then_merges(tmp_path):
    ports = _ports()
    ports.tracker.prs[URL.format(5)] = _pr_open()
    _sibling(ports)
    ports.documents.record_closure(
        "GH-5", {"merge_checks": {"head": HEAD, "master": MASTER, "passed": True}}
    )
    driver = _Driver(ports, tmp_path, on_resume=_redelivered)

    _tick(ports, tmp_path, driver)
    assert ports.tracker.merge_calls == []
    _tick(ports, tmp_path, driver)

    assert [a[1] for a in driver.spawned] == ["land", "resume", "merge"]
    assert ports.tracker.merge_calls == [(URL.format(5), NEW_HEAD)]
    assert _doc(ports)["change_history"][0]["by"] == "landing"


def _ci_world(*reports):
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=("web",))
    )
    ports.tracker.failed_runs = ["https://github.com/o/r/actions/runs/1"]
    ports.tracker.ci_reports = list(reports)
    return ports


def test_acceptance_a_red_ci_that_turns_green_after_a_rerun_files_the_flake_and_merges(tmp_path):
    ports = _ci_world(_report(_check(conclusion="FAILURE")))
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)
    assert len(ports.tracker.rerun_calls) == 1 and ports.tracker.merge_calls == []
    ports.tracker.ci_reports = [_report(_check())]
    _tick(ports, tmp_path, driver)

    assert ports.tracker.merge_calls == [(URL.format(5), HEAD)]
    [(title, body)] = ports.tracker.opened_issues
    assert title == "Flaky check: web"
    assert body.splitlines()[0] == landing_rules.flake_marker("web")
    assert ports.tracker.opened_labels == ["agentic"]


def test_acceptance_an_open_flake_issue_for_the_check_is_not_filed_twice(tmp_path):
    ports = _ci_world(_report(_check(conclusion="FAILURE")))
    ports.inbox._open_issues = [("u", landing_rules.flake_marker("web") + "\nolder")]
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)
    ports.tracker.ci_reports = [_report(_check())]
    _tick(ports, tmp_path, driver)

    assert ports.tracker.opened_issues == [] and len(ports.tracker.merge_calls) == 1


def test_acceptance_a_red_ci_that_stays_red_after_the_rerun_is_fixed_then_merges(tmp_path):
    ports = _ci_world(_report(_check(conclusion="FAILURE")))
    driver = _Driver(ports, tmp_path, on_resume=_redelivered)

    _tick(ports, tmp_path, driver)  # rerun
    _tick(ports, tmp_path, driver)  # still red: fix, resume
    ports.tracker.ci_reports = [_report(_check(), head=NEW_HEAD)]
    _tick(ports, tmp_path, driver)  # the re-delivery is green

    assert [a[1] for a in driver.spawned] == ["land", "land", "resume", "merge"]
    assert len(ports.tracker.rerun_calls) == 1
    assert ports.tracker.merge_calls == [(URL.format(5), NEW_HEAD)]
    assert ports.tracker.opened_issues == []  # no flake: the rerun stayed red


def _red_checks(ports) -> None:
    ports.documents.record_closure(
        "GH-5",
        {
            "merge_checks": {
                "head": HEAD,
                "master": MASTER,
                "passed": False,
                "failed_step": "checks",
                "checks": [{"name": "tests", "passed": False, "tail": ["boom"]}],
            }
        },
    )


def _local_world():
    ports = dataclasses.replace(_ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}))
    _red_checks(ports)
    return ports


def test_acceptance_failed_local_checks_rerun_green_files_the_flake_and_merges(tmp_path):
    ports = _local_world()
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)  # rerun of the failed check only, green this time
    assert _doc(ports)["merge_checks"]["passed"] is True
    _tick(ports, tmp_path, driver)

    assert ports.tracker.merge_calls == [(URL.format(5), HEAD)]
    assert [t for t, _ in ports.tracker.opened_issues] == ["Flaky check: tests"]


def test_acceptance_failed_local_checks_that_stay_red_are_fixed_then_merge(tmp_path):
    from agentic.core.process.application.ports import CommandResult
    from tests.fakes import MemoryCommands

    ports = dataclasses.replace(
        _local_world(), commands=MemoryCommands(t=[CommandResult(1, "boom again")])
    )
    driver = _Driver(ports, tmp_path, on_resume=_redelivered)

    _tick(ports, tmp_path, driver)  # rerun: red again
    assert _doc(ports)["merge_checks"]["passed"] is False
    _tick(ports, tmp_path, driver)  # fix: resume
    _tick(ports, tmp_path, driver)

    assert [a[1] for a in driver.spawned] == ["land", "land", "resume", "merge"]
    assert ports.tracker.merge_calls == [(URL.format(5), NEW_HEAD)]
    assert ports.tracker.opened_issues == []


def test_acceptance_merge_failed_is_diagnosed_the_pull_request_closed_and_a_fresh_run_spawned(
    tmp_path,
):
    from agentic.core.process.application.ports import RawResult
    from tests.fakes import ScriptedRunner

    ports = _ports(5)
    spent = {"head": HEAD, "count": 3, "failure": "gh_rejected"}
    ports.documents.record_closure(
        "GH-5", {"merge_failures": spent, "title": "t", "description": "d", "cost_usd": 2.0}
    )
    ports = dataclasses.replace(
        ports,
        runner=ScriptedRunner(
            landing_diagnoser=[RawResult({"markdown": "### Why\n\nx", "reason": "r"}, 0.5, 2, "s")]
        ),
    )
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)

    assert ports.tracker.closed_pull_requests == [URL.format(5)]
    assert driver.spawned[-1][1:6] == ["run", "--issue", "5", "--fresh", "--detach"]
    assert ports.tracker.merge_calls == []
    closure = ports.documents.closures["GH-5"]
    assert closure["landing_restarts"] == 1 and closure["landing_cost_carried"] == 2.5


def test_acceptance_a_second_exhaustion_at_depth_one_escalates_and_files_nothing(tmp_path):
    from agentic.core.process.application.ports import RawResult
    from tests.fakes import ScriptedRunner

    ports = _ports(5)
    spent = {"head": HEAD, "count": 3, "failure": "gh_rejected"}
    ports.documents.record_closure(
        "GH-5",
        {"merge_failures": spent, "title": "t", "description": "d", "landing_restarts": 1},
    )
    ports = dataclasses.replace(
        ports,
        runner=ScriptedRunner(
            landing_diagnoser=[RawResult({"markdown": "### Why\n\nx", "reason": "r"}, 0.5, 2, "s")]
        ),
    )
    driver = _Driver(ports, tmp_path)

    _tick(ports, tmp_path, driver)

    assert [a[1] for a in driver.spawned] == ["land"]
    assert ports.tracker.closed_pull_requests == [] and ports.tracker.opened_issues == []
    assert ports.documents.saved[-1]["escalation_class"] == "failure"


# --- GH-257: a diagnosis that does not deliver is an attempt, never a stranded escalation ----


START = datetime.fromisoformat(NOW)


def _land_at(ports, tmp_path, when, launcher=None):
    lines: list[str] = []
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: when.isoformat(),
        launcher=launcher,
        argv0="agentic",
    )
    return result, lines


def _merge_failed(ports, runner, **document):
    spent = {"head": HEAD, "count": 3, "failure": "gh_rejected"}
    ports.documents.record_closure(
        "GH-5", {"merge_failures": spent, "title": "t", "description": "d", **document}
    )
    return dataclasses.replace(ports, runner=runner)


def _cut(reason, times=2):
    return [AttemptFailed(f"{reason} {n}", reason=reason) for n in range(times)]


def _delivery():
    return RawResult({"markdown": "### Why\n\nx", "reason": "stale"}, 0.4, 3, "s")


def _diagnoses(ports):
    return [e for e in _doc(ports)["landing"] if e["step"] == "diagnose"]


def test_two_account_limit_failures_leave_the_task_ready_with_the_reason_on_its_entry(tmp_path):
    ports = _merge_failed(
        _ports(5), ScriptedRunner(landing_diagnoser=_cut("account_limit")), cost_usd=2.0
    )

    result, _ = _land_at(ports, tmp_path, START)

    doc = _doc(ports)
    assert result.step == "diagnose"
    assert doc["status"] == "ready_for_review"
    assert not doc.get("escalated") and not doc.get("escalation_class")
    assert not doc.get("pause_log") and not doc.get("failures")
    assert ports.tracker.labels == [] and ports.tracker.comments == []
    assert doc["cost_usd"] >= 2.0
    assert [(e["failure"], e.get("delivered")) for e in _diagnoses(ports)] == [
        ("account_limit", None)
    ]
    assert rules.auto_resumable(doc) is None


def test_a_sdk_failure_escalates_at_once_labelled_and_commented(tmp_path):
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=_cut("sdk")))

    _land_at(ports, tmp_path, START)

    doc = _doc(ports)
    assert doc["escalation_class"] == "failure"
    assert "Landing Diagnoser" in doc["escalated"] and "sdk" in doc["escalated"]
    assert "1 attempt" in doc["escalated"]
    assert ports.tracker.labels == [("GH-5", "needs-human")]
    assert len(ports.tracker.comments) == 1
    assert doc["pause_log"] == [{"kind": "escalation", "value": "failure"}]
    assert rules.auto_resumable(doc) is None


def test_a_retry_waits_for_its_slot_then_runs_the_role_again(tmp_path):
    runner = ScriptedRunner(landing_diagnoser=[*_cut("account_limit"), _delivery()])
    ports = _merge_failed(_ports(5), runner)
    wait = timedelta(minutes=rules.ACCOUNT_LIMIT_SCHEDULE[0])
    _land_at(ports, tmp_path, START)

    early, _ = _land_at(ports, tmp_path, START + wait - timedelta(seconds=1))
    assert early.kind == "skipped" and len(runner.calls) == 2

    launcher = FakeLauncher()
    due, _ = _land_at(ports, tmp_path, START + wait, launcher)

    assert due.kind == "dispatched" and len(runner.calls) == 3
    assert [e.get("delivered") for e in _diagnoses(ports)] == [None, True]
    assert launcher.spawned[0][:3] == ["agentic", "run", "--issue"]


def test_a_spent_calendar_escalates_without_running_the_role_and_is_nobodys_candidate(tmp_path):
    runner = ScriptedRunner()
    ports = _merge_failed(_ports(5), runner)
    spent = [
        {
            "head": HEAD,
            "step": "diagnose",
            "refusal": "merge_failed",
            "at": START.isoformat(),
            "failure": "account_limit",
        }
        for _ in rules.ACCOUNT_LIMIT_SCHEDULE
    ]
    ports.documents.record_closure("GH-5", {"landing": spent})

    result, _ = _land_at(ports, tmp_path, START + timedelta(days=2))

    doc = _doc(ports)
    assert result.step == "diagnose" and runner.calls == []
    assert doc["escalation_class"] == "failure"
    assert ports.tracker.labels == [("GH-5", "needs-human")]
    assert poll.gather_resumes(ports, START + timedelta(days=2)) == []
    again, _ = _land_at(ports, tmp_path, START + timedelta(days=3))
    assert again.kind == "skipped" and len(ports.tracker.comments) == 1


def test_a_delivered_run_is_marked_even_when_the_close_fails_and_land_then_does_nothing(tmp_path):
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]))

    def refuse(reference):
        raise ValueError("gh pr close failed")

    ports.tracker.close_pull_request = refuse
    launcher = FakeLauncher()

    _land_at(ports, tmp_path, START, launcher)

    assert [e.get("delivered") for e in _diagnoses(ports)] == [True]
    assert launcher.spawned == []
    again, _ = _land_at(ports, tmp_path, START + timedelta(days=1), launcher)
    assert again.kind == "skipped" and again.step == "none"


def test_a_delivered_run_is_marked_even_when_the_spawn_raises(tmp_path):
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]))

    with pytest.raises(FileNotFoundError):
        _land_at(ports, tmp_path, START, FakeLauncher(fails={0}))

    assert [e.get("delivered") for e in _diagnoses(ports)] == [True]
    assert ports.documents.closures["GH-5"]["landing_restarts"] == 1
    again, _ = _land_at(ports, tmp_path, START + timedelta(days=1), FakeLauncher())
    assert again.step == "none"


def test_the_depth_one_escalation_keeps_the_delivered_mark_and_the_cost(tmp_path):
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(landing_diagnoser=[_delivery()]),
        landing_restarts=1,
        cost_usd=2.0,
    )

    _land_at(ports, tmp_path, START)

    doc = _doc(ports)
    assert [e.get("delivered") for e in _diagnoses(ports)] == [True]
    assert doc["escalation_class"] == "failure" and doc["escalated"]
    assert max(s.get("cost_usd", 0.0) for s in ports.documents.saved) == pytest.approx(2.4)
    assert all(s.get("cost_usd", 2.0) >= 2.0 for s in ports.documents.saved)
    assert ports.tracker.labels == [("GH-5", "needs-human")]


def test_no_save_after_the_delivered_mark_writes_the_stale_landing_back(tmp_path):
    ports = _merge_failed(
        _ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]), landing_restarts=1
    )
    stale: list[object] = []
    real_save = ports.documents.save

    def watching(state):
        marked = ports.documents.closures.get("GH-5", {}).get("landing")
        if marked and any(e.get("delivered") for e in marked):
            stale.append(state.get("landing") != marked)
        return real_save(state)

    ports.documents.save = watching

    _land_at(ports, tmp_path, START)

    assert stale and not any(stale)


# --- GH-257: a stranded legacy diagnose document is freed by land, once --------------------

DIAGNOSER = "Landing Diagnoser"
_REAL = document.parse(GH150_STRANDED)
_REAL_HEAD = _REAL["landing"][-1]["head"]
LAST_AT = datetime.fromisoformat(landing_rules.diagnose_at(_REAL))
MARGIN = timedelta(minutes=1)


def _stranded_ports(**over):
    """GH-150 as the Landing Diagnoser left it, from its real frontmatter (`GH150_STRANDED`):
    waiting on a human as `failure` after a failed run, its entry with neither `delivered` nor
    `failure`, nobody told. The pull request reports the head of the fixture's last landing
    entry; only the id is the harness's own."""
    ports = _ports()
    # The merge facts the live file does not carry (it stopped at `ci_missing`): the checks
    # green on the reviewed head and master, and the merge spent on it, as `_ready` and
    # `_merge_failed` record them for every other task here.
    merge_facts = {
        "merge_checks": {"head": _REAL_HEAD, "master": MASTER, "passed": True},
        "merge_failures": {"head": _REAL_HEAD, "count": 3, "failure": "gh_rejected"},
    }
    document_ = {**_REAL, "id": "GH-5", **merge_facts, **over}
    ports.documents.save(document_)
    if isinstance(url := document_.get("pull_request"), str):
        ports.tracker.prs[url] = dataclasses.replace(_pr(), head_oid=_REAL_HEAD)
    ports.workspace.conflict_answers[(_REAL_HEAD, MASTER)] = False
    return ports


def _window():
    """When the freed diagnosis is due, read off the shared calendar from the fixture's own
    last diagnose `at`; the tests stand a minute either side of it, never on its edge."""
    freed = {**_REAL, **landing_rules.migration(_REAL)}
    reason = freed["landing"][-1]["failure"]
    count = len(landing_rules.diagnose_attempts(freed))
    limit = rules.auto_resume_limit(landing_rules._landing_view(freed), reason)
    schedule, prior, _ = rules.retake_calendar(reason, count, count, limit)
    return LAST_AT + timedelta(minutes=schedule[prior])


def _told(body, at):
    return IssueComment(body, at.isoformat())


def _untouched(ports) -> bool:
    return not ports.documents.closures and not ports.tracker.comments


def test_land_frees_a_stranded_diagnose_in_one_write_and_keeps_the_cost(tmp_path):
    ports = _stranded_ports()
    before = _doc(ports)
    runner = ScriptedRunner()
    ports = dataclasses.replace(ports, runner=runner)

    result, _ = _land_at(ports, tmp_path, _window() - MARGIN)

    doc = _doc(ports)
    assert doc["status"] == "ready_for_review"
    assert not doc["escalated"] and not doc["escalation_class"]
    assert doc["pause_log"] == [*before["pause_log"], {"kind": "resumed"}]
    assert rules.auto_resumable(doc) is None
    assert doc["cost_usd"] == _REAL["cost_usd"]
    assert [e["failure"] for e in _diagnoses(ports)] == ["account_limit"]
    assert landing_rules.diagnose_retry(doc, _window() - MARGIN) == "waiting"
    assert landing_rules.diagnose_retry(doc, _window() + MARGIN) == "due"
    assert landing_rules.diagnose_retry(before, _window() - MARGIN) == "escalate"
    assert landing_rules.diagnose_retry(before, _window() + MARGIN) == "escalate"
    assert result.kind == "skipped" and result.reason == "diagnose_waiting"
    assert runner.calls == [] and ports.tracker.labels == [] and ports.tracker.comments == []
    assert list(ports.documents.closures) == ["GH-5"]


def test_the_freed_diagnose_then_retries_on_the_calendar_and_delivers(tmp_path):
    runner = ScriptedRunner(landing_diagnoser=[_delivery()])
    ports = dataclasses.replace(_stranded_ports(), runner=runner)

    _land_at(ports, tmp_path, _window() + MARGIN)

    assert len(runner.calls) == 1
    assert _diagnoses(ports)[-1]["delivered"] is True


def test_a_second_land_does_not_migrate_again(tmp_path):
    ports = _stranded_ports()
    _land_at(ports, tmp_path, LAST_AT)
    first = dict(ports.documents.closures["GH-5"])

    _land_at(ports, tmp_path, LAST_AT)

    assert ports.documents.closures["GH-5"] == first
    resumed = _REAL["pause_log"].count({"kind": "resumed"})
    assert _doc(ports)["pause_log"].count({"kind": "resumed"}) == resumed + 1


def test_an_escalated_comment_older_than_the_entry_does_not_keep_it_escalated(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [
        _told("Escalated: earlier 429", LAST_AT - timedelta(hours=3))
    ]

    _land_at(ports, tmp_path, LAST_AT)

    assert _doc(ports)["status"] == "ready_for_review"


@pytest.mark.parametrize(
    "over",
    [
        pytest.param(
            {"pause_log": [{"kind": "escalation", "value": "failure"}]},
            id="depth-one-escalation-without-the-comment",
        ),
        pytest.param(
            {
                "pause_log": [
                    {"kind": "failure", "value": "sdk", "section": DIAGNOSER},
                    {"kind": "escalation", "value": "failure"},
                ]
            },
            id="sdk-failure",
        ),
        pytest.param({"current_role": "Developer"}, id="graph-role"),
        pytest.param({"escalation_class": "questions"}, id="questions"),
        pytest.param({"pull_request": None}, id="no-pull-request"),
    ],
)
def test_a_document_that_is_not_the_legacy_shape_stays_escalated_and_untouched(tmp_path, over):
    ports = _stranded_ports(**over)
    before = _doc(ports)

    _land_at(ports, tmp_path, LAST_AT)

    assert _untouched(ports) and _doc(ports) == before


def test_a_real_depth_one_escalation_with_its_comment_stays_escalated(tmp_path):
    ports = _stranded_ports(pause_log=[{"kind": "escalation", "value": "failure"}])
    ports.tracker.timestamped["GH-5"] = [
        _told("Escalated: restart exhausted", LAST_AT + timedelta(minutes=1))
    ]
    before = _doc(ports)

    _land_at(ports, tmp_path, LAST_AT)

    assert _untouched(ports) and _doc(ports) == before


def test_the_stranded_shape_with_a_comment_after_the_entry_stays_escalated(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [_told("Escalated: x", LAST_AT + timedelta(seconds=1))]
    before = _doc(ports)

    _land_at(ports, tmp_path, LAST_AT)

    assert _untouched(ports) and _doc(ports) == before


def test_an_unreadable_comment_time_keeps_the_stranded_shape_escalated(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [IssueComment("Escalated: x", "garbage")]
    before = _doc(ports)

    _land_at(ports, tmp_path, LAST_AT)

    assert _untouched(ports) and _doc(ports) == before


def test_comments_are_read_only_for_a_document_already_shaped_like_the_stranded_one(tmp_path):
    reads: list[str] = []
    ports = _ports(5, status="waiting_on_human", escalation_class="questions", escalated="q")
    ports.tracker.timestamped_comments = lambda task_id: reads.append(task_id) or []

    _land_at(ports, tmp_path, LAST_AT)
    other = _stranded_ports(current_role="Developer")
    other.tracker.timestamped_comments = lambda task_id: reads.append(task_id) or []
    _land_at(other, tmp_path, LAST_AT)

    assert reads == []
    shaped = _stranded_ports()
    shaped.tracker.timestamped_comments = lambda task_id: reads.append(task_id) or []
    _land_at(shaped, tmp_path, LAST_AT)
    assert reads == ["GH-5"]


def test_one_poll_round_over_a_stranded_document_offers_land_and_no_resume(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [
        _told("Escalated: an earlier 429", LAST_AT - timedelta(hours=3))
    ]
    from tests.core.process.application.test_poll import _Locks

    lands: list[int] = []
    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands=lands, now=LAST_AT)

    assert lands == [5]
    assert poll.gather_resumes(ports, LAST_AT + timedelta(days=1)) == []


def test_a_round_over_a_real_escalation_offers_neither(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [_told("Escalated: real", LAST_AT + timedelta(minutes=1))]
    from tests.core.process.application.test_poll import _Locks

    lands: list[int] = []
    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands=lands, now=LAST_AT)

    assert lands == []
