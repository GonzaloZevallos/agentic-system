"""`CheckoutPresubmit` reusing a stored pass (ADR-0065): fake commands, fake workspace and
fake store. The key is content (tree, command, environment), never the head."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan
from tests.fakes import MemoryCheckResults, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-1.api")
OTHER = Path("/memory/worktrees/GH-1.merge")
TREE = "tree:one"
SHA = "0123456789abcdef0123456789abcdef01234567"
REUSE = Reuse("apostrophe", ("uv sync",), SHA)


def build(
    *,
    commands: MemoryCommands | None = None,
    store: MemoryCheckResults | None = None,
    workspace: MemoryWorkspace | None = None,
    path: Path = PATH,
    checks: dict[str, str] | None = None,
    reuse: Reuse | None = REUSE,
    **options,
) -> CheckoutPresubmit:
    workspace = workspace or MemoryWorkspace()
    workspace.moved.add(path)
    workspace.trees.setdefault(path, TREE)
    # The checkout's setup ran on this very tree, as `record_setup_tree` leaves it.
    workspace.record_setup_tree(path)
    ports = memory_ports(
        commands=commands or MemoryCommands(),
        workspace=workspace,
        check_results=store if store is not None else MemoryCheckResults(),
    )
    options.setdefault("priority", CheckPriority.SUBTASK)
    return CheckoutPresubmit(
        ports, path, "base", checks or {"tests": "pytest"}, 30.0, reuse=reuse, **options
    )


def ran(commands: MemoryCommands) -> list[str]:
    return [command for _, command in commands.ran]


def test_a_second_call_on_the_same_key_runs_nothing_and_marks_the_row_reused():
    commands, store = MemoryCommands(), MemoryCheckResults()
    first = build(commands=commands, store=store)
    assert first() is None
    second = build(commands=commands, store=store, path=OTHER)
    assert second() is None
    assert ran(commands) == ["pytest"]  # only the first call ran it
    assert first.results is not None and "reused" not in first.results[0]
    assert second.results is not None and second.results[0]["reused"] is True
    assert second.results[0]["passed"] is True


def test_a_changed_tree_runs_again():
    commands, store = MemoryCommands(), MemoryCheckResults()
    assert build(commands=commands, store=store)() is None
    workspace = MemoryWorkspace()
    workspace.trees[OTHER] = "tree:two"
    assert build(commands=commands, store=store, workspace=workspace, path=OTHER)() is None
    assert ran(commands) == ["pytest", "pytest"]


def test_a_changed_command_runs_again():
    commands, store = MemoryCommands(), MemoryCheckResults()
    assert build(commands=commands, store=store)() is None
    assert (
        build(commands=commands, store=store, checks={"tests": "pytest -x"}, path=OTHER)() is None
    )
    assert ran(commands) == ["pytest", "pytest -x"]


def test_a_changed_environment_runs_again():
    commands, store = MemoryCommands(), MemoryCheckResults()
    assert build(commands=commands, store=store)() is None
    other_setup = Reuse("apostrophe", ("uv sync --frozen",), SHA)
    assert build(commands=commands, store=store, reuse=other_setup, path=OTHER)() is None
    other_stage0 = Reuse("apostrophe", ("uv sync",), "f" * 40)
    assert build(commands=commands, store=store, reuse=other_stage0, path=OTHER)() is None
    assert ran(commands) == ["pytest"] * 3


def test_a_dirty_tree_neither_reads_nor_writes_nor_runs():
    commands, store = MemoryCommands(), MemoryCheckResults()
    workspace = MemoryWorkspace()
    workspace.dirty.add(PATH)
    gate = build(commands=commands, store=store, workspace=workspace)
    assert gate() is not None  # refused for the dirty tree, as before
    assert ran(commands) == [] and store.gets == [] and store.puts == []


def test_a_dirty_stage0_runs_exactly_as_before_and_never_touches_the_store():
    commands, store = MemoryCommands(), MemoryCheckResults()
    dirty = Reuse("apostrophe", ("uv sync",), f"{SHA}+dirty")
    for _ in range(2):
        assert build(commands=commands, store=store, reuse=dirty)() is None
    assert ran(commands) == ["pytest", "pytest"]
    assert store.gets == [] and store.puts == []


def test_a_failure_is_not_stored_and_the_next_call_runs_again():
    commands = MemoryCommands(pytest=[CommandResult(1, "1 failed"), CommandResult(0, "ok")])
    store = MemoryCheckResults()
    failing = build(commands=commands, store=store)
    assert failing() is not None
    assert store.puts == []
    assert build(commands=commands, store=store, path=OTHER)() is None
    assert ran(commands) == ["pytest", "pytest"]
    assert len(store.puts) == 1  # only the pass


def test_a_timeout_is_not_stored():
    commands = MemoryCommands(pytest=[CommandResult(0, "ok", timed_out=True)])
    store = MemoryCheckResults()
    assert build(commands=commands, store=store)() is not None
    assert store.puts == []


def test_an_affected_form_never_satisfies_the_full_form():
    plan = AffectedPlan(base="abc", commands={"tests": "pytest --since {base}"}, fallback=())
    commands, store = MemoryCommands(), MemoryCheckResults()
    first = build(commands=commands, store=store, affected=plan)
    first.ports.workspace.diff_paths = ("src/a.py",)
    assert first() is None  # the affected form ran and passed
    assert ran(commands) == ["pytest --since abc"]
    # Another checkout of the same tree, asked for the full form: it must run.
    assert build(commands=commands, store=store, path=OTHER)() is None
    assert ran(commands) == ["pytest --since abc", "pytest"]
    # And the affected form itself is reused by the next affected gate on that tree.
    again = build(commands=commands, store=store, affected=plan, path=PATH.with_name("GH-1.c"))
    again.ports.workspace.diff_paths = ("src/a.py",)
    assert again() is None
    assert ran(commands) == ["pytest --since abc", "pytest"]


def test_a_witness_that_disagrees_stores_nothing_and_the_same_pass_without_one_stores_one():
    witness = "pwd"
    commands = MemoryCommands()
    commands.queues[witness] = [CommandResult(0, "/elsewhere")]
    store = MemoryCheckResults()
    gate = build(commands=commands, store=store, witness=witness)
    assert gate() is not None and gate.results is not None and gate.results[0]["passed"]
    assert store.puts == []
    build(commands=MemoryCommands(), store=store, path=OTHER)()
    assert len(store.puts) == 1


def test_a_witness_that_agrees_lets_the_pass_be_stored():
    commands = MemoryCommands()
    commands.queues["pwd"] = [CommandResult(0, str(PATH))]
    store = MemoryCheckResults()
    assert build(commands=commands, store=store, witness="pwd")() is None
    assert len(store.puts) == 1


def test_the_witness_still_runs_on_a_call_that_reuses():
    commands, store = MemoryCommands(), MemoryCheckResults()
    commands.queues["pwd"] = [CommandResult(0, str(PATH)), CommandResult(0, str(OTHER))]
    assert build(commands=commands, store=store, witness="pwd")() is None
    assert build(commands=commands, store=store, witness="pwd", path=OTHER)() is None
    assert ran(commands) == ["pwd", "pytest", "pwd"]


def test_drift_stores_nothing_but_a_read_still_hits():
    commands, store = MemoryCommands(), MemoryCheckResults()
    assert build(commands=commands, store=store)() is None
    assert len(store.puts) == 1
    workspace = MemoryWorkspace()
    workspace.tree_paths = ("api/uv.lock",)
    drifted = build(commands=commands, store=store, workspace=workspace, path=OTHER)
    assert drifted() is None
    assert ran(commands) == ["pytest"]  # a read hit
    assert drifted.results is not None and drifted.results[0]["reused"] is True
    # And a pass that ran under drift is not stored.
    workspace.trees[OTHER] = "tree:changed"
    cold = build(commands=commands, store=MemoryCheckResults(), workspace=workspace, path=OTHER)
    cold.ports.check_results.puts.clear()
    assert cold() is None
    assert cold.ports.check_results.puts == []


def test_no_setup_tree_record_stores_nothing_but_a_read_still_hits():
    commands, store = MemoryCommands(), MemoryCheckResults()
    assert build(commands=commands, store=store)() is None
    workspace = MemoryWorkspace()
    unrecorded = build(commands=commands, store=store, workspace=workspace, path=OTHER)
    workspace.setup_trees.clear()
    assert unrecorded() is None
    assert ran(commands) == ["pytest"]
    workspace.trees[OTHER] = "tree:two"
    cold = build(commands=commands, store=MemoryCheckResults(), workspace=workspace, path=OTHER)
    workspace.setup_trees.clear()
    assert cold() is None
    assert cold.ports.check_results.puts == []


def test_an_unreadable_tree_runs_as_before():
    class Unreadable(MemoryWorkspace):
        broken = False

        def tree(self, path: Path) -> str:
            if self.broken:
                raise GitError("no tree")
            return super().tree(path)

    commands, store = MemoryCommands(), MemoryCheckResults()
    workspace = Unreadable()
    gate = build(commands=commands, store=store, workspace=workspace)
    workspace.broken = True
    assert gate() is None
    assert ran(commands) == ["pytest"] and store.gets == [] and store.puts == []
