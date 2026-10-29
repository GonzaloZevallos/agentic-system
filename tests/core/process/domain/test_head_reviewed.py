"""`rules.head_reviewed` (close-docs-head): the one predicate that says a pull request head
is the one Tech Lead reviewed -- or the close-time capability commit made right on top of it.
Pure: ids in, a bool out; then every reader of it (`auto_merge_decision`, `approved_green`,
`approved_head`) shows the same answer, with and without `close_docs`."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import plan_settlement, rules
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

REVIEWED = "a" * 40
DOCS = "d" * 40
OTHER = "c" * 40
MASTER = "b" * 40


def close_docs(parent=REVIEWED, head=DOCS, paths=("docs/product/dashboard.md",)):
    return {"parent": parent, "head": head, "paths": list(paths)}


# --- head_reviewed -----------------------------------------------------------------------------


def test_the_reviewed_head_itself_is_reviewed_with_or_without_a_record():
    assert rules.head_reviewed(REVIEWED, None, REVIEWED)
    assert rules.head_reviewed(REVIEWED, close_docs(), REVIEWED)
    assert rules.head_reviewed(REVIEWED, None, REVIEWED[:12])  # another length, same commit


def test_the_close_docs_commit_on_the_reviewed_head_is_reviewed():
    assert rules.head_reviewed(REVIEWED, close_docs(), DOCS)
    assert rules.head_reviewed(REVIEWED, close_docs(parent=REVIEWED[:12]), DOCS[:12])


@pytest.mark.parametrize(
    ("reviewed", "record", "head"),
    [
        (REVIEWED, None, DOCS),  # no record: a head nobody approved
        (REVIEWED, close_docs(parent=OTHER), DOCS),  # foreign parent
        (REVIEWED, close_docs(head=OTHER), DOCS),  # stale record: another commit
        (REVIEWED, close_docs(paths=["agentic/x.py"]), DOCS),  # path outside docs/product/
        (REVIEWED, close_docs(paths=["docs/product/a.md", "docs/adr/0001.md"]), DOCS),
        (REVIEWED, close_docs(paths=["docs/product/../../x.md"]), DOCS),
        (REVIEWED, close_docs(paths=["docs/product/a.txt"]), DOCS),  # not a capability file
        (REVIEWED, close_docs(paths=[]), DOCS),  # nothing recorded covers nothing
        (None, close_docs(), DOCS),  # no reviewed head
        (REVIEWED, close_docs(), None),  # no head
    ],
    ids=[
        "no-record",
        "foreign-parent",
        "stale",
        "outside",
        "one-outside",
        "dotdot",
        "ext",
        "empty",
        "no-reviewed",
        "no-head",
    ],
)
def test_nothing_else_is_reviewed(reviewed, record, head):
    assert not rules.head_reviewed(reviewed, record, head)


def test_the_system_decisions_file_is_a_reviewed_close_path():
    """The owner-rules commit may append to the system's decisions file (invariant 8)."""
    record = close_docs(paths=["docs/decisiones-del-dueño.md"])
    assert rules.head_reviewed(REVIEWED, record, DOCS)
    both = ["docs/product/decisiones-del-dueño.md", "docs/decisiones-del-dueño.md"]
    assert rules.head_reviewed(REVIEWED, close_docs(paths=both), DOCS)


@pytest.mark.parametrize(
    "paths",
    [
        ["docs/orquestacion.md"],  # the orchestration rules are never a close path
        ["docs/decisiones-del-dueño.md", "docs/orquestacion.md"],
        ["docs/decisiones-del-dueño.md", "agentic/x.py"],
        ["docs/other/decisiones-del-dueño.md"],  # the name elsewhere is no owner file
        ["docs/../decisiones-del-dueño.md"],
    ],
)
def test_the_orchestration_file_and_other_paths_cover_nothing(paths):
    assert not rules.head_reviewed(REVIEWED, close_docs(paths=paths), DOCS)


# --- auto_merge_decision and approved_green ----------------------------------------------------


def facts(**over) -> MergeFacts:
    base = MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=REVIEWED,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=REVIEWED,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=1,
        checks_head=REVIEWED,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )
    return replace(base, **over)


