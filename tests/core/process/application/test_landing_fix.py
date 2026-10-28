"""GH-239, subtask landing-fix: the executors of `fix`, `rerun` and `file_followups_and_land`,
driven through `landing.land` (the verb the poller spawns) on in-memory ports and the fake
launcher: each refusal ends in a filed issue, a reopen with a resume spawn or a rerun, with
no human step."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import landing, landing_fix, landing_plan, poll
from agentic.core.process.application.landing_plan import LandingContext
from agentic.core.process.application.landing_sync import reopen
from agentic.core.process.application.ports import (
    AttemptFailed,
    IssueComment,
    RawResult,
    Upstream,
)
from agentic.core.process.domain import document, rules
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.adr_numbers import Ambiguous, Renaming
from agentic.core.process.domain.roles import PAIR_PM_TECH_LEAD
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
from tests.core.process.domain.test_landing import _facts
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


def test_an_outside_finding_files_one_needs_triage_follow_up_and_the_next_decision_merges(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )
    assert _candidates(ports) == []

    result, _ = _land(ports, tmp_path)

    assert result.kind == "dispatched" and result.step == "file_followups_and_land"
    [(title, body)] = ports.tracker.opened_issues
    assert title == "Follow-up of #5: open important findings"
    assert ports.tracker.opened_labels == ["needs-triage"]
    assert ports.tracker.created_labels == ["needs-triage"]
    assert body.splitlines()[0] == landing_rules.followup_marker("GH-5", 1)
    assert "a.py:1" in body and "wrong" in body
    assert _doc(ports)["followups_filed"] is True
    assert _candidates(ports) == [5]


def test_a_follow_up_already_open_for_the_task_is_not_filed_twice(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )
    ports.inbox._open_issues = [("u", landing_rules.followup_marker("GH-5", 1) + "\nold")]

    _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    assert _doc(ports)["followups_filed"] is True


def test_a_task_that_is_itself_a_follow_up_files_nothing_and_writes_the_rows_in_a_comment(tmp_path):
    ports = _ports(5, description=landing_rules.followup_marker("GH-5", 1) + "\nfrom a task")
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
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
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )

    _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    [(_, comment)] = ports.tracker.comments
    assert landing_rules.marker_lineage(comment, "GH-5") == 2


def _sibling(ports, severity: str = "blocking", **row) -> None:
    """Two subtasks and the integration: `a` owns the finding (blocking unless said
    otherwise), the integration carries the approval."""
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
            "findings": [
                {**_finding(severity), "subtask": "a", "category": "spec-behaviour", **row}
            ],
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
        "fix_important",
        "rerun",
        "file_followups_and_land",
        "renumber",
        "land_slice",
        "diagnose",
        "settle",
        "escalate_ledger",
        "escalate_adr",
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


@pytest.mark.parametrize(
    "kind", ["fix", "fix_important", "rerun", "file_followups_and_land", "diagnose", "settle"]
)
def test_every_live_step_has_an_executor(kind):
    assert callable(getattr(landing_fix, kind))


# --- GH-294: the final important pass and the needs-triage follow-up ------------------------


def _open_world(ports):
    ports.tracker.prs[URL.format(5)] = _pr_open()


def test_fix_important_reopens_the_owner_as_a_landing_entry_and_never_touches_the_pair(tmp_path):
    ports = _ports()
    _open_world(ports)
    _sibling(ports, "important")
    launcher = FakeLauncher()

    result, _ = _land(ports, tmp_path, launcher)

    assert result.kind == "dispatched" and result.step == "fix_important"
    document = _doc(ports)
    assert document["subtasks"]["a"]["status"] == "returned"
    reason = document["subtasks"]["a"]["reason"]
    assert reason.startswith("Open important findings (final pass before merge, not a return):")
    assert "Unit `a`:" in reason and "a.py:1: wrong" in reason
    assert document["subtasks"]["a"]["change_category"] == "spec-behaviour"
    assert document["subtasks"]["integration"]["status"] == "pending"
    [entry] = document["change_history"]
    assert entry["by"] == "landing"
    assert document["changes_requested"] == {"x": 1}
    [recorded] = document["landing"]
    assert recorded["step"] == "fix_important" and "cost_base" in recorded
    [argv] = launcher.spawned
    assert argv[:6] == ["agentic", "resume", "--issue", "5", "--yes", "--product-dir"]
    assert ports.tracker.opened_issues == []


def test_a_plan_scope_important_row_reopens_the_reviewed_unit(tmp_path):
    ports = _ports()
    _open_world(ports)
    _sibling(ports, "important", scope="plan")

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix_important"
    document = _doc(ports)
    assert document["subtasks"]["integration"]["status"] == "returned"
    assert "Plan:" in document["subtasks"]["integration"]["reason"]
    assert document["subtasks"]["a"]["status"] == "approved"


def test_the_final_pass_runs_once_and_what_stays_open_after_it_is_left_to_the_merge(tmp_path):
    ports = _ports()
    _open_world(ports)
    _sibling(ports, "important")
    ports.documents.record_closure(
        "GH-5", {"merge_checks": {"head": HEAD, "master": MASTER, "passed": True}}
    )
    driver = _Driver(ports, tmp_path, on_resume=_still_open)

    _tick(ports, tmp_path, driver)
    assert ports.tracker.merge_calls == []
    _tick(ports, tmp_path, driver)

    assert [a[1] for a in driver.spawned] == ["land", "resume", "merge"]
    assert ports.tracker.opened_issues == []
    assert ports.tracker.merge_calls == [(URL.format(5), NEW_HEAD)]
    [(_, comment)] = ports.tracker.comments
    assert "Accepted without a fix:" in comment and "`a.py:1`: wrong" in comment
    document = _doc(ports)
    assert document["changes_requested"] == {"x": 1}
    assert [e["by"] for e in document["change_history"]] == ["landing"]


def _still_open(ports) -> None:
    """The Developer delivered but the important row stayed open (the reviewer kept it)."""
    _redelivered(ports)
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "subtask": "a", "category": "spec-behaviour"}]},
    )


def test_an_important_row_left_open_without_a_resolved_pass_goes_to_the_landing_not_a_second_pass(
    tmp_path,
):
    ports = _ports()
    _open_world(ports)
    _sibling(ports, "important")
    ports.documents.record_closure(
        "GH-5",
        {
            "landing": [
                {"head": "e" * 40, "step": "fix_important", "refusal": "open_findings", "at": NOW}
            ]
        },
    )

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "file_followups_and_land"
    assert ports.tracker.opened_issues == []
    assert _doc(ports)["followups_filed"] is True


def test_the_follow_up_carries_only_the_outside_rows(tmp_path):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5",
        {
            "followups_filed": False,
            "findings": [
                {**_finding("important"), "scope": "outside", "category": "standard-other"},
                {
                    **_finding("important"),
                    "location": "own.py:2",
                    "claim": "own claim",
                    "category": "standard-other",
                },
            ],
            "landing": [
                {
                    "head": "e" * 40,
                    "step": "fix_important",
                    "refusal": "open_findings",
                    "at": NOW,
                    "resolved_at": NOW,
                }
            ],
        },
    )

    result, _ = _land(ports, tmp_path)

    assert result.step == "file_followups_and_land"
    [(_, body)] = ports.tracker.opened_issues
    assert "a.py:1" in body and "own.py:2" not in body


def test_a_failing_label_degrades_the_follow_up_to_a_pull_request_comment(tmp_path):
    ports = _ports(5)
    ports.tracker.ensure_label_error = "gh label create failed"
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )

    _, lines = _land(ports, tmp_path)

    assert ports.tracker.opened_issues == []
    [(task, comment)] = ports.tracker.comments
    assert task == "GH-5" and landing_rules.marker_lineage(comment, "GH-5") == 1
    assert any("issue not opened" in line for line in lines)
    assert _doc(ports)["followups_filed"] is True


def test_a_failing_open_issue_degrades_the_follow_up_to_a_comment(tmp_path, monkeypatch):
    ports = _ports(5)
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )

    def refuse(*_args, **_kwargs):
        raise ValueError("gh issue create failed")

    monkeypatch.setattr(ports.tracker, "open_issue", refuse)

    _land(ports, tmp_path)

    [(_, comment)] = ports.tracker.comments
    assert "a.py:1" in comment
    assert _doc(ports)["followups_filed"] is True


def test_a_comment_that_fails_too_is_logged_and_the_follow_up_is_still_marked_filed(
    tmp_path, monkeypatch
):
    ports = _ports(5)
    ports.tracker.ensure_label_error = "boom"
    ports.documents.record_closure(
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
    )

    def refuse(*_args, **_kwargs):
        raise ValueError("no comment either")

    monkeypatch.setattr(ports.tracker, "comment", refuse)

    _, lines = _land(ports, tmp_path)

    assert any("comment failed" in line for line in lines)
    assert _doc(ports)["followups_filed"] is True


def test_no_path_of_the_landing_opens_an_issue_labelled_agentic(tmp_path, monkeypatch):
    labels: list[str | None] = []
    for variant in ("filed", "label_fails", "lineage"):
        description = (
            landing_rules.followup_marker("GH-3", 1) + "\nfrom GH-3" if variant == "lineage" else ""
        )
        ports = _ports(5, description=description)
        if variant == "label_fails":
            ports.tracker.ensure_label_error = "boom"
        ports.documents.record_closure(
            "GH-5",
            {
                "findings": [
                    {**_finding("important"), "category": "standard-other", "scope": "outside"}
                ]
            },
        )
        _land(ports, tmp_path)
        labels += ports.tracker.opened_labels
    assert labels == ["needs-triage"]
    assert not hasattr(landing_fix, "FOLLOWUP_LABEL")
    assert landing_fix.TRIAGE_LABEL == "needs-triage"


def test_a_follow_up_lineage_gets_the_pass_for_its_own_rows_and_a_comment_for_the_outside(
    tmp_path,
):
    ports = _ports(5, description=landing_rules.followup_marker("GH-3", 1) + "\nfrom GH-3")
    ports.documents.record_closure(
        "GH-5",
        {
            "findings": [
                {**_finding("important"), "category": "standard-other"},
                {
                    **_finding("important"),
                    "location": "far.py:9",
                    "claim": "far claim",
                    "scope": "outside",
                    "category": "standard-other",
                },
            ]
        },
    )

    first, _ = _land(ports, tmp_path, FakeLauncher())
    assert first.step == "fix_important"
    assert ports.tracker.comments == []

    ports.documents.record_closure(
        "GH-5",
        {
            "landing": [{**e, "resolved_at": NOW} for e in landing_plan.entries(_doc(ports))],
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
            "status": "ready_for_review",
        },
    )
    second, _ = _land(ports, tmp_path, FakeLauncher())

    assert second.step == "file_followups_and_land"
    assert ports.tracker.opened_issues == []
    [(_, comment)] = ports.tracker.comments
    assert "far.py:9" in comment and "a.py:1" not in comment
    assert landing_rules.marker_lineage(comment, "GH-5") == 2


def test_spent_counts_the_final_pass_apart_from_the_model_passes():
    raw = {
        "landing": [
            {"head": "h", "step": "fix_important", "refusal": "open_findings", "at": NOW},
            {"head": "h", "step": "fix", "refusal": "ci_failed", "at": NOW},
        ]
    }

    spent = landing_plan.spent(raw, "h", None)

    assert spent.important_passes == 1 and spent.passes == 1
    assert landing_plan.spent({}, "h", None).important_passes == 0


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
                    {
                        "markdown": "### Why it does not land\n\nstale premise",
                        "reason": "stale",
                        "verdict": "unlandable",
                    },
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
                RawResult(
                    {
                        "markdown": "### Why\n\nstale",
                        "reason": "stale premise",
                        "verdict": "unlandable",
                    },
                    0.4,
                    3,
                    "s",
                )
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
    assert ports.tracker.comments == [
        ("GH-5", "Restarted by landing: single_unit: stale premise (cost so far: $1.90)")
    ]
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
        "GH-5",
        {"findings": [{**_finding("important"), "category": "standard-other", "scope": "outside"}]},
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
    assert ports.tracker.opened_labels == ["agentic"]  # the flake label is unchanged (ADR-0053)


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
        _local_world(), commands=MemoryCommands(t=[CommandResult(1, "boom again")] * 2)
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
            landing_diagnoser=[
                RawResult(
                    {"markdown": "### Why\n\nx", "reason": "r", "verdict": "unlandable"},
                    0.5,
                    2,
                    "s",
                )
            ]
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
            landing_diagnoser=[
                RawResult(
                    {"markdown": "### Why\n\nx", "reason": "r", "verdict": "unlandable"},
                    0.5,
                    2,
                    "s",
                )
            ]
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
    return RawResult(
        {"markdown": "### Why\n\nx", "reason": "stale", "verdict": "unlandable"}, 0.4, 3, "s"
    )


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


def test_the_label_of_a_landing_escalation_is_the_predicates_call(tmp_path, monkeypatch):
    # The landing labels only through `rules.needs_a_human`: a False answer leaves the
    # comment and drops the label (the sdk test below is the True side).
    monkeypatch.setattr(rules, "needs_a_human", lambda state: False)
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=_cut("sdk")))

    _land_at(ports, tmp_path, START)

    assert ports.tracker.labels == []
    assert len(ports.tracker.comments) == 1


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
    # An account_limit cut is not retried inside the run (ADR-0039, addendum GH-381): one call.
    runner = ScriptedRunner(landing_diagnoser=[*_cut("account_limit", times=1), _delivery()])
    ports = _merge_failed(_ports(5), runner)
    wait = timedelta(minutes=rules.ACCOUNT_LIMIT_SCHEDULE[0])
    _land_at(ports, tmp_path, START)

    early, _ = _land_at(ports, tmp_path, START + wait - timedelta(seconds=1))
    assert early.kind == "skipped" and len(runner.calls) == 1

    launcher = FakeLauncher()
    due, _ = _land_at(ports, tmp_path, START + wait, launcher)

    assert due.kind == "dispatched" and len(runner.calls) == 2
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

    lands: list[rules.LandCandidate] = []
    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands=lands, now=LAST_AT)

    assert lands == [rules.LandCandidate(5, "land_model")]
    assert poll.gather_resumes(ports, LAST_AT + timedelta(days=1)) == []


def test_a_round_over_a_real_escalation_offers_neither(tmp_path):
    ports = _stranded_ports()
    ports.tracker.timestamped["GH-5"] = [_told("Escalated: real", LAST_AT + timedelta(minutes=1))]
    from tests.core.process.application.test_poll import _Locks

    lands: list[rules.LandCandidate] = []
    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands=lands, now=LAST_AT)

    assert lands == []


# --- GH-297: the restart notice carries the closed reason and the cost, and waits for no one ----


def _stamped_entry(monkeypatch, **keys):
    """What the slice executor leaves on the diagnose entry before the diagnoser runs: the
    closed reason the landing decided, written through the landing's own `_stamp`."""
    run = landing_fix.diagnose

    def stamped(context):
        landing_fix._stamp(context, **keys)
        run(context)

    monkeypatch.setattr(landing_fix, "diagnose", stamped)


