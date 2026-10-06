"""`CheckoutPresubmit` in its concurrent mode (ADR-0044), over fakes and over the real
shell: the gate still runs every declared check and reports them in declared order, the
serial mode is the same gate with one check at a time, and a check that raises lets the
others finish before the error leaves. Overlap is proven with an in-flight high-water
mark and events the test releases, never with a `sleep`."""

import subprocess
import threading
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.adapters.shell.commands import ShellCommands
from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
CHECKS = {"tests": "pytest", "lint": "ruff check .", "types": "mypy"}


def gate(
    commands: MemoryCommands,
    *,
    parallel: bool,
    witness: str | None = None,
    checks: dict[str, str] | None = None,
) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)  # a commit landed: `head` now differs from `before`
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    return CheckoutPresubmit(
        ports, PATH, "base", checks or CHECKS, 30.0, witness=witness, parallel=parallel
    )


def in_thread(call) -> tuple[threading.Thread, dict]:
    """Run `call` aside, keeping what it returned or raised."""
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # handed to the test, never swallowed
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def test_parallel_runs_every_check_at_once_and_reports_them_in_declared_order():
    release = threading.Event()
    # The declared-first check finishes last, so order cannot come from completion.
    commands = MemoryCommands(holds={c: release for c in CHECKS.values()})
    gate_ = gate(commands, parallel=True)
    thread, outcome = in_thread(gate_)
    assert commands.wait_until(lambda: commands.in_flight == len(CHECKS))
    release.set()
    thread.join(10)
    assert not thread.is_alive() and "error" not in outcome
    assert commands.high_water == len(CHECKS)
    (invocation,) = gate_.invocations
    assert [c["name"] for c in invocation] == list(CHECKS)
    assert [c["command"] for c in invocation] == list(CHECKS.values())
    assert all(c["duration_s"] >= 0.0 for c in invocation)
    assert gate_.results == invocation


def test_each_check_times_its_own_duration_in_parallel_mode():
    slow = threading.Event()
    commands = MemoryCommands(holds={"ruff check .": slow})
    gate_ = gate(commands, parallel=True)
    thread, _ = in_thread(gate_)
    assert commands.wait_until(
        lambda: "pytest" in commands.completed and "mypy" in commands.completed
    )
    # The held check has to be held for longer than the clock's resolution between the
    # others finishing and this release, or the comparison below is a race of microseconds.
    time.sleep(0.05)
    slow.set()
    thread.join(10)
    durations = {c["name"]: c["duration_s"] for c in gate_.results or []}
    assert set(durations) == set(CHECKS)
    assert durations["lint"] >= max(durations["tests"], durations["types"])


def test_serial_with_the_same_fake_never_overlaps():
    commands = MemoryCommands()
    gate_ = gate(commands, parallel=False)
    assert gate_() is None
    assert commands.high_water == 1
    assert [c for _, c in commands.ran] == list(CHECKS.values())
    assert [c["name"] for c in gate_.results or []] == list(CHECKS)


def test_a_failing_check_still_refuses_in_parallel_mode():
    commands = MemoryCommands(**{"ruff check .": [CommandResult(1, "E501 too long")]})
    gate_ = gate(commands, parallel=True)
    reason = gate_()
    assert reason is not None and "`lint` failed" in reason and "E501 too long" in reason
    assert [c["passed"] for c in gate_.results or []] == [True, False, True]
    assert gate_.refusals == 1


def test_a_raising_check_lets_the_others_finish_before_the_error_leaves():
    slow = threading.Event()
    commands = MemoryCommands(holds={"pytest": slow}, raises={"mypy"})
    gate_ = gate(commands, parallel=True)
    thread, outcome = in_thread(gate_)
    assert commands.wait_until(lambda: "mypy" in commands.completed)
    # The raising check is done, `pytest` is still held: the error has not left.
    assert thread.is_alive() and "error" not in outcome
    slow.set()
    thread.join(10)
    assert isinstance(outcome.get("error"), RuntimeError)
    assert sorted(commands.completed) == sorted(CHECKS.values())  # every one completed
    assert {c for _, c in commands.ran} == set(CHECKS.values())  # every one was called
    # Nothing recorded for that call, the same as serial.
    assert gate_.invocations == [] and gate_.results is None and gate_.head is None


def test_a_raising_check_records_nothing_in_serial_mode_either():
    gate_ = gate(MemoryCommands(raises={"ruff check ."}), parallel=False)
    with pytest.raises(RuntimeError):
        gate_()
    assert gate_.invocations == [] and gate_.results is None


