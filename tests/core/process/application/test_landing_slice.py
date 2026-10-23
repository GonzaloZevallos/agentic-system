"""GH-297, subtask `slice-executor`: `land_slice` through the landing tick over fake
Workspace and Tracker, reproducing GH-53 (three approved units, the sync budget spent)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentic.core.process.application import landing
from agentic.core.process.application.ports import RawResult, Upstream
from agentic.core.process.domain.ci import CiCheck, CiReport
from agentic.core.process.domain.task import PullRequest
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _HeldLocks, _ports
from tests.fakes import FakeLauncher, FakeReaper, MemoryWorkspace, ScriptedRunner

SLICE_URL = URL.format(9)
SLICE_HEAD = "5" * 40
HEADS = {"a": "1" * 40, "b": "2" * 40, "c": "3" * 40}
NOW = "2026-10-22T00:00:00+00:00"


class _Paths(MemoryWorkspace):
    """`changed_paths` per `(base, head)`: master's changes since a unit's head are
    `moved`, a unit's own are `own[unit]`."""

    def __init__(self) -> None:
        super().__init__()
        self.moved: tuple[str, ...] = ("other.py",)
        self.own: dict[str, tuple[str, ...]] = {
            "1" * 40: ("a.py",),
            "2" * 40: ("b.py",),
            "3" * 40: ("c.py",),
        }

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        if head == MASTER:
            return self.moved
        return self.own.get(head, ())


def _world(integration: frozenset[str], runner: ScriptedRunner | None = None):
    ports = _ports(5, checks=False, merge_attempts={"head": HEAD, "count": 3})
    workspace = _Paths()
    workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    workspace.conflict_answers[(HEAD, MASTER)] = False
    workspace.integration_work = integration
    workspace.ancestors_by_ref[SLICE_HEAD] = {HEADS["a"], HEADS["b"]}
    workspace.ancestors_by_ref[MASTER] = set()
    ports = dataclasses.replace(ports, workspace=workspace)
    if runner is not None:
        ports = dataclasses.replace(ports, runner=runner)
    ports.tracker.url = SLICE_URL
    ports.tracker.prs[SLICE_URL] = PullRequest(
        state="OPEN",
        created_at="2026-10-22T00:00:00Z",
        merged_at=None,
        commits=(),
        head_oid=SLICE_HEAD,
    )
    document = ports.documents.load("GH-5") or {}
    ports.documents.save(
        {
            **document,
            "title": "t",
            "description": "d",
            "subtasks": {
                name: {"id": name, "status": "approved", "reviewed_head": head, "depends_on": []}
                for name, head in HEADS.items()
            }
            | {
                "integration": {
                    "id": "integration",
                    "status": "approved",
                    "reviewed_head": HEAD,
                    "depends_on": list(HEADS),
                }
            },
            "landing": [
                {
                    "head": f"{i}" * 40,
                    "step": "sync_master",
                    "refusal": "attempts_exhausted",
                    "resolved_at": "x",
                    "upstream": MASTER,
                }
                for i in range(6)
            ],
        }
    )
    return ports


def _land(ports, launcher=None):
    return landing.land(
        "GH-5",
        _cfg(Path("/nonexistent")),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda line: None,
        clock=lambda: NOW,
        launcher=launcher,
        resume=lambda number: 777,
    )


def _doc(ports):
    return ports.documents.load("GH-5") or {}


def test_gh53_disjoint_paths_land_a_slice_then_the_main_pr_syncs_and_does_not_restart():
    ports = _world(frozenset({"integration.py"}))
    launcher = FakeLauncher()

    first = _land(ports, launcher)

    assert (first.kind, first.step) == ("dispatched", "land_slice")
    doc = _doc(ports)
    assert ports.tracker.merge_calls == [(SLICE_URL, SLICE_HEAD)]
    assert ports.tracker.closes_issue_calls == [False]
    assert doc["pull_request"] == URL.format(5)
    assert [s["units"] for s in doc["landed_slices"]] == [["a", "b"]]
    entry = doc["landing"][-1]
    assert entry["step"] == "land_slice" and entry["slice_units"] == ["a", "b"]
    assert entry["slice_pull_request"] == SLICE_URL and entry["resolved_at"]
    assert ports.workspace.slice_builds[0][3] == "agentic/GH-5.slice-1"
    assert launcher.spawned == []  # no restart

    ports.workspace.ancestors_by_ref[MASTER] = {HEADS["a"], HEADS["b"]}
    second = _land(ports, launcher)

    assert second.step == "sync_master"  # a new sync epoch: the budget is not spent
    assert launcher.spawned == [] or all("--fresh" not in argv for argv in launcher.spawned)


def test_gh53_crossed_paths_restart_with_the_reason_no_clean_slice():
    runner = ScriptedRunner(
        landing_diagnoser=[RawResult({"markdown": "### Why\n\nx", "reason": "r"}, 0.1, 1, "s")]
    )
    ports = _world(frozenset({"a.py", "b.py", "c.py"}), runner)

    result = _land(ports, FakeLauncher())

    assert result.step == "diagnose"
    entry = _doc(ports)["landing"][-1]
    assert (entry["step"], entry["restart_why"]) == ("diagnose", "no_clean_slice")
    assert entry.get("integration_work") is True  # the restart comment's "(integration work)"
    assert ports.tracker.merge_calls == [] and ports.tracker.closes_issue_calls == []


