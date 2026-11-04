"""GH-463: `rules.auto_merge_decision` for a product that declares `ci:` -- a fresh GitHub run
in place of the local checks. Pure: each case is a `MergeFacts` and the decision it must give."""

from dataclasses import replace
from typing import Literal

import pytest

from agentic.core.process.domain import ci, merge_ci, rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.task import MergeCiRecord

HEAD = "a" * 40
MASTER = "b" * 40
NEWER = "c" * 40
TESTED = "d" * 40
RED = CiCheck("check", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
CI_RUN = MergeDecision("ci_run", None)
MERGE = MergeDecision("merge", None)


def _pair(
    *,
    head: str = HEAD,
    master: str = MASTER,
    tested: str = TESTED,
    mode: Literal["push", "dispatch"] = "push",
    republished: bool = False,
    superseded: list[str] | None = None,
    outcome: Literal["stale", "no_verdict", "green", "red"] | None = None,
    run_id: str | None = None,
    released_at: str | None = None,
) -> MergeCiRecord:
    record = MergeCiRecord(
        head=head,
        master=master,
        tested=tested,
        mode=mode,
        published_at="2026-11-03T10:00:00.000Z",
        republished=republished,
        superseded=superseded or [],
    )
    if outcome is not None:
        record["outcome"] = outcome
    if run_id is not None:
        record["run_id"] = run_id
    if released_at is not None:
        record["released_at"] = released_at
    return record


def _verdict(state, failing=()) -> CiVerdict:
    return CiVerdict(state, tuple(failing), () if state != "pending" else ("check",))


def _facts(**over) -> MergeFacts:
    """A `ci:` pull request whose own CI ran on an older master: stale, trusted, nothing
    recorded. It needs a fresh run."""
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
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("check",),
        ci_untrusted=False,
        ci_head=HEAD,
        ci_verdict=_verdict("green"),
        ci_fresh=False,
        ci_waited_s=10.0,
    )
    return replace(base, **over)


def _fresh(record: MergeCiRecord, verdict: CiVerdict | None, **over) -> MergeFacts:
    """The facts once the fresh run of `record` was read: the verdict is about `tested`."""
    return _facts(
        ci_subject="tested",
        ci_head=record["tested"],
        ci_verdict=verdict,
        merge_ci=(record,),
        **over,
    )


# --- stale / fresh x trust x verdict ---------------------------------------------------------


@pytest.mark.parametrize("fresh", [True, False])
@pytest.mark.parametrize(
    ("untrusted", "declared", "dispatched", "stale_want", "fresh_want"),
    [
        (False, False, False, CI_RUN, MERGE),
        (True, True, False, CI_RUN, CI_RUN),
        (True, True, True, CI_RUN, MERGE),
        (True, False, False, "ci_untrusted_no_dispatch", "ci_untrusted_no_dispatch"),
    ],
)
def test_a_green_pr_ci_by_trust_and_freshness(
    fresh, untrusted, declared, dispatched, stale_want, fresh_want
):
    want = fresh_want if fresh else stale_want
    facts = _facts(
        ci_fresh=fresh,
        ci_untrusted=untrusted,
        ci_dispatch_declared=declared,
        ci_dispatched=dispatched,
        ci_covered=False,
    )
    got = rules.auto_merge_decision(facts)
    if isinstance(want, str):
        want = MergeDecision("refuse", want)
    assert got == want


def test_a_stale_pr_ci_never_runs_the_local_checks():
    decision = rules.auto_merge_decision(_facts())
    assert decision.kind != "run_checks"


@pytest.mark.parametrize("state", ["red", "pending"])
def test_the_prs_own_red_and_pending_ci_decide_as_before(state):
    verdict = _verdict(state, failing=(RED,) if state == "red" else ())
    got = rules.auto_merge_decision(_facts(ci_verdict=verdict))
    assert got == (
        MergeDecision("refuse", "ci_failed") if state == "red" else MergeDecision("wait", None)
    )


def test_an_absent_verdict_waits():
    assert rules.auto_merge_decision(_facts(ci_verdict=None)) == MergeDecision("wait", None)


# --- the fresh run's verdict ------------------------------------------------------------------


def test_a_green_fresh_run_on_the_current_master_merges():
    assert rules.auto_merge_decision(_fresh(_pair(), _verdict("green"))) == MERGE


def test_a_green_fresh_run_behind_master_merges_only_by_an_inert_gain():
    behind = _pair()
    assert (
        rules.auto_merge_decision(
            _fresh(behind, _verdict("green"), current_master=NEWER, ci_run_inert=True)
        )
        == MERGE
    )
    for inert in (False, None):
        got = rules.auto_merge_decision(
            _fresh(behind, _verdict("green"), current_master=NEWER, ci_run_inert=inert)
        )
        assert got == CI_RUN


def test_a_red_fresh_run_is_ci_failed_and_a_pending_one_waits():
    assert rules.auto_merge_decision(_fresh(_pair(), _verdict("red", failing=(RED,)))) == (
        MergeDecision("refuse", "ci_failed")
    )
    assert rules.auto_merge_decision(_fresh(_pair(), _verdict("pending"))) == MergeDecision(
        "wait", None
    )


def test_an_absent_fresh_verdict_waits():
    assert rules.auto_merge_decision(_fresh(_pair(), None)) == MergeDecision("wait", None)


