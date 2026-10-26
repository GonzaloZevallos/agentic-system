"""`CheckoutPresubmit` under an `AffectedPlan` (ADR-0046), over fakes: the affected-only
form on a green change, the full rerun on a red one, the fallback and lookup-failure
paths, the instance's stays-full memory, and the byte-identical `None` plan."""

from pathlib import Path

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain import presubmit
from agentic.core.process.domain.presubmit import AffectedPlan
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-215.gate")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
PLAN = AffectedPlan(
    base="abc123", commands={"tests": "pytest --since {base}"}, fallback=("*.lock",)
)
AFFECTED = "pytest --since abc123"


class FailingWorkspace(MemoryWorkspace):
    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        raise GitError("no diff")


def gate(
    commands: MemoryCommands,
    *,
    plan: AffectedPlan | None = PLAN,
    paths: tuple[str, ...] = ("src/a.py",),
    parallel: bool = False,
    witness: str | None = None,
    workspace: MemoryWorkspace | None = None,
) -> CheckoutPresubmit:
    workspace = workspace or MemoryWorkspace()
    workspace.diff_paths = paths
    workspace.moved.add(PATH)
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    return CheckoutPresubmit(
        ports, PATH, "base", CHECKS, 30.0, affected=plan, parallel=parallel, witness=witness
    )


def ran(commands: MemoryCommands) -> list[str]:
    return [c for _, c in commands.ran]


def test_a_green_affected_run_records_one_affected_only_row_and_the_full_form_for_the_rest():
    commands = MemoryCommands()
    g = gate(commands)
    assert g() is None
    assert ran(commands) == [AFFECTED, "ruff check ."]
    assert g.results is not None
    assert [(r["name"], r["mode"]) for r in g.results] == [
        ("tests", "affected-only"),
        ("lint", "full"),
    ]
    markdown = g.markdown()
    assert markdown is not None and "affected-only" in markdown
    assert "selected against `abc123`" in markdown


def test_the_changed_paths_are_read_against_the_plan_base():
    workspace = MemoryWorkspace()
    g = gate(MemoryCommands(), workspace=workspace)
    g()
    assert workspace.diff_reads == [("abc123", g.head)]


def test_a_red_affected_run_is_followed_by_the_full_form_as_a_second_row():
    commands = MemoryCommands(**{AFFECTED: [CommandResult(1, "stale")]})
    g = gate(commands)
    assert g() is None  # the full run passed: the final row decides
    assert ran(commands) == [AFFECTED, "pytest", "ruff check ."]
    assert g.results is not None
    assert [(r["name"], r["mode"], r["passed"]) for r in g.results] == [
        ("tests", "affected-only", False),
        ("tests", "full", True),
        ("lint", "full", True),
    ]
    assert g.invocations == [g.results]


def test_a_red_affected_and_red_full_run_refuses_on_the_final_row():
    commands = MemoryCommands(
        **{AFFECTED: [CommandResult(1, "stale")], "pytest": [CommandResult(1, "real")] * 2}
    )
    g = gate(commands)
    reason = g()
    assert reason is not None and "real" in reason and "stale" not in reason
    assert g.refusals == 1


def test_a_fallback_glob_runs_every_check_full():
    commands = MemoryCommands()
    g = gate(commands, paths=("src/a.py", "uv.lock"))
    assert g() is None
    assert ran(commands) == ["pytest", "ruff check ."]
    assert g.results is not None and {r["mode"] for r in g.results} == {"full"}


def test_a_changed_paths_error_runs_full():
    commands = MemoryCommands()
    g = gate(commands, workspace=FailingWorkspace())
    assert g() is None
    assert ran(commands) == ["pytest", "ruff check ."]


def test_an_os_error_in_the_lookup_runs_full():
    class Broken(MemoryWorkspace):
        def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
            raise OSError("gone")

    commands = MemoryCommands()
    g = gate(commands, workspace=Broken())
    assert g() is None
    assert ran(commands) == ["pytest", "ruff check ."]


def test_an_empty_change_set_runs_full():
    commands = MemoryCommands()
    g = gate(commands, paths=())
    assert g() is None
    assert ran(commands) == ["pytest", "ruff check ."]


def test_a_check_that_failed_in_full_never_runs_affected_again_on_that_instance():
    commands = MemoryCommands(
        **{
            AFFECTED: [CommandResult(1, "stale")],
            "pytest": [CommandResult(1, "real")] * 2 + [CommandResult(0, "ok")] * 2,
        }
    )
    g = gate(commands)
    assert g() is not None  # affected red, full red
    assert g() is None  # now full only, even though it passes
    assert g() is None  # and still full only
    assert ran(commands).count(AFFECTED) == 1
    assert ran(commands).count("pytest") == 4  # the red full run, its rerun, then two greens
    assert len(g.invocations) == 3
    assert g.refusals == 1


