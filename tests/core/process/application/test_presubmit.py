"""`CheckoutPresubmit` over fake ports: what it keeps across invocations, for the
metrics that read it (`agentic.presubmit.checks`, `.duration`)."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from tests.fakes import MemoryCommands, MemoryWorkspace

PATH = Path("/memory/worktrees/GH-1.api")


def presubmit(commands: MemoryCommands, workspace: MemoryWorkspace | None = None) -> tuple:
    if workspace is None:
        workspace = MemoryWorkspace()
        workspace.moved.add(PATH)  # a commit landed: `head` now differs from `before`
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    gate = CheckoutPresubmit(ports, PATH, "base", {"tests": "pytest"}, 30.0)
    return gate, workspace


def test_a_refused_delivery_still_leaves_its_invocation_with_a_duration():
    commands = MemoryCommands(pytest=[CommandResult(1, "1 failed")])
    gate, _ = presubmit(commands)
    reason = gate()
    assert reason is not None and "failed" in reason
    (invocation,) = gate.invocations
    (check,) = invocation
    assert check["name"] == "tests" and check["passed"] is False
    assert check["duration_s"] >= 0.0


def test_every_invocation_that_actually_ran_checks_is_kept_in_order():
    commands = MemoryCommands(pytest=[CommandResult(1, "1 failed"), CommandResult(0, "ok")])
    gate, _ = presubmit(commands)
    first = gate()
    second = gate()
    assert first is not None and second is None  # refused once, then let through
    assert len(gate.invocations) == 2
    assert [inv[0]["passed"] for inv in gate.invocations] == [False, True]


def test_a_call_before_a_commit_leaves_no_invocation():
    """Nothing to check before there is a commit on a clean tree: the presubmit refuses
    on that alone, and no check ever ran, so there is nothing to keep."""
    workspace = MemoryWorkspace()  # never added to `moved`: the branch never advanced
    gate, _ = presubmit(MemoryCommands(), workspace)
    reason = gate()
    assert reason is not None and gate.invocations == []