def _restart_comments(ports):
    return [text for _, text in ports.tracker.comments if text.startswith("Restarted by landing")]


def test_the_restart_comment_carries_the_closed_reason_the_diagnosis_and_the_cost_so_far(
    tmp_path, monkeypatch
):
    _stamped_entry(monkeypatch, restart_why="no_clean_slice")
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(landing_diagnoser=[_delivery()]),
        cost_usd=1.5,
        landing_cost_carried=0.25,
    )
    launcher = FakeLauncher()

    _land_at(ports, tmp_path, START, launcher)

    assert _restart_comments(ports) == [
        "Restarted by landing: no_clean_slice: stale (cost so far: $2.15)"
    ]
    assert ports.documents.closures["GH-5"]["landing_cost_carried"] == pytest.approx(2.15)
    assert launcher.spawned and launcher.spawned[0][:6] == [
        "agentic",
        "run",
        "--issue",
        "5",
        "--fresh",
        "--detach",
    ]


def test_the_restart_comment_names_integration_work_and_the_slices_already_landed(
    tmp_path, monkeypatch
):
    _stamped_entry(monkeypatch, restart_why="no_clean_slice", integration_work=True)
    landed = [
        {"units": ["a"], "pull_request": URL.format(11)},
        {"units": ["b"], "pull_request": URL.format(12)},
    ]
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(landing_diagnoser=[_delivery()]),
        cost_usd=1.0,
        landed_slices=landed,
    )

    _land_at(ports, tmp_path, START, FakeLauncher())

    assert _restart_comments(ports) == [
        "Restarted by landing: no_clean_slice (integration work): stale"
        " (cost so far: $1.40; landed slices: #11, #12)"
    ]


