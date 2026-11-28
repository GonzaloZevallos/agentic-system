"""`run_check` under the delivery gate's affected plan (ADR-0065 addendum, GH-1096), over fakes:
which form the tool ran and why, what it stored and under which key, and what it says."""

import time
from pathlib import Path

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck
from agentic.core.process.domain import check_results
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import (
    MemoryCheckResults,
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
SETUP = ["uv sync"]
STAGE0 = "a" * 40
CHECKS = {"tests": "pytest", "lint": "ruff"}
PLAN = AffectedPlan("abc", {"tests": "pytest --since {base}"}, ("*.lock",))
AFFECTED = "pytest --since abc"
RED = CommandResult(1, "1 failed")


class Pending:
    """The `PendingPaths` port: what the tree holds uncommitted, or the error it raises."""

    def __init__(self, paths=(), error=None):
        self.paths, self.error, self.calls = paths, error, 0

    def pending_paths(self, path):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return tuple(self.paths)


def tool(
    commands=None,
    *,
    paths=("src/a.py",),
    pending="none",
    plan=PLAN,
    store=None,
    workspace=None,
):
    workspace = workspace or MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.record_setup_tree(PATH)
    workspace.diff_paths = paths
    commands = commands or MemoryCommands()
    store = store if store is not None else MemoryCheckResults()
    port = Pending() if pending == "none" else pending
    ports = memory_ports(
        workspace=workspace, commands=commands, check_results=store, pending_paths=port
    )
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        CHECKS,
        30.0,
        priority=CheckPriority.SUBTASK,
        reuse=Reuse("shop", tuple(SETUP), STAGE0),
        affected=plan,
        criteria_base="base",
        via="tool",
    )
    return RunCheck(gate, clock=time.time), store, workspace, commands, port


def key(tree: str, command: str, name: str = "tests") -> str:
    fingerprint = check_results.fingerprint(SETUP, STAGE0, 30.0)
    assert fingerprint is not None
    return check_results.check_key("shop", name, command, tree, fingerprint)


def test_an_uncommitted_edit_to_a_fallback_glob_file_runs_full_with_its_path():
    handler, store, _, commands, _ = tool(pending=Pending(("uv.lock",)))
    run = handler.run("tests")
    assert [c for _, c in commands.ran] == ["pytest"]
    assert run.form == {
        "check": "tests",
        "mode": "full",
        "cause": "fallback_glob",
        "via": "tool",
        "glob": "*.lock",
        "path": "uv.lock",
    }
    assert run.unrecorded == "the tree is not clean" and store.puts == []
    assert "form: full (cause: fallback_glob, glob: *.lock, path: uv.lock)" in handler("tests")


def test_an_untracked_stack_file_runs_full_as_stack_file():
    handler, _, _, commands, _ = tool(pending=Pending((".agentic/stack.yaml",)))
    run = handler.run("tests")
    assert run.form is not None and run.form["cause"] == "stack_file"
    assert run.form["path"] == ".agentic/stack.yaml"
    assert [c for _, c in commands.ran] == ["pytest"]


@pytest.mark.parametrize("error", [GitError("git status failed"), OSError("gone")])
def test_a_pending_lookup_that_raises_is_paths_unknown_and_the_tree_could_not_be_read(error):
    handler, store, _, commands, _ = tool(pending=Pending(error=error))
    run = handler.run("tests")
    assert run.form is not None and run.form["mode"] == "full"
    assert run.form["cause"] == "paths_unknown"
    assert [c for _, c in commands.ran] == ["pytest"]
    assert run.passed and store.puts == []
    assert run.unrecorded == "the tree could not be read"


def test_a_fallback_glob_file_committed_after_before_on_a_clean_tree_runs_full():
    handler, store, _, commands, _ = tool(paths=("uv.lock", "src/a.py"))
    run = handler.run("tests")
    assert run.form is not None and run.form["cause"] == "fallback_glob"
    assert run.form["path"] == "uv.lock"
    assert [c for _, c in commands.ran] == ["pytest"]
    assert len(store.puts) == 1


def test_a_non_glob_commit_runs_the_affected_form_only_and_says_so():
    handler, store, workspace, commands, port = tool()
    reply = handler("tests")
    assert [c for _, c in commands.ran] == [AFFECTED]
    assert reply.splitlines()[:2] == ["check: tests", "form: affected-only"]
    ((stored, row, _),) = store.puts
    assert stored == key(workspace.tree(PATH), AFFECTED) and row["command"] == AFFECTED
    assert port.calls == 1


def test_head_is_read_on_every_call_and_before_decides_only_committed():
    handler, _, workspace, _, _ = tool()
    handler.run("tests")
    handler.run("tests")
    assert [head for _, head in workspace.diff_reads] == ["head1", "head2"]


