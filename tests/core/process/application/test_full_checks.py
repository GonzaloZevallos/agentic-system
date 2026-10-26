"""The `full_checks` node (GH-358) over the in-memory ports: the whole suite once on the
integrated head with no affected form, the record and the section it writes, the return a red
run opens, the clean tree it leaves, and the refusals that never run a command."""

from __future__ import annotations

import pytest

from agentic.core.process.application.full_checks import full_checks
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryWorkspace, memory_ports

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT)
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
TASK_PATH = MemoryWorkspace.ROOT / "GH-1"


def state_of(**more) -> dict:
    subtask = {
        "id": INTEGRATION,
        "title": "integration",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1",
        "base": "b",
    }
    return {
        "id": "GH-1",
        "title": "Tiers",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {INTEGRATION: subtask},
        "task_head": "base",
        **more,
    }


def ports_of(commands=None, **stack):
    stack.setdefault("checks", CHECKS)
    stack.setdefault("delivery_checks", ("tests",))
    return memory_ports(stack=MemoryStack(**stack), commands=commands or MemoryCommands())


class Messy(MemoryCommands):
    """A check that, like a test run, edits a tracked file and writes an untracked one."""

    def __init__(self, workspace: MemoryWorkspace, **queues):
        super().__init__(**queues)
        self.workspace = workspace

    def run(self, cwd, command, timeout_s):
        self.workspace.dirty.add(cwd)
        self.workspace.uncommitted.add(cwd)
        return super().run(cwd, command, timeout_s)


async def test_every_declared_check_runs_in_full_with_no_affected_command():
    ports = ports_of(affected_checks={"tests": "pytest {changed}"}, affected_fallback=("lint",))
    await full_checks(state_of(), CFG, ports)
    assert [command for _, command in ports.commands.ran] == ["pytest", "ruff check ."]
    assert all(cwd == TASK_PATH for cwd, _ in ports.commands.ran)


async def test_a_green_run_writes_a_passed_record_and_no_rows():
    ports = ports_of()
    patch = await full_checks(state_of(), CFG, ports)
    record = patch["full_checks"]
    assert record["passed"] is True
    assert record["version"] == 1
    assert record["head"] == "base"
    assert record["declared"] == CHECKS
    assert [(r["name"], r["passed"]) for r in record["results"]] == [
        ("tests", True),
        ("lint", True),
    ]
    assert "failed_step" not in record
    assert "findings" not in patch and "subtasks" not in patch
    assert "Full checks" in patch["sections"]
    assert "passed" in patch["sections"]["Full checks"].lower()
    assert ports.documents.saved


async def test_a_green_run_of_a_later_version_follows_the_previous_record():
    previous = {
        "version": 2,
        "head": "old",
        "passed": False,
        "duration_s": 1.0,
        "declared": CHECKS,
        "results": [{"name": "lint", "passed": False}],
        "witness": None,
        "failed_step": "checks",
    }
    patch = await full_checks(state_of(full_checks=previous), CFG, ports_of())
    assert patch["full_checks"]["version"] == 3
    assert rules.full_checks_green_version(apply(state_of(), patch)) == 3


async def test_a_red_run_keeps_no_command_output_in_the_record_only_in_the_section():
    commands = MemoryCommands(**{"ruff check .": [CommandResult(1, "E501 line too long")]})
    patch = await full_checks(state_of(), CFG, ports_of(commands))
    assert patch["full_checks"]["results"] == [
        {"name": "tests", "passed": True},
        {"name": "lint", "passed": False},
    ]
    assert "E501" not in repr(patch["full_checks"])
    assert "E501" in patch["sections"]["Full checks"]


async def test_a_red_run_reopens_the_integration_with_a_row_per_failed_check_and_the_section():
    commands = MemoryCommands(**{"ruff check .": [CommandResult(1, "E501 line too long")]})
    ports = ports_of(commands)
    patch = await full_checks(state_of(), CFG, ports)
    reopened = patch["subtasks"][INTEGRATION]
    assert reopened["status"] == "returned"
    assert reopened["returned_by"] == "full_checks"
    [row] = patch["findings"]
    assert row["location"] == "full_checks:lint"
    assert row["severity"] == "blocking" and row["scope"] == "plan"
    assert row["category"] == "process-suite-red"
    assert patch["full_checks"]["passed"] is False
    assert patch["full_checks"]["failed_step"] == "checks"
    assert "lint" in patch["sections"]["Full checks"]
    assert "E501 line too long" in patch["sections"]["Full checks"]
    assert patch["changes_requested"] == rules.record_return(
        state_of(), rules.pair_for(INTEGRATION)
    )
    assert "escalated" not in patch


async def test_a_red_run_at_the_pair_limit_escalates_instead_of_reopening_forever():
    commands = MemoryCommands(**{"pytest": [CommandResult(1, "1 failed")]})
    limited = Configuration(product_dir=MemoryWorkspace.ROOT, max_changes_requested_per_pair=0)
    patch = await full_checks(state_of(), limited, ports_of(commands))
    assert patch["escalated"]
    assert patch["escalation_class"] == "returns"
    assert patch["full_checks"]["passed"] is False


async def test_a_check_that_dirties_the_tree_leaves_it_clean_green_or_red():
    for queue in ({}, {"pytest": [CommandResult(1, "boom")]}):
        workspace = MemoryWorkspace()
        ports = memory_ports(
            stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)),
            workspace=workspace,
            commands=Messy(workspace, **queue),
        )
        await full_checks(state_of(), CFG, ports)
        assert workspace.restored == [TASK_PATH]
        assert workspace.is_clean(TASK_PATH)


async def test_a_dirty_tree_escalates_with_the_setup_class_and_runs_nothing():
    workspace = MemoryWorkspace()
    workspace.dirty.add(TASK_PATH)
    ports = memory_ports(
        stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)), workspace=workspace
    )
    patch = await full_checks(state_of(), CFG, ports)
    assert patch["escalation_class"] == "setup"
    assert "uncommitted" in patch["escalated"]
    assert ports.commands.ran == []
    assert "full_checks" not in patch
    assert workspace.restored == []


async def test_a_setup_that_fails_on_a_new_checkout_escalates_like_deliver():
    commands = MemoryCommands(**{"make env": [CommandResult(1, "no env")]})
    ports = ports_of(commands, setup={"env": "make env"})
    patch = await full_checks(state_of(), CFG, ports)
    assert patch["escalation_class"] == "setup"
    assert [command for _, command in commands.ran] == ["make env"]


async def test_a_second_run_on_the_same_tree_reuses_the_pass_and_runs_no_command():
    from tests.fakes import MemoryCheckResults

    commands, store = MemoryCommands(), MemoryCheckResults()
    ports = memory_ports(
        stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)),
        commands=commands,
        check_results=store,
    )
    first = await full_checks(state_of(), CFG, ports)
    assert [command for _, command in commands.ran] == ["pytest", "ruff check ."]
    second = await full_checks(state_of(), CFG, ports)
    assert [command for _, command in commands.ran] == ["pytest", "ruff check ."]
    assert all("reused" not in row for row in first["full_checks"]["results"])
    assert second["full_checks"]["passed"] is True
