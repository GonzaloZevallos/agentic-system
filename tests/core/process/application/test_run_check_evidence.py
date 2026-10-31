"""What `RunCheck` keeps of each call on a verification (ADR-0065 addendum, GH-451): one
record per call, a refused name included, and the block of them the Developer's section carries."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.run_check import Outcome, Verification
from tests.fakes import MemoryCheckResults, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-1.api")
COMPARE = Verification("scripts/design/compare.sh --strict", 900.0)


class Readings:
    """A load reader that answers the figures it was given, in order."""

    def __init__(self, *figures: float | None) -> None:
        self.figures = list(figures)

    def __call__(self) -> float | None:
        return self.figures.pop(0)


def tool(commands: MemoryCommands | None = None, **kwargs) -> RunCheck:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.record_setup_tree(PATH)
    ports = memory_ports(
        workspace=workspace,
        commands=commands or MemoryCommands(),
        check_results=MemoryCheckResults(),
    )
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest"},
        30.0,
        priority=CheckPriority.SUBTASK,
        reuse=Reuse("shop", ("uv sync",), "a" * 40),
    )
    return RunCheck(gate, verifications={"compare": COMPARE}, **kwargs)


def test_a_call_on_a_verification_leaves_one_record_with_the_systems_figures():
    commands = MemoryCommands(**{COMPARE.command: [CommandResult(0, "offenders: 0\n")]})
    handler = tool(commands, load=Readings(2.0, 7.5), cores=16)
    handler("compare")
    [recorded] = handler.calls
    assert recorded.name == "compare" and recorded.outcome is Outcome.PASSED
    assert (recorded.load_before, recorded.load_after, recorded.cores) == (2.0, 7.5, 16)
    assert recorded.tail == "offenders: 0" and recorded.duration_s >= 0.0


def test_a_verification_that_fails_is_recorded_as_failed_not_as_passed():
    commands = MemoryCommands(**{COMPARE.command: [CommandResult(1, "offender 3\n")]})
    handler = tool(commands)
    handler("compare")
    assert [c.outcome for c in handler.calls] == [Outcome.FAILED]


def test_a_name_that_is_not_declared_is_recorded_as_not_declared_and_ran_nothing():
    commands = MemoryCommands()
    handler = tool(commands)
    reply = handler("mobile-matrix")
    assert reply.startswith("unknown check")
    [recorded] = handler.calls
    assert recorded.name == "mobile-matrix" and recorded.outcome is Outcome.NOT_DECLARED
    assert recorded.duration_s == 0.0 and recorded.tail == ""
    assert commands.ran == []


def test_a_declared_check_is_not_a_verification_call():
    handler = tool()
    handler("tests")
    assert handler.calls == [] and handler.evidence() is None


def test_the_block_names_every_call_so_a_summary_cannot_stand_in_for_it():
    handler = tool()
    handler("compare")
    handler("nope")
    text = handler.evidence()
    assert text is not None and "`compare`" in text and "`not_declared`" in text