def test_a_check_with_no_affected_form_runs_full_with_no_affected_form():
    handler, store, workspace, commands, _ = tool()
    run = handler.run("lint")
    assert run.form is not None and run.form["cause"] == "no_affected_form"
    assert [c for _, c in commands.ran] == ["ruff"]
    assert store.puts[0][0] == key(workspace.tree(PATH), "ruff", "lint")


def test_an_affected_red_is_followed_by_the_full_form_stored_under_its_own_key():
    commands = MemoryCommands(**{AFFECTED: [RED]})
    handler, store, workspace, commands, _ = tool(commands)
    run = handler.run("tests")
    assert [c for _, c in commands.ran] == [AFFECTED, "pytest"]
    assert run.passed and run.form is not None
    assert (run.form["mode"], run.form["cause"]) == ("full", "affected_red")
    ((stored, row, _),) = store.puts
    assert stored == key(workspace.tree(PATH), "pytest") and row["command"] == "pytest"


def test_the_tool_runs_no_rerun_row_even_for_a_red_full():
    commands = MemoryCommands(pytest=[RED, RED, RED], **{AFFECTED: [RED]})
    handler, store, _, commands, _ = tool(commands)
    run = handler.run("tests")
    assert not run.passed and store.puts == []
    assert [c for _, c in commands.ran] == [AFFECTED, "pytest"]


def test_a_full_red_makes_the_next_call_stuck_full_and_a_passing_full_releases_it():
    commands = MemoryCommands(**{AFFECTED: [RED]}, pytest=[RED, RED])
    handler, _, _, commands, _ = tool(commands)
    first = handler.run("tests")
    assert not first.passed and first.form is not None and first.form["cause"] == "affected_red"
    second = handler.run("tests")
    assert not second.passed and second.form is not None
    assert (second.form["mode"], second.form["cause"]) == ("full", "stuck_full")
    third = handler.run("tests")
    assert third.passed and third.form is not None and third.form["cause"] == "stuck_full"
    fourth = handler.run("tests")
    assert fourth.form is not None and fourth.form["mode"] == "affected-only"
    assert [c for _, c in commands.ran] == [AFFECTED, "pytest", "pytest", "pytest", AFFECTED]


def test_the_forms_a_call_made_are_kept_in_call_order_with_via_tool():
    handler, _, _, _, _ = tool()
    handler.run("tests")
    handler.run("lint")
    handler.run("nope")
    assert [(f["check"], f["via"]) for f in handler.forms] == [("tests", "tool"), ("lint", "tool")]


def test_an_affected_pass_the_tool_stored_is_reused_by_the_gate_for_that_form_only():
    store = MemoryCheckResults()
    handler, _, workspace, _, _ = tool(store=store)
    handler.run("tests")
    assert [k for k, _, _ in store.puts] == [key(workspace.tree(PATH), AFFECTED)]

    def gate(paths, commands):
        workspace.diff_paths = paths
        ports = memory_ports(workspace=workspace, commands=commands, check_results=store)
        return CheckoutPresubmit(
            ports,
            PATH,
            "base",
            {"tests": "pytest"},
            30.0,
            priority=CheckPriority.SUBTASK,
            reuse=Reuse("shop", tuple(SETUP), STAGE0),
            affected=PLAN,
        )

    same = MemoryCommands()
    g = gate(("src/a.py",), same)
    assert g() is None and same.ran == []
    assert [r.get("reused") for r in g.results or []] == [True]
    full = MemoryCommands()
    g = gate(("uv.lock",), full)
    assert g() is None
    assert [c for _, c in full.ran] == ["pytest"]


def test_without_a_plan_the_form_is_full_no_affected_form_and_one_row():
    handler, store, workspace, commands, _ = tool(plan=None)
    run = handler.run("tests")
    assert [c for _, c in commands.ran] == ["pytest"]
    assert run.form is not None and run.form["cause"] == "no_affected_form"
    assert store.puts[0][0] == key(workspace.tree(PATH), "pytest")


def test_with_no_pending_port_a_clean_tree_has_no_pending_paths_and_a_dirty_one_none():
    handler, _, workspace, commands, _ = tool(pending=None)
    assert handler.run("tests").form["mode"] == "affected-only"
    workspace.dirty.add(PATH)
    run = handler.run("tests")
    assert run.form is not None and run.form["cause"] == "paths_unknown"
    assert run.unrecorded == "the tree is not clean"


def test_timeout_counts_the_affected_run_of_a_check_that_has_one():
    handler, _, _, _, _ = tool()
    gate = handler.gate
    # tests: affected + full + retry = 3 runs, lint: 2, and the first run of the witness slot.
    assert handler.timeout_s == 30.0 * (1 + 3 + 2) + gate.wait_max_s
    assert handler.timeout_s >= 4 * 30.0 + gate.wait_max_s


