"""`CheckoutPresubmit` reruns a red check once before it decides (ADR-0066), over fakes:
the three outcomes (pass, flake, fail), the checks that are never rerun, which command the
rerun uses, what is stored, and the timeout the harness is told."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.domain.presubmit import AffectedPlan, FlakeGate, RerunForm
from tests.fakes import MemoryCheckResults, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-360.gate")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
FORM = RerunForm(command="pytest --lf", test_id=r"(?m)^FAILED (\S+)")
PYTEST_RED = CommandResult(1, "FAILED tests/test_a.py::test_one\nFAILED tests/test_b.py::t2\n")
PLAN = AffectedPlan(base="abc", commands={"tests": "pytest --since {base}"}, fallback=())
AFFECTED = "pytest --since abc"


def gate(
    commands: MemoryCommands,
    *,
    forms: dict[str, RerunForm] | None = None,
    flake_gate: FlakeGate = "delivery",
    parallel: bool = False,
    plan: AffectedPlan | None = None,
    checks: dict[str, str] | None = None,
) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.diff_paths = ("src/a.py",)
    workspace.moved.add(PATH)
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    return CheckoutPresubmit(
        ports,
        PATH,
        "base",
        checks or CHECKS,
        30.0,
        parallel=parallel,
        affected=plan,
        rerun_forms=forms,
        flake_gate=flake_gate,
    )


def ran(commands: MemoryCommands) -> list[str]:
    return [c for _, c in commands.ran]


@pytest.mark.parametrize("parallel", [False, True])
def test_a_green_check_is_not_rerun(parallel):
    commands = MemoryCommands()
    g = gate(commands, parallel=parallel)
    assert g() is None
    assert sorted(ran(commands)) == sorted(CHECKS.values())
    assert g.flakes() == []
    assert g.results is not None and not any(r.get("rerun") for r in g.results)


@pytest.mark.parametrize("parallel", [False, True])
def test_red_then_green_passes_the_delivery_as_a_flake_with_one_record(parallel):
    commands = MemoryCommands(pytest=[PYTEST_RED, CommandResult(0, "ok")])
    g = gate(commands, parallel=parallel)
    assert g() is None  # the gate lets it through
    assert g.results is not None
    rows = [(r["name"], r["passed"], r.get("rerun", False)) for r in g.results]
    assert rows == [("tests", False, False), ("tests", True, True), ("lint", True, False)]
    assert g.results[1]["command"] == "pytest" and g.results[1]["duration_s"] >= 0.0
    markdown = g.markdown() or ""
    assert "flake" in markdown and "| `pytest` | passed |" not in markdown
    assert g.flakes() == [{"check": "tests", "head": g.head, "gate": "delivery", "tests": []}]


def test_red_twice_refuses_with_the_reruns_output():
    commands = MemoryCommands(
        pytest=[CommandResult(1, "first output"), CommandResult(1, "second output")]
    )
    g = gate(commands)
    reason = g()
    assert reason is not None and "second output" in reason and "first output" not in reason
    assert g.flakes() == []
    assert ran(commands).count("pytest") == 2  # one rerun, never a third run


def test_a_timed_out_check_is_refused_without_a_rerun():
    commands = MemoryCommands(pytest=[CommandResult(-9, "partial", timed_out=True)])
    g = gate(commands, forms={"tests": FORM})
    reason = g()
    assert reason is not None and "timed out" in reason
    assert ran(commands).count("pytest") == 1 and "pytest --lf" not in ran(commands)
    assert g.results is not None and g.results[0]["timed_out"] is True
    assert g.flakes() == []


def test_the_declared_form_runs_when_ids_were_parsed_at_the_delivery_gate():
    commands = MemoryCommands(pytest=[PYTEST_RED], **{"pytest --lf": [CommandResult(0, "ok")]})
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]
    assert g.results is not None and g.results[1]["command"] == "pytest --lf"
    (flake,) = g.flakes()
    assert flake["tests"] == ["tests/test_a.py::test_one", "tests/test_b.py::t2"]


def test_the_full_command_reruns_when_the_form_parsed_no_ids():
    commands = MemoryCommands(pytest=[CommandResult(1, "boom, no ids"), CommandResult(0, "ok")])
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert "pytest --lf" not in ran(commands)
    assert g.flakes()[0]["tests"] == []


@pytest.mark.parametrize("flake_gate", ["merge", "full_checks"])
def test_gates_other_than_delivery_rerun_the_full_command_never_the_form(flake_gate):
    commands = MemoryCommands(pytest=[PYTEST_RED, CommandResult(0, "ok")])
    g = gate(commands, forms={"tests": FORM}, flake_gate=flake_gate)
    assert g() is None
    assert "pytest --lf" not in ran(commands) and ran(commands).count("pytest") == 2
    assert g.flakes()[0]["gate"] == flake_gate


def test_an_affected_red_is_followed_by_the_full_form_and_only_the_full_reds_rerun():
    commands = MemoryCommands(
        **{AFFECTED: [CommandResult(1, "affected red")]},
        pytest=[CommandResult(1, "full red"), CommandResult(0, "ok")],
    )
    g = gate(commands, plan=PLAN)
    assert g() is None
    assert ran(commands) == [AFFECTED, "pytest", "pytest", "ruff check ."]
    assert g.results is not None
    assert [(r["command"], r.get("mode"), r.get("rerun", False)) for r in g.results[:3]] == [
        (AFFECTED, "affected-only", False),
        ("pytest", "full", False),
        ("pytest", "full", True),
    ]


def test_a_green_full_form_after_a_red_affected_one_is_not_rerun():
    commands = MemoryCommands(**{AFFECTED: [CommandResult(1, "stale")]})
    g = gate(commands, plan=PLAN)
    assert g() is None
    assert ran(commands) == [AFFECTED, "pytest", "ruff check ."]
    assert g.flakes() == []


def test_a_flaked_check_stores_nothing_but_a_green_one_still_does():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.trees[PATH] = "tree:one"
    workspace.record_setup_tree(PATH)
    store = MemoryCheckResults()
    ports = memory_ports(
        commands=MemoryCommands(pytest=[PYTEST_RED, CommandResult(0, "ok")]),
        workspace=workspace,
        check_results=store,
    )
    g = CheckoutPresubmit(
        ports, PATH, "base", CHECKS, 30.0, reuse=Reuse("p", ("uv sync",), "a" * 40)
    )
    assert g() is None
    # lint only: the flaked check's green rerun is never stored
    assert [row["name"] for _, row, _ in store.puts] == ["lint"]


@pytest.mark.parametrize(
    ("parallel", "plan", "expected"),
    [
        (False, None, 30.0 * 2 + 30.0 * 2),  # one run per check, plus one rerun per check
        (True, None, 30.0 + 30.0),  # the slowest check, then its rerun
        (False, PLAN, 30.0 * 3 + 30.0 * 2),
        (True, PLAN, 30.0 * 2 + 30.0),  # affected, full, rerun in one worker
    ],
)
def test_timeout_s_includes_the_possible_rerun(parallel, plan, expected):
    assert gate(MemoryCommands(), parallel=parallel, plan=plan).timeout_s == expected
