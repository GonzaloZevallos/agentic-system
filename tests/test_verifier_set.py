"""The pure rules of the verifier set (GH-469): which verifiers are due, how their patches
combine into the one return, how a subtask with several returns is read, and when the
close-time capability files may be committed.

Domain only: states and patches in, states and patches out. The application that runs the
verifiers and the graph that routes to them are other subtasks'; their seams are not here."""

import itertools
from typing import Any

import pytest

from agentic.core.process.domain import document, roles, rules
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, apply

HEAD = "a" * 40
OTHER = "b" * 40
FILES = [{"capability": "checks", "content": "# Checks\n"}]

# The order the plan fixes, written out here on purpose: the test does not read it from the
# code it checks.
AT = "2026-11-06T12:00:00+00:00"
ORDER = ("full_checks", "copy_review", "final_validation", "qa_performance")
PAIRS = {
    "full_checks": "Tech Lead→Developer",
    "final_validation": "PM→Tech Lead",
    "qa_performance": "QA Performance→Tech Lead",
}


def fold(state: Any, patch: Any) -> Any:
    """The state after `patch`, the way the graph folds it in."""
    return apply(state, patch)


def approved_state(**extra: Any) -> Any:
    """An approved single-unit task: the integration reviewed at HEAD, nothing run yet."""
    return {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "type": "feature",
        "task_head": HEAD,
        "sections": {},
        "runs": [{"role": "Tech Lead", "section": rules.review_section(INTEGRATION), "version": 1}],
        "subtasks": {
            INTEGRATION: {"status": "approved", "reviewed_head": HEAD, "reason": ""},
            "api": {"status": "approved"},
        },
        **extra,
    }


# --- verifiers_due ---------------------------------------------------------------------------


def due_state(due: tuple[str, ...]) -> Any:
    state = approved_state()
    if "copy_review" in due:
        state["flags"] = {"touches_copy": True}
    if "final_validation" in due:
        state["flags"] = {**state.get("flags", {}), "touches_ui": True}
    if "qa_performance" in due:
        state["performance"] = [{"scenario": "load"}]
    if "full_checks" not in due:
        state["full_checks"] = {"head": HEAD, "passed": True}
    return state


def test_verifiers_due_is_the_predicates_in_the_fixed_order():
    subsets = itertools.chain.from_iterable(
        itertools.combinations(ORDER, n) for n in range(len(ORDER) + 1)
    )
    for subset in subsets:
        state = due_state(subset)
        assert rules.verifiers_due(state) == subset, subset
        assert rules.verifiers_due(state) == tuple(
            name for name in ORDER if getattr(rules, f"{name}_due")(state)
        )


def test_verifier_order_is_the_fixed_one():
    assert rules.VERIFIER_ORDER == ORDER


def test_only_the_suite_is_due_while_a_unit_is_not_approved():
    waiting = due_state(ORDER)
    waiting["subtasks"]["api"]["status"] = "returned"
    assert rules.verifiers_due(waiting) == ("full_checks",)


# --- returned_by_all / return_sections / returned_developer_delta ----------------------------


def test_returned_by_all_reads_an_old_document_as_one_return():
    assert rules.returned_by_all({"status": "returned"}) == ("review",)
    assert rules.returned_by_all({"returned_by": "qa_performance"}) == ("qa_performance",)
    assert rules.returned_by_all({"returned_by": "full_checks", "returned_with": []}) == (
        "full_checks",
    )


def test_returned_by_all_puts_returned_by_first_then_returned_with():
    subtask: Any = {
        "returned_by": "full_checks",
        "returned_with": ["final_validation", "qa_performance"],
    }
    assert rules.returned_by_all(subtask) == ("full_checks", "final_validation", "qa_performance")


def returned_two() -> Any:
    return {
        "sections": {
            "Full checks": "red suite notes",
            roles.PM_FINAL.name: "pm notes",
            "Tech Lead: findings": "| open |",
        },
        "subtasks": {
            INTEGRATION: {
                "status": "returned",
                "reason": "full_checks: suite red\n\nfinal_validation: spec gap",
                "returned_by": "full_checks",
                "returned_with": ["final_validation"],
            }
        },
    }


def test_return_sections_has_one_section_per_return_in_order():
    assert rules.return_sections(returned_two(), INTEGRATION) == (
        "Full checks",
        roles.PM_FINAL.name,
    )
    assert rules.return_sections({"subtasks": {}}, "api") == (rules.review_section("api"),)
    assert rules.return_section(returned_two(), INTEGRATION) == "Full checks"


