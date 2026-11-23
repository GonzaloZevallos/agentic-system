"""`CheckoutPresubmit` records which form each check ran and why (`last_forms`), from exit
codes, declared globs and changed paths only; and the stored entry never carries it."""

from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.check_results import FilesystemCheckResults
from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.domain import check_results as keys
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan, CheckResult, PlannedForm
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

PATH = Path("/memory/worktrees/GH-1092.gate")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
PLAN = AffectedPlan(base="abc", commands={"tests": "pytest --since {base}"}, fallback=("*.lock",))
AFFECTED = "pytest --since abc"
REUSE = Reuse("agentic", ("uv sync",), "0123456789abcdef0123456789abcdef01234567")


def gate(commands=None, *, plan=PLAN, paths=("src/a.py",), store=None, workspace=None, reuse=None):
    workspace = workspace or MemoryWorkspace()
    workspace.diff_paths = paths
    workspace.moved.add(PATH)
    workspace.trees.setdefault(PATH, "tree:one")
    workspace.record_setup_tree(PATH)
    ports = memory_ports(
        commands=commands or MemoryCommands(),
        workspace=workspace,
        check_results=store if store is not None else MemoryCheckResults(),
    )
    return CheckoutPresubmit(
        ports,
        PATH,
        "base",
        CHECKS,
        30.0,
        affected=plan,
        reuse=reuse,
        priority=CheckPriority.SUBTASK,
    )


def by_check(g):
    return {form["check"]: form for form in g.last_forms}


def test_forms_are_exposed_per_check_with_via_gate():
    g = gate()
    assert g() is None
    assert by_check(g) == {
        "tests": {"check": "tests", "mode": "affected-only", "cause": "none", "via": "gate"},
        "lint": {"check": "lint", "mode": "full", "cause": "no_affected_form", "via": "gate"},
    }
    assert g.forms == [g.last_forms]


def test_a_fallback_glob_names_the_glob_and_the_first_path_that_matched():
    g = gate(paths=("src/a.py", "deep/uv.lock", "z.lock"))
    assert g() is None
    assert by_check(g)["tests"] == {
        "check": "tests",
        "mode": "full",
        "cause": "fallback_glob",
        "via": "gate",
        "glob": "*.lock",
        "path": "deep/uv.lock",
    }


def test_an_ignored_untracked_task_document_on_a_clean_tree_records_none():
    workspace = MemoryWorkspace()
    workspace.ignored.add(".agentic/tasks/GH-1092.md")
    g = gate(workspace=workspace)
    assert g() is None
    assert by_check(g)["tests"]["cause"] == "none"


def test_a_gate_with_no_plan_records_full_with_no_affected_form():
    g = gate(plan=None)
    assert g() is None
    assert {(f["mode"], f["cause"]) for f in g.last_forms} == {("full", "no_affected_form")}


def test_the_exit_of_a_red_affected_form_decides_the_cause_of_the_full_form_after_it():
    for code, cause in ((8, "script_empty"), (9, "script_refused"), (1, "affected_red")):
        g = gate(MemoryCommands(**{AFFECTED: [CommandResult(code, "stderr says exit 1")]}))
        assert g() is None
        assert [r["exit_code"] for r in g.results or [] if r.get("mode") == "affected-only"] == [
            code
        ]
        assert by_check(g)["tests"]["mode"] == "full" and by_check(g)["tests"]["cause"] == cause


def test_a_timed_out_affected_form_is_affected_red_whatever_its_exit():
    g = gate(MemoryCommands(**{AFFECTED: [CommandResult(8, "slow", timed_out=True)]}))
    g()
    assert by_check(g)["tests"]["cause"] == "affected_red"


def test_a_rerun_of_the_full_form_keeps_the_cause_of_the_affected_row():
    commands = MemoryCommands(
        **{
            AFFECTED: [CommandResult(8, "")],
            "pytest": [CommandResult(1, "red"), CommandResult(0, "")],
        }
    )
    g = gate(commands)
    assert g() is None
    assert by_check(g)["tests"]["cause"] == "script_empty"


def test_a_check_stuck_in_full_records_stuck_full_on_the_next_invocation():
    commands = MemoryCommands(
        **{AFFECTED: [CommandResult(1, "")], "pytest": [CommandResult(1, "")] * 2}
    )
    g = gate(commands)
    assert g() is not None
    assert by_check(g)["tests"]["cause"] == "affected_red"
    assert g() is None
    assert by_check(g)["tests"]["cause"] == "stuck_full"
    assert len(g.forms) == 2


