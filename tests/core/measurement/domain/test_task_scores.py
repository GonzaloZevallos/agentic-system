"""The score set of one finished task, its identity and its time (GH-273): pure functions
over the task document, shared by the live path and the backfill."""

from datetime import UTC, datetime

import pytest

from agentic.core.measurement.domain import task_scores as ts
from agentic.core.process.domain.landing import FOLLOWUP_TITLE_PREFIX

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def run(started="2026-10-01T09:00:00+00:00", ended="2026-10-01T10:00:00+00:00", **extra):
    record = {"role": "Developer", "cost_usd": 1.0}
    if started is not None:
        record["started_at"] = started
    if ended is not None:
        record["ended_at"] = ended
    return {**record, **extra}


def document(**over):
    base = {
        "id": "GH-1",
        "title": "Add the thing",
        "type": "feature",
        "status": "ready_for_review",
        "cost_usd": 2.5,
        "changes_requested": {"a→b": 1, "c→d": 2},
        "failures": [{"cost_usd": 0.25}, {"cost_usd": 0.5}],
        "runs": [run()],
    }
    return {**base, **over}


def by_name(scores):
    return {s.name: s for s in scores}


def test_a_finished_document_gives_every_score_it_has_inputs_for():
    got = by_name(ts.task_scores(document()))
    assert set(got) == {"cost_usd", "active_h", "returns", "failed_cost_usd", "origin", "type"}
    assert got["cost_usd"].value == 2.5
    assert got["returns"].value == 3
    assert got["failed_cost_usd"].value == pytest.approx(0.75)
    assert got["type"].value == "feature"
    assert got["type"].data_type == "CATEGORICAL"
    assert got["cost_usd"].data_type == "NUMERIC"


def test_names_follow_the_closed_list_in_order():
    got = ts.task_scores(
        document(delivery_outcome="merged"),
        pr_merged_at="2026-10-02T09:00:00Z",
        issue_created_at="2026-10-01T08:00:00Z",
    )
    assert tuple(s.name for s in got) == ts.SCORE_NAMES


def test_a_score_outside_the_closed_list_is_refused():
    with pytest.raises(ValueError):
        ts.Score(name="made_up", value=1.0, data_type="NUMERIC")


def test_active_h_is_run_time_minus_queue_wait_in_hours():
    runs = [run(queue_wait_s=1800.0), run("2026-10-01T11:00:00Z", "2026-10-01T11:30:00Z")]
    got = by_name(ts.task_scores(document(runs=runs)))
    assert got["active_h"].value == pytest.approx(0.5 + 0.5)


def test_active_h_is_absent_for_a_document_with_no_timestamps():
    got = by_name(ts.task_scores(document(runs=[run(started=None, ended=None)])))
    assert "active_h" not in got


def test_active_h_is_absent_when_any_run_lacks_or_garbles_a_timestamp():
    assert "active_h" not in by_name(ts.task_scores(document(runs=[run(), run(ended=None)])))
    assert "active_h" not in by_name(ts.task_scores(document(runs=[run(started="not a date")])))
    assert "active_h" not in by_name(ts.task_scores(document(runs=[])))


def test_cycle_h_runs_from_the_issue_to_the_merge():
    got = by_name(
        ts.task_scores(
            document(delivery_outcome="merged"),
            pr_merged_at="2026-10-02T12:00:00Z",
            issue_created_at="2026-10-01T09:00:00Z",
        )
    )
    assert got["cycle_h"].value == pytest.approx(27.0)


def test_cycle_h_is_absent_for_a_closed_unmerged_pr_or_a_failed_read():
    closed = document(delivery_outcome="closed")
    assert "cycle_h" not in by_name(
        ts.task_scores(closed, pr_merged_at=None, issue_created_at="2026-10-01T09:00:00Z")
    )
    assert "cycle_h" not in by_name(
        ts.task_scores(closed, pr_merged_at="2026-10-02T12:00:00Z", issue_created_at=None)
    )


@pytest.mark.parametrize("outcome", ["merged", "merged_with_changes", "auto_merged"])
def test_a_merged_outcome_is_delivered(outcome):
    got = by_name(ts.task_scores(document(delivery_outcome=outcome)))
    assert got["delivered"].value == 1.0
    assert got["delivered"].data_type == "BOOLEAN"


def test_a_closed_outcome_is_not_delivered():
    got = by_name(ts.task_scores(document(delivery_outcome="closed")))
    assert got["delivered"].value == 0.0


def test_delivered_is_absent_without_a_delivery_outcome():
    assert "delivered" not in by_name(ts.task_scores(document()))
    assert "delivered" not in by_name(ts.task_scores(document(delivery_outcome="unheard-of")))