def test_a_document_without_a_closed_reason_gets_todays_text_plus_the_cost(tmp_path, monkeypatch):
    _stamped_entry(monkeypatch, restart_why=None)  # a document written before GH-297
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]), cost_usd=1.5)

    _land_at(ports, tmp_path, START, FakeLauncher())

    assert _restart_comments(ports) == ["Restarted by landing: stale (cost so far: $1.90)"]


def test_a_comment_that_fails_is_logged_and_the_fresh_run_still_spawns(tmp_path):
    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]), cost_usd=1.0)
    ports.tracker.comment_failures = 1
    launcher = FakeLauncher()

    _, lines = _land_at(ports, tmp_path, START, launcher)

    assert any("comment failed: gh issue failed: scripted" in line for line in lines)
    assert any(line.startswith("agentic: land: GH-5: comment failed") for line in lines)
    assert len(launcher.spawned) == 1 and "--fresh" in launcher.spawned[0]
    assert ports.documents.closures["GH-5"]["landing_restarts"] == 1


def test_the_restart_never_waits_for_approval_and_its_comment_is_not_a_human_decision(tmp_path):
    from agentic.core.process.application.resume import human_decision

    ports = _merge_failed(_ports(5), ScriptedRunner(landing_diagnoser=[_delivery()]), cost_usd=1.0)
    launcher = FakeLauncher()

    _land_at(ports, tmp_path, START, launcher)

    (text,) = _restart_comments(ports)
    assert len(launcher.spawned) == 1
    assert ports.tracker.labels == []
    assert human_decision([text]) is None
    assert human_decision(["Escalated: x", text, "go on with option B"]) == "go on with option B"