def test_return_sections_refuses_a_returner_it_does_not_know():
    state: Any = {"subtasks": {"api": {"returned_by": "x"}}}
    with pytest.raises(AssertionError):
        rules.return_sections(state, "api")


def test_a_single_return_reads_byte_for_byte_as_before():
    state: Any = {
        "sections": {"QA Performance": "slow", "Tech Lead: findings": "| row |"},
        "subtasks": {
            INTEGRATION: {
                "status": "returned",
                "reason": " too slow ",
                "returned_by": "qa_performance",
            }
        },
    }
    delta = rules.returned_developer_delta(state, INTEGRATION, "agentic/GH-1", HEAD)
    head = (
        f"Branch `agentic/GH-1` is at `{HEAD}`. The checkout may have changed since your "
        "previous delivery: a review ran its checks there, a dependency may have been "
        "re-merged, or the worktree may have been recreated. Your commits and this "
        "conversation are the same; the surroundings may not be."
    )
    assert delta == (
        f"{head}\n\n## Tech Lead: findings\n\n| row |\n\n"
        "## QA Performance\n\n**Returned:** too slow\n\nslow"
    )


def test_two_returns_put_both_sections_in_the_delta_in_order():
    delta = rules.returned_developer_delta(returned_two(), INTEGRATION, "agentic/GH-1", HEAD)
    assert delta.index("## Full checks") < delta.index(f"## {roles.PM_FINAL.name}")
    assert "red suite notes" in delta and "pm notes" in delta
    assert delta.count("**Returned:**") == 1
    assert "**Returned:** full_checks: suite red\n\nfinal_validation: spec gap" in delta
    assert delta.index("**Returned:**") < delta.index("red suite notes")


# --- combined_return -------------------------------------------------------------------------


def finding_of(name: str) -> dict[str, Any]:
    return {
        "subtask": INTEGRATION,
        "version": 1,
        "location": name,
        "claim": f"{name} claim",
        "severity": "blocking",
        "scope": "plan",
        "category": "spec-behaviour",
        "status": "open",
    }


def patch_of(verifier: str, outcome: str, state: Any) -> Any:
    """What one verifier's own logic produces on `state`, in the shape the real ones do."""
    patch: dict[str, Any] = {
        "current_role": verifier,
        "runs": [{"role": verifier, "section": verifier, "version": 1, "cost_usd": 1.0}],
        "cost_usd": 1.0,
        "sections": {verifier: f"{verifier} section"},
    }
    if verifier == "full_checks":
        patch["full_checks"] = {"head": HEAD, "passed": outcome == "approved"}
    if verifier == "final_validation" and outcome == "approved":
        patch["pending_close_docs"] = {"parent": HEAD, "definitions": FILES}
    if verifier == "copy_review":
        # The review never returns: its findings are rows, an important one is fixed later.
        patch["findings"] = [{**finding_of(verifier), "severity": "important"}]
        return patch
    if outcome == "approved":
        return patch
    reason = f"{verifier} reason"
    patch["subtasks"] = {
        INTEGRATION: {
            **state["subtasks"][INTEGRATION],
            "status": "returned",
            "reason": reason,
            "returned_by": verifier,
        }
    }
    patch["findings"] = [finding_of(verifier)]
    if outcome == "returned":
        patch["changes_requested"] = {PAIRS[verifier]: 1}
        patch["change_history"] = [
            {"pair": PAIRS[verifier], "category": "spec-behaviour", "reason": reason}
        ]
    else:
        patch |= rules.logged(
            {**rules.escalation("returns", f"limit of {verifier}: {reason}")}, at=AT
        )
        patch["status"] = WAITING_ON_HUMAN
    return patch


OUTCOMES = ("approved", "returned", "escalated")
CASES = [
    (copy, *outcomes)
    for copy in (False, True)
    for outcomes in itertools.product(OUTCOMES, repeat=3)
]


def scenario(copy: bool, outcomes: tuple[str, str, str]) -> tuple[Any, Any, Any]:
    state = approved_state(pending_close_docs=None)
    named = dict(zip(("full_checks", "final_validation", "qa_performance"), outcomes, strict=True))
    patches = {name: patch_of(name, outcome, state) for name, outcome in named.items()}
    if copy:
        patches["copy_review"] = patch_of("copy_review", "approved", state)
    return state, named, patches


