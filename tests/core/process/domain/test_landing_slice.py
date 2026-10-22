"""Landed slices and the closed restart reason (GH-297): `slice_or_restart` is the one place
that decides to land a slice or to restart; `slice_facts` the one builder of a slice's
`MergeFacts`. Pure: facts in, a decision out."""

from dataclasses import replace
from typing import Any, get_args

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.adr_numbers import Ambiguous
from agentic.core.process.domain.landing import (
    LandingSpent,
    LandingStep,
    SliceInputs,
    SliceUnit,
)

HEAD = "a" * 40
MASTER = "b" * 40
SLICE_HEAD = "c" * 40


def _inputs(units=(("a", ()), ("b", ()), ("c", ())), **over) -> SliceInputs:
    names = [name for name, _ in units]
    base = SliceInputs(
        approved=tuple(SliceUnit(name, tuple(deps)) for name, deps in units),
        carries=dict.fromkeys(names, True),
        integration_paths=frozenset({"integration.py"}),
        unit_paths={name: frozenset({f"{name}.py"}) for name in names},
        landed=frozenset(),
        refused={},
        blocking_units=frozenset(),
        readable=True,
    )
    return replace(base, **over)


# --- run_kind_of ---------------------------------------------------------------------------


def test_a_land_slice_step_registers_under_the_checks_kind():
    assert landing.run_kind_of("land_slice", None) == "land_checks"
    assert landing.run_kind_admits("land_checks", "land_slice", None)


def test_the_step_kind_set_names_land_slice():
    assert "land_slice" in get_args(landing.LandingStepKind)


# --- slice_or_restart ----------------------------------------------------------------------


def test_the_largest_strict_prefix_of_clean_units_is_the_slice():
    assert landing.slice_or_restart(_inputs()) == landing.Slice(("a", "b"))


def test_a_unit_that_does_not_carry_ends_the_prefix():
    result = landing.slice_or_restart(_inputs(carries={"a": True, "b": False, "c": True}))
    assert result == landing.Slice(("a",))


def test_a_unit_missing_from_carries_is_not_clean():
    result = landing.slice_or_restart(_inputs(carries={"a": True, "b": True}))
    assert result == landing.Slice(("a", "b"))
    result = landing.slice_or_restart(_inputs(carries={"b": True, "c": True}))
    assert result == "no_clean_slice"


def test_a_unit_that_touches_the_integration_paths_is_excluded():
    unit_paths = {"a": frozenset({"a.py"}), "b": frozenset({"integration.py"}), "c": frozenset()}
    assert landing.slice_or_restart(_inputs(unit_paths=unit_paths)) == landing.Slice(("a",))


def test_a_unit_with_an_open_blocking_row_is_excluded():
    result = landing.slice_or_restart(_inputs(blocking_units=frozenset({"a"})))
    assert result == "no_clean_slice"


def test_a_prefix_closed_under_depends_on_follows_the_topological_order():
    # `b` is first in the plan but needs `c`: the topological order is a, c, b.
    units = (("a", ()), ("b", ("c",)), ("c", ()))
    assert landing.slice_or_restart(_inputs(units)) == landing.Slice(("a", "c"))


def test_a_unit_depending_on_a_unit_that_is_not_approved_never_enters_a_slice():
    units = (("a", ()), ("b", ("x",)), ("c", ()))
    assert landing.slice_or_restart(_inputs(units)) == landing.Slice(("a", "c"))


def test_a_dependency_already_landed_is_satisfied():
    units = (("a", ()), ("b", ("a",)), ("c", ("b",)))
    result = landing.slice_or_restart(_inputs(units, landed=frozenset({"a"})))
    assert result == landing.Slice(("b",))


def test_a_unit_already_landed_is_not_pending():
    result = landing.slice_or_restart(_inputs(landed=frozenset({"a", "b"})))
    assert result == "single_unit"


def test_fewer_than_two_pending_units_is_single_unit():
    assert landing.slice_or_restart(_inputs((("a", ()),))) == "single_unit"
    assert landing.slice_or_restart(_inputs(())) == "single_unit"


def test_an_unreadable_fact_is_unreadable_before_anything_else():
    assert landing.slice_or_restart(_inputs(readable=False)) == "unreadable"
    assert landing.slice_or_restart(_inputs((("a", ()),), readable=False)) == "unreadable"


def test_integration_paths_that_could_not_be_built_are_no_clean_slice():
    assert landing.slice_or_restart(_inputs(integration_paths=None)) == "no_clean_slice"


def test_master_changing_the_same_files_as_every_unit_is_no_clean_slice():
    result = landing.slice_or_restart(_inputs(carries=dict.fromkeys("abc", False)))
    assert result == "no_clean_slice"