def test_the_restart_at_the_depth_limit_still_escalates_without_the_notice(tmp_path, monkeypatch):
    _stamped_entry(monkeypatch, restart_why="single_unit")
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(landing_diagnoser=[_delivery()]),
        landing_restarts=1,
    )
    launcher = FakeLauncher()

    _land_at(ports, tmp_path, START, launcher)

    assert launcher.spawned == [] and _restart_comments(ports) == []
    assert ports.tracker.labels == [("GH-5", "needs-human")]


def _important_row(location: str, **extra) -> dict:
    return {
        "subtask": "api",
        "version": 1,
        "location": location,
        "claim": "wrong",
        "severity": "important",
        "scope": "subtask",
        "category": "copy-criteria-written" if location.startswith("copy:") else "spec-behaviour",
        "status": "open",
        **extra,
    }


def test_the_reason_for_a_text_row_shows_the_real_path_and_the_corrected_text():
    state = {
        "id": "GH-5",
        "subtasks": {"api": {"status": "approved"}},
        "findings": [_important_row("copy:1.1:src/x.py:12", proposed="Could not export.")],
    }
    reason, category = landing_fix._important_reason(state)
    assert "- src/x.py:12: wrong Corrected text: Could not export." in reason
    assert "copy:" not in reason and category == "copy-criteria-written"


