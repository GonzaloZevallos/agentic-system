"""`CheckoutPresubmit`'s checkout witness, over fakes (glossary: Testigo del checkout;
ADR-0024): the `None` no-op branch that must leave the presubmit of today untouched,
and the refusal-budget arithmetic that keeps a misdeclared witness from spending the
checks' own `REFUSALS`. `tests/core/process/application/test_presubmit.py` stays
byte-identical -- this module exists so it does not have to move."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from tests.fakes import MemoryCommands, MemoryWorkspace

PATH = Path("/memory/worktrees/GH-41.api")


def presubmit(commands: MemoryCommands, witness: str | None = None) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)  # a commit landed: `head` now differs from `before`
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    return CheckoutPresubmit(ports, PATH, "base", {"tests": "pytest"}, 30.0, witness=witness)


def test_no_witness_runs_no_extra_command_and_leaves_no_markdown_line():
    commands = MemoryCommands()
    gate = presubmit(commands)  # no witness declared
    assert gate() is None
    assert [c for _, c in commands.ran] == ["pytest"]  # the check alone, nothing else
    assert gate.invocations and "itness" not in (gate.markdown() or "")
    assert gate.timeout_s == 60.0  # a run and a possible rerun per check (ADR-0066)


def test_no_witness_leaves_the_witness_budget_and_refusals_untouched():
    commands = MemoryCommands(pytest=[CommandResult(1, "boom")] * 2)
    gate = presubmit(commands)
    reason = gate()
    assert reason is not None and "boom" in reason
    assert gate.witness_refusals == 0 and gate.refusals == 1


def test_a_witness_that_agrees_runs_before_the_checks_and_does_not_refuse():
    commands = MemoryCommands()
    commands.queues["echo ok"] = [CommandResult(0, str(PATH))]
    gate = presubmit(commands, witness="echo ok")
    assert gate.timeout_s == 90.0  # a run, its rerun and the witness's own budget
    assert gate() is None
    assert [c for _, c in commands.ran] == ["echo ok", "pytest"]  # witness first
    assert gate.witness_refusals == 0
    markdown = gate.markdown()
    assert markdown is not None
    assert "echo ok" in markdown and str(PATH) in markdown and "agreed" in markdown


def test_a_witness_that_disagrees_refuses_once_on_its_own_budget():
    commands = MemoryCommands()
    commands.queues["echo outside"] = [
        CommandResult(0, "/elsewhere"),
        CommandResult(0, "/elsewhere"),
    ]
    gate = presubmit(commands, witness="echo outside")

    first = gate()
    assert first is not None and "checkout witness" in first.lower()
    assert gate.witness_refusals == 1 and gate.refusals == 0  # the checks' own, untouched

    # The witness's own budget is spent; it keeps running (its markdown line must
    # still show), but it no longer refuses -- and the checks passed, so this delivery
    # goes through, with the disagreement still on record for the reviewer.
    second = gate()
    assert second is None
    assert gate.witness_refusals == 1
    markdown = gate.markdown()
    assert markdown is not None and "disagreed" in markdown and "/elsewhere" in markdown


def test_a_disagreeing_witness_leads_even_when_the_checks_also_fail():
    commands = MemoryCommands(pytest=[CommandResult(1, "boom")])
    commands.queues["echo outside"] = [CommandResult(0, "/elsewhere")]
    gate = presubmit(commands, witness="echo outside")

    reason = gate()
    assert reason is not None and "checkout witness" in reason.lower()
    assert gate.witness_refusals == 1 and gate.refusals == 0
    # The checks still ran, so the record does not lose the table to a witness refusal.
    assert gate.invocations and gate.invocations[0][0]["passed"] is False


def test_no_command_is_spawned_before_a_commit_or_on_a_dirty_tree():
    workspace = MemoryWorkspace()  # never added to `moved`: nothing was committed
    commands = MemoryCommands()
    commands.queues["echo ok"] = [CommandResult(0, str(PATH))]
    ports = type("Ports", (), {"workspace": workspace, "commands": commands})()
    gate = CheckoutPresubmit(ports, PATH, "base", {"tests": "pytest"}, 30.0, witness="echo ok")
    reason = gate()
    assert reason is not None and commands.ran == [] and gate.witness_refusals == 0
