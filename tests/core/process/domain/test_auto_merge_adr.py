"""`rules.auto_merge_decision` when the branch added an ADR (GH-229, ADR-0048). Pure: each
case is a `MergeFacts` and the `MergeDecision` it must give -- the `renumber` decision, its
two refusals, the places it takes in the order and the recorded chain that lets a renumbered
head count as reviewed -- plus the proof that facts without ADR facts decide as before
(ADR-0023)."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.adr_numbers import Collision
from agentic.core.process.domain.ci import CiCheck, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts, RenumberRecord

HEAD = "a" * 40
HEAD_2 = "d" * 40
HEAD_3 = "e" * 40
MASTER = "b" * 40
OTHER = "c" * 40
MINE = "docs/adr/0037-mine.md"
PLAN = {MINE: "docs/adr/0038-mine.md"}
COLLIDES = Collision(colliding=(MINE,))
RENUMBER = MergeDecision("renumber", None)


def _record(from_head: str, to_head: str) -> RenumberRecord:
    return {"from_head": from_head, "to_head": to_head, "map": dict(PLAN), "unrewritten": []}


def _facts(**over) -> MergeFacts:
    """A pull request that merges, checks green on this head and master, whose branch added
    an ADR master took: every other fact in the accepting position."""
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
        touched_paths=("agentic/core/x.py", MINE),
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
        adr_collision=COLLIDES,
        adr_plan=PLAN,
    )
    return replace(base, **over)


def test_a_collision_with_the_caps_to_spare_is_a_renumber_not_a_merge():
    assert rules.auto_merge_decision(_facts()) == RENUMBER
    assert rules.auto_merge_decision(_facts(adr_collision=Collision())) == MergeDecision(
        "merge", None
    )


EARLIER = [
    ("not_ready", {"status": "developing"}),
    ("pr_not_open", {"pr_state": "CLOSED"}),
    ("not_approved", {"reviewed_unit_approved": False}),
    ("head_not_reviewed", {"reviewed_head": OTHER}),
    ("open_findings", {"open_important": 1}),
    ("eval_set_unreadable", {"in_eval_set": None}),
    ("eval_set", {"in_eval_set": True}),
]


@pytest.mark.parametrize(("reason", "over"), EARLIER, ids=[r for r, _ in EARLIER])
@pytest.mark.parametrize("collision", [COLLIDES, Collision(duplicate=True)], ids=["push", "dup"])
def test_every_refusal_before_the_adr_check_wins_so_such_a_task_never_gets_a_push(
    reason, over, collision
):
    facts = _facts(adr_collision=collision, **over)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", reason)


LATER = [
    {"merge_failures": rules.MERGE_CALLS_MAX, "merge_failure": "gh_rejected"},
    {"checks_passed": False},
    {"checks_head": None, "checks_master": None, "checks_passed": None, "attempts": 0},
    {"checks_master": OTHER, "attempts": rules.MERGE_ATTEMPTS_MAX},
]


@pytest.mark.parametrize("over", LATER, ids=["merge_failed", "checks_failed", "run", "exhausted"])
def test_the_renumber_comes_before_the_merge_failure_and_the_checks(over):
    assert rules.auto_merge_decision(_facts(**over)) == RENUMBER


def test_the_renumber_comes_before_the_ci_gate_whatever_the_ci_says():
    red = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
    facts = _facts(
        ci_required=("web",),
        ci_head=HEAD,
        ci_verdict=CiVerdict("red", (red,), ()),
        ci_fresh=True,
    )
    assert rules.auto_merge_decision(facts) == RENUMBER
    assert rules.auto_merge_decision(_facts(ci_required=("web",))) == RENUMBER


def test_two_files_at_head_with_one_number_are_refused_as_taken():
    facts = _facts(adr_collision=Collision(duplicate=True), adr_plan={})
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "adr_number_taken")


def test_a_duplicate_is_refused_even_with_no_renumber_left_to_spend_or_checks_to_run():
    facts = _facts(adr_collision=Collision(colliding=(MINE,), duplicate=True), checks_passed=None)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "adr_number_taken")


def test_the_renumber_cap_is_two_and_the_boundary_is_one_against_two():
    assert rules.ADR_RENUMBERS_MAX == 2
    one = (_record(HEAD, HEAD_2),)
    facts = _facts(reviewed_head=HEAD, pr_head=HEAD_2, adr_renumbers=one)
    assert rules.auto_merge_decision(facts) == RENUMBER
    two = (*one, _record(HEAD_2, HEAD_3))
    facts = _facts(reviewed_head=HEAD, pr_head=HEAD_3, adr_renumbers=two)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "adr_number_taken")


def test_the_cap_refuses_only_when_there_is_a_collision_left_to_settle():
    two = (_record(HEAD, HEAD_2), _record(HEAD_2, HEAD_3))
    facts = _facts(
        pr_head=HEAD_3,
        checks_head=HEAD_3,
        adr_collision=Collision(),
        adr_plan={},
        adr_renumbers=two,
    )
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def _chained(*records: RenumberRecord, pr_head: str, **over) -> MergeFacts:
    return _facts(
        pr_head=pr_head,
        checks_head=pr_head,
        adr_collision=Collision(),
        adr_plan={},
        adr_renumbers=tuple(records),
        **over,
    )


def test_a_renumbered_head_linked_to_the_reviewed_one_by_the_recorded_chain_is_reviewed():
    facts = _chained(_record(HEAD, HEAD_2), pr_head=HEAD_2)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_chain_of_two_links_is_followed_end_to_end():
    facts = _chained(_record(HEAD, HEAD_2), _record(HEAD_2, HEAD_3), pr_head=HEAD_3)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_chain_compares_each_link_by_commit_not_by_text():
    first = _record(HEAD[:7], HEAD_2[:12])
    second = _record(HEAD_2, HEAD_3[:9])
    facts = _chained(first, second, pr_head=HEAD_3)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


BROKEN = {
    "no record": ((), HEAD_2),
    "starts elsewhere": ((_record(OTHER, HEAD_2),), HEAD_2),
    "ends elsewhere": ((_record(HEAD, OTHER),), HEAD_2),
    "gap in the middle": ((_record(HEAD, HEAD_2), _record(OTHER, HEAD_3)), HEAD_3),
    "stops short": ((_record(HEAD, HEAD_2),), HEAD_3),
}


@pytest.mark.parametrize(("records", "pr_head"), BROKEN.values(), ids=BROKEN.keys())
def test_a_chain_that_does_not_link_reviewed_head_to_pr_head_is_not_reviewed(records, pr_head):
    facts = _chained(*records, pr_head=pr_head)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_chain_with_a_malformed_record_is_not_a_chain():
    facts = _chained({"from_head": HEAD, "map": {}, "unrewritten": []}, pr_head=HEAD_2)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_chain_never_stands_in_for_the_approval():
    facts = _chained(_record(HEAD, HEAD_2), pr_head=HEAD_2, reviewed_unit_approved=False)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "not_approved")


def test_a_chain_does_not_make_a_short_pr_head_a_reviewed_one():
    facts = _chained(_record(HEAD, HEAD_2), pr_head=HEAD_2[:7])
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_chain_with_no_reviewed_head_reviews_nothing():
    facts = _chained(_record(HEAD, HEAD_2), pr_head=HEAD_2, reviewed_head=None)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_chain_starts_at_the_record_after_the_last_review_not_at_the_first_ever():
    # The task was reviewed again at HEAD_2 after the first renumber: the old link is history.
    records = (_record(OTHER, HEAD), _record(HEAD_2, HEAD_3))
    facts = _chained(*records, pr_head=HEAD_3, reviewed_head=HEAD_2)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_renumbered_head_that_collides_again_is_renumbered_again_through_the_chain():
    facts = _facts(
        pr_head=HEAD_2,
        checks_head=HEAD_2,
        adr_renumbers=(_record(HEAD, HEAD_2),),
    )
    assert rules.auto_merge_decision(facts) == RENUMBER


def test_facts_built_without_the_adr_fields_decide_as_before():
    plain = MergeFacts(
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
    assert plain.adr_collision == Collision()
    assert not plain.adr_collision
    assert dict(plain.adr_plan) == {}
    assert plain.adr_renumbers == ()
    assert rules.auto_merge_decision(plain) == MergeDecision("merge", None)
    stale = replace(plain, checks_master=OTHER)
    assert rules.auto_merge_decision(stale) == MergeDecision("run_checks", None)