def test_the_merge_gate_refuses_a_docs_head_without_the_record_and_accepts_it_with_it():
    docs_head = facts(pr_head=DOCS, checks_head=DOCS)
    assert rules.auto_merge_decision(docs_head) == MergeDecision("refuse", "head_not_reviewed")
    assert rules.auto_merge_decision(replace(docs_head, close_docs=close_docs())) == MergeDecision(
        "merge", None
    )


def test_the_merge_gate_refuses_a_docs_head_whose_record_has_a_foreign_parent():
    foreign = facts(pr_head=DOCS, checks_head=DOCS, close_docs=close_docs(parent=OTHER))
    assert rules.auto_merge_decision(foreign) == MergeDecision("refuse", "head_not_reviewed")


def test_the_merge_gate_refuses_a_head_on_top_of_the_docs_commit():
    """The task document committed over `close_docs.head` (a bounded body) is not covered."""
    top = facts(pr_head=OTHER, checks_head=OTHER, close_docs=close_docs())
    assert rules.auto_merge_decision(top) == MergeDecision("refuse", "head_not_reviewed")


def test_the_merge_gate_still_refuses_a_document_commit_on_the_reviewed_head_without_a_record():
    """Pinned as it is today: `pr_head` is not `reviewed_head`, nothing covers it."""
    body = facts(pr_head=OTHER, checks_head=OTHER)
    assert rules.auto_merge_decision(body) == MergeDecision("refuse", "head_not_reviewed")


def test_approved_green_reads_the_same_predicate():
    assert plan_settlement.approved_green(facts())
    assert not plan_settlement.approved_green(facts(pr_head=DOCS))
    assert plan_settlement.approved_green(facts(pr_head=DOCS, close_docs=close_docs()))
    foreign = facts(pr_head=DOCS, close_docs=close_docs(parent=OTHER))
    assert not plan_settlement.approved_green(foreign)
    assert not plan_settlement.approved_green(facts(pr_head=OTHER, close_docs=close_docs()))


# --- approved_head -----------------------------------------------------------------------------


def passed(head):
    return {
        "version": 1,
        "head": head,
        "passed": True,
        "duration_s": 1.0,
        "declared": {"tests": "pytest"},
        "results": [{"name": "tests", "passed": True}],
        "witness": {"passed": True},
    }


def state(**more):
    base = {
        "type": "feature",
        "status": "developing",
        "flags": {},
        "pull_request": "https://example.test/pr/9",
        "subtasks": {"integration": {"status": "approved", "reviewed_head": REVIEWED}},
        "runs": [],
    }
    return {**base, **more}


def test_approved_head_without_tiers_is_the_docs_head_when_the_record_is_covered():
    assert rules.approved_head(state()) == REVIEWED
    assert rules.approved_head(state(close_docs=close_docs())) == DOCS
    assert rules.approved_head(state(close_docs=close_docs(parent=OTHER))) == REVIEWED
    assert rules.approved_head(state(close_docs=close_docs(paths=["agentic/x.py"]))) == REVIEWED
    headless = {"parent": REVIEWED, "paths": ["docs/product/dashboard.md"]}  # malformed record
    assert rules.approved_head(state(close_docs=headless)) == REVIEWED


def test_approved_head_with_tiers_accepts_the_docs_head_only_with_the_suite_passed_on_it():
    tiered = state(task_head=DOCS, close_docs=close_docs(), full_checks=passed(DOCS))
    assert rules.approved_head(tiered) == DOCS
    unchecked = state(task_head=DOCS, close_docs=close_docs(), full_checks=passed(REVIEWED))
    assert rules.approved_head(unchecked) is None
    no_record = state(task_head=DOCS, full_checks=passed(DOCS))
    assert rules.approved_head(no_record) is None
    foreign = state(task_head=DOCS, close_docs=close_docs(parent=OTHER), full_checks=passed(DOCS))
    assert rules.approved_head(foreign) is None
    plain = state(task_head=REVIEWED, full_checks=passed(REVIEWED))
    assert rules.approved_head(plain) == REVIEWED


def test_publication_candidate_follows_the_docs_head():
    tiered = state(task_head=DOCS, close_docs=close_docs(), full_checks=passed(DOCS))
    assert rules.publication_candidate(tiered) == DOCS
    assert rules.publication_candidate(state(close_docs=close_docs())) == DOCS
