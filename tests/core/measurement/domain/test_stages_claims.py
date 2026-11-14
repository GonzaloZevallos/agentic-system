"""The stage claims of a task's cycle: `agentic.core.measurement.domain.stages` (GH-750, part 1)."""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest

from agentic.core.measurement.domain import cycle, planning, stages
from agentic.core.measurement.domain.stages import Claim, StageUnknown
from agentic.core.process.domain import roles, rules
from agentic.core.process.domain.task import MechanicalCause

ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
CREATED, PR_CREATED, MERGED = 0, 90, 100


def at(minutes):
    """The instant `minutes` after the origin, as the ISO text a document holds."""
    return (ORIGIN + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(minutes):
    return ORIGIN + timedelta(minutes=minutes)


def run(start, end, slug="developer", **over):
    record = {"role": "Developer", "section": "s", "version": 1, "cost_usd": 0.1}
    record |= {"started_at": at(start), "ended_at": at(end)}
    if slug is not None:
        record["slug"] = slug
    return record | over


def failure(start, end, *, section="s", reason="other", slug="developer"):
    record = {
        "role": "Developer",
        "attempt": 1,
        "error": "boom",
        "cost_usd": 0.0,
        "session_id": "s",
        "reason": reason,
        "section": section,
        "started_at": at(start),
        "ended_at": at(end),
    }
    if slug is not None:
        record["slug"] = slug
    return record


def _epoch(document, failures, escalation=None, *, esc_at=25, resumed=None):
    """One `run_role` batch: the failures go to `document['failures']` and, as the same objects,
    to `pause_log` through `rules.logged`, followed by the escalation; `resumed` closes the epoch
    with `rules.resumed_entry`. No `pause_log` entry is typed by hand."""
    document.setdefault("failures", []).extend(failures)
    logged = rules.logged(escalation or {}, failures, at=at(esc_at))
    log = document.setdefault("pause_log", [])
    log.extend(logged["pause_log"])
    if resumed is not None:
        log.append(rules.resumed_entry(at=at(resumed)))
    return document


def ask(document, *, created=CREATED, pr_created=PR_CREATED, merged=MERGED):
    return stages.claims(
        document,
        issue_created_at=when(created),
        pr_created_at=when(pr_created),
        pr_merged_at=when(merged),
    )


def spans(found, rank=None, stage=None):
    return [
        (c.start, c.end)
        for c in found
        if (rank is None or c.rank == rank) and (stage is None or c.stage == stage)
    ]


def pause_claims(found):
    return [c for c in found if c.rank in (1, 2, 3)]


# --- the tables -----------------------------------------------------------------------------


def test_role_stages_is_keyed_by_every_role_slug():
    assert set(stages.ROLE_STAGES) == {r.slug for r in roles.ROLES}


def test_only_the_reflector_is_in_other():
    assert {s for s, stage in stages.ROLE_STAGES.items() if stage == "other"} == {"reflector"}


def test_stage_precedence_holds_every_stage_once():
    assert len(stages.STAGE_PRECEDENCE) == len(set(stages.STAGE_PRECEDENCE))
    assert set(stages.STAGE_PRECEDENCE) == set(get_args(stages.CycleStage))
    assert stages.STAGE_PRECEDENCE == (
        "human_wait",
        "account_limit",
        "checks",
        "landing",
        "review",
        "development",
        "plan",
        "definition",
        "queue",
        "other",
    )


def test_other_order_is_the_declaration_order_of_other_cause():
    assert stages.OTHER_ORDER == ("gap", "legacy_role", "outage_pause", "reflector")


def test_the_planning_slugs_are_the_plan_stage_less_the_seats_that_are_not_a_tasks_planning():
    plan = {s for s, stage in stages.ROLE_STAGES.items() if stage == "plan"}
    not_planning = {
        "plan_sizer",
        "pm_consultant",
        "tech_lead_ephemeral_stack",
        "tech_lead_interviewee_stack",
        "tech_lead_stack",
    }
    assert set(planning.PLANNING_SLUGS) == plan - not_planning


def test_key_orders_by_rank_then_stage_then_cause():
    def claim(rank, stage, cause=None):
        return Claim(when(0), when(1), rank, stage, cause)

    assert stages.key(claim(1, "other")) < stages.key(claim(2, "human_wait"))
    assert stages.key(claim(6, "review")) < stages.key(claim(6, "development"))
    assert stages.key(claim(3, "checks")) < stages.key(claim(3, "other", "outage_pause"))
    assert stages.key(claim(3, "other", "legacy_role")) < stages.key(
        claim(3, "other", "outage_pause")
    )
    assert stages.key(claim(3, "other")) == (3, 9, 0)
    assert stages.key(claim(3, "other", "gap")) == stages.key(claim(3, "other"))


# --- pauses ---------------------------------------------------------------------------------

HUMAN = ("human_wait", None)
ACCOUNT = ("account_limit", None)
CHECKS = ("checks", None)
OUTAGE = ("other", "outage_pause")

# (the reason of the failure that ended the run, the escalation's mechanical cause) -> the claim
# the pause makes, written by hand. `None` is no failure, or an escalation that is a judgment.
TRUTH = {
    (None, None): HUMAN,
    (None, "ci_gh_unavailable"): CHECKS,
    (None, "ci_gh_unreadable"): CHECKS,
    (None, "ci_run_unfinished"): CHECKS,
    (None, "delivery_transient"): OUTAGE,
    ("account_limit", None): ACCOUNT,
    ("account_limit", "ci_gh_unavailable"): ACCOUNT,
    ("account_limit", "ci_gh_unreadable"): ACCOUNT,
    ("account_limit", "ci_run_unfinished"): ACCOUNT,
    ("account_limit", "delivery_transient"): ACCOUNT,
    ("upstream", None): OUTAGE,
    ("upstream", "ci_gh_unavailable"): CHECKS,
    ("upstream", "ci_gh_unreadable"): CHECKS,
    ("upstream", "ci_run_unfinished"): CHECKS,
    ("upstream", "delivery_transient"): OUTAGE,
    ("network", None): OUTAGE,
    ("network", "ci_gh_unavailable"): CHECKS,
    ("network", "ci_gh_unreadable"): CHECKS,
    ("network", "ci_run_unfinished"): CHECKS,
    ("network", "delivery_transient"): OUTAGE,
    ("disk_full", None): OUTAGE,
    ("disk_full", "ci_gh_unavailable"): CHECKS,
    ("disk_full", "ci_gh_unreadable"): CHECKS,
    ("disk_full", "ci_run_unfinished"): CHECKS,
    ("disk_full", "delivery_transient"): OUTAGE,
}


def _truth_gaps(table):
    """What `table` leaves out of the reasons and causes the code knows: empty when it covers
    every `rules.EXTERNAL_REASONS` and `MechanicalCause` member (and `None` for each)."""
    reasons = {r for r, _ in table}
    causes = {c for _, c in table}
    return (
        (set(rules.EXTERNAL_REASONS) | {None}) ^ reasons,
        (set(get_args(MechanicalCause)) | {None}) ^ causes,
    )


def test_the_truth_table_covers_every_external_reason_and_mechanical_cause():
    assert _truth_gaps(TRUTH) == (set(), set())
    assert len(TRUTH) == 25


def test_the_coverage_guard_rejects_a_table_missing_a_reason_or_a_cause():
    without_network = {k: v for k, v in TRUTH.items() if k[0] != "network"}
    assert _truth_gaps(without_network) == ({"network"}, set())
    without_cause = {k: v for k, v in TRUTH.items() if k[1] != "delivery_transient"}
    assert _truth_gaps(without_cause) == (set(), {"delivery_transient"})


def _mechanical(cause):
    cls = "system_delivery" if cause == "delivery_transient" else "ci"
    return rules.escalation(cls, "text", cause=cause)


def _pause_document(reason, cause):
    document = {}
    failures = [failure(10, 20, reason=reason)] if reason else []
    if cause is not None:
        escalation = _mechanical(cause)
    elif reason is not None:
        escalation = rules.escalation("failure", "text")
    else:
        escalation = rules.escalation("questions", "text")
    return _epoch(document, failures, escalation, resumed=40)


@pytest.mark.parametrize(("reason", "cause"), sorted(TRUTH, key=str))
def test_a_pause_claims_the_stage_of_its_reason_and_cause(reason, cause):
    found = ask(_pause_document(reason, cause))
    (claim,) = pause_claims(found)
    assert (claim.stage, claim.cause) == TRUTH[(reason, cause)]
    assert claim.rank == {"human_wait": 1, "account_limit": 2}.get(claim.stage, 3)


def test_account_limit_then_a_mechanical_ci_escalation_goes_to_account_limit():
    document = _epoch(
        {}, [failure(10, 20, reason="account_limit")], _mechanical("ci_gh_unavailable"), resumed=40
    )
    (claim,) = pause_claims(ask(document))
    assert (claim.rank, claim.stage, claim.cause) == (2, "account_limit", None)


def test_account_limit_then_upstream_goes_to_other_as_outage_pause():
    failures = [
        failure(10, 15, section="a", reason="account_limit"),
        failure(15, 20, section="b", reason="upstream"),
    ]
    document = _epoch({}, failures, rules.escalation("failure", "text"), resumed=40)
    (claim,) = pause_claims(ask(document))
    assert (claim.rank, claim.stage, claim.cause) == (3, "other", "outage_pause")


def test_account_limit_then_a_human_escalation_goes_to_human_wait():
    document = _epoch(
        {},
        [failure(10, 20, reason="account_limit")],
        rules.escalation("questions", "q"),
        resumed=40,
    )
    (claim,) = pause_claims(ask(document))
    assert (claim.rank, claim.stage) == (1, "human_wait")


def test_upstream_then_ci_gh_unavailable_goes_to_checks():
    document = _epoch(
        {}, [failure(10, 20, reason="upstream")], _mechanical("ci_gh_unavailable"), resumed=40
    )
    (claim,) = pause_claims(ask(document))
    assert (claim.rank, claim.stage, claim.cause) == (3, "checks", None)


def _terminal_reasons(document):
    epoch = [e for e in document["pause_log"] if e["kind"] != "resumed"]
    return tuple(e["value"] for e in rules.terminal_failures(epoch) if e["kind"] == "failure")


def test_a_failure_the_run_retried_does_not_count_for_the_account_limit():
    # (a) one logged call, same section: the later failure supersedes the earlier.
    failures = [
        failure(10, 15, section="s", reason="other"),
        failure(15, 20, section="s", reason="account_limit"),
    ]
    document = _epoch({}, failures, rules.escalation("failure", "text"), resumed=40)
    (pause,) = cycle.pause_intervals(document).closed
    assert pause.failure_reasons == _terminal_reasons(document) == ("account_limit",)
    (claim,) = pause_claims(ask(document))
    assert claim.stage == "account_limit"


def test_the_same_failures_across_an_escalation_are_both_terminal_and_the_pause_is_human():
    # (b) two logged calls: the escalation between them closes the first batch.
    document = {}
    _epoch(document, [failure(10, 15, reason="other")], rules.escalation("failure", "text"))
    _epoch(
        document,
        [failure(16, 20, reason="account_limit")],
        rules.escalation("failure", "text"),
        esc_at=26,
        resumed=40,
    )
    (pause,) = cycle.pause_intervals(document).closed
    assert pause.failure_reasons == _terminal_reasons(document) == ("other", "account_limit")
    assert pause.kind == "human"
    (claim,) = pause_claims(ask(document))
    assert claim.stage == "human_wait"


def test_failures_with_no_section_are_never_superseded():
    # (c) an empty section stays: account_limit then upstream is an outage, not the limit.
    failures = [
        failure(10, 15, section="", reason="account_limit"),
        failure(15, 20, section="", reason="upstream"),
    ]
    document = _epoch({}, failures, rules.escalation("failure", "text"), resumed=40)
    (pause,) = cycle.pause_intervals(document).closed
    assert pause.failure_reasons == _terminal_reasons(document) == ("account_limit", "upstream")
    (claim,) = pause_claims(ask(document))
    assert (claim.stage, claim.cause) == ("other", "outage_pause")


def test_failure_reasons_follow_rules_terminal_failures(monkeypatch):
    failures = [
        failure(10, 15, section="a", reason="upstream"),
        failure(15, 20, section="b", reason="account_limit"),
    ]
    document = _epoch({}, failures, rules.escalation("failure", "text"), resumed=40)
    assert cycle.pause_intervals(document).closed[0].failure_reasons == (
        "upstream",
        "account_limit",
    )

    def first_failure_only(entries):
        kept, seen = [], False
        for entry in entries:
            if entry["kind"] == "failure":
                if seen:
                    continue
                seen = True
            kept.append(entry)
        return kept

    monkeypatch.setattr(rules, "terminal_failures", first_failure_only)
    assert cycle.pause_intervals(document).closed[0].failure_reasons == ("upstream",)


def test_a_pause_starts_where_its_failure_ended_and_the_two_claims_abut():
    document = _epoch(
        {}, [failure(10, 20, reason="upstream")], rules.escalation("failure", "text"), resumed=40
    )
    found = ask(document)
    (failed,) = [c for c in found if c.rank == 6]
    (paused,) = pause_claims(found)
    assert failed.end == paused.start == when(20)
    assert paused.end == when(40)


# --- the other claims -----------------------------------------------------------------------


def test_the_landing_claim_runs_from_the_pull_request_to_the_merge():
    assert spans(ask({}), rank=4) == [(when(90), when(100))]


def test_a_pull_request_created_before_the_issue_lands_from_the_creation():
    found = ask({}, created=10, pr_created=5)
    assert spans(found, rank=4) == [(when(10), when(100))]


def test_ci_waits_claim_checks():
    timing = {
        "pushed_at": at(10),
        "node_started_at": at(11),
        "run_started_at": at(12),
        "run_ended_at": at(20),
        "verdict": "green",
    }
    found = ask({"full_checks": {"ci_timing": timing}})
    assert spans(found, rank=5, stage="checks") == [(when(11), when(20))]


@pytest.mark.parametrize(("slug", "stage"), sorted(stages.ROLE_STAGES.items()))
def test_a_run_claims_the_stage_of_its_slug(slug, stage):
    found = ask({"runs": [run(10, 20, slug)]})
    (claim,) = [c for c in found if c.rank == 6]
    assert (claim.stage, claim.start, claim.end) == (stage, when(10), when(20))
    assert claim.cause == ("reflector" if slug == "reflector" else None)


def test_a_run_with_no_slug_claims_other_as_legacy_role():
    found = ask({"runs": [run(10, 20, slug=None)]})
    (claim,) = [c for c in found if c.rank == 6]
    assert (claim.stage, claim.cause) == ("other", "legacy_role")


def test_a_run_with_a_slug_no_table_knows_claims_legacy_role():
    (claim,) = [c for c in ask({"runs": [run(10, 20, "retired_role")]}) if c.rank == 6]
    assert (claim.stage, claim.cause) == ("other", "legacy_role")


def test_a_bounded_failure_with_no_slug_claims_legacy_role_and_never_reads_its_role():
    document = {"failures": [failure(10, 20, slug=None)]}
    (claim,) = [c for c in ask(document) if c.rank == 6]
    assert (claim.stage, claim.cause) == ("other", "legacy_role")


def test_a_failure_claims_the_stage_of_its_slug():
    document = {"failures": [failure(10, 20, slug="tech_lead_review")]}
    (claim,) = [c for c in ask(document) if c.rank == 6]
    assert claim.stage == "review"


def test_the_queue_is_the_time_before_the_first_run_and_each_runs_own_wait():
    document = {
        "runs": [run(10, 20, queue_wait_s=120), run(30, 40, queue_wait_s=60)],
    }
    found = ask(document, created=5)
    assert sorted(spans(found, rank=7)) == [
        (when(5), when(10)),
        (when(8), when(10)),
        (when(29), when(30)),
    ]


# --- clipping -------------------------------------------------------------------------------


def test_a_run_starting_before_the_issue_is_clipped_to_the_creation():
    found = ask({"runs": [run(5, 20)]}, created=10)
    assert spans(found, rank=6) == [(when(10), when(20))]
    assert spans(found, rank=7) == []


def test_a_run_ending_after_the_merge_is_clipped_to_the_merge():
    found = ask({"runs": [run(50, 120)]})
    assert spans(found, rank=6) == [(when(50), when(100))]


def test_a_queue_wait_wider_than_the_time_before_the_first_run_stops_at_the_creation():
    found = ask({"runs": [run(10, 20, queue_wait_s=3600)]})
    assert spans(found, rank=7) == [(when(0), when(10))] * 2


def test_a_first_run_after_the_merge_leaves_the_queue_and_drops_the_run():
    found = ask({"runs": [run(60, 70, queue_wait_s=600)]}, created=0, pr_created=40, merged=50)
    assert spans(found, rank=7) == [(when(0), when(50))]
    assert spans(found, rank=6) == []


@pytest.mark.parametrize("wait", [-5, "x", True, None, math.nan, math.inf, -math.inf, [3]])
def test_a_queue_wait_that_is_not_a_finite_non_negative_number_reads_as_zero(wait):
    found = ask({"runs": [run(10, 20, queue_wait_s=wait)]})
    assert spans(found, rank=7) == [(when(0), when(10))]


def test_a_queue_wait_that_is_missing_reads_as_zero():
    assert spans(ask({"runs": [run(10, 20)]}), rank=7) == [(when(0), when(10))]


def test_every_claim_lies_inside_the_issue_and_the_merge():
    document = {
        "runs": [run(-30, 5, queue_wait_s=1e12), run(95, 130)],
        "failures": [failure(-5, 3)],
    }
    found = ask(document)
    assert found
    assert all(when(0) <= c.start < c.end <= when(100) for c in found)


# --- what is unknown ------------------------------------------------------------------------

PAUSE_OPEN = _epoch({}, [failure(10, 20, reason="upstream")], rules.escalation("failure", "text"))
BAD_TIMING = {
    "pushed_at": "not a time",
    "node_started_at": at(11),
    "run_started_at": None,
    "run_ended_at": at(20),
    "verdict": "green",
}


@pytest.mark.parametrize(
    ("document", "cause"),
    [
        (PAUSE_OPEN, "pause_open"),
        # an old-style document: failures recorded before they carried their bounds
        (
            {"failures": [{"role": "Developer", "attempt": 1, "error": "x", "reason": "other"}]},
            "failure_unbounded",
        ),
        ({"failures": [failure(10, 20) | {"ended_at": "garbage"}]}, "failure_unbounded"),
        ({"failures": [failure(20, 10)]}, "failure_unbounded"),
        # an unbounded failure with a slug is still unknown, not a claim
        ({"failures": [{"slug": "developer", "reason": "other"}]}, "failure_unbounded"),
        ({"failures": "not a list"}, "failure_unbounded"),
        ({"full_checks": {"ci_timing": BAD_TIMING}}, "ci_wait_unknown"),
        ({"runs": [{"slug": "developer", "started_at": at(10)}]}, "run_unbounded"),
        ({"runs": [run(20, 10)]}, "run_unbounded"),
        ({"runs": [run(10, 20) | {"started_at": "garbage"}]}, "run_unbounded"),
        ({"runs": ["not a run"]}, "run_unbounded"),
        # the first unknown of the validation order wins
        (PAUSE_OPEN | {"failures": [{"reason": "other"}]}, "pause_open"),
        (
            {"failures": [{"reason": "other"}], "runs": [run(20, 10)]},
            "failure_unbounded",
        ),
        (
            {"full_checks": {"ci_timing": BAD_TIMING}, "runs": [run(20, 10)]},
            "ci_wait_unknown",
        ),
        # an entry with no `kind` is unreadable, and never raises
        ({"pause_log": [{"value": "x"}]}, "pause_open"),
    ],
)
def test_an_unreadable_document_is_unknown_with_the_cause(document, cause):
    assert ask(document) == StageUnknown(cause)


def test_a_missing_argument_is_unknown_with_its_own_cause():
    def asked(**kw):
        args = {"issue_created_at": when(0), "pr_created_at": when(90), "pr_merged_at": when(100)}
        return stages.claims({}, **(args | kw))

    assert asked(issue_created_at=None) == StageUnknown("no_issue_created")
    assert asked(pr_created_at=None) == StageUnknown("no_pr_created")
    assert asked(pr_merged_at=None) == StageUnknown("no_merge")


def test_a_merge_before_the_issue_or_a_pull_request_after_the_merge_is_reversed():
    assert ask({}, created=50, merged=40, pr_created=45) == StageUnknown("bounds_reversed")
    assert ask({}, pr_created=101) == StageUnknown("bounds_reversed")


def test_a_naive_and_an_aware_argument_mixed_do_not_raise():
    naive = ORIGIN.replace(tzinfo=None)
    document = {"runs": [run(10, 20)], "failures": [failure(30, 40)]}
    found = stages.claims(
        document,
        issue_created_at=naive,
        pr_created_at=when(90),
        pr_merged_at=naive + timedelta(minutes=100),
    )
    assert isinstance(found, list)
    assert found == ask(document)


def test_a_document_with_naive_instants_reads_them_as_utc():
    document = {"runs": [run(10, 20) | {"started_at": "2026-10-20T10:10:00"}]}
    found = ask(document)
    assert spans(found, rank=6) == [(when(10), when(20))]


def test_stages_reads_no_instant_by_itself():
    assert "fromisoformat" not in Path(stages.__file__).read_text()


# --- the claims against the time split ------------------------------------------------------


def test_mixed_pauses_with_retried_failures():
    document = {"runs": [run(0, 10), run(75, 85, "tech_lead_review")], "closed_at": at(100)}
    retried = [
        failure(12, 14, section="s", reason="upstream"),
        failure(14, 16, section="s", reason="upstream"),
    ]
    _epoch(document, retried, rules.escalation("failure", "text"), esc_at=16, resumed=30)
    _epoch(document, [], rules.escalation("questions", "q"), esc_at=50, resumed=70)
    assert cycle.time_split(document) == cycle.TimeSplit(
        failed_s=240.0,
        waiting_s=0.0,
        paused_external_s=840.0,
        paused_human_s=1200.0,
        unattributed_s=2520.0,
    )
    found = ask(document)
    assert isinstance(found, list)
    assert sorted(spans(found, rank=1)) == [(when(50), when(70))]
    assert sorted(spans(found, rank=3)) == [(when(16), when(30))]


def test_ci_waits():
    timing = {
        "pushed_at": at(10),
        "node_started_at": at(11),
        "run_started_at": at(12),
        "run_ended_at": at(20),
        "verdict": "green",
    }
    document = {
        "runs": [run(0, 10)],
        "closed_at": at(60),
        "full_checks": {"ci_timing": timing},
    }
    assert cycle.time_split(document) == cycle.TimeSplit(
        failed_s=0.0,
        waiting_s=540.0,
        paused_external_s=0.0,
        paused_human_s=0.0,
        unattributed_s=2460.0,
    )
    assert isinstance(ask(document, pr_created=50, merged=60), list)


def test_legacy_failures_without_bounds():
    document = {
        "runs": [run(0, 10)],
        "closed_at": at(60),
        "failures": [{"role": "Developer", "attempt": 1, "error": "x", "reason": "other"}],
    }
    assert cycle.time_split(document) == cycle.TimeSplit(None, None, None, None, None)
    assert ask(document, pr_created=50, merged=60) == StageUnknown("failure_unbounded")