def test_a_slice_refused_twice_is_not_tried_again_but_a_smaller_one_is():
    result = landing.slice_or_restart(_inputs(refused={("a", "b"): 2}))
    assert result == landing.Slice(("a",))


def test_one_refusal_does_not_exclude_a_slice():
    result = landing.slice_or_restart(_inputs(refused={("a", "b"): 1}))
    assert result == landing.Slice(("a", "b"))


def test_refusals_are_counted_per_unit_set_whatever_the_order_of_the_key():
    result = landing.slice_or_restart(_inputs(refused={("b", "a"): 2}))
    assert result == landing.Slice(("a",))


def test_when_every_candidate_was_refused_the_reason_is_slice_refused():
    result = landing.slice_or_restart(_inputs(refused={("a", "b"): 2, ("a",): 3}))
    assert result == "slice_refused"


def test_no_candidate_at_all_stays_no_clean_slice_even_with_refusals_on_record():
    result = landing.slice_or_restart(
        _inputs(carries=dict.fromkeys("abc", False), refused={("a", "b"): 2})
    )
    assert result == "no_clean_slice"


def test_the_whole_pending_set_is_never_a_slice():
    result = landing.slice_or_restart(_inputs((("a", ()), ("b", ()))))
    assert result == landing.Slice(("a",))


def test_integration_is_blocked_when_a_pending_unit_touches_its_paths():
    touching = {"a": frozenset({"integration.py"}), "b": frozenset(), "c": frozenset()}
    assert landing.integration_blocked(_inputs(unit_paths=touching))
    assert not landing.integration_blocked(_inputs())
    assert not landing.integration_blocked(_inputs(integration_paths=None))


# --- review_carries ------------------------------------------------------------------------


def test_review_carries_without_landed_paths_is_what_it_always_was():
    assert landing.review_carries(False, {"x.py"}, {"y.py"})
    assert not landing.review_carries(False, {"x.py"}, {"x.py"})
    assert not landing.review_carries(True, set(), set())


def test_paths_a_landed_slice_brought_do_not_count_against_the_carry():
    assert landing.review_carries(False, {"x.py"}, {"x.py"}, landed_paths={"x.py"})
    assert not landing.review_carries(False, {"x.py", "z.py"}, {"x.py", "z.py"}, {"x.py"})
    assert not landing.review_carries(True, {"x.py"}, {"y.py"}, landed_paths={"x.py"})


# --- slice_landed --------------------------------------------------------------------------


def test_a_slice_has_landed_when_every_reviewed_head_is_an_ancestor_of_master():
    assert landing.slice_landed(("a", "b"), {"a": True, "b": True})
    assert not landing.slice_landed(("a", "b"), {"a": True, "b": False})


def test_ancestry_that_was_not_read_for_a_unit_is_not_landed():
    assert not landing.slice_landed(("a", "b"), {"a": True})
    assert not landing.slice_landed((), {})


# --- slice_facts ---------------------------------------------------------------------------


def _state(*rows: dict[str, Any], b_status: str = "approved") -> Any:
    return {
        "status": "ready_for_review",
        "subtasks": {
            "a": {"status": "approved", "reviewed_head": HEAD},
            "b": {"status": b_status, "reviewed_head": HEAD},
            "c": {"status": "approved", "reviewed_head": HEAD},
        },
        "findings": list(rows),
    }


def _slice_facts(state: Any = None, **over: Any) -> rules.MergeFacts:
    args: dict[str, Any] = {
        "task_id": "GH-9",
        "slice_head": SLICE_HEAD,
        "built_clean": True,
        "contains_reviewed": {"a": True, "b": True},
        "carries": {"a": True, "b": True},
        "pr_state": "OPEN",
        "pr_head": SLICE_HEAD,
        "in_eval_set": False,
        "touched_paths": ("a.py", "b.py"),
        "current_master": MASTER,
        "checks_head": SLICE_HEAD,
        "checks_master": MASTER,
        "checks_passed": True,
    }
    args.update(over)
    return landing.slice_facts(landing.Slice(("a", "b")), state or _state(), **args)


def test_a_clean_slice_is_merged_by_the_ordinary_decision():
    assert rules.auto_merge_decision(_slice_facts()) == rules.MergeDecision("merge", None)


def test_a_unit_that_does_not_carry_leaves_the_head_not_reviewed():
    facts = _slice_facts(carries={"a": True, "b": False})
    assert facts.reviewed_head is None
    assert rules.auto_merge_decision(facts) == rules.MergeDecision("refuse", "head_not_reviewed")


