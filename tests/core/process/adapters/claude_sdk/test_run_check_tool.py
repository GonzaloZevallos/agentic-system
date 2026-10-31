"""The `run_check` tool on the SDK side (ADR-0065, GH-359): which roles carry it, the
environment of the attempt that does, the call that blocks without stalling the loop, and the
hooks that keep the store out of a role's reach."""

import asyncio
import time
from pathlib import Path

import anyio
import pytest

from agentic.core.process.adapters.claude_sdk import hooks
from agentic.core.process.adapters.claude_sdk.check_server import (
    SERVER,
    TIMEOUT_ENV,
    run_check_tool,
    tool_timeout_ms,
)
from agentic.core.process.adapters.claude_sdk.hooks import HookActivity
from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.application import presubmit
from agentic.core.process.application.ports import CommandResult, RunStopped
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck, resolved_walls
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, ROLES
from agentic.core.process.domain.run_check import DESCRIPTION, Verification
from tests.fakes import MemoryCheckResults, MemoryCommands, MemoryWorkspace, memory_ports

STORE = Path("/state/agentic/check-results")
PATH = Path("/memory/worktrees/GH-1.api")


class SlowTool:
    names = ("tests",)
    timeout_s = 90.0

    def __init__(self, seconds: float = 0.0) -> None:
        self.seconds, self.asked = seconds, []

    def __call__(self, name: str) -> str:
        self.asked.append(name)
        time.sleep(self.seconds)
        return f"check: {name}\npassed: yes"


def options(role, tmp_path, check_tool=None):
    return options_for(
        role,
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
        check_tool=check_tool,
        check_results_dir=STORE,
    )


@pytest.mark.parametrize("role", [DEVELOPER, DEVELOPER_INTEGRATOR], ids=lambda r: r.slug)
def test_a_developer_given_the_tool_carries_the_server_and_a_timeout_that_covers_it(role, tmp_path):
    built = options(role, tmp_path, SlowTool())
    assert list(built.mcp_servers) == [SERVER]
    assert built.env[TIMEOUT_ENV] == "90000"
    assert built.strict_mcp_config is True


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.slug)
def test_a_role_not_given_the_tool_has_no_server_and_no_timeout(role, tmp_path):
    built = options(role, tmp_path)
    assert built.mcp_servers == {} and TIMEOUT_ENV not in built.env


def test_a_call_that_blocks_does_not_stop_the_event_loop_and_is_in_flight_meanwhile():
    activity = HookActivity()
    tool = run_check_tool(SlowTool(0.5), activity)
    ticks, seen = 0, []

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            seen.append(activity.in_flight)
            await asyncio.sleep(0.01)

    async def go():
        task = asyncio.create_task(ticker())
        reply = await tool.handler({"name": "tests"})
        task.cancel()
        return reply

    reply = asyncio.run(go())
    assert reply["content"][0]["text"].startswith("check: tests")
    assert ticks > 10 and max(seen) == 1  # the loop kept ticking, the hook was in flight
    assert activity.in_flight == 0 and activity.last_exit is not None


def test_the_model_supplies_only_the_name():
    tool = SlowTool()
    asyncio.run(run_check_tool(tool).handler({"name": "tests", "passed": True, "output": "x"}))
    assert tool.asked == ["tests"]


# -- the hooks: the store is not the role's to read or edit ---------------------------


@pytest.mark.parametrize(
    "tool_input",
    [
        {"file_path": str(STORE / "abc.json")},
        {"file_path": str(STORE / ".key")},
        {"path": str(STORE)},
        {"path": "/state/agentic/check-results/../check-results/x"},
    ],
)
def test_a_path_under_the_store_is_denied_for_every_file_tool(tool_input, tmp_path):
    assert hooks.store_path_denial(tool_input, tmp_path, STORE) == hooks._DENIED + hooks.STORE_PATH