def test_the_reason_for_any_other_row_is_what_it_always_was():
    state = {
        "id": "GH-5",
        "subtasks": {"api": {"status": "approved"}},
        "findings": [_important_row("x.py:1")],
    }
    reason, _ = landing_fix._important_reason(state)
    assert reason == (
        "Open important findings (final pass before merge, not a return):\n"
        "Unit `api`:\n- x.py:1: wrong"
    )


# --- GH-328: a human decision releases an escalated landing diagnose ----------------------------

ESCALATED_AT = START + timedelta(minutes=5)
ANSWERED_AT = START + timedelta(minutes=47)
LATER = START + timedelta(hours=2)


def _escalated_by_max_turns(tmp_path, **document):
    """GH-320 as the landing left it: the Diagnoser failed with `max_turns` and the task is
    escalated with `needs-human`, one undelivered attempt on the entry."""
    runner = ScriptedRunner(landing_diagnoser=_cut("max_turns"))
    ports = _merge_failed(_ports(5), runner, **document)
    _land_at(ports, tmp_path, START)
    assert _doc(ports)["escalation_class"] == "failure"
    return ports


def _answered(ports, *bodies, labels=(), state="OPEN"):
    """The issue as GH-320's was: the system's `Escalated:` comment, then what the human wrote."""
    from agentic.core.process.application.ports import IssueState

    stamps = [ESCALATED_AT, *(ANSWERED_AT for _ in bodies)]
    ports.tracker.timestamped["GH-5"] = [
        IssueComment(body, at.isoformat())
        for body, at in zip(["Escalated: Landing Diagnoser did not deliver", *bodies], stamps)
    ]
    ports.tracker.states[5] = IssueState(state, "", (), None, labels)


def _diagnoser(ports, *results):
    runner = ScriptedRunner(landing_diagnoser=list(results))
    return dataclasses.replace(ports, runner=runner), runner


