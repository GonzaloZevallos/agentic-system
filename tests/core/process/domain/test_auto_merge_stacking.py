"""GH-859: `rules.auto_merge_decision` for a stacked child. Pure: each case is a `MergeFacts` and
the decision it must give; a task without `stack` must decide exactly as it did before."""

import ast
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from agentic.core.process.domain import rules, stacking
from agentic.core.process.domain.ci import CiCheck, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.stacking import StackFacts
from agentic.core.process.domain.task import StackParent

HEAD = "a" * 40
MASTER = "b" * 40
RECORD = StackParent(issue=7, task_id="GH-7", head="c" * 40, started_at="2026-11-16T10:00:00Z")
RED = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
State = Literal["open", "closed", "unknown"]


def _stack(
    state: State = "open", in_upstream: bool | None = None, has: bool | None = None
) -> StackFacts:
    return StackFacts(
        record=RECORD, parent_state=state, parent_in_upstream=in_upstream, child_has_parent=has
    )


UNMERGED = _stack("open", False, True)
LANDED = _stack("closed", True, True)


def _facts(**over) -> MergeFacts:
    """A pull request that merges: checks already green on this head and this master."""
    base = MergeFacts(
        open_escalation=False,
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/core/x.py",),
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )
    return replace(base, **over)


def _green(**over) -> dict:
    return {
        "ci_required": ("web",),
        "ci_untrusted": False,
        "ci_head": HEAD,
        "ci_verdict": CiVerdict("green", (), ()),
        "ci_fresh": True,
        "ci_waited_s": 10.0,
        **over,
    }


MERGE = MergeDecision("merge", None)

# Fact sets that today reach each outcome the stacking check must come before.
OUTCOMES = [
    pytest.param({}, MERGE, id="merge: local checks cover"),
    pytest.param(_green(), MERGE, id="merge: CI covered"),
    pytest.param(
        {"checks_passed": None, "checks_head": None, "checks_master": None, "attempts": 0},
        MergeDecision("run_checks", None),
        id="run_checks",
    ),
    pytest.param(
        _green(ci_fresh=False, ci_covered=False, ci_plan_only=()),
        MergeDecision("ci_run", None),
        id="ci_run",
    ),
    pytest.param(
        _green(ci_verdict=CiVerdict("pending", (), ("web",)), ci_fresh=None),
        MergeDecision("wait", None),
        id="CI wait",
    ),
]


@pytest.mark.parametrize(("over", "today"), OUTCOMES)
def test_an_unmerged_parent_refuses_whatever_would_be_decided_next(over, today):
    assert rules.auto_merge_decision(_facts(**over)) == today
    got = rules.auto_merge_decision(_facts(**over, stack=UNMERGED))
    assert got == MergeDecision("refuse", "stack_parent_unmerged")


@pytest.mark.parametrize(("over", "today"), OUTCOMES)
def test_a_landed_parent_leaves_the_decision_as_it_was(over, today):
    assert rules.auto_merge_decision(_facts(**over, stack=LANDED)) == today


def test_a_task_without_stack_triggers_no_stacking_read(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("stacking was read")

    monkeypatch.setattr(stacking, "refusal", boom)
    assert rules.auto_merge_decision(_facts()) == MERGE


# --- refusals --------------------------------------------------------------------------------


def test_a_child_lacking_the_parent_head_is_due_a_restack():
    got = rules.auto_merge_decision(_facts(stack=_stack("closed", True, False)))
    assert got == MergeDecision("refuse", "stack_restack_due")


def test_a_restack_that_conflicts_is_a_merge_conflict():
    facts = _facts(stack=_stack("closed", True, False), conflicts=True)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "merge_conflict")


@pytest.mark.parametrize(
    "stack",
    [
        pytest.param(_stack("closed", False, False), id="rebuilt parent"),
        pytest.param(_stack("closed", False, None), id="parent closed NOT_PLANNED"),
        pytest.param(_stack("closed", False, True), id="squashed parent"),
        pytest.param(StackFacts(None, "open", True, True), id="unreadable record"),
    ],
)
def test_a_parent_that_closed_without_its_head_orphans_the_child(stack):
    got = rules.auto_merge_decision(_facts(stack=stack))
    assert got == MergeDecision("refuse", "stack_base_orphaned")