def test_a_timed_out_check_does_not_stop_the_others_results():
    commands = MemoryCommands(**{"ruff check .": [CommandResult(-1, "partial", timed_out=True)]})
    gate_ = gate(commands, parallel=True)
    reason = gate_()
    assert reason is not None and "`lint`" in reason
    results = gate_.results or []
    assert [c["name"] for c in results] == list(CHECKS)
    assert [c["passed"] for c in results] == [True, False, True]
    assert "partial" in results[1]["output"]


@pytest.mark.parametrize(
    ("parallel", "witness", "expected"),
    [
        (False, None, 90.0),  # today's formula: one budget per check
        (False, "echo w", 120.0),
        (True, None, 30.0),  # concurrent: the slowest check bounds the call
        (True, "echo w", 60.0),  # plus the witness, which runs first and alone
    ],
)
def test_timeout_s_per_mode_with_and_without_a_witness(parallel, witness, expected):
    assert gate(MemoryCommands(), parallel=parallel, witness=witness).timeout_s == expected


def test_the_witness_runs_first_and_alone_before_any_check_in_parallel_mode():
    commands = MemoryCommands()
    commands.queues["echo w"] = [CommandResult(0, str(PATH))]
    gate_ = gate(commands, parallel=True, witness="echo w")
    assert gate_() is None
    assert [c for _, c in commands.ran][0] == "echo w"
    assert sorted(c for _, c in commands.ran[1:]) == sorted(CHECKS.values())
    assert gate_.witness_result is not None and gate_.witness_result["passed"] is True


@pytest.mark.anyio
async def test_develop_hands_the_stacks_parallel_checks_to_the_gate(tmp_path):
    release = threading.Event()
    checks = {"tests": "pytest", "lint": "ruff check ."}
    commands = MemoryCommands(holds={c: release for c in checks.values()})
    ports = memory_ports(
        ScriptedRunner(developer=[delivered()]),
        stack=MemoryStack(checks, parallel_checks=True),
        commands=commands,
    )
    state = {
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
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    ports.workspace.existing_branches["agentic/GH-12"] = ("head", "base0")

    def releaser() -> None:
        commands.wait_until(lambda: commands.in_flight == len(checks))
        release.set()  # a serial gate never reaches 2 in flight: this fires on timeout

    helper = threading.Thread(target=releaser, daemon=True)
    helper.start()
    await develop(state, Configuration(product_dir=tmp_path), ports)
    helper.join(10)
    assert commands.high_water == len(checks)


# --- the real shell -----------------------------------------------------------------


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def checkout(tmp_path):
    git("init", "-q", "-b", "master", ".", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "v0", cwd=tmp_path)
    ports = type(
        "Ports",
        (),
        {
            "workspace": GitWorkspace(tmp_path, Configuration(product_dir=tmp_path).git_identity),
            "commands": ShellCommands(),
        },
    )()
    before = ports.workspace.head(tmp_path)
    (tmp_path / "b.txt").write_text("b\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "deliver", cwd=tmp_path)
    return ports, tmp_path, before


@pytest.mark.parametrize("parallel", [False, True])
def test_the_real_shell_gate_runs_quick_checks_in_declared_order_in_both_modes(checkout, parallel):
    ports, path, before = checkout
    checks = {"first": "echo one; sleep 0.2", "second": "echo two", "third": "exit 3"}
    gate_ = CheckoutPresubmit(ports, path, before, checks, 20.0, parallel=parallel)
    reason = gate_()
    assert reason is not None and "`third` failed" in reason
    results = gate_.results or []
    assert [c["name"] for c in results] == ["first", "second", "third"]
    assert [c["passed"] for c in results] == [True, True, False]
    assert "one" in results[0]["output"] and "two" in results[1]["output"]
    assert results[0]["duration_s"] >= 0.2


def test_the_real_shell_runs_concurrent_checks_at_the_same_time(checkout):
    """Each check waits (bounded) for the other's flag: only a gate that really overlaps
    them lets both pass."""
    ports, path, before = checkout

    def meet(mine: str, theirs: str) -> str:
        return (
            f"touch {mine}; for i in $(seq 100); do [ -f {theirs} ] && exit 0; "
            "sleep 0.05; done; exit 1"
        )

    checks = {"left": meet("l.flag", "r.flag"), "right": meet("r.flag", "l.flag")}
    gate_ = CheckoutPresubmit(ports, path, before, checks, 20.0, parallel=True)
    assert gate_() is None
    assert [c["passed"] for c in gate_.results or []] == [True, True]
