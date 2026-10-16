"""Spend-paced concurrency, the pure half (GH-177): `spend_runs`, `merged_spend`,
`trailing_spend`, `paced_launch_fits` and `poll_plan(pace=...)`."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.rules import (
    LaunchCandidate,
    PollAction,
    ResumeCandidate,
    SpendPace,
    SpendRun,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
FLOOR = rules.SPEND_PACE_FLOOR_S
WINDOW_S = rules.TRAILING_SPEND_WINDOW.total_seconds()


def at(minutes_ago: float) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def run(start_ago: float, end_ago: float, cost: float) -> SpendRun:
    return SpendRun(at(start_ago), at(end_ago), cost)


def raw(started: object, ended: object, cost: object = 1.0, **extra: object) -> dict[str, object]:
    return {"started_at": started, "ended_at": ended, "cost_usd": cost, **extra}


# --- spend_runs ---------------------------------------------------------------------


def test_spend_runs_reads_a_complete_run_and_converts_to_utc():
    runs, skipped = rules.spend_runs(
        [raw("2026-10-06T10:00:00+02:00", "2026-10-06T10:30:00+02:00", 4.5)]
    )
    assert skipped == 0
    assert runs == [SpendRun(datetime(2026, 10, 6, 8, 0, tzinfo=UTC), at(210), 4.5)]
    assert runs[0].started_at.utcoffset() == timedelta(0)
    assert runs[0].ended_at.utcoffset() == timedelta(0)


def test_spend_runs_skips_and_counts_a_run_without_a_usable_ended_at():
    good = "2026-10-06T10:00:00+00:00"
    runs, skipped = rules.spend_runs(
        [
            {"started_at": good, "cost_usd": 1.0},  # no ended_at
            raw(good, None),
            raw(good, ""),
            raw(good, "yesterday-ish"),
            raw(good, "2026-10-06T10:30:00"),  # naive
            raw(good, 12345),  # not text
        ]
    )
    assert runs == []
    assert skipped == 6


def test_spend_runs_without_a_usable_started_at_is_zero_length_at_ended_at():
    end = "2026-10-06T10:30:00+00:00"
    for started in (None, "", "garbage", "2026-10-06T10:00:00"):
        runs, skipped = rules.spend_runs([raw(started, end, 2.0)])
        assert skipped == 0
        assert runs == [SpendRun(at(90), at(90), 2.0)]
    runs, skipped = rules.spend_runs([{"ended_at": end, "cost_usd": 2.0}])
    assert (runs, skipped) == ([SpendRun(at(90), at(90), 2.0)], 0)


def test_spend_runs_started_after_ended_is_zero_length_at_ended_at():
    runs, skipped = rules.spend_runs(
        [raw("2026-10-06T11:00:00+00:00", "2026-10-06T10:30:00+00:00")]
    )
    assert skipped == 0
    assert runs[0].started_at == runs[0].ended_at == at(90)


def test_spend_runs_skips_a_cost_that_is_not_a_number():
    ok = "2026-10-06T10:00:00+00:00"
    runs, skipped = rules.spend_runs(
        [
            raw(ok, ok, "3.0"),
            raw(ok, ok, None),
            raw(ok, ok, True),
            raw(ok, ok, float("nan")),
            raw(ok, ok, float("inf")),
            raw(ok, ok, -1.0),
            {"started_at": ok, "ended_at": ok},
        ]
    )
    assert runs == []
    assert skipped == 7


def test_spend_runs_accepts_an_integer_cost():
    runs, _ = rules.spend_runs([raw(None, "2026-10-06T10:00:00+00:00", 3)])
    assert runs[0].cost_usd == 3.0


def test_spend_runs_sums_only_cost_usd():
    runs, _ = rules.spend_runs(
        [
            raw(
                "2026-10-06T10:00:00+00:00",
                "2026-10-06T10:30:00+00:00",
                5.0,
                subagent_cost_usd=100.0,
                failed_cost_usd=40.0,
            )
        ]
    )
    assert sum(r.cost_usd for r in runs) == 5.0


def test_spend_runs_failed_cost_embedded_then_resumed_v2_sums_the_two_costs():
    runs, skipped = rules.spend_runs(
        [
            raw(
                "2026-10-06T09:00:00+00:00",
                "2026-10-06T09:40:00+00:00",
                12.0,
                failed_cost_usd=7.0,
                subagent_cost_usd=3.0,
            ),
            raw("2026-10-06T10:00:00+00:00", "2026-10-06T10:20:00+00:00", 8.0, version=2),
        ]
    )
    assert skipped == 0
    assert sum(r.cost_usd for r in runs) == 20.0


# --- merged_spend -------------------------------------------------------------------


def test_merged_spend_keeps_a_published_run_absent_from_fresh():
    published = [run(60, 30, 5.0)]
    assert rules.merged_spend(published, [], NOW) == published


def test_merged_spend_counts_a_repeat_once():
    r = run(60, 30, 5.0)
    assert rules.merged_spend([r], [r], NOW) == [r]


def test_merged_spend_lets_the_fresh_cost_replace_a_published_one():
    old, new = run(60, 30, 5.0), run(60, 30, 6.5)
    assert rules.merged_spend([old], [new], NOW) == [new]


def test_merged_spend_drops_an_entry_older_than_the_window_from_both_sides():
    stale = run(400, 301, 9.0)
    kept = run(400, 299, 9.0)
    assert rules.merged_spend([stale, kept], [], NOW) == [kept]
    assert rules.merged_spend([], [stale], NOW) == []


def test_merged_spend_is_sorted_and_independent_of_input_order():
    a, b, c = run(200, 150, 1.0), run(100, 50, 2.0), run(30, 10, 3.0)
    assert rules.merged_spend([c, a], [b], NOW) == [a, b, c]
    assert rules.merged_spend([b], [c, a], NOW) == [a, b, c]


# --- trailing_spend -----------------------------------------------------------------


def test_trailing_spend_with_no_runs_is_zero_over_the_floor():
    assert rules.trailing_spend([], NOW) == (0.0, 1800.0)


def test_trailing_spend_counts_a_run_inside_the_window_whole():
    spent, covered = rules.trailing_spend([run(120, 60, 10.0)], NOW)
    assert spent == pytest.approx(10.0)
    assert covered == pytest.approx(120 * 60)


def test_trailing_spend_prorates_a_run_straddling_the_window_start():
    # 100 minutes long, the last 50 of them inside the 5 h window.
    spent, covered = rules.trailing_spend([run(350, 250, 10.0)], NOW)
    assert spent == pytest.approx(5.0)
    assert covered == pytest.approx(WINDOW_S)


def test_trailing_spend_run_ending_one_second_after_the_window_start():
    start = NOW - rules.TRAILING_SPEND_WINDOW - timedelta(minutes=59)
    end = NOW - rules.TRAILING_SPEND_WINDOW + timedelta(seconds=1)
    spent, covered = rules.trailing_spend([SpendRun(start, end, 60.0)], NOW)
    assert spent == pytest.approx(60.0 / (59 * 60 + 1))
    assert covered == pytest.approx(WINDOW_S)


def test_trailing_spend_ten_minute_burst_is_divided_by_the_floor_not_ten_minutes():
    spent, covered = rules.trailing_spend([run(10, 0, 10.0)], NOW)
    assert spent == pytest.approx(10.0)
    assert covered == FLOOR
    assert spent / (covered / 3600) == pytest.approx(20.0)


def test_trailing_spend_a_run_entirely_before_the_window_counts_zero():
    assert rules.trailing_spend([run(400, 310, 50.0)], NOW) == (0.0, FLOOR)


def test_trailing_spend_clamps_a_future_ended_at_to_now_and_counts_it():
    future = SpendRun(at(60), NOW + timedelta(hours=1), 10.0)
    spent, covered = rules.trailing_spend([future], NOW)
    assert spent == pytest.approx(5.0)
    assert covered == pytest.approx(3600)


def test_trailing_spend_zero_length_run_counts_whole_inside_and_nothing_outside():
    inside = SpendRun(at(30), at(30), 4.0)
    outside = SpendRun(at(301), at(301), 4.0)
    assert rules.trailing_spend([inside], NOW)[0] == 4.0
    assert rules.trailing_spend([outside], NOW) == (0.0, FLOOR)
    spent, covered = rules.trailing_spend([inside, outside], NOW)
    assert spent == 4.0
    assert covered == pytest.approx(30 * 60)


def test_trailing_spend_zero_length_run_in_the_future_is_clamped_to_now():
    later = NOW + timedelta(hours=1)
    spent, covered = rules.trailing_spend([SpendRun(later, later, 3.0)], NOW)
    assert spent == 3.0
    assert covered == FLOOR


def test_trailing_spend_caps_covered_time_at_the_window():
    spent, covered = rules.trailing_spend([run(299, 290, 1.0), run(10, 5, 1.0)], NOW)
    assert covered == pytest.approx(299 * 60)
    assert spent == pytest.approx(2.0)


def test_trailing_spend_covered_starts_at_the_earliest_overlapping_start():
    spent, covered = rules.trailing_spend([run(90, 80, 2.0), run(40, 20, 3.0)], NOW)
    assert spent == pytest.approx(5.0)
    assert covered == pytest.approx(90 * 60)


# --- paced_launch_fits --------------------------------------------------------------


def fits(
    *,
    spent_usd: float = 0.0,
    covered_s: float = 3600.0,
    live_runs: int = 1,
    ceiling: int = 3,
    pace_usd_per_h: float | None = 35.0,
) -> bool:
    return rules.paced_launch_fits(
        spent_usd=spent_usd,
        covered_s=covered_s,
        live_runs=live_runs,
        ceiling=ceiling,
        pace_usd_per_h=pace_usd_per_h,
    )


def test_fits_under_pace_and_under_ceiling_launches():
    assert fits(spent_usd=20.0)


def test_fits_pace_exceeded_does_not_launch():
    assert not fits(spent_usd=40.0)
    assert not fits(spent_usd=35.0)  # at the pace is not under it


def test_fits_ceiling_reached_does_not_launch_even_under_pace():
    assert not fits(live_runs=3, spent_usd=1.0)
    assert not fits(live_runs=4, spent_usd=1.0)


def test_fits_pacing_disabled_is_todays_ceiling_only_rule():
    assert fits(pace_usd_per_h=None, spent_usd=10_000.0)
    assert not fits(pace_usd_per_h=None, live_runs=3)


def test_fits_the_first_run_is_never_held_by_the_pace():
    assert fits(live_runs=0, spent_usd=10_000.0)
    assert not fits(live_runs=0, ceiling=0)


def test_fits_divides_by_covered_hours():
    assert fits(spent_usd=10.0, covered_s=1800.0)  # 20 USD/h
    assert not fits(spent_usd=20.0, covered_s=1800.0)  # 40 USD/h


# --- poll_plan(pace=...) ------------------------------------------------------------

OLD = NOW - timedelta(days=2)
L7 = PollAction("launch", 7)


def launch(issue: int) -> LaunchCandidate:
    return LaunchCandidate(issue, False, None, 0, 1)


def due_resume(issue: int) -> ResumeCandidate:
    # The first calendar slot elapsed long ago: due now.
    return ResumeCandidate(issue, "account_limit", OLD, 0, 6)


def waiting_resume(issue: int) -> ResumeCandidate:
    # Escalated just now: its next slot has not arrived, but one is still scheduled.
    return ResumeCandidate(issue, "account_limit", NOW, 0, 6)


def plan(
    *,
    closes: list[int] | None = None,
    resumes: list[ResumeCandidate] | None = None,
    launches: list[LaunchCandidate] | None = None,
    available_slots: int = 1,
    retakes: list[int] | None = None,
    pace: SpendPace | None = None,
) -> list[PollAction]:
    return rules.poll_plan(
        closes=closes or [],
        resumes=resumes or [],
        launches=[launch(7)] if launches is None else launches,
        available_slots=available_slots,
        now=NOW,
        retakes=retakes or [],
        pace=pace,
    ).actions


def paced(live: int = 1, spent: float = 0.0, covered: float = 3600.0) -> SpendPace:
    return SpendPace(spent_usd=spent, covered_s=covered, live_runs=live, pace_usd_per_h=35.0)


@pytest.mark.parametrize("slots", [0, 1, 3])
@pytest.mark.parametrize("with_resume", [False, True])
@pytest.mark.parametrize("with_retake", [False, True])
@pytest.mark.parametrize("with_wait", [False, True])
def test_poll_plan_pace_none_equals_the_call_without_pace(
    slots, with_resume, with_retake, with_wait
):
    resumes = ([due_resume(3)] if with_resume else []) + ([waiting_resume(4)] if with_wait else [])
    retakes = [5] if with_retake else []
    launches = [launch(7), launch(8)]
    without = rules.poll_plan(
        closes=[9],
        resumes=resumes,
        launches=launches,
        available_slots=slots,
        now=NOW,
        retakes=retakes,
    ).actions
    with_none = plan(
        closes=[9],
        resumes=resumes,
        launches=launches,
        available_slots=slots,
        retakes=retakes,
        pace=None,
    )
    assert with_none == without


def test_poll_plan_under_pace_and_under_ceiling_launches():
    assert plan(available_slots=2, pace=paced(live=1, spent=10.0)) == [L7]


def test_poll_plan_pace_exceeded_holds_the_launch():
    assert plan(available_slots=2, pace=paced(live=1, spent=100.0)) == []


def test_poll_plan_ceiling_reached_holds_the_launch_even_under_pace():
    assert plan(available_slots=0, pace=paced(live=1, spent=1.0)) == []


def test_poll_plan_the_first_run_is_never_held_even_with_pace_exceeded():
    assert plan(available_slots=1, pace=paced(live=0, spent=10_000.0)) == [L7]


def test_poll_plan_pace_exceeded_still_sends_a_due_resume_and_a_retake():
    got = plan(
        available_slots=3,
        resumes=[due_resume(3)],
        retakes=[2],
        pace=paced(live=1, spent=100.0),
    )
    assert got == [PollAction("retake", 2), PollAction("resume", 3)]


def test_poll_plan_pace_exceeded_still_closes():
    got = plan(closes=[1], available_slots=2, pace=paced(live=1, spent=100.0))
    assert got == [PollAction("close", 1)]


def test_poll_plan_a_retake_on_an_empty_machine_makes_the_launch_the_second_run():
    held = plan(available_slots=2, retakes=[2], pace=paced(live=0, spent=10_000.0))
    assert held == [PollAction("retake", 2)]
    allowed = plan(available_slots=2, retakes=[2], pace=paced(live=0, spent=1.0))
    assert allowed == [PollAction("retake", 2), L7]


def test_poll_plan_a_resume_on_an_empty_machine_makes_the_launch_the_second_run():
    held = plan(available_slots=2, resumes=[due_resume(3)], pace=paced(live=0, spent=10_000.0))
    assert held == [PollAction("resume", 3)]
    allowed = plan(available_slots=2, resumes=[due_resume(3)], pace=paced(live=0, spent=1.0))
    assert allowed == [PollAction("resume", 3), L7]


def test_poll_plan_a_waiting_pause_blocks_the_launch_even_under_pace():
    got = plan(available_slots=2, resumes=[waiting_resume(4)], pace=paced(live=1, spent=1.0))
    assert got == []
    first = plan(available_slots=1, resumes=[waiting_resume(4)], pace=paced(live=0))
    assert first == []


def test_poll_plan_the_ceiling_counts_slots_left_after_retakes():
    # One slot: the retake takes it, so the launch has none left, whatever the pace.
    got = plan(available_slots=1, retakes=[2], pace=paced(live=0, spent=0.0))
    assert got == [PollAction("retake", 2)]