def test_a_parent_that_got_a_landing_fix_and_then_merged_gives_no_stack_refusal():
    assert rules.auto_merge_decision(_facts(stack=_stack("closed", True, True))) == MERGE


def test_a_clean_restack_followed_by_a_red_ci_is_ci_failed():
    red = CiVerdict("red", (RED,), ())
    facts = _facts(**_green(ci_verdict=red), stack=_stack("closed", True, True), conflicts=False)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "ci_failed")


@pytest.mark.parametrize("state", ["open", "closed", "unknown"])
def test_an_unknown_ancestry_refuses(state):
    got = rules.auto_merge_decision(_facts(stack=_stack(state, None, None)))
    assert got.kind == "refuse"
    assert got.reason == "stack_parent_unmerged"


def test_the_stacking_check_comes_after_the_eval_set_and_before_the_conflict():
    unmerged = _facts(stack=UNMERGED, in_eval_set=True)
    assert rules.auto_merge_decision(unmerged) == MergeDecision("refuse", "eval_set")
    conflicting = _facts(stack=UNMERGED, conflicts=True)
    assert rules.auto_merge_decision(conflicting) == MergeDecision(
        "refuse", "stack_parent_unmerged"
    )


# --- probes ----------------------------------------------------------------------------------


def test_an_unmerged_parent_wants_the_criteria_probe_and_not_the_conflict_one():
    facts = _facts(stack=UNMERGED)
    assert rules.conflict_probe_wanted(facts) is False
    assert rules.criteria_probe_wanted(facts) is True


def test_the_stack_probe_is_wanted_only_when_the_decision_reaches_the_stacking_check():
    assert rules.stack_probe_wanted(_facts()) is True
    assert rules.stack_probe_wanted(_facts(status="developing")) is False
    assert rules.stack_probe_wanted(_facts(status="developing", stack=UNMERGED)) is False
    assert rules.stack_probe_wanted(_facts(in_eval_set=True)) is False


# --- every production builder passes `stack=` ------------------------------------------------


def _callee(func: ast.expr) -> str | None:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _merge_facts_calls_off_contract(root: Path) -> list[str]:
    """Every `MergeFacts(...)` call without `stack=facts_of(...)`, and every import that aliases
    the class (a call under another name would escape the scan)."""
    off: list[str] = []
    for path in sorted(root.rglob("*.py")):
        where = path.relative_to(root.parent)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "MergeFacts" and alias.asname for alias in node.names
            ):
                off.append(f"{where}:{node.lineno}")
            if not isinstance(node, ast.Call) or _callee(node.func) != "MergeFacts":
                continue
            stack = {kw.arg: kw.value for kw in node.keywords}.get("stack")
            if not (isinstance(stack, ast.Call) and _callee(stack.func) == "facts_of"):
                off.append(f"{where}:{node.lineno}")
    return off


def test_every_production_merge_facts_call_passes_stack_from_facts_of():
    assert _merge_facts_calls_off_contract(Path(rules.__file__).parents[3]) == []


def test_the_scan_rejects_a_call_without_stack_or_with_anything_but_facts_of(tmp_path):
    """The scan is only worth its name if a builder that forgets `stack=`, passes the literal
    `None` or aliases the class fails it."""
    package = tmp_path / "agentic"
    package.mkdir()
    (package / "builder.py").write_text("facts = MergeFacts(task_id='x')\n")
    (package / "none.py").write_text("facts = rules.MergeFacts(task_id='x', stack=None)\n")
    (package / "alias.py").write_text("from x import MergeFacts as MF\n")
    (package / "ok.py").write_text(
        "facts = rules.MergeFacts(task_id='x', stack=stacking.facts_of(raw))\n"
        "more = MergeFacts(stack=facts_of(raw))\n"
    )
    assert _merge_facts_calls_off_contract(package) == [
        "agentic/alias.py:1",
        "agentic/builder.py:1",
        "agentic/none.py:1",
    ]
