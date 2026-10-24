"""The merge gate of the criteria caps (GH-334): `auto_merge_decision` refuses a head that
pushes a criteria document past its cap (`criteria_over_cap`) or whose documents could not be
read (`criteria_unreadable`), right after `perf_unverified` and before the findings. Both are
left to the human by the landing."""

from dataclasses import replace
from typing import Any, get_args

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
CAP_MESSAGE = "`DESIGN_STANDARDS.md`: 305 lines (cap 300), 295 at the base"


def _facts(**over: Any) -> MergeFacts:
    base = MergeFacts(
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


def _decide(**over: Any) -> MergeDecision:
    return rules.auto_merge_decision(_facts(**over))


def test_a_head_without_a_regression_decides_as_it_always_did():
    assert _facts().criteria_regressed == ()
    assert _decide() == MergeDecision("merge", None)


def test_a_head_that_pushes_a_document_past_its_cap_is_refused():
    assert _decide(criteria_regressed=(CAP_MESSAGE,)) == MergeDecision(
        "refuse", "criteria_over_cap"
    )


def test_documents_that_could_not_be_read_are_refused_not_passed():
    assert _decide(criteria_regressed=None) == MergeDecision("refuse", "criteria_unreadable")


def test_the_regression_is_refused_before_the_open_findings():
    assert _decide(open_blocking=1, criteria_regressed=(CAP_MESSAGE,)).reason == (
        "criteria_over_cap"
    )
    assert _decide(open_blocking=1, criteria_regressed=None).reason == "criteria_unreadable"


def test_a_head_nobody_reviewed_is_refused_for_that_first():
    assert _decide(reviewed_head="c" * 40, criteria_regressed=(CAP_MESSAGE,)).reason == (
        "head_not_reviewed"
    )


def test_a_convened_performance_that_is_unverified_is_refused_before_the_regression():
    over = {"perf_convened": True, "perf_verdict": "returned", "perf_head": HEAD}
    assert _decide(criteria_regressed=(CAP_MESSAGE,), **over).reason == "perf_unverified"


def test_the_criteria_are_read_exactly_when_nothing_ahead_of_them_refuses():
    assert rules.criteria_probe_wanted(_facts()) is True
    assert rules.criteria_probe_wanted(_facts(reviewed_head="c" * 40)) is False
    assert rules.criteria_probe_wanted(_facts(status="developing")) is False


@pytest.mark.parametrize("reason", ["criteria_over_cap", "criteria_unreadable"])
def test_both_reasons_are_refusals_the_landing_knows(reason):
    assert reason in get_args(rules.MergeRefusal)