@pytest.mark.parametrize(("copy", "full", "final", "qa"), CASES)
def test_combined_return_over_every_combination(copy, full, final, qa):
    state, named, patches = scenario(copy, (full, final, qa))
    ordered = [name for name in ORDER if name in patches]
    returners = [name for name in ordered if named.get(name, "approved") != "approved"]
    escalators = [name for name in ordered if named.get(name) == "escalated"]

    combined = rules.combined_return(state, patches)
    after = fold(state, combined)

    # Deltas are the plain concatenation in the fixed order, whatever the mapping's own.
    assert [run["role"] for run in combined.get("runs", [])] == ordered
    assert combined["cost_usd"] == float(len(ordered))
    assert [f["location"] for f in combined.get("findings", [])] == [
        name for name in ordered if name in returners or name == "copy_review"
    ]
    assert set(combined.get("changes_requested", {})) == {
        PAIRS[name] for name in returners if named[name] == "returned"
    }

    target = after["subtasks"][INTEGRATION]
    if not returners:
        assert target["status"] == "approved"
        assert "returned_by" not in target
        assert not combined.get("escalated")
    else:
        assert target["status"] == "returned"
        assert target["returned_by"] == returners[0]
        assert target.get("returned_with", []) == returners[1:]
        if len(returners) == 1:
            assert target["reason"] == f"{returners[0]} reason"
        else:
            assert target["reason"] == "\n\n".join(f"{n}: {n} reason" for n in returners)

    if escalators:
        assert after["status"] == WAITING_ON_HUMAN
        assert after["escalation_class"] == "returns"
        assert after["escalated"].startswith(f"limit of {escalators[0]}: {escalators[0]} reason")
        for name in returners:
            assert f"{name} reason" in after["escalated"]
    else:
        assert not after.get("escalated")

    if returners:
        assert after["pending_close_docs"] is None
    elif final == "approved":
        assert after["pending_close_docs"] == {"parent": HEAD, "definitions": FILES}


@pytest.mark.parametrize(("copy", "full", "final", "qa"), CASES[:27])
def test_combined_return_ignores_the_order_the_mapping_arrived_in(copy, full, final, qa):
    state, _, patches = scenario(copy, (full, final, qa))
    expected = rules.combined_return(state, patches)
    for names in itertools.permutations(patches):
        shuffled = {name: patches[name] for name in names}
        assert rules.combined_return(state, shuffled) == expected


@pytest.mark.parametrize("verifier", ORDER)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_one_patch_comes_out_as_it_went_in(verifier, outcome):
    state = approved_state(pending_close_docs=None)
    patch = patch_of(verifier, outcome, state)
    assert rules.combined_return(state, {verifier: patch}) == patch


def test_an_empty_set_has_nothing_to_combine():
    with pytest.raises(ValueError, match="at least one"):
        rules.combined_return(approved_state(), {})


def test_an_escalating_sibling_never_discards_the_deltas_of_the_others():
    state, _, patches = scenario(True, ("approved", "approved", "escalated"))
    after = fold(state, rules.combined_return(state, patches))
    assert [run["role"] for run in after["runs"][1:]] == list(ORDER)
    assert after["cost_usd"] == 4.0
    assert set(after["sections"]) == set(ORDER)


def test_a_returner_that_did_not_reach_its_limit_keeps_its_return_count_beside_an_escalation():
    state, _, patches = scenario(False, ("returned", "approved", "escalated"))
    combined = rules.combined_return(state, patches)
    assert combined["changes_requested"] == {PAIRS["full_checks"]: 1}
    assert [e["pair"] for e in combined["change_history"]] == [PAIRS["full_checks"]]


def test_the_class_and_text_come_from_the_first_escalating_patch():
    state, _, patches = scenario(False, ("approved", "escalated", "escalated"))
    patches["qa_performance"] |= rules.escalation("ci", "qa text")
    after = fold(state, rules.combined_return(state, patches))
    assert after["escalation_class"] == "returns"
    assert after["escalated"].startswith("limit of final_validation")
    assert "qa_performance reason" in after["escalated"]