def test_gh53_master_moving_over_the_units_own_paths_restarts_with_no_clean_slice():
    runner = ScriptedRunner(
        landing_diagnoser=[RawResult({"markdown": "### Why\n\nx", "reason": "r"}, 0.1, 1, "s")]
    )
    ports = _world(frozenset({"integration.py"}), runner)  # the integration paths are disjoint
    ports.workspace.moved = ("a.py", "b.py", "c.py")  # master changed what each unit changed

    result = _land(ports, FakeLauncher())

    assert result.step == "diagnose"
    entry = _doc(ports)["landing"][-1]
    assert (entry["step"], entry["restart_why"]) == ("diagnose", "no_clean_slice")
    assert "integration_work" not in entry  # master, not the integrator, blocked the slices
    assert ports.tracker.merge_calls == [] and ports.workspace.slice_builds == []


def _ci(ports, conclusion: str | None, status: str = "COMPLETED", head: str = SLICE_HEAD):
    ports.stack.declared_ci_all = ("ci",)
    checks = (CiCheck("ci", status, conclusion, "link"),)
    ports.tracker.ci_reports[SLICE_URL] = CiReport(head, None, None, "2026-10-22T00:00:00Z", checks)
    # the task's own pull request is green on CI, so its budget (the sync attempts) is what ends
    green = (CiCheck("ci", "COMPLETED", "SUCCESS", "link"),)
    ports.tracker.ci_reports[URL.format(5)] = CiReport(HEAD, "master", MASTER, NOW, green)


def test_a_slice_whose_ci_turns_red_after_opening_is_closed_and_its_entry_refused():
    ports = _world(frozenset({"integration.py"}))
    _ci(ports, None, status="IN_PROGRESS")
    _land(ports)
    assert ports.tracker.merge_calls == [] and ports.tracker.closed_pull_requests == []

    _ci(ports, "FAILURE")
    _land(ports)

    entry = _doc(ports)["landing"][-1]
    assert entry["step"] == "land_slice" and entry["closed_at"] and "resolved_at" not in entry
    assert ports.tracker.closed_pull_requests == [SLICE_URL]
    assert ports.tracker.merge_calls == [] and _doc(ports).get("landed_slices", []) == []


def test_a_slice_whose_master_moved_after_opening_is_closed_and_its_entry_refused():
    ports = _world(frozenset({"integration.py"}))
    _ci(ports, None, status="IN_PROGRESS")
    _land(ports)

    moved = "9" * 40
    ports.workspace.upstream_answer = Upstream("origin/master", moved, None)
    landing.land(
        "GH-5",
        _cfg(Path("/nonexistent")),
        ports,
        _HeldLocks(),
        Upstream("origin/master", moved, None),
        frozenset(),
        lambda line: None,
        clock=lambda: NOW,
        launcher=None,
        resume=lambda number: 777,
    )

    entry = _doc(ports)["landing"][-1]
    assert entry["closed_at"] and "resolved_at" not in entry
    assert ports.tracker.closed_pull_requests == [SLICE_URL] and ports.tracker.merge_calls == []


def test_a_slice_waiting_for_its_ci_stays_open_and_merges_the_same_pull_request_later():
    ports = _world(frozenset({"integration.py"}))
    _ci(ports, None, status="IN_PROGRESS")

    _land(ports)

    entry = _doc(ports)["landing"][-1]
    assert entry["slice_pull_request"] == SLICE_URL
    assert "resolved_at" not in entry and "closed_at" not in entry
    assert ports.tracker.merge_calls == [] and len(ports.workspace.slice_builds) == 1

    _ci(ports, "SUCCESS")
    _land(ports)

    assert ports.tracker.merge_calls == [(SLICE_URL, SLICE_HEAD)]
    assert len(ports.workspace.slice_builds) == 1  # the same pull request, not rebuilt
    assert [s["units"] for s in _doc(ports)["landed_slices"]] == [["a", "b"]]
    assert _doc(ports)["landing"][-1]["resolved_at"]


def test_a_crash_between_the_merge_and_the_record_does_not_land_the_slice_twice():
    ports = _world(frozenset({"integration.py"}))
    ports.workspace.ancestors_by_ref[MASTER] = {HEADS["a"], HEADS["b"]}  # git: it landed
    doc = _doc(ports)
    ports.documents.save(
        {
            **doc,
            "landing": [
                *doc["landing"],
                {
                    "head": HEAD,
                    "step": "land_slice",
                    "refusal": "attempts_exhausted",
                    "at": NOW,
                    "slice_units": ["a", "b"],
                    "slice_pull_request": SLICE_URL,
                },
            ],
        }
    )

    _land(ports)

    saved = _doc(ports)
    assert [s["units"] for s in saved["landed_slices"]] == [["a", "b"]]
    assert saved["landing"][-1]["resolved_at"]
    assert ports.tracker.merge_calls == [] and ports.workspace.slice_builds == []
    assert ports.tracker.closes_issue_calls == []


def test_a_refused_slice_is_closed_and_its_set_is_excluded_after_two_refusals():
    ports = _world(frozenset({"integration.py"}))
    ports.workspace.slice_conflicts = {HEADS["a"]}  # the build does not merge on master

    _land(ports)
    _land(ports)
    third = _land(ports, FakeLauncher())

    slices = [e for e in _doc(ports)["landing"] if e["step"] == "land_slice"]
    assert [e["slice_units"] for e in slices] == [["a", "b"], ["a", "b"], ["a"]]
    assert all(e["closed_at"] and "resolved_at" not in e for e in slices)
    assert third.step == "land_slice"  # {a, b} is excluded: the smaller prefix is next
    assert ports.tracker.merge_calls == []


def test_the_slice_checkout_is_reaped_before_it_is_removed():
    ports = _world(frozenset({"integration.py"}))
    journal: list[str] = []
    assert isinstance(ports.workspace, MemoryWorkspace) and isinstance(ports.reaper, FakeReaper)
    ports.workspace.journal = journal
    ports.reaper.journal = journal

    _land(ports, FakeLauncher())

    assert journal == ["reap:slice-GH-5", "remove:slice-GH-5"]
