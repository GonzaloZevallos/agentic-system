"""The `held` stage of the cycle split: `stage_split` over documents with a holds record (GH-839,
part B1). Hand-written records and hand-written expected minutes; nothing writes the record yet."""

from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.measurement.domain import stages
from agentic.core.measurement.domain.stages import StageSplit, StageUnknown
from agentic.core.process.domain import rules

ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
CREATED, MERGED = 0, 100
TOTAL_S = 100 * 60.0


def at(minutes):
    return (ORIGIN + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(minutes):
    return ORIGIN + timedelta(minutes=minutes)


def run(start, end, **over):
    return {
        "role": "Developer",
        "slug": "developer",
        "cost_usd": 0.1,
        "started_at": at(start),
        "ended_at": at(end),
        **over,
    }


def failure(start, end, reason):
    return {
        "role": "Developer",
        "slug": "developer",
        "attempt": 1,
        "error": "boom",
        "cost_usd": 0.0,
        "session_id": "s",
        "reason": reason,
        "section": "s",
        "started_at": at(start),
        "ended_at": at(end),
    }


def pause(escalation, esc_at, resumed, failures=()):
    """A closed pause as `run_role` writes it: failures, the escalation, then the resume."""
    logged = rules.logged(escalation, list(failures), at=at(esc_at))
    logged["pause_log"].append(rules.resumed_entry(at=at(resumed)))
    return {"failures": list(failures), **logged}


def holds(*spans):
    return {"holds": {"intervals": [{"start": at(s), "end": at(e)} for s, e in spans]}}


def split(document, *, pr_created=MERGED, created=CREATED, merged=MERGED):
    return stages.stage_split(
        document,
        issue_created_at=when(created),
        pr_created_at=when(pr_created),
        pr_merged_at=when(merged),
    )


def minutes(result, stage):
    assert isinstance(result, StageSplit)
    return result.seconds[stage] / 60.0


CI_TIMING = {
    "pushed_at": at(5),
    "node_started_at": at(11),
    "run_started_at": at(12),
    "run_ended_at": at(20),
    "verdict": "green",
}

# (document, the pull request's creation, the minutes each named stage must hold). The held minutes
# and the minutes of the stage the hold competes with are both written by hand.
ROWS = {
    "human pause keeps its time": (
        {**pause(rules.escalation("questions", "q"), 20, 60), **holds((10, 70))},
        MERGED,
        {"held": 20, "human_wait": 40, "queue": 0},
    ),
    "account-limit pause keeps its time": (
        {
            **pause(rules.escalation("failure", "t"), 20, 60, [failure(15, 20, "account_limit")]),
            **holds((30, 70)),
        },
        MERGED,
        {"held": 10, "account_limit": 40, "development": 5},
    ),
    "other pause to checks keeps its time": (
        {
            **pause(rules.escalation("ci", "t", cause="ci_gh_unavailable"), 20, 60),
            **holds((10, 70)),
        },
        MERGED,
        {"held": 20, "checks": 40},
    ),
    "other pause to outage keeps its time": (
        {
            **pause(rules.escalation("system_delivery", "t", cause="delivery_transient"), 20, 60),
            **holds((10, 70)),
        },
        MERGED,
        {"held": 20, "other": 80},
    ),
    "open pull request (landing) keeps its time": (
        holds((30, 60)),
        40,
        {"held": 10, "landing": 60},
    ),
    "ci wait keeps its time": (
        {"full_checks": {"ci_timing": CI_TIMING}, **holds((5, 15))},
        MERGED,
        {"held": 6, "checks": 9},
    ),
    "run keeps its time and the gap before it is held": (
        {"runs": [run(20, 60)], **holds((10, 70))},
        MERGED,
        {"held": 20, "development": 40, "queue": 10},
    ),
    "queue_wait_s span is held and not counted twice": (
        {"runs": [run(10, 20), run(60, 70, queue_wait_s=1200)], **holds((30, 50))},
        MERGED,
        {"held": 20, "queue": 20, "development": 20},
    ),
    "gap before the first run is held": (
        {"runs": [run(60, 70)], **holds((10, 40))},
        MERGED,
        {"held": 30, "queue": 30, "development": 10},
    ),
    "overlapping holds count once": (
        holds((10, 40), (30, 60)),
        MERGED,
        {"held": 50, "queue": 0, "other": 50},
    ),
    "a hold from before creation to after the merge is clipped": (
        holds((-20, 130)),
        MERGED,
        {"held": 100, "other": 0},
    ),
    "a zero-length hold holds nothing": (
        {"runs": [run(60, 70)], **holds((30, 30))},
        MERGED,
        {"held": 0, "queue": 60},
    ),
    "no holds read": (
        {"runs": [run(60, 70)], "holds": {"intervals": []}},
        MERGED,
        {"held": 0, "queue": 60},
    ),
}


@pytest.mark.parametrize(("document", "pr_created", "expected"), ROWS.values(), ids=ROWS)
def test_a_hold_yields_to_every_stronger_claim_and_the_eleven_stages_sum_to_the_cycle(
    document, pr_created, expected
):
    result = split(document, pr_created=pr_created)
    assert isinstance(result, StageSplit)
    assert set(result.seconds) == set(get_args(stages.CycleStage)) and len(result.seconds) == 11
    assert sum(result.seconds.values()) == pytest.approx(TOTAL_S)
    for stage, expected_minutes in expected.items():
        assert minutes(result, stage) == pytest.approx(expected_minutes), stage


# --- unknown ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cause", ["unreadable", "unpaired"])
def test_an_unknown_holds_record_gives_hold_unknown_never_zero_hours(cause):
    assert split({"holds": {"unknown": cause}}) == StageUnknown("hold_unknown")


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        {},
        {"intervals": [], "unknown": "unpaired"},
        {"intervals": [{"start": at(10)}]},
        {"intervals": [{"start": at(20), "end": at(10)}]},
        {"intervals": [{"start": at(10), "end": at(20)}, "x"]},
    ],
)
def test_a_malformed_holds_record_is_unknown_not_a_dropped_interval(record):
    assert split({"holds": record}) == StageUnknown("hold_unknown")


