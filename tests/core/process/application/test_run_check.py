"""The `run_check` handler over fake ports (ADR-0065, GH-359): what the Developer's tool
runs, reports and stores. Every figure it returns comes from `CheckoutPresubmit._run`."""

import time
from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult, Grant
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck
from agentic.core.process.domain import check_results
from agentic.core.process.domain.check_schedule import CheckPriority
from tests.fakes import (
    FakeCheckScheduler,
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
SETUP = ["uv sync"]
STAGE0 = "a" * 40
NOW = 1_000_000.0


def tool(
    commands: MemoryCommands | None = None,
    *,
    workspace: MemoryWorkspace | None = None,
    witness: str | None = None,
    stage0: str = STAGE0,
    store: MemoryCheckResults | None = None,
    set_up: bool = True,
) -> tuple[RunCheck, MemoryCheckResults, MemoryWorkspace, MemoryCommands]:
    workspace = workspace or MemoryWorkspace()
    workspace.moved.add(PATH)  # a commit landed
    if set_up:
        workspace.record_setup_tree(PATH)
    commands = commands or MemoryCommands()
    store = store if store is not None else MemoryCheckResults()
    ports = memory_ports(workspace=workspace, commands=commands, check_results=store)
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest", "lint": "ruff"},
        30.0,
        priority=CheckPriority.SUBTASK,
        witness=witness,
        reuse=Reuse("shop", tuple(SETUP), stage0),
    )
    handler = RunCheck(gate, clock=lambda: NOW)
    return handler, store, workspace, commands


def key(tree: str, name: str = "tests", command: str = "pytest") -> str:
    fingerprint = check_results.fingerprint(SETUP, STAGE0, 30.0)
    assert fingerprint is not None
    return check_results.check_key("shop", name, command, tree, fingerprint)


def test_an_unknown_name_is_refused_with_the_declared_list_and_nothing_runs():
    handler, store, _, commands = tool()
    run = handler.run("e2e")
    assert run.refused is not None and "tests, lint" in run.refused
    assert commands.ran == [] and store.puts == []
    assert handler.names == ("tests", "lint")


def test_a_clean_witnessed_pass_is_stored_under_the_content_key():
    handler, store, workspace, commands = tool()
    run = handler.run("tests")
    assert run.passed and not run.reused and run.unrecorded is None
    assert commands.ran == [(PATH, "pytest")]
    ((stored, row, at),) = store.puts
    assert stored == key(workspace.tree(PATH)) and at == NOW
    assert row["name"] == "tests" and row["command"] == "pytest"


def test_the_exit_and_the_output_are_the_commands_never_the_models():
    failing = CommandResult(1, "3 failed")
    handler, store, _, _ = tool(MemoryCommands(pytest=[failing, failing]))
    run = handler.run("tests")
    assert not run.passed and "3 failed" in run.output
    assert store.puts == [] and run.unrecorded == "the check did not pass"
    assert "passed: no" in handler("tests")


def test_a_dirty_tree_runs_and_is_not_recorded():
    workspace = MemoryWorkspace()
    workspace.dirty.add(PATH)
    handler, store, _, commands = tool(workspace=workspace)
    run = handler.run("tests")
    assert run.passed and commands.ran == [(PATH, "pytest")]
    assert store.puts == [] and run.unrecorded == "the tree is not clean"
    assert "not recorded: the tree is not clean" in handler("tests")


def test_a_check_with_no_commit_yet_is_not_recorded():
    handler, store, workspace, _ = tool()
    workspace.moved.discard(PATH)
    assert handler.run("tests").unrecorded == "there is no commit yet"
    assert store.puts == []


def test_a_witness_that_disagrees_leaves_nothing_stored():
    commands = MemoryCommands(witness=[CommandResult(1, "wrong checkout")])
    handler, store, _, _ = tool(commands, witness="witness")
    run = handler.run("tests")
    assert run.passed and store.puts == []
    assert run.unrecorded == "the checkout witness did not agree"
    assert run.witness is not None and run.witness.startswith("disagreed")


def test_a_witness_that_agrees_lets_the_pass_be_stored():
    commands = MemoryCommands(witness=[CommandResult(0, f"{PATH}\n")])
    handler, store, _, commands = tool(commands, witness="witness")
    run = handler.run("tests")
    assert [command for _, command in commands.ran] == ["witness", "pytest"]
    assert len(store.puts) == 1 and run.witness == "agreed"


def test_no_setup_record_no_store():
    handler, store, _, _ = tool(set_up=False)
    assert handler.run("tests").unrecorded == "the checkout has no valid setup record"
    assert store.puts == []


def test_a_manifest_that_moved_since_the_setup_is_drift_and_not_recorded():
    workspace = MemoryWorkspace()
    workspace.tree_paths = ("api/uv.lock",)
    handler, store, _, _ = tool(workspace=workspace)
    run = handler.run("tests")
    assert run.passed and store.puts == []
    assert run.unrecorded == "the environment drifted since the setup"


def test_a_dirty_stage0_has_no_fingerprint_and_nothing_is_stored():
    handler, store, _, _ = tool(stage0=f"{STAGE0}+dirty")
    run = handler.run("tests")
    assert run.passed and store.puts == [] and store.gets == []
    assert run.unrecorded == "this system version has no fingerprint"


