"""The merge gate of QA Performance (GH-318, ADR-0060): `rules.perf_merge_facts` reads
"convened" only from the plan and the verdict only from the latest QA Performance run, and
`auto_merge_decision` refuses with `perf_unverified` unless that verdict is `approved` and was
measured on the pull request's own head. A table, one row per input that provokes it
(ADR-0023), and the old chain for every case without the new facts."""

from dataclasses import replace
from typing import Any, cast, get_args

import pytest

from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.task import TaskState

HEAD = "a" * 40
MASTER = "b" * 40
OTHER = "c" * 40
SCENARIO = {
    "name": "list",
    "volume": 100000,
    "volume_unit": "rows",
    "basis": "the largest customer",
    "budgets": [
        {"metric": "wall_ms", "limit": 200, "unit": "ms", "source": "issue", "basis": "issue"}
    ],
}


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


def _perf(verdict: str | None, head: str | None) -> dict[str, Any]:
    return {"perf_convened": True, "perf_verdict": verdict, "perf_head": head}


PERF_REFUSALS = [
    ("unverified", _perf("unverified", HEAD)),
    ("returned", _perf("returned", HEAD)),
    ("no_run", _perf(None, None)),
    ("approved_on_another_head", _perf("approved", OTHER)),
    ("approved_without_a_sha", _perf("approved", None)),
    ("approved_on_a_short_sha", _perf("approved", "aaaa")),
]


@pytest.mark.parametrize(("name", "over"), PERF_REFUSALS, ids=[n for n, _ in PERF_REFUSALS])
def test_a_convened_task_without_an_approval_on_this_head_is_refused(name, over):
    assert _decide(**over) == MergeDecision("refuse", "perf_unverified")


def test_a_convened_task_approved_on_this_head_goes_on_to_the_old_chain():
    over = _perf("approved", HEAD)
    assert _decide(**over) == MergeDecision("merge", None)
    assert _decide(**over, checks_passed=False) == MergeDecision("refuse", "checks_failed")
    assert _decide(**over, open_blocking=1) == MergeDecision("refuse", "open_findings_blocking")


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"perf_verdict": "unverified"},
        {"perf_verdict": "returned", "perf_head": OTHER},
        {"perf_head": OTHER},
    ],
    ids=["no_facts", "verdict_only", "returned_elsewhere", "head_only"],
)
def test_a_task_that_is_not_convened_is_decided_as_before(over):
    assert _decide(**over) == MergeDecision("merge", None)
    assert _decide(**over, checks_passed=False) == MergeDecision("refuse", "checks_failed")


def test_the_perf_refusal_comes_right_after_head_not_reviewed_and_before_the_findings():
    perf = _perf("unverified", HEAD)
    assert _decide(**perf, reviewed_head=OTHER) == MergeDecision("refuse", "head_not_reviewed")
    assert _decide(**perf, reviewed_unit_approved=False) == MergeDecision("refuse", "not_approved")
    assert _decide(**perf, open_blocking=1) == MergeDecision("refuse", "perf_unverified")
    assert _decide(**perf, in_eval_set=True) == MergeDecision("refuse", "perf_unverified")


def test_a_perf_refused_head_needs_no_conflict_probe_but_still_the_adr_read():
    facts = _facts(**_perf("unverified", HEAD))
    assert rules.conflict_probe_wanted(facts) is False
    assert rules.adr_probe_wanted(facts) is True


def test_a_perf_refusal_is_in_the_closed_set_and_the_landing_leaves_it_to_the_human():
    assert "perf_unverified" in get_args(rules.MergeRefusal)
    facts = _facts(**_perf("unverified", HEAD))
    spent = landing.LandingSpent(
        passes=0, syncs=0, reruns_on_head=0, diagnosed=False, waited_s=None
    )
    step = landing.landing_step("perf_unverified", facts, spent, ("api",))
    assert step == landing.LandingStep("none", "perf_unverified", ())


def _run(verdict: str | None, head: str | None, *, role: str = "QA Performance") -> dict:
    run: dict[str, Any] = {"role": role, "cost_usd": 0.1}
    if verdict is not None:
        run["perf_verdict"] = verdict
    if head is not None:
        run["perf_head"] = head
    return run


def _state(performance: Any, runs: list[dict]) -> TaskState:
    state: dict[str, Any] = {"runs": runs}
    if performance is not None:
        state["performance"] = performance
    return cast(TaskState, state)


def test_perf_merge_facts_reads_convened_from_the_plan_and_the_verdict_from_the_latest_run():
    runs = [_run("returned", OTHER), _run("approved", HEAD)]
    assert rules.perf_merge_facts(_state([SCENARIO], runs)) == (True, "approved", HEAD)


def test_perf_merge_facts_without_a_run_is_convened_and_unknown():
    assert rules.perf_merge_facts(_state([SCENARIO], [])) == (True, None, None)


@pytest.mark.parametrize("performance", [None, []], ids=["absent", "empty"])
def test_perf_merge_facts_is_not_convened_without_scenarios_whatever_the_runs_say(performance):
    state = _state(performance, [_run("approved", HEAD)])
    assert rules.perf_merge_facts(state) == (False, None, None)


def test_perf_merge_facts_ignores_the_runs_of_other_roles_and_a_run_without_a_verdict():
    runs = [_run("approved", HEAD), _run("returned", OTHER, role="Developer"), _run(None, None)]
    assert rules.perf_merge_facts(_state([SCENARIO], runs)) == (True, None, None)
