"""The score set of one finished task, its identity and its time (GH-273): pure functions
over the task document, shared by the live path and the backfill."""

from datetime import UTC, datetime
from typing import get_args

import pytest

from agentic.core.measurement.domain import task_scores as ts
from agentic.core.process.domain.landing import FOLLOWUP_TITLE_PREFIX
from agentic.core.process.domain.roles import ROLES
from agentic.core.process.domain.schemas import TaskType
from agentic.core.process.domain.task import ChangeCategory, EscalationClass, FailureReason


def flat(alias):
    """The strings of a `Literal` or of a union of `Literal`s, flattened by hand."""
    out = []
    for arg in get_args(alias):
        out.extend(flat(arg) if get_args(arg) else [arg])
    return out


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
    assert set(got) == {
        "cost_usd",
        "active_h",
        "returns",
        "failed_cost_usd",
        "origin",
        "type",
        "cost_usd.origin.original",
    }
    assert got["cost_usd"].value == 2.5
    assert got["returns"].value == 3
    assert got["failed_cost_usd"].value == pytest.approx(0.75)
    assert got["type"].value == "feature"
    assert got["type"].data_type == "CATEGORICAL"
    assert got["cost_usd"].data_type == "NUMERIC"


def test_the_base_names_follow_the_closed_list_in_order():
    got = ts.task_scores(
        document(delivery_outcome="merged"),
        pr_merged_at="2026-10-02T09:00:00Z",
        issue_created_at="2026-10-01T08:00:00Z",
    )
    names = tuple(s.name for s in got)
    assert set(ts.BASE_SCORE_NAMES) <= set(names)
    assert names == tuple(n for n in ts.SCORE_NAMES if n in names)


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


# --- GH-274: per-task score families derived from the closed domain sets -----------------


def test_every_closed_set_member_yields_a_score_name():
    expected = {
        *(f"returns.{c}" for c in flat(ChangeCategory)),
        *(f"failures.{r}" for r in flat(FailureReason)),
        *(f"escalations.{e}" for e in flat(EscalationClass)),
        *(f"cost_usd.role.{r.slug}" for r in ROLES),
        *(f"delivered_cost_usd.type.{t}" for t in flat(TaskType)),
        *(f"cost_usd.origin.{o}" for o in ("original", "followup")),
    }
    assert expected <= set(ts.SCORE_NAMES)
    assert set(ts.BASE_SCORE_NAMES) <= set(ts.SCORE_NAMES)
    assert {"delivered_cost_usd", "findings_important", "followups", "checks_red"} <= set(
        ts.SCORE_NAMES
    )
    assert len(ts.SCORE_NAMES) == len(set(ts.SCORE_NAMES))


def test_the_base_names_keep_their_order_at_the_head():
    assert ts.SCORE_NAMES[: len(ts.BASE_SCORE_NAMES)] == ts.BASE_SCORE_NAMES
    assert ts.BASE_SCORE_NAMES[0] == "cost_usd" and ts.BASE_SCORE_NAMES[-1] == "type"


def test_adding_a_member_to_a_closed_set_adds_a_name():
    members = ("a", "b")
    assert ts.family_names("returns", members) == ("returns.a", "returns.b")
    assert ts.family_names("returns", (*members, "c")) == ("returns.a", "returns.b", "returns.c")
    assert ts.FAMILIES["returns"] == ts.family_names("returns", flat(ChangeCategory))


def test_a_name_no_family_builds_is_refused():
    with pytest.raises(ValueError):
        ts.Score(name="returns.not_a_category", value=1.0, data_type="NUMERIC")
    with pytest.raises(ValueError):
        ts.Score(name="cost_usd.role.nobody", value=1.0, data_type="NUMERIC")


def history(*categories, by=None):
    return [
        {"pair": "a→b", "category": c, "reason": "r", **({"by": by} if by else {})}
        for c in categories
    ]


def test_returns_equals_the_sum_of_its_categories():
    doc = document(
        changes_requested={"a→b": 2, "c→d": 1},
        change_history=history("spec-behaviour", "spec-behaviour", "definition"),
    )
    got = by_name(ts.task_scores(doc))
    parts = {n: s.value for n, s in got.items() if n.startswith("returns.")}
    assert parts == {"returns.spec-behaviour": 2.0, "returns.definition": 1.0}
    assert got["returns"].value == sum(parts.values())
    assert "returns.definition" in ts.SCORE_NAMES


def test_a_landing_reopen_is_not_a_return_category():
    doc = document(
        changes_requested={"a→b": 1},
        change_history=history("spec-behaviour") + history("definition", by="landing"),
    )
    assert "returns.definition" not in by_name(ts.task_scores(doc))


def test_a_category_nobody_returned_is_omitted_not_zero():
    got = by_name(ts.task_scores(document(change_history=[])))
    assert not [n for n in got if n.startswith("returns.")]


