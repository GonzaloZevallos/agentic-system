"""`run_check` over the product's declared verifications (ADR-0065 addendum, GH-451): the
name resolves against `checks` and `verifications`, the command runs under its own resolved
wall, and a verification is never a pass the gate may reuse."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck, resolved_walls
from agentic.core.process.domain import run_check as rules
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.run_check import Verification, WallOverCeiling
from tests.fakes import MemoryCheckResults, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-1.api")
SETUP = ("uv sync",)
STAGE0 = "a" * 40
CHECK_TIMEOUT = 30.0
COMPARE = Verification("scripts/design/compare.sh --strict", 900.0)


class TimedCommands(MemoryCommands):
    """`MemoryCommands` that also keeps the wall each command was given."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.walls: list[float] = []

    def run(
        self,
        cwd: Path,
        command: str,
        timeout_s: float,
        *,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        self.walls.append(timeout_s)
        return super().run(cwd, command, timeout_s, env=env)


def tool(
    commands: TimedCommands | None = None,
    *,
    verifications: dict[str, Verification] | None = None,
    check_timeouts: dict[str, float] | None = None,
    ceiling: float = 3600.0,
    witness: str | None = None,
    store: MemoryCheckResults | None = None,
) -> tuple[RunCheck, MemoryCheckResults, MemoryWorkspace, TimedCommands]:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.record_setup_tree(PATH)
    commands = commands or TimedCommands()
    store = store if store is not None else MemoryCheckResults()
    ports = memory_ports(workspace=workspace, commands=commands, check_results=store)
    declared = verifications or {}
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest", "lint": "ruff"},
        CHECK_TIMEOUT,
        priority=CheckPriority.SUBTASK,
        witness=witness,
        reuse=Reuse("shop", SETUP, STAGE0),
        timeouts=resolved_walls(declared, check_timeouts or {}, CHECK_TIMEOUT, ceiling),
    )
    return RunCheck(gate, verifications=declared), store, workspace, commands


def test_a_declared_verification_runs_its_command_under_its_own_wall():
    handler, _, _, commands = tool(verifications={"compare": COMPARE})
    run = handler.run("compare")
    assert run.passed and run.refused is None
    assert commands.ran == [(PATH, COMPARE.command)]
    assert commands.walls == [900.0]  # not the 30 s a check gets


def test_a_failing_verification_reports_the_systems_figures_and_the_tail_of_its_log():
    log = "\n".join(f"offender {n}" for n in range(500))
    commands = TimedCommands(**{COMPARE.command: [CommandResult(1, log)]})
    handler, _, _, _ = tool(commands, verifications={"compare": COMPARE})
    reply = handler("compare")
    assert "passed: no" in reply and "offender 499" in reply and "offender 0\n" not in reply


def test_a_verification_that_passes_is_never_stored_and_says_why():
    handler, store, _, _ = tool(verifications={"compare": COMPARE})
    run = handler.run("compare")
    assert run.passed and store.puts == []
    assert run.unrecorded == rules.UNRECORDED_VERIFICATION


def test_a_verification_never_reads_a_stored_pass_even_under_the_name_of_a_stored_one():
    handler, store, _, commands = tool(verifications={"compare": COMPARE})
    handler.run("compare")
    handler.run("compare")
    assert len(commands.ran) == 2 and store.gets == []  # run twice, nothing looked up


def test_a_declared_check_keeps_being_stored_beside_a_verification():
    handler, store, _, _ = tool(verifications={"compare": COMPARE})
    assert handler.run("tests").unrecorded is None
    assert len(store.puts) == 1


def test_a_check_timeouts_figure_is_the_wall_of_that_check_only():
    handler, _, _, commands = tool(check_timeouts={"tests": 120.0})
    handler.run("tests")
    handler.run("lint")
    assert commands.walls == [120.0, CHECK_TIMEOUT]


def test_an_unknown_name_lists_the_checks_and_the_verifications():
    handler, _, _, commands = tool(verifications={"compare": COMPARE})
    run = handler.run("e2e")
    assert run.refused is not None and "tests, lint, compare" in run.refused
    assert commands.ran == []


def test_the_call_timeout_covers_the_retry_of_a_wall_longer_than_the_check_timeout():
    handler, _, _, _ = tool(verifications={"compare": COMPARE}, witness="witness")
    gate = handler.gate
    assert handler.timeout_s >= 2 * 900.0 + 2 * gate.wait_max_s + CHECK_TIMEOUT
    assert handler.timeout_s == max(
        30.0 * (2 + 1 + 2) + gate.wait_max_s,
        rules.call_timeout_s([900.0], gate.wait_max_s, CHECK_TIMEOUT),
    )


def test_a_product_with_no_verification_has_the_call_timeout_it_always_had():
    handler, _, _, _ = tool()
    assert handler.timeout_s == 30.0 * (2 + 1 + 2) + handler.gate.wait_max_s
    assert handler.verification_names == ()


def test_the_share_is_held_as_long_as_the_longest_wall_may_run():
    handler, _, _, _ = tool(verifications={"compare": COMPARE})
    assert handler.gate.timeout_s >= 2 * 900.0


def test_a_wall_over_the_ceiling_is_refused_and_never_clipped():
    with pytest.raises(WallOverCeiling, match="ceiling of 600 s"):
        resolved_walls({"compare": COMPARE}, {}, CHECK_TIMEOUT, 600.0)
    with pytest.raises(WallOverCeiling):
        resolved_walls({}, {"tests": 601.0}, CHECK_TIMEOUT, 600.0)


def test_resolved_walls_holds_only_what_the_product_declared():
    assert resolved_walls({}, {}, CHECK_TIMEOUT, 3600.0) == {}
    assert resolved_walls({"compare": COMPARE}, {"tests": 60.0}, CHECK_TIMEOUT, 3600.0) == {
        "compare": 900.0,
        "tests": 60.0,
    }
