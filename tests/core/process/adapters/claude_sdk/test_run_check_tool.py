"""The `run_check` tool on the SDK side (ADR-0065, GH-359): which roles carry it, the
environment of the attempt that does, the call that blocks without stalling the loop, and the
hooks that keep the store out of a role's reach."""

import asyncio
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.claude_sdk import hooks
from agentic.core.process.adapters.claude_sdk.check_server import (
    SERVER,
    TIMEOUT_ENV,
    run_check_tool,
)
from agentic.core.process.adapters.claude_sdk.hooks import HookActivity
from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, ROLES

STORE = Path("/state/agentic/check-results")


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