def test_a_unit_whose_reviewed_head_is_not_contained_leaves_the_head_not_reviewed():
    facts = _slice_facts(contains_reviewed={"a": True, "b": False})
    assert rules.auto_merge_decision(facts).reason == "head_not_reviewed"


def test_a_build_that_was_not_clean_leaves_the_head_not_reviewed():
    facts = _slice_facts(built_clean=False)
    assert rules.auto_merge_decision(facts).reason == "head_not_reviewed"


def test_a_unit_that_is_not_approved_is_not_approved():
    facts = _slice_facts(_state(b_status="returned"))
    assert not facts.reviewed_unit_approved
    assert rules.auto_merge_decision(facts).reason == "not_approved"


def test_the_pull_request_head_must_be_the_slice_head():
    facts = _slice_facts(pr_head="d" * 40)
    assert rules.auto_merge_decision(facts).reason == "head_not_reviewed"


def _row(subtask: str, severity: str, scope: str = "subtask") -> dict[str, Any]:
    return {
        "severity": severity,
        "scope": scope,
        "category": "x",
        "subtask": subtask,
        "location": f"{subtask}.py:1",
        "claim": f"{subtask} {severity}",
        "status": "open",
        "version": 1,
    }


def test_only_the_rows_owned_by_the_slice_units_are_counted():
    state = _state(
        _row("a", "blocking"),
        _row("c", "blocking"),
        _row("b", "important"),
        _row("c", "important"),
    )
    facts = _slice_facts(state)
    assert (facts.open_blocking, facts.open_important, facts.open_outside) == (1, 1, 0)
    assert rules.auto_merge_decision(facts).reason == "open_findings_blocking"


def test_a_row_of_a_unit_outside_the_slice_never_refuses_it():
    state = _state(_row("c", "blocking"), _row("plan", "important", "plan"))
    assert rules.auto_merge_decision(_slice_facts(state)) == rules.MergeDecision("merge", None)


def test_the_ci_untrusted_fact_is_computed_over_the_slice_paths():
    assert _slice_facts().ci_untrusted is False
    assert _slice_facts(touched_paths=("a.py", ".github/ci.yml")).ci_untrusted is True
    assert _slice_facts(touched_paths=None).ci_untrusted is None


def test_a_green_ci_does_not_merge_a_slice_that_touches_the_ci_definition():
    facts = _slice_facts(
        touched_paths=(".github/workflows/ci.yml",),
        ci_required=("tests",),
        checks_passed=None,
    )
    assert rules.auto_merge_decision(facts).kind != "merge"


def test_the_evaluation_set_is_the_tasks_own():
    facts = _slice_facts(in_eval_set=True)
    assert rules.auto_merge_decision(facts).reason == "eval_set"


# --- landing_step --------------------------------------------------------------------------


def _facts(**over: Any) -> rules.MergeFacts:
    return replace(_slice_facts(), reviewed_head=HEAD, pr_head=HEAD, **over)


def _spent(**over: Any) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnosed=False, waited_s=None)
    return replace(base, **over)


SLICE = landing.Slice(("a", "b"))

EXHAUSTED: list[tuple[Any, dict[str, Any]]] = [
    ("not_approved", {"passes": landing.LANDING_PASSES_MAX}),
    ("open_findings_blocking", {"passes": landing.LANDING_PASSES_MAX}),
    ("attempts_exhausted", {"syncs": landing.LANDING_SYNCS_MAX}),
    ("merge_conflict", {"passes": landing.LANDING_PASSES_MAX}),
    ("merge_failed", {}),
    ("ci_missing", {"waited_s": 10**9}),
    ("ci_unreadable", {"waited_s": 10**9}),
]


@pytest.mark.parametrize(("refusal", "spent"), EXHAUSTED)
def test_every_exhaustion_site_lands_the_slice_when_one_was_found(refusal, spent):
    step = landing.landing_step(refusal, _facts(), _spent(**spent), (), slicing=SLICE)
    assert step == LandingStep("land_slice", refusal, ("a", "b"))


@pytest.mark.parametrize(("refusal", "spent"), EXHAUSTED)
@pytest.mark.parametrize("why", get_args(landing.RestartWhy))
def test_every_exhaustion_site_is_a_diagnose_carrying_the_reason_otherwise(refusal, spent, why):
    step = landing.landing_step(refusal, _facts(), _spent(**spent), (), slicing=why)
    assert step == LandingStep("diagnose", refusal, (), restart_why=why)