def test_a_human_answer_releases_the_escalated_diagnose_and_the_role_reads_it(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    # Saved, not recorded as a closure: the fake lets a closure shadow what is saved.
    ports.documents.save({**ports.documents.last, "sections": {"Tech Lead": "keep this plan"}})
    _answered(ports, "merge master in, resolve the one conflicted test file")
    ports, runner = _diagnoser(ports, _delivery())
    launcher = FakeLauncher()

    result, lines = _land_at(ports, tmp_path, LATER, launcher)

    doc = _doc(ports)
    assert result.kind == "dispatched" and result.step == "diagnose"
    assert len(runner.calls) == 1
    prompt = runner.calls[0]["prompt"]
    assert "merge master in, resolve the one conflicted test file" in prompt
    assert "### Decision 1" in doc["sections"]["Human"]
    assert "keep this plan" in prompt  # the other sections reach the role too
    assert doc["sections"]["Tech Lead"] == "keep this plan"  # the other sections survive
    assert not doc.get("escalated") and not doc.get("escalation_class")
    assert doc["pause_log"][-1] == {"kind": "resumed"}
    assert sum(entry["kind"] == "resumed" for entry in doc["pause_log"]) == 1
    assert [e.get("released_at") for e in _diagnoses(ports)][0] == LATER.isoformat()
    assert any("escalation released by a human decision" in line for line in lines)
    assert launcher.spawned[0][:3] == ["agentic", "run", "--issue"]  # delivered at depth 0


def test_an_escalated_diagnose_with_no_answer_waits_and_names_the_human(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    ports, runner = _diagnoser(ports)
    _answered(ports)  # only the system's own comment
    before = _doc(ports)

    result, lines = _land_at(ports, tmp_path, LATER)

    assert (result.kind, result.reason, result.step) == (
        "skipped",
        "escalated_waiting_on_human",
        "diagnose",
    )
    assert any("diagnose: escalated, waiting on a human" in line for line in lines)
    assert not any("not due" in line or "already taken" in line for line in lines)
    assert runner.calls == [] and _doc(ports) == before


def test_a_second_failure_escalates_after_exactly_one_new_attempt(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in")
    ports, runner = _diagnoser(ports, *_cut("max_turns"))

    result, _ = _land_at(ports, tmp_path, LATER)

    doc = _doc(ports)
    assert result.kind == "dispatched" and doc["escalation_class"] == "failure"
    assert len(runner.calls) == len(_cut("max_turns"))  # one new attempt, no more
    assert [e.get("released_at") is not None for e in _diagnoses(ports)] == [True, False]
    assert len(landing_rules.diagnose_attempts(doc)) == 1
    assert len([t for _, t in ports.tracker.comments if t.startswith("Escalated: ")]) == 2
    calls = len(runner.calls)

    again, lines = _land_at(ports, tmp_path, LATER + timedelta(minutes=5))

    assert again.reason == "escalated_waiting_on_human" and len(runner.calls) == calls
    assert len([t for _, t in ports.tracker.comments if t.startswith("Escalated: ")]) == 2


@pytest.mark.parametrize(
    ("answer", "world"),
    [
        ("merge master in", dict(labels=("needs-human",))),
        ("merge master in", dict(labels=None)),
        ("merge master in", dict(state="CLOSED")),
    ],
    ids=["label_still_there", "labels_unreadable", "issue_closed"],
)
def test_a_comment_leaves_the_task_escalated_while_a_condition_fails(tmp_path, answer, world):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, answer, **world)
    ports, runner = _diagnoser(ports)
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    assert result.reason == "escalated_waiting_on_human"
    assert runner.calls == [] and _doc(ports) == before


def test_a_tracker_error_leaves_the_task_escalated(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in")
    ports.tracker.state_errors.add(5)
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    assert result.reason == "escalated_waiting_on_human" and _doc(ports) == before


def test_a_spent_cap_leaves_it_escalated_without_a_comment(tmp_path):
    ports = _escalated_by_max_turns(tmp_path, landing_cost_carried=300.0)
    _answered(ports, "merge master in")
    comments = list(ports.tracker.comments)
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    assert result.reason == "escalated_task_cap"
    assert ports.tracker.comments == comments and _doc(ports) == before


def test_an_escalation_that_has_no_attempt_to_retry_is_a_restart_not_a_release(tmp_path):
    """The shape `release` refuses: class `failure` and the diagnose step chosen, but no
    undelivered attempt behind it. (After a *delivered* diagnosis `landing_step` answers
    `none`, so `land` never gets here for the depth-1 escalation: its log is as it was.)"""
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(),
        escalated="stale premise",
        escalation_class="failure",
    )
    _answered(ports, "merge master in")
    before = _doc(ports)

    result, lines = _land_at(ports, tmp_path, LATER)

    assert result.reason == "escalated_needs_restart"
    assert any("agentic run --fresh" in line for line in lines)
    assert _doc(ports) == before


def test_a_depth_one_escalation_after_a_delivered_diagnosis_keeps_todays_answer(tmp_path):
    delivered = {"head": HEAD, "step": "diagnose", "at": NOW, "delivered": True}
    ports = _merge_failed(
        _ports(5),
        ScriptedRunner(),
        landing=[delivered],
        landing_restarts=1,
        escalated="stale premise",
        escalation_class="failure",
        status="waiting_on_human",
    )
    _answered(ports, "merge master in")
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    assert result.reason == "not_live" and _doc(ports) == before


def test_a_head_that_moved_after_the_decision_writes_nothing(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in")
    ports, runner = _diagnoser(ports, _delivery())
    reads: list[int] = []

    class Moving(type(ports.tracker)):
        def pull_request(self, reference):
            pull_request_number(reference)
            reads.append(1)
            found = super().pull_request(reference)
            if len(reads) > 1 and found is not None:
                return dataclasses.replace(found, head_oid="c" * 40)  # pushed after the decision
            return found

    moving = Moving()
    moving.__dict__.update(ports.tracker.__dict__)
    ports = dataclasses.replace(ports, tracker=moving)
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    assert result.reason == "head_moved"
    assert runner.calls == [] and _doc(ports) == before
    assert _doc(ports)["escalated"]


def test_the_cap_the_release_checks_is_the_spent_figure_the_diagnoser_run_receives(
    tmp_path, monkeypatch
):
    from agentic.core.process.application import run_role

    ports = _escalated_by_max_turns(tmp_path, landing_cost_carried=7.0, cost_usd=2.0)
    _answered(ports, "merge master in")
    ports, _ = _diagnoser(ports, _delivery())
    seen: list[float] = []
    real = run_role.run_with_retry

    async def spy(*args, **kwargs):
        seen.append(kwargs["spent_usd"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(run_role, "run_with_retry", spy)
    expected = rules.task_spent(_doc(ports))

    _land_at(ports, tmp_path, LATER)

    assert seen and seen[0] == expected


def _round(ports, tmp_path, now):
    from tests.core.process.application.test_poll import _Locks

    lands: list[rules.LandCandidate] = []
    poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), [], lands=lands, now=now, cfg=_cfg(tmp_path)
    )
    return lands


def test_the_poller_offers_a_released_diagnose_as_a_model_spending_land(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in")

    assert _round(ports, tmp_path, LATER) == [rules.LandCandidate(5, "land_model")]


def test_the_poller_offers_nothing_for_an_unanswered_escalated_diagnose(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports)
    assert _round(ports, tmp_path, LATER) == []
    _answered(ports, "merge master in", labels=("needs-human",))
    assert _round(ports, tmp_path, LATER) == []


def test_a_refusal_that_maps_to_another_step_is_not_released(tmp_path):
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in")
    ports.workspace.conflict_answers[(HEAD, MASTER)] = True
    ports.documents.record_closure("GH-5", {"merge_failures": None})
    before = _doc(ports)

    result, _ = _land_at(ports, tmp_path, LATER)

    # Today's behaviour for that step holds: the sync is dispatched as it always was...
    assert (result.kind, result.step) == ("dispatched", "sync_master")
    # ...and nothing of the release was written: no decision, no marker, no stamp.
    after = _doc(ports)
    assert after.get("sections") == before.get("sections")
    assert after["pause_log"] == before["pause_log"]
    assert after["escalated"] == before["escalated"]
    assert not any("released_at" in entry for entry in _diagnoses(ports))


def test_a_released_diagnose_that_delivers_a_sync_verdict_closes_and_spawns_nothing(tmp_path):
    """GH-330: the escalation a human released ends in the same decision as any delivery."""
    ports = _escalated_by_max_turns(tmp_path)
    _answered(ports, "merge master in, resolve the one conflicted test file")
    sync = RawResult(
        {
            "markdown": "### Why\n\nOne conflicted line.",
            "reason": "merge origin/master",
            "verdict": "sync",
        },
        0.4,
        3,
        "s",
    )
    ports, _ = _diagnoser(ports, sync)
    launcher = FakeLauncher()

    result, _ = _land_at(ports, tmp_path, LATER, launcher)

    assert result.kind == "dispatched" and result.step == "diagnose"
    delivered = [e for e in _diagnoses(ports) if e.get("delivered")]
    assert [(e["verdict"], e["upstream"]) for e in delivered] == [("sync", MASTER)]
    assert ports.tracker.closed_pull_requests == [] and launcher.spawned == []
    assert not _doc(ports).get("landing_restarts")
    assert "landing_restarts" not in ports.documents.closures.get("GH-5", {})
    assert not _doc(ports).get("escalated")

    again, _ = _land_at(ports, tmp_path, LATER, launcher)

    assert again.step == "sync_master"
    assert _doc(ports)["landing"][-1]["head"] == HEAD


# --- GH-374: a refusal that is only PM final validation ledger state -------------------------


def _pm_ledger(ports, *, approved: bool = True) -> None:
    pm = "PM: final validation"
    runs = [
        {"role": "PM", "section": pm, "version": v} for v in ((1, 2, 3) if approved else (1, 2))
    ]
    rows = [
        {**_finding("blocking"), "location": pm, "claim": claim, "scope": "plan", "version": v}
        for claim, v in (("Phone captures exist", 1), ("Phone captures are present", 2))
    ]
    ports.documents.record_closure("GH-5", {"findings": rows, "runs": runs})
    ports.documents.record_closure(
        "GH-5",
        {
            "change_history": [
                {"pair": PAIR_PM_TECH_LEAD.key, "category": "implementation", "reason": "x"}
            ]
            * 2
        },
    )


def test_a_settle_step_writes_plan_settled_spends_no_pass_and_the_next_decision_merges(tmp_path):
    ports = _ports(5)
    _pm_ledger(ports)
    launcher = FakeLauncher()

    result, _ = _land(ports, tmp_path, launcher)

    assert result.step == "settle"
    document = _doc(ports)
    assert [(r["claim"], r["version"], r["by"]) for r in document["plan_settled"]] == [
        ("Phone captures exist", 3, "approved"),
        ("Phone captures are present", 3, "approved"),
    ]
    [entry] = document["landing"]
    assert (entry["step"], entry["head"]) == ("settle", HEAD)
    assert entry["resolved_at"]
    assert landing_plan.spent(document, HEAD, None, MASTER).passes == 0
    assert launcher.spawned == []
    assert document["subtasks"]["only"]["status"] == "approved"
    assert _candidates(ports) == [5]


def test_rows_no_approval_settles_are_asked_once_as_questions_and_never_restart(tmp_path):
    ports = _ports(5)
    _pm_ledger(ports, approved=False)
    launcher = FakeLauncher()

    result, _ = _land(ports, tmp_path, launcher)

    assert result.step == "escalate_ledger"
    document = _doc(ports)
    assert document["escalation_class"] == "questions"
    assert document.get("plan_settled", []) == []
    [question] = document["questions_for_human"]
    assert "1: Phone captures exist" in question and "2: Phone captures are present" in question
    assert "by hand" in question
    assert ports.tracker.labels  # needs-human
    assert launcher.spawned == []

    again, _ = _land(ports, tmp_path, launcher)
    assert again.kind == "skipped"
    assert launcher.spawned == []


A_MOVE = ("docs/adr/0054-a.md", "docs/adr/0061-a.md")
WAY_OUT = "renumber the colliding ADRs by hand and push"


def test_a_duplicate_own_number_names_the_files_and_says_no_number_was_calculated():
    found = Ambiguous(
        "duplicate_own_number", "0054 is the number of docs/adr/0054-a.md, docs/adr/0054-b.md"
    )

    text = landing_fix.adr_collision_reason(found, 3)

    assert "docs/adr/0054-a.md, docs/adr/0054-b.md" in text
    assert "duplicate_own_number" in text
    assert "No number was calculated" in text
    assert "->" not in text
    assert WAY_OUT in text and "a reply without a new head" in text


def test_an_ambiguous_rewrite_lists_each_move_the_reason_and_the_line_to_fix_by_hand():
    found = Ambiguous("range", "docs/notes.md:2: ADR-0054…ADR-0060", (A_MOVE,))

    text = landing_fix.adr_collision_reason(found, 3)

    assert "docs/adr/0054-a.md -> docs/adr/0061-a.md" in text
    assert "`range`" in text
    assert "docs/notes.md:2: ADR-0054…ADR-0060" in text
    assert WAY_OUT in text and "a reply without a new head" in text


def test_a_renaming_past_its_cap_says_how_many_renumbers_were_spent_and_the_moves():
    found = Renaming((A_MOVE,), {})

    text = landing_fix.adr_collision_reason(found, 3)

    assert "3 renumbers" in text
    assert "docs/adr/0054-a.md -> docs/adr/0061-a.md" in text
    assert "master" in text
    assert WAY_OUT in text and "a reply without a new head" in text


def _adr_context(ports, tmp_path, found, lines):
    """The executor's context for a recorded, unresolved `escalate_adr` entry of `HEAD`."""
    ports.documents.record_closure(
        "GH-5",
        {
            "landing": [
                {
                    "head": HEAD,
                    "step": "escalate_adr",
                    "refusal": "adr_number_taken",
                    "at": NOW,
                    "cost_base": 0.0,
                }
            ]
        },
    )
    return LandingContext(
        "GH-5",
        _cfg(tmp_path),
        ports,
        landing_rules.LandingStep("escalate_adr", "adr_number_taken"),
        HEAD,
        Upstream("origin/master", MASTER, None),
        _facts(pr_head=HEAD, adr_collision=found),
        lines.append,
        clock=lambda: NOW,
    )


def _adr_asked(document) -> bool:
    return landing_plan.escalation_recorded(
        document, HEAD, "adr_number_taken", "escalate_adr", resolved_only=True
    )


def test_a_posted_adr_escalation_resolves_its_entry_and_escalates_as_questions(tmp_path):
    ports = _ports(5)
    found = Ambiguous("range", "docs/notes.md:2: ADR-0054…ADR-0060", (A_MOVE,))
    context = _adr_context(ports, tmp_path, found, [])

    landing_fix.escalate_adr(context)

    document = _doc(ports)
    assert document["escalation_class"] == "questions"
    [question] = document["questions_for_human"]
    assert question == landing_fix.adr_collision_reason(found, landing_rules.LANDING_RENUMBERS_MAX)
    assert ports.tracker.labels and len(ports.tracker.comments) == 1
    [entry] = document["landing"]
    assert entry["resolved_at"] == NOW
    assert _adr_asked(document)
    assert landing_rules.already_dispatched(
        landing_plan.live_entries(document), HEAD, "escalate_adr", MASTER
    )


def test_a_failed_comment_leaves_the_entry_unresolved_and_the_question_is_asked_again(tmp_path):
    ports = _ports(5)
    ports.tracker.comment_failures = 1
    found = Ambiguous("range", "docs/notes.md:2: ADR-0054…ADR-0060", (A_MOVE,))
    context = _adr_context(ports, tmp_path, found, [])

    with pytest.raises(ValueError, match="scripted"):
        landing_fix.escalate_adr(context)

    document = _doc(ports)
    [entry] = document["landing"]
    assert "resolved_at" not in entry
    assert not _adr_asked(document)
    assert ports.tracker.comments == []
    assert not landing_rules.already_dispatched(
        landing_plan.live_entries(document), HEAD, "escalate_adr", MASTER
    )