def test_a_call_that_refuses_before_running_has_no_forms():
    workspace = MemoryWorkspace()
    g = gate(workspace=workspace)
    workspace.moved.discard(PATH)
    assert g() is not None
    assert g.last_forms == [] and g.forms == []


def test_the_affected_and_the_full_form_have_different_keys():
    full = keys.check_key("p", "tests", "pytest", "tree", "fp")
    affected = keys.check_key("p", "tests", AFFECTED, "tree", "fp")
    assert full != affected


def test_a_stored_affected_pass_has_no_exit_code_and_an_entry_without_one_still_verifies(tmp_path):
    store = FilesystemCheckResults(tmp_path / "store")
    first = gate(MemoryCommands(), store=store, reuse=REUSE)
    assert first() is None
    row = next(r for r in first.results or [] if r.get("mode") == "affected-only")
    assert "exit_code" in row and "reused" not in row
    stored = [p for p in (tmp_path / "store").glob("*.json")]
    assert stored and all("exit_code" not in p.read_text(encoding="utf-8") for p in stored)

    second = gate(MemoryCommands(), store=store, reuse=REUSE)
    assert second() is None
    again = next(r for r in second.results or [] if r.get("mode") == "affected-only")
    assert again["reused"] is True and "exit_code" not in again
    assert by_check(second)["tests"]["cause"] == "none"


# The scaffolding the tool's plan is built on: `_paths`, `form_of`, `_declared_of`.


def test_paths_are_the_sorted_union_of_the_diff_and_the_pending_paths():
    g = gate(paths=("src/b.py", "src/a.py"))
    assert g._paths(PLAN, "head", ("src/c.py", "src/a.py")) == ("src/a.py", "src/b.py", "src/c.py")
    assert g._paths(PLAN, "head") == ("src/a.py", "src/b.py")


def test_paths_with_a_failed_pending_lookup_are_unknown():
    assert gate()._paths(PLAN, "head", None) is None


def test_paths_with_a_failed_diff_are_unknown(monkeypatch):
    for error in (GitError("no such commit"), OSError("gone")):
        g = gate()

        def failing(base, head, error=error):
            raise error

        monkeypatch.setattr(g.ports.workspace, "changed_paths", failing)
        assert g._paths(PLAN, "head", ("src/a.py",)) is None


def test_form_of_carries_the_via_the_instance_was_built_with():
    planned = PlannedForm("full", "no_affected_form", None, None)
    row: CheckResult = {
        "name": "tests",
        "command": "pytest",
        "passed": True,
        "output": "",
        "duration_s": 0.1,
    }
    assert gate().form_of(planned, [row])["via"] == "gate"
    tool = gate()
    tool.via = "tool"
    assert tool.form_of(planned, [row]) == {
        "check": "tests",
        "mode": "full",
        "cause": "no_affected_form",
        "via": "tool",
    }


def test_declared_of_gives_the_declared_command_of_an_executed_row_and_the_row_otherwise():
    g = gate()
    g._declared[("tests", "pytest -n 6")] = "pytest -n {workers}"
    ran: CheckResult = {
        "name": "tests",
        "command": "pytest -n 6",
        "passed": True,
        "output": "",
        "duration_s": 0.1,
    }
    never: CheckResult = {**ran, "name": "lint", "command": "ruff check ."}
    assert g._declared_of(ran) == "pytest -n {workers}"
    assert g._declared_of(never) == "ruff check ."


# The develop node's RoleRun.


@pytest.mark.anyio
async def test_the_developer_run_carries_the_forms_of_the_gates_last_invocation(tmp_path):
    runner = ScriptedRunner(developer=[delivered(), delivered()])
    ports = memory_ports(
        runner,
        stack=MemoryStack(checks={"tests": "pytest", "lint": "ruff"}),
        commands=MemoryCommands(),
    )
    state, _ = building(ports)
    patch = await develop(state, Configuration(product_dir=tmp_path), ports)
    run = patch["runs"][-1]
    assert [(f["check"], f["via"]) for f in run["forms"]] == [("tests", "gate"), ("lint", "gate")]
    assert "forms_dropped" not in run
