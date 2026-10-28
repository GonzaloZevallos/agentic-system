"""GH-406, subtask landing-readers, the pure side: a restored task never restarts, a diagnosis or a
sync from before the restoration no longer blocks a step, the rewriters keep the position the cut
points at, and no reader of the `landing` list bypasses `rules.live_landing`. Every refusal comes
from `rules.auto_merge_decision` over the fixture's facts."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from tests.core.process.application.test_landing_restored import HEAD, NEW_MASTER, document, facts

ROOT = Path(__file__).resolve().parents[4] / "agentic" / "core" / "process"
FIX = {"head": HEAD, "step": "fix", "refusal": "checks_failed", "at": "2026-10-19T00:00:00+00:00"}
SYNC = {"head": HEAD, "step": "sync_master", "upstream": NEW_MASTER, "refusal": "merge_conflict"}


def _refusal(state: dict, **over) -> rules.MergeRefusal:
    decision = rules.auto_merge_decision(facts(state, **over))
    assert decision.reason is not None
    return decision.reason


def _spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


# --- a restored task never restarts -------------------------------------------------------------


@pytest.mark.parametrize("verdict", [None, "unlandable", "sync"])
def test_a_restored_task_never_restarts_after_a_diagnosis(verdict):
    assert landing.after_diagnosis(verdict, _spent(), 0) in ("sync_master", "restart")
    assert landing.after_diagnosis(verdict, _spent(), 0, restored=True) in (
        "sync_master",
        "escalate",
    )
    assert landing.after_diagnosis(None, _spent(), 0, restored=True) == "escalate"


def test_a_restored_sync_verdict_still_syncs_while_the_budget_lasts():
    due = _spent(verdict_sync_due=True, diagnoses=1, last_verdict="sync")
    assert landing.after_diagnosis("sync", due, 0, restored=True) == "sync_master"


def test_spent_passes_carry_over_a_restoration_and_end_in_a_diagnose_then_an_escalation():
    fixes = [{**FIX, "step": "fix"}] * landing.LANDING_PASSES_MAX
    state = document(landing_entries=fixes, cut=len(fixes), plan_settled=[])
    state["findings"] = []
    refusal = _refusal(state, merge_failures=rules.MERGE_CALLS_MAX, conflicts=False)
    assert refusal == "merge_failed"
    spent = landing_plan.spent(state, HEAD, None, NEW_MASTER)
    assert spent.passes == landing.LANDING_PASSES_MAX
    assert spent.diagnoses == 0
    step = landing_plan.step_for(state, refusal, facts(state, merge_failures=rules.MERGE_CALLS_MAX))
    assert step == LandingStep("diagnose", "merge_failed")
    assert landing.after_diagnosis(None, spent, 0, restored=rules.restored(state)) == "escalate"


# --- the idempotency key reads the live entries -------------------------------------------------


def test_a_pre_cut_fix_or_diagnose_at_the_same_head_no_longer_blocks_its_step():
    state = document(landing_entries=[FIX, document()["landing"][0], SYNC], cut=3)
    for step in ("fix", "diagnose", "sync_master"):
        assert landing.already_dispatched(state["landing"], HEAD, step, NEW_MASTER)
        assert not landing.already_dispatched(landing.live_entries(state), HEAD, step, NEW_MASTER)


def test_the_pending_step_of_a_restored_task_is_not_held_back_by_the_archived_sync():
    state = document(landing_entries=[SYNC], cut=1, plan_settled=[])
    state["findings"] = []
    f = facts(state)
    assert _refusal(state) == "merge_conflict"
    pending = landing_plan.pending(state, "merge_conflict", f, landing_plan.LIVE_STEPS)
    assert pending == LandingStep("sync_master", "merge_conflict")
    unrestored = {k: v for k, v in state.items() if k != "restorations"}
    assert landing_plan.pending(unrestored, "merge_conflict", f, landing_plan.LIVE_STEPS) is None


def test_the_diagnose_readers_ignore_the_archived_attempts():
    undelivered = {"head": HEAD, "step": "diagnose", "at": "2026-10-20T00:00:00+00:00"}
    state = document(landing_entries=[undelivered], cut=1)
    assert landing.diagnose_attempts(state) == []
    assert landing.diagnose_at(state) is None
    assert landing.refused_slices(landing.live_entries(state)) == {}
    plain = {k: v for k, v in state.items() if k != "restorations"}
    assert len(landing.diagnose_attempts(plain)) == 1
    assert landing.diagnose_at(plain) == undelivered["at"]


# --- the rewriters keep the position the cut points at ------------------------------------------


def _shape(entries) -> list[tuple[object, object]]:
    return [(e.get("head"), e.get("step")) for e in entries]


def _mixed() -> dict:
    entries = [
        {"head": "1" * 40, "step": "fix", "resolved_at": "t"},
        {"head": "2" * 40, "step": "diagnose", "delivered": True, "verdict": "unlandable"},
        {"head": "3" * 40, "step": "sync_master", "upstream": NEW_MASTER, "cost_base": 1.0},
        {"head": "4" * 40, "step": "diagnose", "delivered": True},
    ]
    state = document(landing_entries=entries, cut=2, escalated="failure")
    state["pause_log"] = []
    return state


def test_every_rewriter_keeps_the_order_and_the_length_of_the_entries():
    state = _mixed()
    before = _shape(state["landing"])
    rewritten = {
        "released": landing.released(state, "keep going", "2026-10-28T00:00:00Z")["landing"],
        "migration": landing.migration(state)["landing"],
        "resolve_open": landing.resolve_open(state["landing"], "t", 3.0),
        "close_unsynced": landing.close_unsynced(state["landing"], "t", 3.0, NEW_MASTER),
        "with_cost_base": landing.with_cost_base(state["landing"], "4" * 40, "diagnose", 2.0),
    }
    for name, written in rewritten.items():
        assert _shape(written) == before, name


def test_the_rewriters_only_touch_the_entry_they_close_or_resolve():
    state = _mixed()
    closed = landing.resolve_open(state["landing"], "t", 3.0)
    assert closed[:3] == [landing.entry_of(e) for e in state["landing"][:3]]
    assert closed[3].get("resolved_at") == "t"
    stamped = landing.released(state, "again", "t")["landing"]
    assert [e.get("released_at") for e in stamped] == [None, None, None, "t"]


# --- no reader of `landing` bypasses the cut ----------------------------------------------------

READERS = {
    "domain/landing.py": {"released", "migration", "resume_owed"},
    "application/landing_plan.py": {"all_entries"},
    "domain/rules.py": {"live_landing", "important_pass_spent"},
}
"""Per file, the functions allowed to read `"landing"` off a document: the cut itself, the budget
counter that reads the whole list, the rewriters (which write by index) and `resume_owed`, which
reads the `_landing_view` it built from `rules.live_landing`."""


def landing_reads(source: str, allowed: set[str]) -> list[str]:
    """The functions of `source` outside `allowed` that read `"landing"` through `.get("landing")`
    or `["landing"]`; a store (`state["landing"] = ...`) is a write and does not count."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name in allowed:
            continue
        for inner in ast.walk(node):
            via_get = (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "get"
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and inner.args[0].value == "landing"
            )
            via_subscript = (
                isinstance(inner, ast.Subscript)
                and isinstance(inner.ctx, ast.Load)
                and isinstance(inner.slice, ast.Constant)
                and inner.slice.value == "landing"
            )
            if via_get or via_subscript:
                found.append(node.name)
    return found


@pytest.mark.parametrize(("path", "allowed"), READERS.items())
def test_no_landing_reader_bypasses_the_live_cut(path, allowed):
    assert landing_reads((ROOT / path).read_text(), allowed) == []


def test_the_guard_rejects_a_reader_that_takes_the_whole_list():
    bad = (
        "def spent(raw):\n    return raw.get('landing')\n"
        "def other(raw):\n    return raw['landing'][0]\n"
        "def writer(raw):\n    raw['landing'] = []\n"
        "def fine(raw):\n    return raw.get('landing')\n"
    )
    assert landing_reads(bad, {"fine"}) == ["spent", "other"]