def test_failures_count_by_reason_and_skip_an_unknown_reason():
    doc = document(
        failures=[
            {"reason": "account_limit", "cost_usd": 0.1},
            {"reason": "account_limit", "cost_usd": 0.1},
            {"reason": "schema", "cost_usd": 0.1},
            {"cost_usd": 0.1},
            {"reason": "from_the_future", "cost_usd": 0.1},
        ]
    )
    got = by_name(ts.task_scores(doc))
    assert got["failures.account_limit"].value == 2.0
    assert got["failures.schema"].value == 1.0
    assert {n for n in got if n.startswith("failures.")} == {
        "failures.account_limit",
        "failures.schema",
    }


def test_escalations_count_by_class_from_the_pause_log_only():
    doc = document(
        pause_log=[
            {"kind": "escalation", "value": "returns"},
            {"kind": "failure", "value": "schema", "section": "x"},
            {"kind": "resumed"},
            {"kind": "escalation", "value": "returns"},
            {"kind": "escalation", "value": "cap"},
        ]
    )
    got = by_name(ts.task_scores(doc))
    assert got["escalations.returns"].value == 2.0
    assert got["escalations.cap"].value == 1.0
    assert {n for n in got if n.startswith("escalations.")} == {
        "escalations.returns",
        "escalations.cap",
    }


def test_cost_by_role_sums_the_runs_of_a_slug():
    doc = document(
        runs=[
            run(slug="developer", cost_usd=1.0),
            run(slug="developer", cost_usd=0.5),
            run(slug="pm", cost_usd=0.25),
            run(cost_usd=9.0),
            run(slug="removed_role", cost_usd=9.0),
        ]
    )
    got = by_name(ts.task_scores(doc))
    assert got["cost_usd.role.developer"].value == 1.5
    assert got["cost_usd.role.pm"].value == 0.25
    assert len([n for n in got if n.startswith("cost_usd.role.")]) == 2


def test_delivered_cost_is_the_task_cost_only_when_delivered():
    got = by_name(ts.task_scores(document(delivery_outcome="merged")))
    assert got["delivered_cost_usd"].value == 2.5
    assert got["delivered_cost_usd.type.feature"].value == 2.5
    closed = by_name(ts.task_scores(document(delivery_outcome="closed")))
    undecided = by_name(ts.task_scores(document()))
    for absent in (closed, undecided):
        assert not [n for n in absent if n.startswith("delivered_cost_usd")]


def test_delivered_cost_by_type_needs_a_known_type():
    got = by_name(ts.task_scores(document(delivery_outcome="merged", type="mystery")))
    assert "delivered_cost_usd" in got
    assert not [n for n in got if n.startswith("delivered_cost_usd.type.")]


def test_cost_by_origin_follows_the_title():
    original = by_name(ts.task_scores(document()))
    assert original["cost_usd.origin.original"].value == 2.5
    assert "cost_usd.origin.followup" not in original
    follow = by_name(ts.task_scores(document(title=f"{FOLLOWUP_TITLE_PREFIX}12: more")))
    assert follow["cost_usd.origin.followup"].value == 2.5
    assert not [n for n in by_name(ts.task_scores(document(title=""))) if "origin." in n]


def finding(location, severity="important", subtask="s", claim="c", status="open"):
    return {
        "subtask": subtask,
        "version": 1,
        "location": location,
        "claim": claim,
        "severity": severity,
        "scope": "subtask",
        "status": status,
        "category": "spec-behaviour",
    }


def test_findings_important_counts_each_folded_important_row_once():
    doc = document(
        findings=[
            finding("a.py:1"),
            finding("a.py:1", subtask="t"),
            finding("b.py:2"),
            finding("c.py:3", severity="observation"),
        ]
    )
    assert by_name(ts.task_scores(doc))["findings_important"].value == 2.0


def test_findings_important_is_zero_with_an_empty_register_and_absent_without_one():
    assert by_name(ts.task_scores(document(findings=[])))["findings_important"].value == 0.0
    assert "findings_important" not in by_name(ts.task_scores(document()))


def test_followups_is_one_when_filed_zero_after_a_landing_and_unknown_before_it():
    assert by_name(ts.task_scores(document(followups_filed=True)))["followups"].value == 1.0
    assert by_name(ts.task_scores(document(landing=[{"step": "merge"}])))["followups"].value == 0.0
    assert "followups" not in by_name(ts.task_scores(document()))


def test_checks_red_counts_the_failed_merge_checks_and_is_absent_without_a_record():
    record = {
        "passed": False,
        "checks": [
            {"name": "tests", "passed": False, "tail": []},
            {"name": "lint", "passed": True},
            {"name": "types", "passed": False},
            "not an entry",
        ],
    }
    assert by_name(ts.task_scores(document(merge_checks=record)))["checks_red"].value == 2.0
    green = {"passed": True, "checks": [{"name": "tests", "passed": True}]}
    assert by_name(ts.task_scores(document(merge_checks=green)))["checks_red"].value == 0.0
    assert "checks_red" not in by_name(ts.task_scores(document()))


def test_a_malformed_register_reads_as_unknown_not_zero():
    got = by_name(
        ts.task_scores(
            document(findings="x", merge_checks={"checks": "x"}, pause_log=None, runs=None)
        )
    )
    assert "findings_important" not in got and "checks_red" not in got
