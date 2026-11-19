"""The stage report (GH-752): the lines of `stage_lines` over splits, unknowns and unread rows."""

from agentic.core.measurement.domain import stage_report as sr
from agentic.core.measurement.domain.stages import (
    OTHER_ORDER,
    CycleStage,
    OtherCause,
    StageSplit,
    StageUnknown,
)

HEADER = (
    "task | queue definition plan development review checks human_wait account_limit held "
    "landing other"
)


def split(other_by_cause: dict[OtherCause, float] | None = None, **hours: float) -> StageSplit:
    """A split from hours by stage; `other` is the sum of `other_by_cause` (seconds)."""
    by_cause: dict[OtherCause, float] = dict.fromkeys(OTHER_ORDER, 0.0)
    by_cause.update(other_by_cause or {})
    stages: tuple[CycleStage, ...] = (
        "queue",
        "definition",
        "plan",
        "development",
        "review",
        "checks",
        "human_wait",
        "account_limit",
        "held",
        "landing",
        "other",
    )
    seconds: dict[CycleStage, float] = {s: hours.get(s, 0.0) * 3600.0 for s in stages}
    seconds["other"] = sum(by_cause.values())
    return StageSplit(seconds, by_cause)


def row(task_id, outcome, active_h=None):
    return sr.StageRow(task_id, outcome, active_h)


def test_hours_divides_the_seconds_by_3600_in_stage_order():
    result = sr.hours(split(development=2.0, queue=0.5))
    assert result["development"] == 2.0
    assert result["queue"] == 0.5
    assert list(result)[0] == "queue" and list(result)[-1] == "other"


def test_golden_of_a_split_an_unknown_and_each_unread_row():
    rows = [
        row("GH-2", StageUnknown("pause_open")),
        row("GH-1", split({"gap": 3600.0}, development=3.0), 2.0),
        row("GH-3", sr.Unread("unreadable")),
        row("GH-4", sr.Unread("not_finished")),
        row("GH-5", sr.Unread("no_time")),
        row("GH-6", sr.Unread("gh_failed")),
    ]
    assert sr.stage_lines(rows) == [
        HEADER,
        "GH-1 | 0.000 0.000 0.000 3.000 0.000 0.000 0.000 0.000 0.000 0.000 1.000 | other 25.0%",
        "GH-2 | unknown:pause_open",
        "GH-3 | unread:unreadable",
        "GH-4 | unread:not_finished",
        "GH-5 | unread:no_time",
        "GH-6 | unread:gh_failed",
        "unknown no_merge 0 no_issue_created 0 no_pr_created 0 pause_open 1 "
        "failure_unbounded 0 ci_wait_unknown 0 run_unbounded 0 bounds_reversed 0 hold_unknown 0",
        "unread unreadable 1 not_finished 1 no_time 1 gh_failed 1",
        "other share gap 25.0% legacy_role 0.0% outage_pause 0.0% reflector 0.0%",
        "legacy_role 0",
        "queue>active_h (unclipped) 0/1",
    ]


def test_rows_sort_naturally_by_task_id():
    lines = sr.stage_lines([row(f"GH-{n}", sr.Unread("no_time")) for n in (10, 9, 100, 2)])
    assert [line.split(" | ")[0] for line in lines[1:5]] == ["GH-2", "GH-9", "GH-10", "GH-100"]


def test_a_zero_cycle_prints_other_n_a():
    lines = sr.stage_lines([row("GH-1", split())])
    assert lines[1].endswith("| other n/a")
    assert "other share n/a" in lines


def test_empty_input_is_the_header_and_the_total_block_at_zero():
    lines = sr.stage_lines([])
    assert lines[0] == HEADER
    assert lines[1].startswith("unknown no_merge 0")
    assert lines[2] == "unread unreadable 0 not_finished 0 no_time 0 gh_failed 0"
    assert lines[3:] == ["other share n/a", "legacy_role 0", "queue>active_h (unclipped) 0/0"]


def test_queue_over_active_counts_only_known_active_hours_and_compares_in_hours():
    rows = [
        row("GH-1", split(queue=2.0), 1.0),
        row("GH-2", split(queue=1.0), 2.0),
        row("GH-3", split(queue=9.0), None),
        row("GH-4", StageUnknown("no_merge"), 0.0),
    ]
    assert sr.stage_lines(rows)[-1] == "queue>active_h (unclipped) 1/2"


def test_legacy_role_counts_the_splits_with_legacy_role_seconds():
    rows = [
        row("GH-1", split({"legacy_role": 60.0})),
        row("GH-2", split({"gap": 60.0})),
        row("GH-3", split({"legacy_role": 1.0, "reflector": 5.0})),
        row("GH-4", StageUnknown("no_merge")),
    ]
    lines = sr.stage_lines(rows)
    assert "legacy_role 2" in lines
    share = next(line for line in lines if line.startswith("other share"))
    assert share == "other share gap 47.6% legacy_role 48.4% outage_pause 0.0% reflector 4.0%"