def test_timeout_with_a_declared_wall_counts_three_runs_of_it_when_a_check_has_a_plan():
    handler, _, _, _, _ = tool()
    gate = handler.gate
    gate.walls = {"tests": 900.0}
    assert handler.timeout_s >= 3 * 900.0 + 2 * gate.wait_max_s
    planless, _, _, _, _ = tool(plan=None)
    planless.gate.walls = {"tests": 900.0}
    assert planless.timeout_s == max(
        30.0 * (1 + 4) + gate.wait_max_s, 2 * 900.0 + 2 * gate.wait_max_s
    )


# The develop node's RoleRun.


class CallingRunner(ScriptedRunner):
    """A Developer that calls `run_check` on `names` before it delivers."""

    def __init__(self, names, *, gateless=False, **queues):
        super().__init__(**queues)
        self.names = names
        # Withhold the presubmit, so the delivery gate never runs the checks.
        self.gateless = gateless

    async def run_once(self, role, prompt, **kwargs):
        if (check_tool := kwargs.get("check_tool")) is not None:
            for name in self.names:
                check_tool(name)
        if self.gateless:
            kwargs.pop("presubmit", None)
        return await super().run_once(role, prompt, **kwargs)


RED_TESTS = {
    "subtask": "other",
    "version": 1,
    "location": "full_checks:tests",
    "claim": "the full tier is red",
    "severity": "blocking",
    "scope": "subtask",
    "category": "correctness",
    "status": "open",
}


async def developed(tmp_path, names, *, developer=None, gateless=False):
    runner = CallingRunner(names, gateless=gateless, developer=developer or [delivered()])
    stack = MemoryStack(
        checks=CHECKS,
        affected_checks={"tests": "pytest --since {base}"},
        affected_fallback=("*.lock",),
    )
    ports = memory_ports(runner, stack=stack, check_results=MemoryCheckResults())
    ports.workspace.diff_paths = ("src/a.py",)
    state, checkout = building(ports)
    ports.workspace.record_setup_tree(checkout.path)
    # `tests` is a delivery check with an affected form, which the tool refuses (GH-1070) unless
    # the full tier is red on it: an open `full_checks:tests` finding opens it, as for a Developer
    # reproducing a red.
    state = {**state, "findings": [RED_TESTS]}
    return await develop(state, Configuration(product_dir=tmp_path), ports), ports


@pytest.mark.anyio
async def test_the_run_carries_the_tools_forms_before_the_gates(tmp_path):
    patch, _ = await developed(tmp_path, ("tests", "lint"))
    forms = patch["runs"][-1]["forms"]
    assert [(f["check"], f["via"]) for f in forms] == [
        ("tests", "tool"),
        ("lint", "tool"),
        ("tests", "gate"),
        ("lint", "gate"),
    ]
    assert "forms_dropped" not in patch["runs"][-1]


@pytest.mark.anyio
async def test_the_forms_are_capped_at_fifty_and_the_older_ones_counted(tmp_path):
    patch, _ = await developed(tmp_path, ("lint",) * 60)
    run = patch["runs"][-1]
    assert len(run["forms"]) == 50 and run["forms_dropped"] == 12
    assert [f["via"] for f in run["forms"][-2:]] == ["gate", "gate"]


@pytest.mark.anyio
async def test_the_tool_and_the_gate_share_one_plan_so_the_gate_reuses_the_tools_pass(tmp_path):
    patch, ports = await developed(tmp_path, ("tests",))
    ran = [command for _, command in ports.commands.ran]
    assert ran.count("pytest --since base0") == 1
    assert [(f["check"], f["mode"], f["via"]) for f in patch["runs"][-1]["forms"]][:2] == [
        ("tests", "affected-only", "tool"),
        ("tests", "affected-only", "gate"),
    ]


@pytest.mark.anyio
async def test_two_invocations_do_not_carry_forms_across(tmp_path):
    first, _ = await developed(tmp_path, ("lint",) * 3)
    second, _ = await developed(tmp_path, ())
    assert len(first["runs"][-1]["forms"]) == 5
    assert {f["via"] for f in second["runs"][-1]["forms"]} == {"gate"}


@pytest.mark.anyio
async def test_the_tools_forms_are_recorded_when_the_gate_never_ran(tmp_path):
    patch, _ = await developed(tmp_path, ("tests", "lint"), gateless=True)
    run = patch["runs"][-1]
    assert [(f["check"], f["via"]) for f in run["forms"]] == [("tests", "tool"), ("lint", "tool")]
    assert "forms_dropped" not in run