def test_an_escalation_with_no_return_still_escalates_and_keeps_the_others_deltas():
    state, _, patches = scenario(True, ("approved", "approved", "approved"))
    patches["qa_performance"] = {
        "cost_usd": 1.0,
        "status": WAITING_ON_HUMAN,
        **rules.escalation("failure", "qa crashed"),
    }
    after = fold(state, rules.combined_return(state, patches))
    assert after["escalated"] == "qa crashed"
    assert after["escalation_class"] == "failure"
    assert after["status"] == WAITING_ON_HUMAN
    assert after["subtasks"][INTEGRATION]["status"] == "approved"
    assert after["cost_usd"] == 4.0
    # PM approved with files owed and nothing returned: the files stay owed.
    assert after["pending_close_docs"] == {"parent": HEAD, "definitions": FILES}


def test_a_return_clears_a_pending_record_the_state_already_held():
    state, _, patches = scenario(False, ("approved", "approved", "returned"))
    state["pending_close_docs"] = {"parent": OTHER, "definitions": FILES}
    del patches["final_validation"]["pending_close_docs"]
    after = fold(state, rules.combined_return(state, patches))
    assert after["pending_close_docs"] is None


def test_only_final_validation_can_leave_a_pending_record():
    state, _, patches = scenario(False, ("approved", "approved", "approved"))
    del patches["final_validation"]["pending_close_docs"]
    patches["qa_performance"]["pending_close_docs"] = {"parent": HEAD, "definitions": FILES}
    assert "pending_close_docs" not in rules.combined_return(state, patches)


def test_a_set_with_no_return_leaves_no_trace_of_the_new_fields_on_the_subtask():
    state, _, patches = scenario(True, ("approved", "approved", "approved"))
    del patches["final_validation"]["pending_close_docs"]
    after = fold(state, rules.combined_return(state, patches))
    assert "returned_with" not in after["subtasks"][INTEGRATION]


# --- close_docs_committable / close_docs_stale -----------------------------------------------


def pending_state(parent: str | None, reviewed: str | None = HEAD, single: bool = False) -> Any:
    subtasks: dict[str, Any] = {"api": {"status": "approved"}}
    if single:
        if reviewed:
            subtasks["api"]["reviewed_head"] = reviewed
    else:
        subtasks[INTEGRATION] = {"status": "approved"}
        if reviewed:
            subtasks[INTEGRATION]["reviewed_head"] = reviewed
    state: dict[str, Any] = {"subtasks": subtasks}
    if parent:
        state["pending_close_docs"] = {"parent": parent, "definitions": FILES}
    return state


@pytest.mark.parametrize("single", [False, True])
def test_pending_files_are_committable_on_the_head_they_were_approved_on(single):
    state = pending_state(HEAD, single=single)
    assert rules.close_docs_committable(state)
    assert not rules.close_docs_stale(state)
    # `same_commit`: a shorter spelling of the same commit is the same commit.
    assert rules.close_docs_committable(pending_state(HEAD[:12], single=single))


@pytest.mark.parametrize("single", [False, True])
def test_pending_files_on_another_head_are_stale_never_committable(single):
    state = pending_state(OTHER, single=single)
    assert not rules.close_docs_committable(state)
    assert rules.close_docs_stale(state)


def test_a_unit_with_no_reviewed_head_makes_pending_files_stale():
    state = pending_state(HEAD, reviewed=None, single=True)
    assert not rules.close_docs_committable(state)
    assert rules.close_docs_stale(state)


def test_without_pending_files_nothing_is_committable_or_stale():
    for state in (pending_state(None), {**pending_state(HEAD), "pending_close_docs": None}):
        assert not rules.close_docs_committable(state)
        assert not rules.close_docs_stale(state)


# --- the task document -----------------------------------------------------------------------


def test_the_new_fields_round_trip_through_the_frontmatter():
    state = {
        "id": "T-9",
        "title": "Verifiers",
        "description": "d",
        "sections": {},
        "pending_close_docs": {"parent": HEAD, "definitions": FILES},
        "subtasks": returned_two()["subtasks"],
    }
    parsed = document.parse(document.render(state))
    assert parsed["pending_close_docs"] == state["pending_close_docs"]
    assert parsed["subtasks"][INTEGRATION]["returned_with"] == ["final_validation"]
    assert rules.returned_by_all(parsed["subtasks"][INTEGRATION]) == (
        "full_checks",
        "final_validation",
    )


def test_a_document_without_the_new_fields_is_unchanged_and_reads_as_before():
    state = {"id": "T-9", "title": "t", "description": "d", "sections": {}}
    for absent in (state, {**state, "pending_close_docs": None}):
        text = document.render(absent)
        assert "pending_close_docs" not in text
        assert "pending_close_docs" not in document.parse(text)
