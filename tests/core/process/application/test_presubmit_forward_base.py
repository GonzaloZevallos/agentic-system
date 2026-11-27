"""The forward affected base at the Developer's gate (GH-1125, application half): over two gate
calls of one `CheckoutPresubmit`, a check's affected form is measured against the head it was
last green on, and an empty selection keeps that verdict instead of running the full suite.
Without `advance_base` the gate is the one of before."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan, GateForm
from tests.fakes import (
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1130.gate")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
PLAN = AffectedPlan(base="abc", commands={"tests": "pytest --since {base}"}, fallback=("*.lock",))
CONTEXT_PLAN = AffectedPlan(
    base="abc", commands={"tests": "pytest --since {base}"}, fallback=("*CONTEXT.md",)
)
AT_TASK_BASE = "pytest --since abc"
AT_G = "pytest --since head1"
LINT = "ruff check ."
WITNESS = "witness"
RED = CommandResult(1, "red")
EMPTY = CommandResult(8, "")
REFUSED = CommandResult(9, "")
SLOW = CommandResult(0, "slow", timed_out=True)
SLOW_EMPTY = CommandResult(8, "slow", timed_out=True)


class Branch(MemoryWorkspace):
    """A workspace whose changed paths depend on the base, as git's do: `since` maps a base to
    the paths changed from it (or the `GitError` reading them raises), and any other base gets
    `diff_paths`, the task base's."""

    def __init__(self) -> None:
        super().__init__()
        self.since: dict[str, tuple[str, ...] | GitError] = {}

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        found = self.since.get(base)
        if found is None:
            return super().changed_paths(base, head)
        self.diff_reads.append((base, head))
        if isinstance(found, GitError):
            raise found
        return found


def build(
    commands: MemoryCommands,
    *,
    plan: AffectedPlan | None = PLAN,
    advance: bool = True,
    ancestors: set[str] | None = None,
    since: dict[str, tuple[str, ...] | GitError] | None = None,
    task_paths: tuple[str, ...] = ("src/a.py",),
    witness: str | None = None,
    store: MemoryCheckResults | None = None,
) -> tuple[CheckoutPresubmit, Branch]:
    workspace = Branch()
    workspace.diff_paths = task_paths
    workspace.since = {"head1": ("README.md",)} if since is None else dict(since)
    workspace.ancestors = {"head1"} if ancestors is None else set(ancestors)
    workspace.moved.add(PATH)  # each look is a new head: head1, head2, ...
    workspace.trees[PATH] = "tree:one"
    workspace.record_setup_tree(PATH)
    ports = memory_ports(commands=commands, workspace=workspace, check_results=store)
    reuse = Reuse("agentic", ("uv sync",), "0123456789abcdef0123456789abcdef01234567")
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        CHECKS,
        30.0,
        priority=CheckPriority.SUBTASK,
        affected=plan,
        witness=witness,
        reuse=reuse if store is not None else None,
        advance_base=advance,
    )
    return gate, workspace


def forms(gate: CheckoutPresubmit) -> dict[str, GateForm]:
    return {form["check"]: form for form in gate.last_forms}


def rows_of_tests(gate: CheckoutPresubmit) -> list[str]:
    return [
        "kept" if "reused_from" in row else str(row["mode"])
        for row in gate.results or []
        if row["name"] == "tests"
    ]


@dataclass
class Case:
    queues: dict[str, list[CommandResult]]
    greens: dict[str, str]
    ran: list[str]
    form: tuple[str, str]
    rows: list[str]
    ancestors: set[str] = field(default_factory=lambda: {"head1"})
    since: dict[str, tuple[str, ...] | GitError] = field(
        default_factory=lambda: {"head1": ("README.md",)}
    )
    plan: AffectedPlan = PLAN
    task_paths: tuple[str, ...] = ("src/a.py",)
    witness: str | None = None
    first_refused: bool = True