def test_a_failed_comparison_of_trees_is_not_recorded_and_never_raises():
    class Broken(MemoryWorkspace):
        def tree_changes(self, path: Path, old: str, new: str) -> tuple[str, ...]:
            from agentic.core.process.application.ports import GitError

            raise GitError("not a tree")

    handler, store, _, _ = tool(workspace=Broken())
    assert handler.run("tests").unrecorded == "the environment drifted since the setup"
    assert store.puts == []


def test_the_timeout_of_one_call_covers_every_declared_check_and_the_witness():
    handler, _, _, _ = tool(witness="witness")
    # The wait for a share is part of the call too (ADR-0066), on top of the checks' own.
    assert handler.timeout_s == 30.0 * 3 + handler.gate.wait_max_s


def gate_over(ports, **kwargs) -> CheckoutPresubmit:
    """A delivery gate built the way `develop` builds it: the same Reuse as the tool's."""
    return CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest"},
        30.0,
        priority=CheckPriority.SUBTASK,
        reuse=Reuse("shop", tuple(SETUP), STAGE0),
        **kwargs,
    )


def test_a_pass_the_tool_stored_is_reused_by_the_delivery_gate_without_running_it():
    handler, store, workspace, commands = tool()
    handler.clock = time.time  # the gate reads at the real time
    handler.run("tests")
    assert commands.ran == [(PATH, "pytest")]
    gate = gate_over(memory_ports(workspace=workspace, commands=commands, check_results=store))
    assert gate() is None
    assert commands.ran == [(PATH, "pytest")]  # the gate ran nothing
    assert gate.results is not None and gate.results[0].get("reused") is True


def test_the_tool_reuses_a_pass_the_gate_stored_and_says_so():
    handler, store, workspace, commands = tool()
    handler.clock = time.time  # the gate reads at the real time
    gate = gate_over(memory_ports(workspace=workspace, commands=commands, check_results=store))
    assert gate() is None
    assert commands.ran == [(PATH, "pytest")] and len(store.puts) == 1
    run = handler.run("tests")
    assert run.reused and run.passed and commands.ran == [(PATH, "pytest")]
    assert "reused: yes" in handler("tests") and len(store.puts) == 1


def test_a_dirty_tree_never_reads_the_pass_stored_for_an_earlier_clean_one():
    handler, store, workspace, commands = tool()
    handler.clock = time.time  # the gate reads at the real time
    handler.run("tests")
    workspace.dirty.add(PATH)
    run = handler.run("tests")
    assert not run.reused and commands.ran == [(PATH, "pytest")] * 2
    assert run.unrecorded == "the tree is not clean"


def test_a_store_that_cannot_be_written_is_reported_and_never_raises():
    class Full(MemoryCheckResults):
        def put(self, key, row, now):
            raise OSError("disk full")

    handler, _, _, _ = tool(store=Full())
    run = handler.run("tests")
    assert run.passed and run.unrecorded == "the store could not be written"


# -- under a share of the machine's cores (GH-361, ADR-0066) --------------------------


def scheduled_tool(commands, scheduler, store=None, check="pytest -n {workers}"):
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.record_setup_tree(PATH)
    store = store if store is not None else MemoryCheckResults()
    ports = memory_ports(
        workspace=workspace, commands=commands, check_results=store, check_scheduler=scheduler
    )
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": check},
        30.0,
        priority=CheckPriority.SUBTASK,
        reuse=Reuse("shop", tuple(SETUP), STAGE0),
    )
    return RunCheck(gate, clock=lambda: NOW), store, ports


def test_a_check_asked_for_by_hand_holds_a_share_sized_into_its_workers_and_gives_it_back():
    scheduler = FakeCheckScheduler(6)
    commands = MemoryCommands()
    handler, _, _ = scheduled_tool(commands, scheduler)
    handler.run("tests")
    assert [command for _, command in commands.ran] == ["pytest -n 6"]
    assert [r.priority for r in scheduler.requests] == [CheckPriority.SUBTASK]
    assert len(scheduler.released) == 1


def test_every_call_of_the_tool_takes_a_new_place_in_the_queue_apart_from_the_gates():
    scheduler = FakeCheckScheduler(6)
    handler, _, _ = scheduled_tool(MemoryCommands(), scheduler)
    handler.gate.grant = Grant(6, 0.0, 0, False, 123.0, "gate-token")  # the gate's own place
    handler.run("tests")
    handler.run("tests")
    assert [(r.enqueued_at, r.token) for r in scheduler.requests] == [(None, None), (None, None)]
    assert handler.gate.grant is not None and handler.gate.grant.token == "gate-token"
    assert handler.gate.waited_s == 0.0  # its wait is not the gate's


def test_the_share_is_given_back_when_the_check_raises():
    scheduler = FakeCheckScheduler(6)
    commands = MemoryCommands(raises={"pytest -n 6"})
    handler, _, _ = scheduled_tool(commands, scheduler)
    with pytest.raises(RuntimeError):
        handler.run("tests")
    assert len(scheduler.released) == 1


def test_a_pass_stored_at_one_share_is_read_at_another_the_key_is_the_declared_command():
    store = MemoryCheckResults()
    first, _, _ = scheduled_tool(MemoryCommands(), FakeCheckScheduler(6), store)
    first.clock = time.time  # the lookup reads at the real time
    assert first.run("tests").passed
    commands = MemoryCommands()
    second, _, _ = scheduled_tool(commands, FakeCheckScheduler(12), store)
    row = second.run("tests")
    assert row.reused and commands.ran == []
