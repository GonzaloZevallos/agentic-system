"""`CheckoutPresubmit` over fake ports: what it keeps across invocations, for the
metrics that read it (`agentic.presubmit.checks`, `.duration`)."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from tests.fakes import FakeCheckScheduler, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-1.api")


def presubmit(commands: MemoryCommands, workspace: MemoryWorkspace | None = None) -> tuple:
    if workspace is None:
        workspace = MemoryWorkspace()
        workspace.moved.add(PATH)  # a commit landed: `head` now differs from `before`
    ports = type(
        "Ports",
        (),
        {"workspace": workspace, "commands": commands, "check_scheduler": FakeCheckScheduler()},
    )()
    gate = CheckoutPresubmit(
        ports, PATH, "base", {"tests": "pytest"}, 30.0, priority=CheckPriority.SUBTASK
    )
    return gate, workspace


def test_a_refused_delivery_still_leaves_its_invocation_with_a_duration():
    commands = MemoryCommands(pytest=[CommandResult(1, "1 failed")] * 2)
    gate, _ = presubmit(commands)
    reason = gate()
    assert reason is not None and "failed" in reason
    (invocation,) = gate.invocations
    check, again = invocation  # the red row and its one rerun (ADR-0066)
    assert again["rerun"] is True
    assert check["name"] == "tests" and check["passed"] is False
    assert check["duration_s"] >= 0.0


def test_every_invocation_that_actually_ran_checks_is_kept_in_order():
    commands = MemoryCommands(pytest=[CommandResult(1, "1 failed")] * 2 + [CommandResult(0, "ok")])
    gate, _ = presubmit(commands)
    first = gate()
    second = gate()
    assert first is not None and second is None  # refused once, then let through
    assert len(gate.invocations) == 2
    assert [inv[-1]["passed"] for inv in gate.invocations] == [False, True]


def test_a_call_before_a_commit_leaves_no_invocation():
    """Nothing to check before there is a commit on a clean tree: the presubmit refuses
    on that alone, and no check ever ran, so there is nothing to keep."""
    workspace = MemoryWorkspace()  # never added to `moved`: the branch never advanced
    gate, _ = presubmit(MemoryCommands(), workspace)
    reason = gate()
    assert reason is not None and gate.invocations == []


# -- criteria documents the delivery makes longer than the cap (GH-334) --------------


def _lines(count: int) -> str:
    return "".join(f"rule {n}\n" for n in range(count))


class UnreadableWorkspace(MemoryWorkspace):
    def show(self, ref: str, relative: str) -> str | None:
        raise GitError("cannot read")


def criteria_gate(workspace: MemoryWorkspace | None = None, *, base: str | None = "base") -> tuple:
    workspace = workspace or MemoryWorkspace()
    workspace.moved.add(PATH)
    ports = memory_ports()
    ports = type(
        "Ports",
        (),
        {
            "workspace": workspace,
            "commands": MemoryCommands(),
            "knowledge": ports.knowledge,
            "check_scheduler": FakeCheckScheduler(),
        },
    )()
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest"},
        30.0,
        criteria_base=base,
        priority=CheckPriority.SUBTASK,
    )
    return gate, workspace


def put(workspace: MemoryWorkspace, ref: str, name: str, count: int) -> None:
    workspace.files[(ref, name)] = _lines(count)


def test_a_delivery_that_grows_a_criteria_document_past_the_cap_is_refused_with_file_and_count():
    gate, workspace = criteria_gate()
    put(workspace, "base", "DESIGN_STANDARDS.md", 290)
    put(workspace, "head1", "DESIGN_STANDARDS.md", 611)
    reason = gate()
    assert reason is not None
    assert "DESIGN_STANDARDS.md" in reason and "611" in reason and "290" in reason


def test_a_regression_spends_the_same_budget_and_then_shows_in_the_developers_section():
    gate, workspace = criteria_gate()
    put(workspace, "base", "DESIGN_STANDARDS.md", 290)
    for head in ("head1", "head2", "head3"):
        put(workspace, head, "DESIGN_STANDARDS.md", 611)
    assert gate() is not None and gate() is not None  # REFUSALS
    assert gate() is None  # the budget is spent: let through
    markdown = gate.markdown()
    assert markdown is not None and "DESIGN_STANDARDS.md" in markdown and "611" in markdown


def test_a_delivery_that_leaves_an_already_over_cap_document_alone_is_not_refused():
    gate, workspace = criteria_gate()
    put(workspace, "base", "DESIGN_STANDARDS.md", 611)
    put(workspace, "head1", "DESIGN_STANDARDS.md", 611)
    assert gate() is None
    markdown = gate.markdown()
    assert markdown is not None and "DESIGN_STANDARDS" not in markdown


def test_a_gate_given_no_criteria_base_reads_no_document():
    gate, workspace = criteria_gate(base=None)
    put(workspace, "head1", "DESIGN_STANDARDS.md", 611)
    assert gate() is None


def test_a_git_error_while_reading_the_documents_reports_no_regression():
    """The merge decision is the guarantee; the early warning never blocks on a failed read."""
    gate, _ = criteria_gate(UnreadableWorkspace())
    assert gate() is None


# -- paths the delivery introduces that the system never versions (GH-420) ----------

DOC = ".agentic/tasks/GH-1.md"


def test_the_cleanup_shape_passes_and_the_gate_read_against_the_fetched_upstream():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.introduced = ()  # `git rm --cached` only deletes: the view never returns it
    gate, _ = presubmit(MemoryCommands(), workspace)
    assert gate() is None
    assert workspace.introduced_reads == [("base", "head1", "upstream-tip")]
    assert gate.markdown() is not None and "nstageable" not in gate.markdown()


def test_an_added_document_is_refused_with_its_path_before_any_check_runs():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.introduced = ("src/app.py", DOC)
    commands = MemoryCommands()
    gate, _ = presubmit(commands, workspace)
    reason = gate()
    assert reason is not None and f"`{DOC}`" in reason and "git rm --cached" in reason
    assert "(1 of 2)" in reason and "src/app.py" not in reason
    assert gate.invocations == [] and commands.ran == []


def test_a_document_past_the_refusal_budget_passes_with_a_line_in_the_gate_result():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.introduced = (DOC,)
    gate, _ = presubmit(MemoryCommands(), workspace)
    assert gate() is not None and gate() is not None
    assert gate() is None
    assert len(gate.invocations) == 1  # the checks ran once the budget was spent
    markdown = gate.markdown()
    assert markdown is not None and "Unstageable paths" in markdown and f"`{DOC}`" in markdown


def test_a_git_error_from_the_view_is_no_refusal_and_is_written_in_the_gate_result():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.introduced_error = "git diff base...head1 failed: bad object"
    gate, _ = presubmit(MemoryCommands(), workspace)
    assert gate() is None
    markdown = gate.markdown()
    assert markdown is not None and "could not be read" in markdown and "bad object" in markdown


def test_a_gate_with_nothing_before_it_never_asks_what_it_introduced():
    """The merge and full-checks gates start from no commit (`before=""`): nothing is
    "introduced" against nothing."""
    workspace = MemoryWorkspace()
    workspace.introduced = (DOC,)
    ports = type(
        "Ports",
        (),
        {
            "workspace": workspace,
            "commands": MemoryCommands(),
            "check_scheduler": FakeCheckScheduler(),
        },
    )()
    gate = CheckoutPresubmit(
        ports, PATH, "", {"tests": "pytest"}, 30.0, priority=CheckPriority.LAND
    )
    assert gate() is None and workspace.introduced_reads == []
