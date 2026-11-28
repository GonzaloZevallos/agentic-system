"""`CheckoutPresubmit` over a check that timed out with no failing test (GH-1075), over port
fakes: no refusal and no refusal spent, nothing stored, the fact only for the head the call
checked, the unverified line in `### Checks`, and `workers` on the rows that ran."""

from pathlib import Path

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.domain import presubmit as rules
from agentic.core.process.domain.check_schedule import CheckPriority
from tests.fakes import (
    FakeCheckScheduler,
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
TIMED_OUT = CommandResult(-9, "partial", timed_out=True)
RED = CommandResult(1, "1 failed")


def gate(commands: MemoryCommands, store: MemoryCheckResults | None = None):
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.trees[PATH] = "tree:one"
    workspace.record_setup_tree(PATH)
    ports = memory_ports(
        commands=commands,
        workspace=workspace,
        check_scheduler=FakeCheckScheduler(share=6),
        check_results=store,
    )
    reuse = Reuse("p", ("uv sync",), "a" * 40) if store is not None else None
    g = CheckoutPresubmit(
        ports, PATH, "base", CHECKS, 30.0, priority=CheckPriority.SUBTASK, reuse=reuse
    )
    return g, workspace


def test_a_timeout_spends_no_refusal_and_stores_nothing():
    store = MemoryCheckResults()
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT]), store)
    assert g() is None
    assert g.refusals == 0 and g._refused_ids == []
    assert [row["name"] for _, row, _ in store.puts] == ["lint"]  # never the timed-out one
    assert [fact["check"] for fact in g.timeouts()] == ["tests"]
    assert g.timeouts()[0]["workers"] == 6 and g.timeouts()[0]["head"] == g.head


def test_a_timeout_is_never_shown_as_a_pass_and_its_line_is_under_checks():
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT]))
    g()
    text = g.markdown() or ""
    line = rules.timeout_line(g.timeouts()[0])
    assert line in text and "timed out after" in line and "unverified" in line
    assert "| `pytest` | passed |" not in text


def test_a_timeout_followed_by_a_real_red_refuses_once_and_spends_one_refusal():
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT, RED, RED]))
    assert g() is None  # the timeout
    assert g() is not None  # the same commit's next look is a red
    assert g.refusals == 1 and len(g._refused_ids) == 1
    assert g.timeouts() == []


def test_a_real_red_beside_a_timeout_refuses_with_no_line_and_no_fact():
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT], **{"ruff check .": [RED, RED]}))
    assert g() is not None
    assert g.timeouts() == []
    assert "timed out after" not in (g.markdown() or "").replace("(timed out after", "")


def test_a_timeout_after_the_refusals_are_spent_still_returns_none_with_its_line():
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT]))
    g.refusals = rules.REFUSALS
    assert g() is None
    assert [fact["check"] for fact in g.timeouts()] == ["tests"]
    assert "unverified" in (g.markdown() or "")


def test_a_timeout_then_a_green_call_on_the_next_head_leaves_no_fact():
    g, _ = gate(MemoryCommands(pytest=[TIMED_OUT, CommandResult(0, "ok")]))
    assert g() is None and g.timeouts()
    assert g() is None
    assert g.timeouts() == []
    assert "unverified" not in (g.markdown() or "")


def test_a_timeout_then_an_unclean_call_with_refusals_spent_leaves_no_fact_and_no_line():
    g, workspace = gate(MemoryCommands(pytest=[TIMED_OUT]))
    assert g() is None and g.timeouts()
    g.refusals = rules.REFUSALS
    workspace.uncommitted.add(PATH)
    g()
    assert g.timeouts() == []
    assert "unverified" not in (g.markdown() or "")


def test_workers_is_set_on_the_rows_that_ran_and_absent_on_a_reused_one():
    store = MemoryCheckResults()
    g, workspace = gate(MemoryCommands(), store)
    assert g() is None
    assert g.results is not None and all(row["workers"] == 6 for row in g.results)
    store.puts.clear()
    again, _ = gate(MemoryCommands(), store)
    assert again() is None
    reused = [row for row in again.results or [] if row.get("reused")]
    assert reused and all("workers" not in row for row in reused)