@pytest.mark.parametrize(("refusal", "spent"), EXHAUSTED)
def test_without_slicing_the_exhaustion_is_the_diagnose_it_always_was(refusal, spent):
    step = landing.landing_step(refusal, _facts(), _spent(**spent), ())
    assert step == LandingStep("diagnose", refusal, ())
    assert step.restart_why is None


def test_the_adr_collision_that_cannot_be_renamed_goes_through_the_same_helper():
    facts = _facts(adr_collision=Ambiguous("duplicate_own_number", "docs/adr/0001-x.md"))
    step = landing.landing_step("adr_number_taken", facts, _spent(), (), slicing=SLICE)
    assert step == LandingStep("land_slice", "adr_number_taken", ("a", "b"))


def test_a_step_with_budget_left_is_not_touched_by_slicing():
    step = landing.landing_step("not_approved", _facts(), _spent(), (), slicing=SLICE)
    assert step.kind == "re_review"
    step = landing.landing_step("merge_conflict", _facts(), _spent(), (), slicing="single_unit")
    assert step.kind == "sync_master"
    assert step.restart_why is None


def test_a_diagnosed_task_has_no_step_left_whatever_the_slicing():
    step = landing.landing_step("merge_failed", _facts(), _spent(diagnosed=True), (), slicing=SLICE)
    assert step.kind == "none"


# --- sync epoch ----------------------------------------------------------------------------


def _entry(step: str, resolved: bool = False, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "head": HEAD,
        "step": step,
        "refusal": "attempts_exhausted",
        **extra,
    }
    if resolved:
        entry["resolved_at"] = "2026-10-22T10:00:00+00:00"
    return entry


def test_syncs_are_counted_only_after_the_last_resolved_land_slice():
    raw = {
        "landing": [
            _entry("sync_master", True),
            _entry("sync_master", True),
            _entry("land_slice", True),
            _entry("sync_master", True),
        ]
    }
    assert landing_plan.spent(raw, HEAD, None).syncs == 1


def test_passes_are_never_reset_by_a_land_slice():
    raw = {
        "landing": [
            _entry("fix", True),
            _entry("sync_master", True, refusal="merge_conflict"),
            _entry("land_slice", True),
            _entry("re_review", True),
        ]
    }
    assert landing_plan.spent(raw, HEAD, None).passes == 3


def test_a_land_slice_entry_still_open_does_not_open_a_new_epoch():
    raw = {"landing": [_entry("sync_master", True), _entry("land_slice", False)]}
    assert landing_plan.spent(raw, HEAD, None).syncs == 1


def test_unsynced_syncs_count_only_after_the_last_resolved_land_slice():
    old = "1" * 40
    raw = {
        "landing": [
            _entry("sync_master", closed_at="t", lacks=old),
            _entry("land_slice", True),
            _entry("sync_master", closed_at="t", lacks=old),
        ]
    }
    assert landing_plan.spent(raw, HEAD, None, old).unsynced_for_upstream == 1


def test_without_a_land_slice_entry_the_counts_are_what_they_were():
    raw = {"landing": [_entry("sync_master", True), _entry("sync_master", True)]}
    assert landing_plan.spent(raw, HEAD, None).syncs == 2


# --- the landing entry ---------------------------------------------------------------------


def test_the_refused_slices_are_the_land_slice_entries_closed_without_resolving():
    entries = [
        _entry("land_slice", slice_units=["a", "b"], closed_at="t"),
        _entry("land_slice", slice_units=["a", "b"], closed_at="t"),
        _entry("land_slice", True, slice_units=["a", "b"]),
        _entry("land_slice", slice_units=["a"], closed_at="t"),
        _entry("land_slice", slice_units=["c"]),
        _entry("sync_master", slice_units=["x"], closed_at="t"),
        _entry("land_slice", slice_units=[1], closed_at="t"),
    ]
    assert landing.refused_slices(entries) == {("a", "b"): 2, ("a",): 1}


def test_refusals_read_from_the_list_exclude_the_slice_they_name():
    entries = [_entry("land_slice", slice_units=["a", "b"], closed_at="t")] * 2
    result = landing.slice_or_restart(_inputs(refused=landing.refused_slices(entries)))
    assert result == landing.Slice(("a",))


def test_the_new_entry_keys_survive_reading_and_a_document_without_them_reads_as_before():
    raw = {
        "head": HEAD,
        "step": "land_slice",
        "slice_units": ["a", "b"],
        "slice_pull_request": "7",
        "restart_why": "no_clean_slice",
    }
    assert landing.entry_of(raw) == raw
    assert landing.entry_of({"head": HEAD, "step": "fix", "slice_units": "a"}) == {
        "head": HEAD,
        "step": "fix",
    }
