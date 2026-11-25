"""The check_timeout rules (GH-1075): retake, escalation, resume key and limit, and the guards on
the categories only the system writes. Pure; no application code calls them yet."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.rules import (
    PAIR_PM_PA,
    ResumeCandidate,
    auto_resumable,
    auto_resume_limit,
    change_entry,
    check_timeout_escalation,
    check_timeout_retake,
    pause_kind,
    record_return,
    resume_exhausted,
)
from agentic.core.process.domain.task import TaskState, TimeoutFact

AT = "2026-11-24T10:00:00+00:00"


def _fact(
    check: str = "tests", head: str = "h1", workers: int | None = 4, seconds: float = 1500.0
) -> TimeoutFact:
    fact: TimeoutFact = {"head": head, "check": check, "seconds": seconds}
    if workers is not None:
        fact["workers"] = workers
    return fact


def _state(**over: Any) -> TaskState:
    return cast(TaskState, {"pause_log": [], "findings": [], **over})


def _pause(state: TaskState, timeouts: list[TimeoutFact]) -> TaskState:
    """`state` after one more check_timeout escalation, folded as the graph folds a patch."""
    patch = check_timeout_escalation(state, timeouts, "s1", at=AT)
    return cast(
        TaskState,
        {
            **state,
            "pause_log": [*state["pause_log"], *patch["pause_log"]],
            "findings": [*state["findings"], *patch["findings"]],
        },
    )


def _resumed(state: TaskState) -> TaskState:
    log = [*state["pause_log"], rules.resumed_entry(at=AT)]
    return cast(TaskState, {**state, "pause_log": log})


# -- check_timeout_retake: the truth table ------------------------------------------------------


def test_the_same_head_and_the_same_workers_escalate():
    assert not check_timeout_retake([_fact()], [_fact()], spent=False)


def test_the_same_head_with_different_workers_retakes():
    assert check_timeout_retake([_fact(workers=2)], [_fact(workers=4)], spent=False)


def test_a_different_head_retakes():
    assert check_timeout_retake([_fact(head="h2")], [_fact(head="h1")], spent=False)


def test_a_previous_fact_of_another_check_counts_as_no_previous_fact():
    assert check_timeout_retake([_fact(check="lint")], [_fact(check="tests")], spent=False)


def test_no_previous_fact_retakes():
    assert check_timeout_retake([_fact()], [], spent=False)


@pytest.mark.parametrize(
    ("current", "previous"), [(None, 4), (4, None), (None, None)], ids=["now", "before", "both"]
)
def test_unknown_workers_on_either_side_retake(current, previous):
    assert check_timeout_retake([_fact(workers=current)], [_fact(workers=previous)], spent=False)


def test_a_spent_ceiling_escalates_even_with_changed_evidence():
    assert not check_timeout_retake([_fact(head="h2")], [_fact(head="h1")], spent=True)


def test_an_empty_current_list_never_retakes_whatever_the_previous_facts_are():
    assert not check_timeout_retake([], [], spent=False)
    assert not check_timeout_retake([], [_fact()], spent=False)


def test_with_several_checks_one_that_fails_the_rule_makes_the_task_escalate():
    current = [_fact("tests", workers=2), _fact("lint", workers=4)]
    previous = [_fact("tests", workers=4), _fact("lint", workers=4)]
    assert not check_timeout_retake(current, previous, spent=False)
    assert check_timeout_retake(current[:1], previous, spent=False)


# -- check_timeout_escalation ----------------------------------------------------------------


def test_an_escalation_with_no_timeouts_is_a_programming_error():
    with pytest.raises(ValueError, match="timeout"):
        check_timeout_escalation(_state(), [], "s1", at=AT)


def test_the_pause_entry_is_ci_class_with_the_cause_and_both_lists_ordered_by_check():
    first = [_fact("tests"), _fact("lint")]
    state = _pause(_state(), first)
    patch = check_timeout_escalation(
        state, [_fact("tests", head="h2"), _fact("lint", head="h2")], "s1", at=AT
    )
    assert patch["escalation_class"] == "ci"
    assert patch["escalation_cause"] == "check_timeout"
    (entry,) = patch["pause_log"]
    assert entry["kind"] == "escalation"
    assert entry["value"] == "ci"
    assert entry["cause"] == "check_timeout"
    assert entry["at"] == AT
    assert [f["check"] for f in entry["timeouts"]] == ["lint", "tests"]
    assert [f["head"] for f in entry["timeouts"]] == ["h2", "h2"]
    assert [f["check"] for f in entry["previous_timeouts"]] == ["lint", "tests"]
    assert [f["head"] for f in entry["previous_timeouts"]] == ["h1", "h1"]


def test_the_first_timeout_has_no_previous_timeouts():
    (entry,) = check_timeout_escalation(_state(), [_fact()], "s1", at=AT)["pause_log"]
    assert entry["previous_timeouts"] == []


def test_previous_timeouts_come_from_the_latest_earlier_check_timeout_across_resumes():
    state = _state()
    for head in ("h1", "h2"):
        state = _resumed(_pause(state, [_fact(head=head)]))
    other = rules.escalation_entry("ci", AT, cause="network")
    state = cast(TaskState, {**state, "pause_log": [*state["pause_log"], other]})
    (entry,) = check_timeout_escalation(state, [_fact(head="h3")], "s1", at=AT)["pause_log"]
    assert [f["head"] for f in entry["previous_timeouts"]] == ["h2"]


def test_previous_timeouts_never_read_the_full_checks_record():
    state = _state(full_checks={"timeouts": [_fact(head="stale")]})
    (entry,) = check_timeout_escalation(state, [_fact()], "s1", at=AT)["pause_log"]
    assert entry["previous_timeouts"] == []


def test_the_patch_adds_the_timeout_rows_of_the_target_and_nothing_a_return_writes():
    patch = check_timeout_escalation(_state(), [_fact("tests"), _fact("lint")], "s1", at=AT)
    assert [(row["subtask"], row["location"]) for row in patch["findings"]] == [
        ("s1", "checks:lint"),
        ("s1", "checks:tests"),
    ]
    assert {row["category"] for row in patch["findings"]} == {"process-check-timeout"}
    assert not {"full_checks", "changes_requested", "change_history"} & patch.keys()


def test_an_open_row_of_the_target_is_not_added_twice():
    state = _pause(_state(), [_fact()])
    patch = check_timeout_escalation(state, [_fact(head="h2")], "s1", at=AT)
    assert patch["findings"] == []


def test_the_reason_lists_the_seconds_and_workers_of_both_lists():
    state = _pause(_state(), [_fact(seconds=1500.0, workers=4)])
    reason = check_timeout_escalation(state, [_fact(seconds=1800.0, workers=None)], "s1", at=AT)[
        "escalated"
    ]
    assert "1800s" in reason and "unknown workers" in reason
    assert "1500s" in reason and "4 workers" in reason


# -- auto_resumable, pause_kind ---------------------------------------------------------------


def test_a_first_check_timeout_is_retaken_and_is_an_external_pause():
    state = _pause(_state(), [_fact()])
    assert auto_resumable(state) == "check_timeout"
    assert pause_kind(state["pause_log"]) == "external"


def test_two_consecutive_timeouts_on_the_same_head_and_workers_escalate_at_once():
    state = _pause(_resumed(_pause(_state(), [_fact()])), [_fact()])
    assert "full_checks" not in state
    assert auto_resumable(state) is None
    assert pause_kind(state["pause_log"]) == "external"


def test_a_second_timeout_on_a_different_head_is_retaken_and_a_third_is_spent():
    state = _pause(_state(), [_fact(head="h1")])
    state = _pause(_resumed(state), [_fact(head="h2")])
    assert auto_resumable(state) == "check_timeout"
    assert pause_kind(state["pause_log"]) == "external"
    state = _pause(_resumed(state), [_fact(head="h3")])
    assert auto_resumable(state) is None
    assert pause_kind(state["pause_log"]) == "external"


def test_a_different_head_timeouts_after_a_spent_ceiling_stay_with_the_human():
    state = _state()
    for head in ("h1", "h2", "h3", "h4"):
        state = _pause(_resumed(state), [_fact(head=head)])
    assert auto_resumable(state) is None


def test_another_mechanical_cause_keeps_its_key_whatever_the_timeouts_before_it_were():
    state = _pause(_resumed(_pause(_state(), [_fact()])), [_fact()])
    log = [*state["pause_log"], rules.escalation_entry("ci", AT, cause="network")]
    assert auto_resumable(_state(pause_log=log)) == "network"


def test_an_entry_without_facts_is_external_but_never_retaken():
    log = [rules.escalation_entry("ci", AT, cause="check_timeout")]
    assert pause_kind(log) == "external"
    assert auto_resumable(_state(pause_log=log)) is None


# -- auto_resume_limit ------------------------------------------------------------------------


def test_the_limit_of_a_check_timeout_is_two_ahead_of_the_mechanical_cap():
    assert auto_resume_limit(_state(), "check_timeout") == 2
    assert rules.MECHANICAL_RETAKE_MAX != 2


def _candidate(prior: int) -> ResumeCandidate:
    state = _pause(_state(), [_fact()])
    return ResumeCandidate(
        issue=1,
        reason="check_timeout",
        since=datetime(2026, 11, 24, tzinfo=UTC),
        prior=prior,
        limit=auto_resume_limit(state, "check_timeout"),
    )


def test_the_poller_exhausts_the_retakes_at_the_number_spent_allows():
    assert not resume_exhausted(_candidate(prior=1))
    assert resume_exhausted(_candidate(prior=2))


def test_the_limit_fits_the_calendar_and_equals_the_retakes_spent_allows():
    assert auto_resume_limit(_state(), "check_timeout") <= len(rules.RETAKE_SCHEDULE)
    state = _state()
    retakes = 0
    for head in ("h1", "h2", "h3", "h4"):
        state = _pause(_resumed(state), [_fact(head=head)])
        retakes += auto_resumable(state) == "check_timeout"
    assert retakes == auto_resume_limit(_state(), "check_timeout")


# -- the guards on the system categories ------------------------------------------------------


def test_change_entry_refuses_a_category_that_does_not_return():
    with pytest.raises(ValueError, match="process-check-timeout"):
        change_entry(PAIR_PM_PA, "process-check-timeout", "timed out")


def test_record_return_refuses_a_category_that_does_not_return():
    with pytest.raises(ValueError, match="process-check-timeout"):
        record_return(_state(), PAIR_PM_PA, category="process-check-timeout")


def test_record_return_with_a_returning_category_or_none_counts_as_before():
    expected = {PAIR_PM_PA.key: 1}
    assert record_return(_state(), PAIR_PM_PA) == expected
    assert record_return(_state(), PAIR_PM_PA, category="process-suite-red") == expected


def test_change_entry_of_a_returning_category_is_unchanged():
    entry = change_entry(PAIR_PM_PA, "process-suite-red", "red")
    assert entry["category"] == "process-suite-red"


# -- landing ----------------------------------------------------------------------------------


def test_a_check_timeout_pause_entry_is_no_landing_ci_escalation():
    raw = {
        "escalated": "tests timed out",
        "escalation_class": "ci",
        "pause_log": [rules.escalation_entry("ci", AT, cause="check_timeout")],
        "landing": [],
    }
    assert landing.ci_escalation_entry(raw, "h1") is None
