"""`agentic perf` (GH-318, ADR-0059): measure a product against the performance budgets its own
stack declares, with no task and no role.

`measure_scenarios` is the measuring itself over a runner and a clock; `perf` is the verb
around it: it validates, takes the machine-wide lock, makes a throwaway `perf-<uuid>` worktree
at the revision under test, overlays the harness, measures and, however that ends, removes the
worktree and gives the lock back. `PerfRunner.run` is called only here, only under the lock.

The harness (the scenarios, the volume, the limits, the scripts) is the product's HEAD; the
code under test is `--revision`. Without `--overlay`, a command must be self-contained in the
revision it measures.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path, PurePosixPath
from uuid import uuid4

from agentic.shared.configuration import Configuration

from ..domain import performance
from ..domain.performance import Result, Run, Scenario
from .ports import GitError, PerfLock, PerfRunner, Ports
from .preflight import RunsOnItsOwnCheckout, refuse_its_own_checkout

STALE_PREFIX = "perf-"


class PerfExit(IntEnum):
    """The verb's exit code: the worst outcome, inconclusive over over over within."""

    WITHIN = 0
    OVER = 1
    INCONCLUSIVE = 2
    REFUSED = 3


class PerfRefused(ValueError):
    """Nothing was measured: the arguments, the product or the stack are not usable."""


class PerfInterrupted(BaseException):
    """SIGTERM or SIGHUP reached the verb. Not an `Exception`: nothing in between may swallow
    it, and the `finally` of `perf` still removes the worktree and releases the lock."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...]
    harness_sha: str
    code_sha: str
    overlay: tuple[str, ...]


def measure_scenarios(
    scenarios: Sequence[Scenario],
    checkout: Path,
    setup: Mapping[str, str],
    runner: PerfRunner,
    deadline: Callable[[], float],
    config: Configuration,
) -> list[Result]:
    """Setup once, then each scenario in order: one seed and `FIRST_RUNS` measures, and, only
    when the reason is `spread`, a re-seed and `RETRY_RUNS` measures that replace them. Each
    command is bounded by `perf_timeout_s` capped by what `deadline()` has left; a
    scenario that finds nothing left is `not_started`. Neither a failure nor a timeout raises."""
    spread = config.perf_spread_max
    for command in setup.values():
        run = _run(runner, checkout, command, {}, deadline, config)
        if run.timed_out or run.exit_code != 0:
            return [performance.verdict(s, run, [], spread) for s in scenarios]
    results: list[Result] = []
    for scenario in scenarios:
        if deadline() <= 0:
            results.append(performance.not_started(scenario))
            continue
        result = _pass(scenario, checkout, performance.FIRST_RUNS, runner, deadline, config)
        if result.reason == "spread" and deadline() > 0:
            result = _pass(scenario, checkout, performance.RETRY_RUNS, runner, deadline, config)
        results.append(result)
    return results


def _pass(
    scenario: Scenario,
    checkout: Path,
    count: int,
    runner: PerfRunner,
    deadline: Callable[[], float],
    config: Configuration,
) -> Result:
    env = {
        "PERF_VOLUME": _number(scenario.volume),
        "PERF_VOLUME_UNIT": scenario.volume_unit,
    }
    seed = _run(runner, checkout, scenario.seed, env, deadline, config)
    runs: list[Run] = []
    if not seed.timed_out and seed.exit_code == 0:
        for _ in range(count):
            run = _run(runner, checkout, scenario.measure, env, deadline, config)
            runs.append(run)
            if run.timed_out or run.exit_code != 0 or run.measurement is None:
                break  # the verdict is final whatever follows
    return performance.verdict(scenario, seed, runs, config.perf_spread_max)


def _run(
    runner: PerfRunner,
    checkout: Path,
    command: str,
    env: Mapping[str, str],
    deadline: Callable[[], float],
    config: Configuration,
) -> Run:
    left = deadline()
    if left <= 0:
        # The node's budget ran out between two commands: a timeout, and nothing is started.
        return Run(None, -1, True, None, None)
    return runner.run(checkout, command, env, min(config.perf_timeout_s, left))


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(value)


def perf(
    product: str,
    scenario: str | None,
    revision: str | None,
    overlay: Sequence[str],
    ports: Ports,
    runner: PerfRunner,
    lock: PerfLock,
    config: Configuration,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[PerfExit, Report]:
    """The verb. Raises `PerfRefused` for anything that measured nothing."""
    try:
        refuse_its_own_checkout(config, ports)
        selected = _selected(product, scenario, ports)
        setup = ports.stack.setup("HEAD")
    except RunsOnItsOwnCheckout as exc:
        raise PerfRefused(str(exc)) from exc
    except ValueError as exc:
        raise PerfRefused(str(exc)) from exc
    head = ports.workspace.full_head(config.product_dir)
    code = revision or head
    paths = tuple(overlay)
    _check_overlay(paths, head, ports, config)
    if not lock.acquire(config.perf_lock_wait_s):
        results = tuple(performance.busy(s) for s in selected)
        return PerfExit.INCONCLUSIVE, Report(results, head, code, paths)
    started = clock()
    name = f"{STALE_PREFIX}{uuid4().hex}"
    try:
        _sweep(ports)
        try:
            checkout = ports.workspace.detached_checkout(name, code)
            if paths:
                ports.workspace.overlay(checkout, head, paths)
        except GitError as exc:
            raise PerfRefused(f"could not prepare the checkout of {code}: {exc}") from exc
        measured = measure_scenarios(
            selected,
            checkout,
            setup,
            runner,
            lambda: config.perf_node_timeout_s - (clock() - started),
            config,
        )
    finally:
        ports.workspace.remove_checkout(name)
        lock.release()
    report = Report(tuple(measured), head, code, paths)
    return _exit(performance.overall(measured)), report


def _selected(product: str, scenario: str | None, ports: Ports) -> list[Scenario]:
    name = ports.stack.product()
    if name != product:
        raise PerfRefused(f"{product!r} is not the product in this directory ({name!r})")
    declared = ports.stack.performance("HEAD")
    if not declared:
        raise PerfRefused(f"{product} declares no `performance` in its stack")
    if scenario is None:
        return list(declared.values())
    if scenario not in declared:
        raise PerfRefused(f"no scenario {scenario!r}; the stack declares: {', '.join(declared)}")
    return [declared[scenario]]


def _check_overlay(paths: Sequence[str], head: str, ports: Ports, config: Configuration) -> None:
    root = config.product_dir.resolve()
    for relative in paths:
        parts = PurePosixPath(relative).parts
        if not relative or relative.startswith("/") or ".." in parts:
            raise PerfRefused(f"overlay path {relative!r} must be relative and stay in the product")
        if not (root / relative).resolve().is_relative_to(root):
            raise PerfRefused(f"overlay path {relative!r} resolves outside the product")
        if ports.workspace.show(head, relative.rstrip("/")) is None:
            raise PerfRefused(f"overlay path {relative!r} is not in the product's HEAD")


def _sweep(ports: Ports) -> None:
    """Every `perf-*` checkout is stale by construction: the lock is ours, so no other verb
    is using one. A hard kill is what left it."""
    for stale in ports.workspace.checkout_names(STALE_PREFIX):
        ports.reaper.reap_within([ports.workspace.checkout_path(stale)])
        ports.workspace.remove_checkout(stale)


def _exit(outcome: performance.Outcome) -> PerfExit:
    return {
        "within": PerfExit.WITHIN,
        "over": PerfExit.OVER,
        "inconclusive": PerfExit.INCONCLUSIVE,
    }[outcome]
