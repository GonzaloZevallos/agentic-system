"""`measure_scenarios` and `perf` (GH-318, ADR-0058) over a fake runner, a fake lock, a fake
clock and the in-memory workspace and stack: no process, no git, no real time."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from agentic.core.process.application.perf import (
    PerfExit,
    PerfRefused,
    measure_scenarios,
    perf,
)
from agentic.core.process.domain.performance import Limit, Measurement, Result, Run, Scenario
from agentic.shared.configuration import Configuration
from tests.fakes import FakePerfLock, FakePerfRunner, FakeReaper, MemoryStack, memory_ports
from tests.fakes import MemoryWorkspace as Workspace

CHECKOUT = Path("/memory/worktrees/perf-x")


def scenario(name: str = "list", volume: float = 1000) -> Scenario:
    limits = {"a_ms": Limit(100, "ms")}
    return Scenario(name, f"seed {name}", f"measure {name}", volume, "rows", limits)


def good(value: float = 50.0) -> Run:
    return Run(Measurement(1000, "rows", {"a_ms": value}), 0, False, 0.5, 0.6)


def seed() -> Run:
    return Run(None, 0, False, 0.5, 0.5)


def timed_out() -> Run:
    return Run(None, -9, True, 0.5, 0.5)


def measure(
    runner: FakePerfRunner,
    tmp_path: Path,
    scenarios: list[Scenario] | None = None,
    setup: Mapping[str, str] | None = None,
    left: float = 1000.0,
) -> list[Result]:
    cfg = Configuration(product_dir=tmp_path)
    return measure_scenarios(
        scenarios or [scenario()], CHECKOUT, setup or {}, runner, lambda: left, cfg
    )


def test_five_measures_after_one_seed_with_the_volume_in_the_environment(tmp_path):
    runner = FakePerfRunner([seed(), *[good() for _ in range(5)]])
    (result,) = measure(runner, tmp_path)
    assert (result.outcome, result.runs) == ("within", 5)
    assert [c[1] for c in runner.calls] == ["seed list", *["measure list"] * 5]
    assert all(c[2] == {"PERF_VOLUME": "1000", "PERF_VOLUME_UNIT": "rows"} for c in runner.calls)
    assert all(c[0] == CHECKOUT and c[3] == 1000 for c in runner.calls)


def test_a_wide_spread_reseeds_and_measures_nine_that_replace_the_first_five(tmp_path):
    first = [good(v) for v in (10, 50, 90, 50, 50)]
    runner = FakePerfRunner([seed(), *first, seed(), *[good(50) for _ in range(9)]])
    (result,) = measure(runner, tmp_path)
    assert (result.outcome, result.runs) == ("within", 9)
    assert [c[1] for c in runner.calls].count("seed list") == 2
    assert len(runner.calls) == 1 + 5 + 1 + 9


def test_a_spread_that_stays_wide_after_the_retry_is_inconclusive_and_not_retried_twice(tmp_path):
    wide = [good(v) for v in (10, 50, 90, 50, 50, 10, 90, 50, 50)]
    runner = FakePerfRunner([seed(), *wide[:5], seed(), *wide])
    (result,) = measure(runner, tmp_path)
    assert (result.outcome, result.reason, result.runs) == ("inconclusive", "spread", 9)
    assert len(runner.calls) == 16


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([seed(), Run(None, 1, False, None, None)], "broken"),
        ([seed(), *[Run(Measurement(10, "rows", {"a_ms": 5.0}), 0, False, 0, 0)] * 5], "volume"),
        ([seed(), timed_out()], "timeout"),
    ],
)
def test_broken_volume_and_timeout_are_final_after_one_pass(tmp_path, script, reason):
    runner = FakePerfRunner(script)
    (result,) = measure(runner, tmp_path)
    assert (result.outcome, result.reason) == ("inconclusive", reason)
    assert [c[1] for c in runner.calls].count("seed list") == 1


def test_a_failed_seed_ends_the_scenario_with_no_measure(tmp_path):
    runner = FakePerfRunner([Run(None, 3, False, None, None)])
    (result,) = measure(runner, tmp_path)
    assert (result.reason, result.runs) == ("broken", 0)
    assert [c[1] for c in runner.calls] == ["seed list"]


def test_a_failed_setup_breaks_every_scenario_and_a_timed_out_one_times_them_out(tmp_path):
    two = [scenario("a"), scenario("b")]
    failing = FakePerfRunner([Run(None, 1, False, None, None)])
    results = measure(failing, tmp_path, two, {"deps": "uv sync"})
    assert [(r.scenario, r.reason) for r in results] == [("a", "broken"), ("b", "broken")]
    assert [c[1] for c in failing.calls] == ["uv sync"]
    stuck = FakePerfRunner([timed_out()])
    results = measure(stuck, tmp_path, two, {"deps": "uv sync"})
    assert [r.reason for r in results] == ["timeout", "timeout"]


def test_the_budget_left_caps_each_command_and_a_spent_one_starts_nothing(tmp_path):
    runner = FakePerfRunner([seed(), *[good() for _ in range(5)]])
    measure(runner, tmp_path, left=7.0)
    assert {c[3] for c in runner.calls} == {7.0}
    idle = FakePerfRunner()
    (result,) = measure(idle, tmp_path, left=0.0)
    assert (result.reason, result.started) == ("timeout", False)
    assert idle.calls == []


def test_three_scenarios_where_the_first_exhausts_the_budget(tmp_path):
    left = [100.0]

    class Spending(FakePerfRunner):
        def run(
            self, checkout: Path, command: str, extra_env: Mapping[str, str], timeout_s: float
        ) -> Run:
            run = super().run(checkout, command, extra_env, timeout_s)
            if run.timed_out:
                left[0] = 0.0
            return run

    runner = Spending([seed(), timed_out()])
    scenarios = [scenario("a"), scenario("b"), scenario("c")]
    cfg = Configuration(product_dir=tmp_path)
    results = measure_scenarios(scenarios, CHECKOUT, {}, runner, lambda: left[0], cfg)
    assert [(r.reason, r.started) for r in results] == [
        ("timeout", True),
        ("timeout", False),
        ("timeout", False),
    ]
    assert len(runner.calls) == 2


def ports_for(*scenarios: Scenario, workspace: Workspace | None = None, reaper=None):
    stack = MemoryStack(
        product="shop", setup={"deps": "uv sync"}, performance={s.name: s for s in scenarios}
    )
    return memory_ports(stack=stack, workspace=workspace, reaper=reaper)


def run_perf(tmp_path, ports, runner, lock, scenario_name=None, overlay=()):
    cfg = Configuration(product_dir=tmp_path)
    return perf("shop", scenario_name, None, overlay, ports, runner, lock, cfg)


def test_the_exit_follows_the_worst_outcome_and_the_worktree_is_made_and_removed(tmp_path):
    ports = ports_for(scenario("a"), scenario("b"))
    runner = FakePerfRunner(
        [Run(None, 0, False, 0, 0), seed(), *[good(50) for _ in range(5)], seed()]
        + [good(500) for _ in range(5)]
    )
    lock = FakePerfLock()
    code, report = run_perf(tmp_path, ports, runner, lock)
    assert code is PerfExit.OVER
    assert [r.outcome for r in report.results] == ["within", "over"]
    (made,) = ports.workspace.detached
    assert made.startswith("perf-") and ports.workspace.removed_checkouts == [made]
    assert lock.acquired == [1800] and lock.released == 1
    assert report.harness_sha == report.code_sha == "base"


def test_a_busy_lock_makes_every_scenario_busy_with_no_worktree_and_exit_two(tmp_path):
    ports = ports_for(scenario("a"), scenario("b"))
    runner = FakePerfRunner()
    code, report = run_perf(tmp_path, ports, runner, FakePerfLock(available=False))
    assert code is PerfExit.INCONCLUSIVE
    assert [r.reason for r in report.results] == ["busy", "busy"]
    assert ports.workspace.detached == [] and runner.calls == []


def test_a_named_scenario_alone_is_measured(tmp_path):
    ports = ports_for(scenario("a"), scenario("b"))
    runner = FakePerfRunner([seed(), seed(), *[good() for _ in range(5)]])
    code, report = run_perf(tmp_path, ports, runner, FakePerfLock(), "b")
    assert code is PerfExit.WITHIN and [r.scenario for r in report.results] == ["b"]


def test_stale_perf_worktrees_are_reaped_then_removed_before_the_new_one(tmp_path):
    workspace = Workspace()
    workspace.stale_checkouts = ["perf-old", "perf-older"]
    reaper = FakeReaper()
    ports = ports_for(scenario(), workspace=workspace, reaper=reaper)
    runner = FakePerfRunner([seed(), seed(), *[good() for _ in range(5)]])
    run_perf(tmp_path, ports, runner, FakePerfLock())
    assert workspace.removed_checkouts[:2] == ["perf-old", "perf-older"]
    assert len(workspace.removed_checkouts) == 3
    assert reaper.within_calls == [
        (workspace.checkout_path("perf-old"),),
        (workspace.checkout_path("perf-older"),),
    ]


def test_the_worktree_is_removed_and_the_lock_released_when_the_runner_raises(tmp_path):
    class Exploding(FakePerfRunner):
        def run(
            self, checkout: Path, command: str, extra_env: Mapping[str, str], timeout_s: float
        ) -> Run:
            raise RuntimeError("boom")

    ports = ports_for(scenario())
    lock = FakePerfLock()
    with pytest.raises(RuntimeError):
        run_perf(tmp_path, ports, Exploding(), lock)
    assert len(ports.workspace.removed_checkouts) == 1 and lock.released == 1


def test_overlay_paths_are_copied_from_head_after_the_checkout(tmp_path):
    workspace = Workspace()
    workspace.files[("base", "scripts")] = "tree"
    ports = ports_for(scenario(), workspace=workspace)
    runner = FakePerfRunner([seed(), seed(), *[good() for _ in range(5)]])
    _, report = run_perf(tmp_path, ports, runner, FakePerfLock(), overlay=["scripts"])
    checkout = workspace.checkout_path(workspace.detached[0])
    assert workspace.overlays == [(checkout, "base", ("scripts",))]
    assert report.overlay == ("scripts",)


@pytest.mark.parametrize("overlay", [["/etc/passwd"], ["../x"], ["a/../../x"], ["missing"], [""]])
def test_a_bad_overlay_path_is_refused_before_anything_is_created(tmp_path, overlay):
    lock = FakePerfLock()
    ports = ports_for(scenario())
    with pytest.raises(PerfRefused):
        run_perf(tmp_path, ports, FakePerfRunner(), lock, overlay=overlay)
    assert ports.workspace.detached == [] and lock.acquired == []


def test_other_refusals_measure_nothing(tmp_path):
    lock = FakePerfLock()
    ports = ports_for(scenario("a"))
    with pytest.raises(PerfRefused, match="no scenario"):
        run_perf(tmp_path, ports, FakePerfRunner(), lock, "nope")
    with pytest.raises(PerfRefused, match="declares no"):
        run_perf(tmp_path, ports_for(), FakePerfRunner(), lock)
    other = Configuration(product_dir=tmp_path)
    with pytest.raises(PerfRefused, match="not the product"):
        perf("other", None, None, (), ports, FakePerfRunner(), lock, other)
    broken = memory_ports(stack=MemoryStack(product=None))
    with pytest.raises(PerfRefused):
        run_perf(tmp_path, broken, FakePerfRunner(), lock)
    assert lock.acquired == []


def test_a_git_failure_creating_the_worktree_is_refused_and_still_releases_the_lock(tmp_path):
    workspace = Workspace()
    workspace.checkout_error = "bad revision"
    lock = FakePerfLock()
    ports = ports_for(scenario(), workspace=workspace)
    with pytest.raises(PerfRefused, match="bad revision"):
        run_perf(tmp_path, ports, FakePerfRunner(), lock)
    assert lock.released == 1 and len(workspace.removed_checkouts) == 1


def test_the_product_being_the_repo_the_system_runs_from_is_refused(tmp_path, monkeypatch):
    workspace = Workspace()
    repo = Path("/the/system")
    workspace.repositories = {repo: repo, tmp_path: repo}
    monkeypatch.setattr("agentic.core.process.application.preflight.CHECKOUT", repo)
    ports = ports_for(scenario(), workspace=workspace)
    with pytest.raises(PerfRefused, match="repository this package runs from"):
        run_perf(tmp_path, ports, FakePerfRunner(), FakePerfLock())
