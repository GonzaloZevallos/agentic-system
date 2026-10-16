"""GH-263, subtask land-kind: a `land` child is registered under a kind fixed at spawn
(`land_checks` or `land_model`), and `landing.land` refuses, before it records or dispatches
anything, a step that kind was not registered for: a `land_checks` child never runs a model
pass and never spawns a resume or a diagnoser, even when the head moved since the poller
classified it."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentic.core.process.application import landing
from agentic.core.process.application.ports import Upstream, UpstreamMerge
from tests.core.process.application.test_land import _Clock, _Spawner, _stuck
from tests.core.process.application.test_land import _land as _land_stuck
from tests.core.process.application.test_landing_fix import (
    MARGIN,
    NOW,
    _doc,
    _land_at,
    _stranded_ports,
    _untouched,
    _window,
)
from tests.core.process.application.test_landing_sync import NEW, _behind, _diffs
from tests.core.process.application.test_merge_ci import _check, _report
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _HeldLocks,
    _ports,
)
from tests.fakes import FakeLauncher, MemoryStack


def _land(ports, tmp_path, run_kind, launcher, resume=None):
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
        resume=resume,
        argv0="agentic",
        run_kind=run_kind,
    )
    return result, lines


def _failed_local_check_after_a_rerun(ports) -> None:
    """Step `fix`: the local check failed on the head after the rerun was spent."""
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


def _red_ci(tmp_path):
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=("web",))
    )
    ports.tracker.failed_runs = ["https://github.com/o/r/actions/runs/1"]
    ports.tracker.ci_reports = [_report(_check(conclusion="FAILURE"))] * 3
    return ports


def test_a_checks_child_whose_step_is_a_rerun_proceeds_exactly_as_a_model_child_would(tmp_path):
    ports = _red_ci(tmp_path)

    result, _ = _land(ports, tmp_path, "land_checks", FakeLauncher())

    assert (result.kind, result.step) == ("dispatched", "rerun")
    assert ports.tracker.rerun_calls == [(URL.format(5), HEAD)]
    assert [e["step"] for e in _doc(ports)["landing"]] == ["rerun"]


def test_a_checks_child_that_finds_a_fix_records_nothing_and_spawns_nothing(tmp_path):
    ports = _ports(5, checks=False)
    _failed_local_check_after_a_rerun(ports)
    before = dict(_doc(ports))
    closures = dict(ports.documents.closures)
    launcher, resumed = FakeLauncher(), _Spawner()

    result, lines = _land(ports, tmp_path, "land_checks", launcher, resumed)

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert _doc(ports) == before and ports.documents.closures == closures
    assert launcher.spawned == [] and resumed.spawned == []
    assert any(
        "skipped: run_kind_mismatch (registered land_checks, step fix)" in line for line in lines
    )


def test_the_same_fix_under_a_model_child_is_dispatched(tmp_path):
    ports = _ports(5, checks=False)
    _failed_local_check_after_a_rerun(ports)

    result, _ = _land(ports, tmp_path, "land_model", FakeLauncher())

    assert (result.kind, result.step) == ("dispatched", "fix")


def test_a_checks_child_that_finds_a_conflicting_sync_records_nothing_and_spawns_nothing(tmp_path):
    ports = _ports(
        5,
        checks=False,
        merge_checks={"head": HEAD, "master": MASTER, "passed": False, "failed_step": "conflict"},
    )
    ports.workspace.upstream_merges[MASTER] = UpstreamMerge(
        clean=False, conflicted=("shared.py",), head=HEAD
    )
    before = dict(_doc(ports))
    launcher, resumed = FakeLauncher(), _Spawner()

    result, lines = _land(ports, tmp_path, "land_checks", launcher, resumed)

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert _doc(ports) == before and not ports.documents.closures.get("GH-5", {}).get("landing")
    assert launcher.spawned == [] and resumed.spawned == []
    assert any("step sync_master" in line for line in lines)


def test_a_checks_child_that_finds_a_clean_but_overlapping_sync_spawns_no_resume(tmp_path):
    ports, _ = _behind(tmp_path)
    _diffs(ports, merged=("feature.py",), own=("feature.py",))
    before, closures = dict(_doc(ports)), dict(ports.documents.closures)
    launcher, resumed = FakeLauncher(), _Spawner()

    result, lines = _land(ports, tmp_path, "land_checks", launcher, resumed)

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert _doc(ports) == before and ports.documents.closures == closures
    assert launcher.spawned == [] and resumed.spawned == [] and not ports.workspace.pushed
    assert any("step sync_master" in line for line in lines)
    # The same document under a model child reopens the task and spawns the resume.
    again, _ = _land(ports, tmp_path, "land_model", launcher, resumed)
    assert (again.kind, again.step) == ("dispatched", "sync_master")
    assert resumed.spawned == [5]


def test_a_checks_child_that_finds_a_sync_conflicting_at_run_time_spawns_no_resume(tmp_path):
    ports, _ = _behind(tmp_path)
    ports.workspace.upstream_merges[MASTER] = UpstreamMerge(
        clean=False, conflicted=("shared.py",), head=NEW
    )
    before, closures = dict(_doc(ports)), dict(ports.documents.closures)
    launcher, resumed = FakeLauncher(), _Spawner()

    result, _ = _land(ports, tmp_path, "land_checks", launcher, resumed)

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert _doc(ports) == before and ports.documents.closures == closures
    assert launcher.spawned == [] and resumed.spawned == []


def test_a_checks_child_does_not_respawn_a_reopened_pass_whose_child_is_gone():
    ports, spawner, clock, lines = _stuck(), _Spawner(), _Clock(), []
    before = dict(ports.documents.load("GH-5") or {})

    result = landing.land(
        "GH-5",
        _cfg_stuck(),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        clock=clock,
        resume=spawner,
        run_kind="land_checks",
    )

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert spawner.spawned == [] and ports.documents.load("GH-5") == before
    assert any("step recovery" in line for line in lines)
    # The same document under the default kind is respawned: the guard is the kind's.
    assert _land_stuck(ports, spawner, clock).kind == "dispatched"
    assert spawner.spawned == [5]


def test_a_checks_child_does_not_free_a_stranded_diagnosis(tmp_path):
    ports = _stranded_ports()
    before = _doc(ports)
    launcher = FakeLauncher()

    result = _stranded(ports, tmp_path, launcher)

    assert result == landing.LandResult("skipped", "run_kind_mismatch")
    assert _untouched(ports) and _doc(ports) == before and launcher.spawned == []


def _stranded(ports, tmp_path, launcher):
    return landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda line: None,
        clock=lambda: (_window() - MARGIN).isoformat(),
        launcher=launcher,
        argv0="agentic",
        run_kind="land_checks",
    )


def _cfg_stuck():
    return _cfg(Path("/nonexistent"))


def test_land_at_default_kind_still_frees_the_stranded_diagnosis(tmp_path):
    ports = _stranded_ports()

    _land_at(ports, tmp_path, _window() - MARGIN)

    assert list(ports.documents.closures) == ["GH-5"]