CASES = {
    # a lint refusal, then a delivery touching no test file: the empty selection keeps the verdict
    "kept": Case(
        {LINT: [RED, RED], AT_G: [EMPTY]},
        greens={"tests": "head1"},
        ran=[AT_G, LINT],
        form=("affected-only", "kept_green"),
        rows=["kept"],
    ),
    # a witness that disagrees on call 1 leaves G where it was: the task base measures again
    "witness disagreement does not move G": Case(
        {WITNESS: [CommandResult(0, "/elsewhere"), CommandResult(0, str(PATH))]},
        witness=WITNESS,
        greens={},
        ran=[WITNESS, AT_TASK_BASE, LINT],
        form=("affected-only", "none"),
        rows=["affected-only"],
    ),
    # a timed-out affected form is followed by full and neither is a source
    "affected timeout is not a source": Case(
        {AT_TASK_BASE: [SLOW], "pytest": [SLOW]},
        greens={"lint": "head1"},
        ran=["pytest", LINT],
        form=("full", "stuck_full"),
        rows=["full"],
        first_refused=False,
    ),
    # an exit 8 that timed out is no empty selection: full follows
    "a timed-out exit 8 keeps nothing": Case(
        {LINT: [RED, RED], AT_G: [SLOW_EMPTY]},
        greens={"tests": "head1"},
        ran=[AT_G, "pytest", LINT],
        form=("full", "affected_red"),
        rows=["affected-only", "full"],
    ),
    "G is not an ancestor: the task base": Case(
        {LINT: [RED, RED]},
        ancestors=set(),
        greens={"tests": "head1"},
        ran=[AT_TASK_BASE, LINT],
        form=("affected-only", "none"),
        rows=["affected-only"],
    ),
    "G unreadable: the task base": Case(
        {LINT: [RED, RED]},
        since={"head1": GitError("git diff failed")},
        greens={"tests": "head1"},
        ran=[AT_TASK_BASE, LINT],
        form=("affected-only", "none"),
        rows=["affected-only"],
    ),
    "nothing changed since G: the task base": Case(
        {LINT: [RED, RED]},
        since={"head1": ()},
        greens={"tests": "head1"},
        ran=[AT_TASK_BASE, LINT],
        form=("affected-only", "none"),
        rows=["affected-only"],
    ),
    # a fallback-glob file touched before G no longer sends the check full
    "CONTEXT.md edited before G": Case(
        {LINT: [RED, RED]},
        plan=CONTEXT_PLAN,
        task_paths=("src/a.py", "agentic/core/process/CONTEXT.md"),
        greens={"tests": "head1"},
        ran=[AT_G, LINT],
        form=("affected-only", "none"),
        rows=["affected-only"],
    ),
    # exit 9: an unmapped path is a refusal of the selector, never a kept verdict
    "exit 9 is followed by full": Case(
        {LINT: [RED, RED], AT_G: [REFUSED]},
        greens={"tests": "head1"},
        ran=[AT_G, "pytest", LINT],
        form=("full", "script_refused"),
        rows=["affected-only", "full"],
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_two_gate_calls_move_the_base_to_the_last_green_head(case: Case) -> None:
    commands = MemoryCommands(**case.queues)
    gate, _ = build(
        commands,
        plan=case.plan,
        ancestors=case.ancestors,
        since=case.since,
        task_paths=case.task_paths,
        witness=case.witness,
    )
    # Call 1 is refused for the lint or the witness; a timeout alone passes unverified (GH-1075).
    assert (gate() is not None) is case.first_refused
    assert gate.green_heads == case.greens
    first = len(commands.ran)

    assert gate() is None
    assert [command for _, command in commands.ran[first:]] == case.ran
    assert (forms(gate)["tests"]["mode"], forms(gate)["tests"]["cause"]) == case.form
    assert rows_of_tests(gate) == case.rows


def test_g_equal_to_the_task_base_is_no_advance_and_an_empty_selection_runs_full() -> None:
    same = AffectedPlan(base="head1", commands=PLAN.commands, fallback=PLAN.fallback)
    commands = MemoryCommands(**{LINT: [RED, RED], AT_G: [CommandResult(0, "ok"), EMPTY]})
    gate, _ = build(commands, plan=same, ancestors={"head1"}, since={"head1": ("README.md",)})
    assert gate() is not None
    first = len(commands.ran)
    assert gate() is None
    assert [c for _, c in commands.ran[first:]] == [AT_G, "pytest", LINT]
    assert all("affected_base" not in row for row in gate.results or [])
    assert (forms(gate)["tests"]["mode"], forms(gate)["tests"]["cause"]) == (
        "full",
        "script_empty",
    )
    assert rows_of_tests(gate) == ["affected-only", "full"]


def test_the_row_against_g_names_g_and_the_row_against_the_task_base_does_not() -> None:
    commands = MemoryCommands(**{LINT: [RED, RED]})
    gate, _ = build(commands)
    assert gate() is not None
    assert [r.get("affected_base") for r in gate.results or [] if r["name"] == "tests"] == [None]
    assert gate() is None
    assert [r.get("affected_base") for r in gate.results or [] if r["name"] == "tests"] == ["head1"]


def test_the_kept_row_is_a_passed_row_that_ran_nothing_and_needs_no_rerun() -> None:
    commands = MemoryCommands(**{LINT: [RED, RED], AT_G: [EMPTY]})
    gate, _ = build(commands)
    assert gate() is not None
    assert gate() is None
    (row,) = [r for r in gate.results or [] if r["name"] == "tests"]
    assert row == {
        "name": "tests",
        "command": AT_G,
        "passed": True,
        "output": "",
        "duration_s": 0.0,
        "mode": "affected-only",
        "reused_from": "head1",
    }
    # Held, never moved: the kept check does not take the head as its G (and the lint that
    # failed in full is stuck, so it is no source either).
    assert gate.green_heads == {"tests": "head1"}
    assert gate.stuck_full == {"lint"}


def test_the_g_of_each_distinct_base_is_read_once_per_gate_call() -> None:
    commands = MemoryCommands(**{LINT: [RED, RED], AT_G: [EMPTY]})
    gate, workspace = build(commands)
    assert gate() is not None
    workspace.ancestor_checks.clear()
    workspace.diff_reads.clear()
    assert gate() is None
    # Only `tests` has a G and an affected form: one ancestry look and one diff of G..head;
    # the task base's paths are read once, the one source of `lint`'s plan too.
    assert workspace.ancestor_checks == [(PATH, "head1", "head2")]
    assert sorted(workspace.diff_reads) == [("abc", "head2"), ("head1", "head2")]


def test_a_kept_row_is_never_stored() -> None:
    store = MemoryCheckResults()
    commands = MemoryCommands(**{LINT: [RED, RED], AT_G: [EMPTY]})
    gate, _ = build(commands, store=store)
    assert gate() is not None
    assert gate() is None
    assert rows_of_tests(gate) == ["kept"]
    stored = [row["command"] for _, row, _ in store.puts]
    assert stored == [AT_TASK_BASE, LINT]
    assert all("reused_from" not in row for _, row, _ in store.puts)


def test_without_advance_base_the_gate_is_the_one_of_before() -> None:
    commands = MemoryCommands(**{LINT: [RED] * 4, AT_TASK_BASE: [EMPTY, EMPTY]})
    gate, workspace = build(commands, advance=False, ancestors={"head1"})
    assert gate() is not None
    assert gate() is not None
    # Never a kept row, never G: exit 8 is followed by the full form against the task base.
    assert [command for _, command in commands.ran] == [
        AT_TASK_BASE,
        "pytest",
        LINT,
        LINT,
        AT_TASK_BASE,
        "pytest",
        LINT,
        LINT,
    ]
    assert gate.green_heads == {}
    assert workspace.ancestor_checks == []
    assert workspace.diff_reads == [("abc", "head1"), ("abc", "head2")]
    assert all(set(row) & {"reused_from", "affected_base"} == set() for row in gate.results or [])
    assert [(f["mode"], f["cause"]) for f in gate.last_forms] == [
        ("full", "script_empty"),
        ("full", "no_affected_form"),
    ]
    markdown = gate.markdown()
    assert markdown is not None
    # The literal of today's gate, its seconds column normalised (the fakes run in no time).
    assert re.sub(r"\| [\d.]+ \|$", "| 0.0 |", markdown, flags=re.MULTILINE) == (
        "### Checks\n\n"
        "Run by the system on `head2` when the Developer delivered.\n\n"
        "| Check | Command | Mode | Result | Seconds |\n"
        "|---|---|---|---|---|\n"
        "| `tests` | `pytest --since abc` | affected-only | **failed** | 0.0 |\n"
        "| `tests` | `pytest` | full | passed | 0.0 |\n"
        "| `lint` | `ruff check .` | full | **failed** | 0.0 |\n"
        "| `lint` | `ruff check .` | full | **failed** (rerun) | 0.0 |\n\n"
        "`affected-only` rows ran the tests selected against `abc` (the whole branch, "
        "`task_base..head`, not the increment under review); the full suite runs at merge "
        "(ADR-0045).\n\n"
        "`tests`, last lines:\n\n```text\n\n```\n\n"
        "`lint`, last lines:\n\n```text\nred\n```\n\n"
        "`lint`, last lines:\n\n```text\nred\n```"
    )


def test_run_check_never_reads_or_writes_the_green_heads() -> None:
    commands = MemoryCommands()
    store = MemoryCheckResults()
    gate, _ = build(commands, store=store)
    gate.green_heads = {"tests": "head0"}
    handler = RunCheck(gate, clock=lambda: 1_000_000.0)
    run = handler.run("tests")
    assert run.passed
    assert commands.ran == [(PATH, "pytest")]  # today's form: the declared command, full
    assert gate.green_heads == {"tests": "head0"}