def test_origin_tells_a_followup_from_an_original():
    follow = by_name(ts.task_scores(document(title=f"{FOLLOWUP_TITLE_PREFIX}12: more")))
    assert follow["origin"].value == "followup"
    assert by_name(ts.task_scores(document()))["origin"].value == "original"


def test_origin_is_absent_without_a_title():
    assert "origin" not in by_name(ts.task_scores(document(title="")))
    assert "origin" not in by_name(
        ts.task_scores({k: v for k, v in document().items() if k != "title"})
    )


def test_scores_carry_the_ids_of_the_identity_they_are_given():
    who = ts.identity("shop", "GH-1", START)
    got = ts.task_scores(document(), identity=who)
    assert {s.id for s in got} == {who.score_id(s.name) for s in got}
    assert len({s.id for s in got}) == len(got)


def test_task_time_takes_the_earliest_start_and_the_latest_end_of_the_runs():
    runs = [
        run("2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z"),
        run("2026-10-01T08:00:00Z", "2026-10-01T09:30:00Z"),
        run("2026-10-02T08:00:00Z", "2026-10-02T11:00:00Z"),
    ]
    got = ts.task_time(document(runs=runs), "2026-09-01T00:00:00Z", "2026-08-01T00:00:00Z")
    assert got == ts.TaskTime(
        datetime(2026, 10, 1, 8, tzinfo=UTC), datetime(2026, 10, 2, 11, tzinfo=UTC)
    )


def test_task_time_falls_back_to_the_pr_then_the_issue_then_nothing():
    old = document(runs=[run(started=None, ended=None)])
    pr = datetime(2026, 9, 1, tzinfo=UTC)
    issue = datetime(2026, 8, 1, tzinfo=UTC)
    assert ts.task_time(old, "2026-09-01T00:00:00Z", "2026-08-01T00:00:00Z") == ts.TaskTime(pr, pr)
    assert ts.task_time(old, None, "2026-08-01T00:00:00Z") == ts.TaskTime(issue, issue)
    assert ts.task_time(old, None, None) is None
    assert ts.task_time(old, "garbage", None) is None


def test_task_time_is_always_timezone_aware():
    got = ts.task_time(
        document(runs=[run("2026-10-01T09:00:00", "2026-10-01T10:00:00")]), None, None
    )
    assert got is not None
    assert got.start.tzinfo is not None and got.end.tzinfo is not None


def test_a_fresh_rerun_has_another_identity_and_a_close_shares_the_finish_one():
    first = ts.identity("shop", "GH-1", START)
    again = ts.identity("shop", "GH-1", START)
    fresh = ts.identity("shop", "GH-1", datetime(2026, 10, 3, tzinfo=UTC))
    other = ts.identity("shop", "GH-2", START)
    assert first == again
    assert fresh.trace_id != first.trace_id
    assert other.trace_id != first.trace_id
    assert ts.identity("blog", "GH-1", START).trace_id != first.trace_id


def test_the_same_instant_in_another_offset_is_the_same_identity():
    shifted = datetime.fromisoformat("2026-10-01T11:00:00+02:00")
    assert ts.identity("shop", "GH-1", shifted) == ts.identity("shop", "GH-1", START)


def test_every_id_is_hex_of_the_right_length():
    who = ts.identity("shop", "GH-1", START)
    for value, size in [
        (who.trace_id, 32),
        (who.span_id, 16),
        (who.score_id("cost_usd"), 32),
        (who.event_id(who.score_id("cost_usd")), 32),
    ]:
        assert len(value) == size
        int(value, 16)
    assert who.score_id("cost_usd") != who.score_id("returns")
    assert who.event_id(who.score_id("cost_usd")) != who.score_id("cost_usd")


@pytest.mark.parametrize("status", ["ready_for_review", "discarded"])
def test_a_finished_status_is_scoreable(status):
    assert ts.scoreable({"status": status})


@pytest.mark.parametrize("status", ["waiting_on_human", "developing", "crashed", "open"])
def test_an_unfinished_status_is_not_scoreable(status):
    assert not ts.scoreable({"status": status})
    assert not ts.scoreable({})


def test_dimensions_come_from_the_document():
    got = ts.dimensions(
        document(
            system_version="v1.2",
            system_baseline="bl-1",
            experiment={"review_mode": "light", "skip_ephemeral": "True"},
        )
    )
    assert got == ts.Dimensions(
        release="v1.2", baseline="bl-1", arm="review_mode=light,skip_ephemeral=True"
    )


def test_dimensions_fall_back_when_the_document_is_silent():
    assert ts.dimensions(document()) == ts.Dimensions(
        release="unknown", baseline="unknown", arm="default"
    )
