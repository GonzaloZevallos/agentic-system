"""`landing.review_scope` and `landing.scope_patch` (GH-589, ADR-0089): what a review pass of
a head covers, and the one writer of the head and its record."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import landing
from agentic.core.process.domain.landing import (
    RESOLUTION_LINES_MAX,
    ScopeFacts,
    ScopeStep,
    review_scope,
    scope_patch,
)
from agentic.core.process.domain.task import apply


def merge_step(text: str = "", sha: str = "m" * 40) -> ScopeStep:
    return ScopeStep(sha, "upstream_merge", text)


def own_step(text: str = "diff of own", sha: str = "o" * 40) -> ScopeStep:
    return ScopeStep(sha, "own_commit", text)


def facts(*steps: ScopeStep, **overrides) -> ScopeFacts:
    base = {
        "reviewed": "r1",
        "readable": True,
        "unreadable_cause": None,
        "reviewed_is_ancestor": True,
        "system_merge": True,
        "steps": steps,
    }
    return ScopeFacts(**{**base, **overrides})


def test_no_accepted_tip_is_a_full_first_review():
    verdict = review_scope(facts(merge_step(), reviewed=None))
    assert (verdict.scope, verdict.cause, verdict.text) == ("full", "first", "")


@pytest.mark.parametrize("cause", ["unreadable", "no_remerge_diff"])
def test_an_unreadable_fact_is_full_with_its_cause(cause):
    verdict = review_scope(facts(merge_step(), readable=False, unreadable_cause=cause))
    assert (verdict.scope, verdict.cause) == ("full", cause)


def test_unreadable_without_a_named_cause_is_unreadable():
    verdict = review_scope(facts(merge_step(), readable=False))
    assert (verdict.scope, verdict.cause) == ("full", "unreadable")


def test_a_tip_that_is_not_an_ancestor_is_full_rewritten():
    verdict = review_scope(facts(merge_step(), reviewed_is_ancestor=False))
    assert (verdict.scope, verdict.cause) == ("full", "rewritten")


def test_a_chain_with_no_upstream_merge_is_full_no_merge():
    assert review_scope(facts(own_step())).cause == "no_merge"
    assert review_scope(facts()).cause == "no_merge"


def test_a_foreign_merge_is_full():
    verdict = review_scope(facts(merge_step(), ScopeStep("f" * 40, "foreign_merge", "")))
    assert (verdict.scope, verdict.cause) == ("full", "foreign_merge")


def test_an_own_commit_before_the_first_merge_is_full():
    verdict = review_scope(facts(own_step(), merge_step()))
    assert (verdict.scope, verdict.cause) == ("full", "own_before_merge")


def test_a_clean_merge_carries():
    verdict = review_scope(facts(merge_step()))
    assert (verdict.scope, verdict.cause, verdict.text) == ("carried", "clean", "")


def test_a_developers_clean_merge_with_nothing_after_carries_too():
    assert review_scope(facts(merge_step(), system_merge=False)).scope == "carried"


def test_a_resolved_merge_sends_only_the_resolution_text():
    verdict = review_scope(facts(merge_step("@@ -1 +1 @@\n-a\n+b", sha="abc" * 13)))
    assert (verdict.scope, verdict.cause) == ("resolution", "resolved")
    assert verdict.text == "## abcabcabcabc (upstream_merge)\n@@ -1 +1 @@\n-a\n+b"


def test_an_own_commit_after_a_clean_merge_is_reviewed_as_resolution():
    verdict = review_scope(facts(merge_step(), own_step("+fix", sha="d" * 40)))
    assert (verdict.scope, verdict.cause) == ("resolution", "resolved")
    assert "dddddddddddd (own_commit)\n+fix" in verdict.text
    assert "upstream_merge" not in verdict.text


def test_an_own_commit_with_no_diff_after_a_merge_is_still_not_carried():
    verdict = review_scope(facts(merge_step(), own_step("")))
    assert verdict.scope == "resolution"
    assert "(no changes)" in verdict.text


def _text_of(lines: int) -> str:
    # The header line counts: `lines` is the whole text the reviewer gets.
    return "\n".join(f"+l{n}" for n in range(lines - 1))


def test_exactly_the_cap_is_a_resolution_and_one_more_line_is_full_over_cap():
    at_cap = review_scope(facts(merge_step(_text_of(RESOLUTION_LINES_MAX))))
    assert (at_cap.scope, at_cap.cause) == ("resolution", "resolved")
    assert len(at_cap.text.splitlines()) == RESOLUTION_LINES_MAX
    over = review_scope(facts(merge_step(_text_of(RESOLUTION_LINES_MAX + 1))))
    assert (over.scope, over.cause, over.text) == ("full", "over_cap", "")


def test_every_decision_is_stable_for_the_same_facts():
    given = facts(merge_step("+x"))
    assert review_scope(given) == review_scope(given)


# --- scope_patch --------------------------------------------------------------------------


def test_scope_patch_writes_the_head_and_the_record_together():
    subtasks = {"a": {"id": "a", "reviewed_head": "r1"}, "b": {"id": "b"}}
    moved, record = scope_patch(
        subtasks, "a", "resolution", "resolved", "r1", "h2", "2026-11-11T00:00:00+00:00"
    )
    assert moved["a"]["reviewed_head"] == "h2"
    assert "reviewed_head_carried_from" not in moved["a"]
    assert moved["b"] == {"id": "b"}
    assert subtasks["a"]["reviewed_head"] == "r1"
    assert record == {
        "unit": "a",
        "head": "h2",
        "base": "r1",
        "scope": "resolution",
        "cause": "resolved",
        "at": "2026-11-11T00:00:00+00:00",
    }


def test_scope_patch_sets_carried_from_only_for_carried():
    subtasks = {"a": {"id": "a", "reviewed_head": "r1"}}
    carried, _ = scope_patch(subtasks, "a", "carried", "clean", "r1", "h2", "t")
    assert carried["a"]["reviewed_head_carried_from"] == "r1"
    later, _ = scope_patch(carried, "a", "full", "rewritten", "h2", "h3", "t")
    assert "reviewed_head_carried_from" not in later["a"]


def test_a_first_review_records_no_base():
    _, record = scope_patch({"a": {"id": "a"}}, "a", "full", "first", None, "h1", "t")
    assert record["base"] is None


def test_the_record_appends_through_apply_and_a_retry_adds_nothing():
    subtasks = {"a": {"id": "a"}}
    _, record = scope_patch(subtasks, "a", "full", "first", None, "h1", "t")
    once = apply({"id": "GH-1"}, {"review_scopes": [record]})
    again = apply(once, {"review_scopes": [record]})
    assert once["review_scopes"] == again["review_scopes"] == [record]
    other = apply(once, {"review_scopes": [{**record, "head": "h2"}]})
    assert [r["head"] for r in other["review_scopes"]] == ["h1", "h2"]


def test_a_document_without_the_key_loads_unchanged():
    assert "review_scopes" not in apply({"id": "GH-1"}, {})
    assert landing.review_scope is review_scope