def test_a_check_that_failed_in_full_runs_full_only_on_the_next_parallel_call():
    commands = MemoryCommands(
        **{
            AFFECTED: [CommandResult(1, "stale")],
            "pytest": [CommandResult(1, "real")] * 2 + [CommandResult(0, "ok")],
        }
    )
    g = gate(commands, parallel=True)
    assert g() is not None  # affected red, full red
    assert g() is None  # now full only
    assert g.results is not None
    assert [(r["name"], r["mode"]) for r in g.results] == [("tests", "full"), ("lint", "full")]
    assert ran(commands).count(AFFECTED) == 1
    assert ran(commands).count("pytest") == 3


def test_a_fresh_instance_starts_on_affected_only_again():
    commands = MemoryCommands(
        **{AFFECTED: [CommandResult(1, "x")], "pytest": [CommandResult(1, "y")]}
    )
    gate(commands)()
    commands.ran.clear()
    gate(commands)()
    assert ran(commands)[0] == AFFECTED


def test_the_refusal_budget_is_the_same_with_a_plan():
    commands = MemoryCommands(
        **{
            AFFECTED: [CommandResult(1, "a")] * 5,
            "pytest": [CommandResult(1, "b")] * 5,
        }
    )
    g = gate(commands)
    reasons = [g() for _ in range(presubmit.REFUSALS + 1)]
    assert all(r is not None for r in reasons[: presubmit.REFUSALS])
    assert reasons[-1] is None
    assert g.refusals == presubmit.REFUSALS


def test_the_parallel_gate_keeps_declared_order_and_each_rerun_inside_its_check():
    commands = MemoryCommands(**{AFFECTED: [CommandResult(1, "stale")]})
    g = gate(commands, parallel=True)
    assert g() is None
    assert g.results is not None
    assert [(r["name"], r["mode"]) for r in g.results] == [
        ("tests", "affected-only"),
        ("tests", "full"),
        ("lint", "full"),
    ]


def test_timeout_with_a_plan_counts_the_affected_runs():
    commands = MemoryCommands()
    # Three runs and one possible rerun per declared check (ADR-0066).
    assert gate(commands).timeout_s == 30.0 * (2 + 1) + 30.0 * 2
    assert gate(commands, witness="echo").timeout_s == 30.0 * 3 + 30.0 * 2 + 30.0
    # Affected, full and the rerun, one after the other in one worker.
    assert gate(commands, parallel=True).timeout_s == 90.0
    assert gate(commands, parallel=True, witness="echo").timeout_s == 120.0


def test_a_none_plan_is_the_gate_of_today():
    commands = MemoryCommands()
    workspace = MemoryWorkspace()
    g = gate(commands, plan=None, workspace=workspace)
    assert g.timeout_s == 120.0  # a run and a possible rerun per check
    assert g() is None
    assert ran(commands) == ["pytest", "ruff check ."]
    assert g.results is not None and all("mode" not in r for r in g.results)
    assert g.markdown() is not None and "affected" not in (g.markdown() or "")
    assert workspace.diff_reads == []  # no plan, no lookup


def develop_state() -> dict:
    return {
        "id": "GH-12",
        "title": "Dashboard",
        "description": "How is my gym doing.",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
        "subtasks": {
            sid: {"id": sid, "title": sid, "brief": "b", "seams": ["s"]}
            for sid in ("api", "domain")
        },
        "subtask_id": "api",
    }


async def run_develop(tmp_path: Path, stack: MemoryStack) -> tuple[MemoryCommands, MemoryWorkspace]:
    commands = MemoryCommands()
    ports = memory_ports(ScriptedRunner(developer=[delivered()]), stack=stack, commands=commands)
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    ports.workspace.existing_branches["agentic/GH-12"] = ("head", "base0")
    ports.workspace.diff_paths = ("src/a.py",)
    await develop(develop_state(), Configuration(product_dir=tmp_path), ports)
    return commands, ports.workspace


@pytest.mark.anyio
async def test_develop_builds_the_plan_from_the_stack_at_the_task_base(tmp_path):
    stack = MemoryStack(
        CHECKS, affected_checks={"tests": "pytest --since {base}"}, affected_fallback=("*.lock",)
    )
    commands, workspace = await run_develop(tmp_path, stack)
    assert [c for _, c in commands.ran] == ["pytest --since base0", "ruff check ."]
    assert [base for base, _ in workspace.diff_reads] == ["base0"]


@pytest.mark.anyio
async def test_develop_without_affected_checks_runs_the_gate_of_today(tmp_path):
    commands, workspace = await run_develop(tmp_path, MemoryStack(CHECKS))
    assert [c for _, c in commands.ran] == ["pytest", "ruff check ."]
    assert workspace.diff_reads == []