def test_a_verdict_of_a_superseded_tested_sha_never_stands():
    old = "e" * 40
    record = _pair(superseded=[old])
    facts = replace(_fresh(record, _verdict("green")), ci_head=old)
    assert rules.auto_merge_decision(facts) == MergeDecision("wait", None)


def test_a_fresh_run_of_another_head_is_not_read_as_this_ones():
    record = _pair(head=NEWER)
    assert rules.auto_merge_decision(_fresh(record, _verdict("green"))) == MergeDecision(
        "wait", None
    )


def test_an_untrusted_head_merges_only_on_a_dispatched_fresh_run():
    push = _pair()
    facts = _fresh(push, _verdict("green"), ci_untrusted=True, ci_dispatch_declared=True)
    assert rules.auto_merge_decision(facts) == CI_RUN
    dispatch = _pair(mode="dispatch")
    facts = _fresh(dispatch, _verdict("green"), ci_untrusted=True, ci_dispatch_declared=True)
    assert rules.auto_merge_decision(facts) == MERGE


def test_the_rollup_with_two_check_runs_one_green_and_one_red_reads_red():
    report = CiReport(
        HEAD,
        "main",
        MASTER,
        None,
        (
            CiCheck("check", "COMPLETED", "SUCCESS", "https://github.com/o/r/actions/runs/1"),
            CiCheck("check", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/2"),
        ),
    )
    verdict = ci.ci_state(report, ("check",))
    assert verdict.state == "red"
    assert rules.auto_merge_decision(_facts(ci_verdict=verdict)) == MergeDecision(
        "refuse", "ci_failed"
    )


# --- a recorded pass (#353) -------------------------------------------------------------------


def test_a_covering_github_pass_merges_without_a_run():
    assert rules.auto_merge_decision(_facts(ci_covered=True)) == MERGE


def test_a_covered_verdict_on_an_untrusted_head_needs_a_dispatched_pass():
    facts = _facts(ci_covered=True, ci_untrusted=True, ci_dispatch_declared=True)
    assert rules.auto_merge_decision(facts) == CI_RUN
    assert rules.auto_merge_decision(replace(facts, ci_dispatched=True)) == MERGE


def test_a_dispatched_pair_vouches_for_a_covered_head_only_once_green():
    facts = _facts(ci_covered=True, ci_untrusted=True, ci_dispatch_declared=True)
    green = _pair(mode="dispatch", outcome="green")
    assert rules.auto_merge_decision(replace(facts, merge_ci=(green,))) == MERGE
    for outcome in ("stale", "no_verdict", None):
        pair = _pair(mode="dispatch", outcome=outcome)
        assert rules.auto_merge_decision(replace(facts, merge_ci=(pair,))) == CI_RUN


def test_a_short_head_counts_no_pairs():
    pair = _pair(outcome="stale")
    assert merge_ci.pair_counts([pair], HEAD[:7]).stale == 0
    assert merge_ci.pair_counts([pair], HEAD).stale == 1


def test_a_local_pass_never_covers_a_ci_product():
    facts = _facts(checks_passed=True, checks_head=HEAD, checks_master=MASTER, masters_covered=True)
    assert rules.auto_merge_decision(facts) == CI_RUN


def test_a_document_from_before_this_change_decides_a_run():
    assert rules.auto_merge_decision(_facts(merge_ci=(), ci_covered=None)) == CI_RUN


# --- the bounds -------------------------------------------------------------------------------


def _n(i: int) -> str:
    return f"{i:040x}"


def test_the_stale_bound_spent_refuses_ci_stale_exhausted():
    spent = tuple(_pair(master=_n(i), outcome="stale") for i in range(ci.CI_STALE_PAIRS_MAX))
    assert rules.auto_merge_decision(_facts(merge_ci=spent)) == MergeDecision(
        "refuse", "ci_stale_exhausted"
    )
    assert rules.auto_merge_decision(_facts(merge_ci=spent[:-1])) == CI_RUN


def test_the_no_verdict_bound_spent_refuses_ci_no_verdict_and_not_before_the_republish():
    spent = (_pair(outcome="no_verdict", republished=True),)
    assert rules.auto_merge_decision(_facts(merge_ci=spent)) == MergeDecision(
        "refuse", "ci_no_verdict"
    )
    waiting = (_pair(outcome="no_verdict", republished=False),)
    assert rules.auto_merge_decision(_facts(merge_ci=waiting)) == CI_RUN


def test_the_two_bounds_are_counted_apart():
    mixed = (
        _pair(master=_n(1), outcome="stale"),
        _pair(master=_n(2), outcome="stale"),
        _pair(master=_n(3), outcome="no_verdict", republished=True, released_at="t"),
    )
    assert rules.auto_merge_decision(_facts(merge_ci=mixed)) == CI_RUN


# --- products without ci: --------------------------------------------------------------------


def test_a_product_without_ci_still_gets_run_checks():
    assert rules.auto_merge_decision(_facts(ci_required=(), ci_verdict=None)) == MergeDecision(
        "run_checks", None
    )
    plan_only = _facts(ci_required=("check",), ci_plan_only=("check",))
    assert rules.auto_merge_decision(plan_only) == MergeDecision("run_checks", None)


def test_the_gain_probe_asks_for_ci_covered_too():
    assert rules.gain_probe_wanted(_facts())
