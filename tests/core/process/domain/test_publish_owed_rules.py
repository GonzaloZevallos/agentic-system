"""The pure rules of an owed publication (GH-373): a task whose integration review approved
head H and whose full checks passed on H, stopped before H was published, resumes into
`deliver` and nothing else; a task delivered for real still resumes into nothing."""

import pytest

from agentic.core.process.domain import document, rules

HEAD = "a" * 40
OTHER = "b" * 40


def passed(head=HEAD, ok=True):
    return {
        "version": 1,
        "head": head,
        "passed": ok,
        "duration_s": 1.0,
        "declared": {"tests": "pytest"},
        "results": [{"name": "tests", "passed": ok}],
        "witness": {"passed": True},
    }


def state(**more):
    base = {
        "type": "feature",
        "status": "developing",
        "flags": {},
        "pull_request": "https://example.test/pr/370",
        "subtasks": {
            "api": {"status": "approved", "reviewed_head": "1" * 40},
            "integration": {"status": "approved", "reviewed_head": HEAD},
        },
        "runs": [],
        "task_head": HEAD,
        "full_checks": passed(),
        "publish_owed": HEAD,
    }
    return {**base, **more}


def with_landing(closed):
    entry = {"head": OTHER, "step": "fix_important"}
    if closed:
        entry["resolved_at"] = "2026-10-26T10:00:00+00:00"
    return {"landing": [entry]}


# --- approved_head -----------------------------------------------------------------------------


def test_approved_head_of_a_tiered_product_is_the_task_head_both_reviewed_and_checked():
    assert rules.approved_head(state()) == HEAD
    # The same commit at another length is the same head.
    assert rules.approved_head(state(task_head=HEAD[:12])) == HEAD[:12]


@pytest.mark.parametrize(
    "more",
    [
        {"full_checks": passed(ok=False)},
        {"full_checks": passed(head=OTHER)},
        {"full_checks": None},
        {"task_head": OTHER},  # the unit was reviewed on another head: a heal
        {"subtasks": {"integration": {"status": "approved"}}},  # no reviewed_head
        {"subtasks": {"a": {"status": "approved"}, "b": {"status": "approved"}}},  # no unit
    ],
)
def test_approved_head_of_a_tiered_product_is_none_without_review_and_passed_checks(more):
    assert rules.approved_head(state(**more)) is None


def test_approved_head_without_tiers_is_the_reviewed_head_of_the_reviewed_unit():
    plain = state(task_head=None, full_checks=None)
    plain.pop("task_head")
    plain.pop("full_checks")
    assert rules.approved_head(plain) == HEAD
    lone = {"subtasks": {"only": {"status": "approved", "reviewed_head": OTHER}}}
    assert rules.approved_head(state(task_head=None, **lone)) == OTHER
    assert rules.approved_head(state(task_head=None, subtasks={"integration": {}})) is None


# --- publication_candidate ---------------------------------------------------------------------


def test_the_candidate_is_the_approved_head_of_a_task_with_a_pull_request():
    assert rules.publication_candidate(state()) == HEAD


@pytest.mark.parametrize(
    "more",
    [
        {"pull_request": None},
        {"escalated": "stop"},
        {"full_checks": passed(ok=False)},
        {"subtasks": {"integration": {"status": "returned", "reviewed_head": HEAD}}},
        {"subtasks": {"integration": {"status": "delivered", "reviewed_head": HEAD}}},
        {"task_head": OTHER},
    ],
)
def test_there_is_no_candidate_for_a_task_that_is_not_fully_approved_and_checked(more):
    assert rules.publication_candidate(state(**more)) is None


def test_there_is_no_candidate_while_a_landing_pass_is_open():
    reopened = {
        "subtasks": {"integration": {"status": "returned", "reviewed_head": HEAD}},
        **with_landing(closed=False),
    }
    assert rules.publication_candidate(state(**reopened)) is None


# --- owed_publication --------------------------------------------------------------------------


def test_owed_when_the_remote_lacks_the_head_and_the_branch_is_at_it():
    assert rules.owed_publication(state(), published=False, branch_at_head=True) == HEAD
    ready = state(status="ready_for_review")
    assert rules.owed_publication(ready, published=False, branch_at_head=True) == HEAD


def test_owed_when_pushed_but_the_status_never_moved():
    assert rules.owed_publication(state(), published=True, branch_at_head=True) == HEAD


@pytest.mark.parametrize("status", ["ready_for_review", "waiting_on_human"])
def test_not_owed_when_pushed_and_the_status_says_it_was_delivered(status):
    paused = state(status=status)
    assert rules.owed_publication(paused, published=True, branch_at_head=True) is None


def test_not_owed_when_the_branch_is_elsewhere_or_there_is_no_candidate():
    assert rules.owed_publication(state(), published=False, branch_at_head=False) is None
    red = state(full_checks=passed(ok=False))
    assert rules.owed_publication(red, published=False, branch_at_head=True) is None


# --- resume_target and resumed_status ----------------------------------------------------------

# The three stops differ in what `prepare` read from the repository, not in the state it
# derives: every one of them reaches the rules as `publish_owed == H`.
STOPS = ["not pushed", "pushed without status", "document committed but not pushed"]


@pytest.mark.parametrize("landing", [{}, with_landing(closed=True), with_landing(closed=False)])
@pytest.mark.parametrize("stop", STOPS)
def test_each_stop_after_the_approval_resumes_into_deliver(stop, landing):
    owed = state(**landing)
    assert rules.resume_target(owed) == "deliver"
    assert rules.resumed_status(owed) == "developing"


def test_a_task_delivered_for_real_resumes_into_nothing():
    delivered = state(status="ready_for_review")
    delivered.pop("publish_owed")
    assert rules.resume_target(delivered) is None
    assert rules.resumed_status(delivered) == "ready_for_review"
    cleared = state(status="ready_for_review", publish_owed=None)
    assert rules.resume_target(cleared) is None
    assert rules.resumed_status(cleared) == "ready_for_review"


@pytest.mark.parametrize(
    "more",
    [
        {"publish_owed": OTHER},
        {"publish_owed": None},
        {"full_checks": passed(ok=False)},
        {"full_checks": passed(head=OTHER)},
        {"subtasks": {"integration": {"status": "approved", "reviewed_head": OTHER}}},  # a heal
        {"escalated": "stop"},
        {"subtasks": {"integration": {"status": "returned", "reviewed_head": HEAD}}},
    ],
)
def test_nothing_is_owed_for_another_head_red_checks_a_heal_or_an_escalation(more):
    refused = state(**more)
    assert rules.resume_target(refused) != "deliver"
    assert rules.resumed_status(refused) == "ready_for_review"


def test_an_owed_publication_without_tiers_resumes_into_deliver():
    plain = state()
    plain.pop("task_head")
    plain.pop("full_checks")
    assert rules.resume_target(plain) == "deliver"
    assert rules.resumed_status(plain) == "developing"


# --- the document ------------------------------------------------------------------------------


def _doc(**more):
    return {"id": "GH-1", "title": "t", "description": "d", "sections": {}, **more}


def test_publish_owed_round_trips_a_sha_and_none_disappears():
    parsed = document.parse(document.render(_doc(publish_owed=HEAD)))
    assert parsed["publish_owed"] == HEAD
    plain = document.render(_doc(publish_owed=None))
    assert "publish_owed" not in plain
    assert plain == document.render(_doc())