def test_a_path_beside_the_store_or_a_symlink_to_it_is_judged_by_where_it_resolves(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (tmp_path / "link").symlink_to(store)
    assert hooks.store_path_denial({"file_path": "link/x.json"}, tmp_path, store) is not None
    assert hooks.store_path_denial({"file_path": "store2/x.json"}, tmp_path, store) is None
    assert hooks.store_path_denial({"file_path": "src/a.py"}, tmp_path, STORE) is None


@pytest.mark.parametrize(
    "command",
    [
        f"cat {STORE}/abc.json",
        "cat $STATE/check-results/.key",
        "echo x > $(git rev-parse --git-path agentic-setup-tree)",
        "cd /state/agentic/check-results && ls",
    ],
)
def test_a_bash_command_that_names_the_store_its_key_or_the_setup_record_is_denied(command):
    reason = hooks.store_command_denial(command, STORE)
    assert reason == hooks._DENIED + hooks.STORE_COMMAND


def test_an_ordinary_command_is_not_denied():
    assert hooks.store_command_denial("uv run pytest -q", STORE) is None


def test_the_matchers_carry_the_store_denial_for_an_executing_role_only(tmp_path):
    with_store = hooks.hooks_for(DEVELOPER, tmp_path, None, store=STORE)["PreToolUse"]
    assert "Read|Edit|Write|Glob|Grep" in [m.matcher for m in with_store]
    without = hooks.hooks_for(DEVELOPER, tmp_path, None)["PreToolUse"]
    assert "Read|Edit|Write|Glob|Grep" not in [m.matcher for m in without]


def test_the_bash_hook_denies_a_store_command(tmp_path):
    matcher = next(
        m
        for m in hooks.hooks_for(DEVELOPER, tmp_path, None, store=STORE)["PreToolUse"]
        if m.matcher == "Bash"
    )
    data = {"tool_input": {"command": f"ls {STORE}"}}
    out = asyncio.run(matcher.hooks[0](data, None, None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class StoppedTool(SlowTool):
    def __call__(self, name: str) -> str:
        raise RunStopped


def test_a_stop_in_the_run_check_tool_gives_no_result_and_waits_for_the_cancellation():
    async def drive() -> tuple[bool, list]:
        done: list = []
        with anyio.move_on_after(0.3) as scope:
            done.append(await run_check_tool(StoppedTool()).handler({"name": "tests"}))
        return scope.cancelled_caught, done

    cancelled, done = asyncio.run(drive())
    assert cancelled and done == []  # no tool result: the stop is not one


# -- a verification longer than the foreground limit (ADR-0065 addendum, GH-451) ------


FOREGROUND_LIMIT_S = 600.0  # what a Bash step may take in the foreground
LONG_WALL_S = 900.0


class StepClock:
    """The clock the presubmit reads, moved by the command that ran: a 900 s verification
    in no real time."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


class LongCommands(MemoryCommands):
    """A command port that reports a verification of `LONG_WALL_S`, exit 1 and a log."""

    def __init__(self, clock: StepClock) -> None:
        super().__init__()
        self.clock = clock
        self.walls: list[float] = []

    def run(self, cwd, command, timeout_s, *, env=None):
        self.walls.append(timeout_s)
        self.clock.now += LONG_WALL_S
        log = "\n".join(f"line {n}" for n in range(400)) + "\n3 offenders"
        return CommandResult(1, log)


def long_tool(monkeypatch, verifications=None, witness=None):
    clock = StepClock()
    monkeypatch.setattr(presubmit, "time", clock)
    commands = LongCommands(clock)
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    verifications = verifications or {"compare": Verification("compare.sh --strict", LONG_WALL_S)}
    gate = CheckoutPresubmit(
        memory_ports(workspace=workspace, commands=commands, check_results=MemoryCheckResults()),
        PATH,
        "base",
        {"tests": "pytest"},
        FOREGROUND_LIMIT_S,
        priority=CheckPriority.SUBTASK,
        wait_max_s=45.0,
        witness=witness,
        reuse=Reuse("shop", ("uv sync",), "a" * 40),
        timeouts=resolved_walls(verifications, {}, FOREGROUND_LIMIT_S, 3600.0),
    )
    return RunCheck(gate, verifications=verifications), commands


def test_a_step_that_starts_a_verification_past_the_foreground_limit_gets_its_exit_and_log(
    monkeypatch,
):
    handler, commands = long_tool(monkeypatch)
    activity = HookActivity()
    reply = asyncio.run(run_check_tool(handler, activity).handler({"name": "compare"}))
    text = reply["content"][0]["text"]
    assert "check: compare" in text and "passed: no" in text
    assert "duration: 900.0 s" in text and "3 offenders" in text and "line 0\n" not in text
    assert commands.walls == [LONG_WALL_S] and LONG_WALL_S > FOREGROUND_LIMIT_S
    assert activity.in_flight == 0 and activity.last_exit is not None


def test_the_tool_timeout_covers_the_wall_its_retry_and_the_wait(monkeypatch):
    handler, _ = long_tool(monkeypatch)
    assert int(tool_timeout_ms(handler)) >= int((2 * LONG_WALL_S + 2 * 45.0) * 1000)


def test_the_retry_of_a_wall_longer_than_the_check_timeout_fits_in_the_tool_timeout(monkeypatch):
    handler, _ = long_tool(monkeypatch, witness="witness")
    two_runs_and_waits = 2 * LONG_WALL_S + 2 * 45.0 + FOREGROUND_LIMIT_S
    assert float(tool_timeout_ms(handler)) / 1000 >= two_runs_and_waits


def test_the_verification_call_runs_inside_the_hook_activity_the_watchdog_reads(monkeypatch):
    handler, _ = long_tool(monkeypatch)
    activity = HookActivity()
    seen: list[int] = []
    original = handler.__call__

    class Spy:
        names = handler.names
        timeout_s = handler.timeout_s
        verification_names = handler.verification_names

        def __call__(self, name: str) -> str:
            seen.append(activity.in_flight)
            return original(name)

    asyncio.run(run_check_tool(Spy(), activity).handler({"name": "compare"}))
    assert seen == [1] and activity.in_flight == 0


def test_the_description_names_the_verifications_and_is_todays_for_a_product_with_none(
    monkeypatch,
):
    with_one, _ = long_tool(monkeypatch)
    assert run_check_tool(with_one).description.endswith("compare.")
    assert run_check_tool(SlowTool()).description == DESCRIPTION
