"""The domain types of a check that timed out (GH-1075): the reserved category, the timeout
rows and the cap of `RoleRun.timeouts`. Pure; nothing here is called by application code yet."""

from __future__ import annotations

from typing import get_args

from agentic.core.process.domain import task
from agentic.core.process.domain.task import (
    ROLE_TIMEOUTS_MAX,
    SYSTEM_CATEGORIES,
    FindingRecord,
    FindingStatus,
    TimeoutFact,
    capped_timeouts,
    returns,
    system_written,
    timeout_rows,
)


def _fact(check: str = "tests", seconds: float = 1500.0, workers: int | None = None) -> TimeoutFact:
    fact: TimeoutFact = {"head": "abc123", "check": check, "seconds": seconds}
    if workers is not None:
        fact["workers"] = workers
    return fact


def _row(
    subtask: str = "s1", location: str = "checks:tests", status: FindingStatus = "open"
) -> FindingRecord:
    return {
        "subtask": subtask,
        "version": 1,
        "location": location,
        "claim": "tests timed out after 45s with 4 workers, unverified",
        "severity": "observation",
        "scope": "plan",
        "category": "process-check-timeout",
        "status": status,
    }


def test_the_category_and_the_cause_are_in_the_closed_sets():
    assert "process-check-timeout" in get_args(task.FindingCategory)
    assert "check_timeout" in get_args(task.MechanicalCause)
    assert SYSTEM_CATEGORIES == frozenset({"process-check-timeout"})


def test_a_system_category_is_written_by_the_system_and_never_returns():
    assert system_written("process-check-timeout")
    assert not returns("process-check-timeout")


def test_an_ordinary_category_returns_and_is_not_system_written():
    assert returns("process-suite-red")
    assert not system_written("process-suite-red")


def test_one_row_per_timed_out_check_with_the_fixed_claim():
    rows = timeout_rows(
        "s1",
        [_fact("lint", 45.0), _fact("tests", 1500.0, workers=4)],
        [],
    )
    assert rows == [
        {
            "subtask": "s1",
            "version": 0,
            "location": "checks:lint",
            "claim": "lint timed out after 45s with unknown workers, unverified",
            "severity": "observation",
            "scope": "plan",
            "category": "process-check-timeout",
            "status": "open",
        },
        {
            "subtask": "s1",
            "version": 0,
            "location": "checks:tests",
            "claim": "tests timed out after 1500s with 4 workers, unverified",
            "severity": "observation",
            "scope": "plan",
            "category": "process-check-timeout",
            "status": "open",
        },
    ]


def test_the_claim_keeps_a_fractional_second():
    (row,) = timeout_rows("s1", [_fact("tests", 12.5, workers=2)], [])
    assert row["claim"] == "tests timed out after 12.5s with 2 workers, unverified"


def test_no_row_while_an_open_row_has_the_same_subtask_and_location():
    # The seconds differ (45 vs 1500): the dedup is on (subtask, location), never the claim.
    assert timeout_rows("s1", [_fact("tests", 1500.0, workers=4)], [_row()]) == []


def test_a_row_is_added_when_the_existing_one_is_fixed():
    assert len(timeout_rows("s1", [_fact()], [_row(status="fixed")])) == 1


def test_a_row_is_added_when_the_existing_one_belongs_to_another_subtask():
    assert len(timeout_rows("s1", [_fact()], [_row(subtask="s2")])) == 1


def test_a_row_is_added_when_the_open_row_is_at_another_location():
    assert len(timeout_rows("s1", [_fact("tests")], [_row(location="checks:lint")])) == 1


def test_the_same_check_twice_in_one_call_adds_one_row():
    assert len(timeout_rows("s1", [_fact(), _fact(seconds=9.0)], [])) == 1


def test_a_run_keeps_at_most_twenty_timeouts():
    facts = [_fact(f"check{n:02d}") for n in range(ROLE_TIMEOUTS_MAX + 5)]
    assert ROLE_TIMEOUTS_MAX == 20
    assert capped_timeouts(facts) == facts[:20]
    assert capped_timeouts(facts[:3]) == facts[:3]


def test_every_mechanical_cause_has_a_prompt_row_of_its_own():
    from agentic.core.process.domain.escalation_prompts import ESCALATION_PROMPTS

    causes = {cause for _, cause in ESCALATION_PROMPTS}
    assert set(get_args(task.MechanicalCause)) <= causes
    question, recommendation, options = ESCALATION_PROMPTS[("ci", "check_timeout")]
    assert question
    assert recommendation in options
    assert all(option.startswith(("agentic resume", "gh issue close")) for option in options)