@pytest.mark.parametrize("bound", ["start", "end"])
def test_a_holds_interval_with_a_naive_bound_is_hold_unknown_where_it_read_as_utc(bound):
    """ADR-0108: the entry that reads the record (`stage_split`, through `hold_record.read`)
    excludes the task, never counts it as zero held minutes. The same record with `Z` or an
    offset splits."""
    zoned = {"start": at(10), "end": at(40)}
    naive = {**zoned, bound: zoned[bound].removesuffix("Z")}
    assert split({"holds": {"intervals": [naive]}}) == StageUnknown("hold_unknown")
    assert split({"holds": {"intervals": [zoned, naive]}}) == StageUnknown("hold_unknown")
    offset = {"start": "2026-10-20T12:10:00+02:00", "end": "2026-10-20T12:40:00+02:00"}
    for record in (zoned, offset):
        result = split({"holds": {"intervals": [record]}})
        assert isinstance(result, StageSplit)
        assert minutes(result, "held") == pytest.approx(30)


def test_a_document_with_an_earlier_unknown_cause_keeps_it_over_a_bad_holds_record():
    bad = {"unknown": "unpaired"}
    assert split({"runs": [run(20, 10)], "holds": bad}) == StageUnknown("run_unbounded")
    open_pause = {"pause_log": rules.logged(rules.escalation("questions", "q"), [], at=at(20))}
    assert split({**open_pause, "holds": bad}) == StageUnknown("pause_open")
    assert split({"failures": [{"slug": "developer"}], "holds": bad}) == StageUnknown(
        "failure_unbounded"
    )
    assert split({"holds": bad}, merged=-5) == StageUnknown("bounds_reversed")


def test_the_hold_unknown_cause_is_a_stage_unknown_cause_after_the_others():
    assert get_args(stages.StageUnknownCause)[-1] == "hold_unknown"


# --- equivalence -----------------------------------------------------------------------------

EQUIVALENT = {
    "empty": ({}, MERGED, {"other": 100}),
    "a run": ({"runs": [run(20, 60)]}, MERGED, {"queue": 20, "development": 40, "other": 40}),
    "a human pause": (
        pause(rules.escalation("questions", "q"), 20, 60),
        MERGED,
        {"human_wait": 40, "other": 60},
    ),
    "an open pull request": (
        {"runs": [run(20, 60)]},
        70,
        {"queue": 20, "development": 40, "landing": 30, "other": 10},
    ),
    "a ci wait": (
        {"full_checks": {"ci_timing": CI_TIMING}},
        MERGED,
        {"checks": 9, "other": 91},
    ),
}


@pytest.mark.parametrize(
    ("document", "pr_created", "expected"), EQUIVALENT.values(), ids=EQUIVALENT
)
def test_a_document_without_holds_gives_the_seconds_it_always_did_and_held_is_zero(
    document, pr_created, expected
):
    result = split(document, pr_created=pr_created)
    assert isinstance(result, StageSplit)
    assert "holds" not in document
    assert result.seconds["held"] == 0.0
    for stage in get_args(stages.CycleStage):
        want = expected.get(stage, 0)
        assert minutes(result, stage) == pytest.approx(want), stage
