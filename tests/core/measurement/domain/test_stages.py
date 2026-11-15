"""The stage split of a task's cycle: `agentic.core.measurement.domain.stages.stage_split`
(GH-750, part 2). Every fixture is computed by hand, in minutes, over a cycle of 100 minutes with
the pull request open from minute 90."""

import random
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.measurement.domain import cycle, stages
from agentic.core.measurement.domain.stages import Claim, StageSplit, StageUnknown
from agentic.core.process.domain import rules

ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
CREATED, PR_CREATED, MERGED = 0, 90, 100
STAGES = get_args(stages.CycleStage)
CAUSES = get_args(stages.OtherCause)


def when(minutes):
    return ORIGIN + timedelta(minutes=minutes)


def at(minutes):
    return when(minutes).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(start, end, slug="developer", **over):
    record = {"role": "R", "section": "s", "version": 1, "cost_usd": 0.1, "slug": slug}
    return record | {"started_at": at(start), "ended_at": at(end)} | over


def failure(start, end, *, section="s", reason="other"):
    return {
        "role": "R",
        "attempt": 1,
        "error": "boom",
        "cost_usd": 0.0,
        "session_id": "s",
        "reason": reason,
        "section": section,
        "slug": "developer",
        "started_at": at(start),
        "ended_at": at(end),
    }


def timing(pushed, node, started, ended):
    return {
        "ci_timing": {
            "pushed_at": at(pushed),
            "node_started_at": at(node),
            "run_started_at": at(started),
            "run_ended_at": at(ended),
            "verdict": "green",
        }
    }


def mechanical(cause):
    return rules.escalation(
        "system_delivery" if cause == "delivery_transient" else "ci", "t", cause=cause
    )


def pause(document, failures, escalation, *, esc_at, resumed):
    """One closed pause, built only through `rules.logged` and `rules.resumed_entry`."""
    document.setdefault("failures", []).extend(failures)
    logged = rules.logged(escalation, failures, at=at(esc_at))
    document.setdefault("pause_log", []).extend(logged["pause_log"])
    document["pause_log"].append(rules.resumed_entry(at=at(resumed)))
    return document


def split(document, *, merged=when(MERGED)):
    return stages.stage_split(
        document,
        issue_created_at=when(CREATED),
        pr_created_at=when(PR_CREATED),
        pr_merged_at=merged,
    )


def assert_split(document, expected, causes=None):
    """`expected` is minutes per stage (absent is 0), `causes` minutes per `other` cause."""
    causes = causes or {}
    found = split(document)
    assert isinstance(found, StageSplit)
    assert set(found.seconds) == set(STAGES)
    assert set(found.other_by_cause) == set(CAUSES)
    for stage in STAGES:
        want = sum(causes.values()) if stage == "other" else expected.get(stage, 0)
        assert found.seconds[stage] == pytest.approx(want * 60), stage
    for cause in CAUSES:
        assert found.other_by_cause[cause] == pytest.approx(causes.get(cause, 0) * 60), cause
    assert sum(found.seconds.values()) == pytest.approx((MERGED - CREATED) * 60, abs=1)
    return found


# --- fixtures, by hand ------------------------------------------------------------------------


def test_two_parallel_developers_cover_development_until_the_later_end():
    document = {"runs": [run(10, 40), run(20, 60)]}
    assert_split(
        document, {"queue": 10, "development": 50, "landing": 10}, {"gap": 30}
    )  # 0-10 queue, 10-60 dev, 60-90 gap, 90-100 landing


def test_a_ci_wait_beats_the_run_it_overlaps():
    document = {"runs": [run(5, 30)], "full_checks": timing(10, 11, 12, 20)}
    assert_split(  # the wait is 11-20
        document,
        {"queue": 5, "development": 6 + 10, "checks": 9, "landing": 10},
        {"gap": 60},
    )


def test_an_escalation_and_resume_is_human_wait():
    document = pause(
        {"runs": [run(5, 30)]}, [], rules.escalation("questions", "q"), esc_at=30, resumed=50
    )
    assert_split(
        document, {"queue": 5, "development": 25, "human_wait": 20, "landing": 10}, {"gap": 40}
    )


def test_an_account_limit_pause_runs_from_where_its_failure_ended():
    document = {"runs": [run(0, 10)]}
    failures = [failure(10, 20, reason="account_limit")]
    pause(document, failures, rules.escalation("failure", "t"), esc_at=25, resumed=40)
    assert_split(  # the failure is development 10-20, the pause 20-40
        document, {"development": 20, "account_limit": 20, "landing": 10}, {"gap": 50}
    )


def test_a_landing_holds_the_runs_of_the_open_pull_request():
    document = {"runs": [run(5, 30), run(92, 96), run(95, 99, "tech_lead_review")]}
    assert_split(document, {"queue": 5, "development": 25, "landing": 10}, {"gap": 60})


def test_a_slot_wait_is_queue_before_the_run_it_delayed():
    document = {"runs": [run(5, 15), run(40, 60, queue_wait_s=600)]}
    assert_split(  # queue 0-5 and the wait 30-40; gap 15-30 and 60-90
        document,
        {"queue": 5 + 10, "development": 10 + 20, "landing": 10},
        {"gap": 15 + 30},
    )


def test_account_limit_then_upstream_is_other_as_outage_pause():
    document = {}
    failures = [
        failure(10, 15, section="a", reason="account_limit"),
        failure(15, 20, section="b", reason="upstream"),
    ]
    pause(document, failures, rules.escalation("failure", "t"), esc_at=25, resumed=40)
    assert_split(document, {"development": 10, "landing": 10}, {"gap": 60, "outage_pause": 20})


def test_account_limit_then_a_human_escalation_is_human_wait():
    document = {}
    failures = [failure(10, 20, reason="account_limit")]
    pause(document, failures, rules.escalation("questions", "q"), esc_at=25, resumed=40)
    assert_split(document, {"development": 10, "human_wait": 20, "landing": 10}, {"gap": 60})


def test_an_outage_pause_is_other_and_delivery_transient_is_one():
    document = pause(
        {"runs": [run(5, 30)]}, [], mechanical("delivery_transient"), esc_at=30, resumed=50
    )
    assert_split(
        document, {"queue": 5, "development": 25, "landing": 10}, {"gap": 40, "outage_pause": 20}
    )


def test_a_developer_and_a_review_over_the_same_minutes_go_to_review():
    document = {"runs": [run(10, 40), run(30, 50, "tech_lead_review")]}
    assert_split(  # development 10-30, review 30-50
        document, {"queue": 10, "development": 20, "review": 20, "landing": 10}, {"gap": 40}
    )


def test_a_ci_pause_in_the_open_pull_request_is_checks_and_a_merge_ci_wait_is_landing():
    document = {"merge_ci": timing(96, 97, 98, 99)}
    pause(document, [], mechanical("ci_gh_unavailable"), esc_at=92, resumed=96)
    assert_split(document, {"checks": 4, "landing": 6}, {"gap": 90})


def test_a_naive_merge_instant_is_read_as_utc_and_does_not_raise():
    naive = when(MERGED).replace(tzinfo=None)
    found = split({"runs": [run(10, 20)]}, merged=naive)
    assert found == split({"runs": [run(10, 20)]})
    assert isinstance(found, StageSplit)


def test_an_unreadable_pause_is_unknown_and_never_raises():
    document = pause({}, [], rules.escalation("questions", "q"), esc_at=30, resumed=50)
    del document["pause_log"][0]["kind"]
    assert split(document) == StageUnknown("pause_open")


@pytest.mark.parametrize(("merged", "cause"), [(None, "no_merge"), (when(-5), "bounds_reversed")])
def test_a_stage_unknown_from_the_claims_passes_through_unchanged(merged, cause):
    document = {"runs": [run(10, 20)]}
    assert split(document, merged=merged) == StageUnknown(cause)


def test_the_human_pauses_add_up_to_cycle_human_wait_seconds():
    document = {"runs": [run(5, 30)]}
    pause(document, [], rules.escalation("questions", "q"), esc_at=30, resumed=40)
    pause(document, [], rules.escalation("questions", "q2"), esc_at=50, resumed=65)
    waited = cycle.human_wait_seconds(document)
    found = split(document)
    assert isinstance(found, StageSplit) and waited is not None
    assert sum(waited.values()) == found.seconds["human_wait"] == (10 + 15) * 60


def test_the_split_does_not_depend_on_the_order_of_runs_and_failures():
    base = {
        "runs": [run(10, 40), run(20, 60), run(30, 50, "tech_lead_review"), run(70, 80, "pm")],
        "failures": [failure(1, 3), failure(2, 9, section="b"), failure(60, 65)],
    }
    want = split(base)
    rng = random.Random(7)
    for _ in range(20):
        shuffled = {name: rng.sample(records, len(records)) for name, records in base.items()}
        assert split(shuffled) == want


# --- the property test --------------------------------------------------------------------------

# (rank, stage, cause): a small pool, so keys repeat on purpose.
POOL = [
    (1, "human_wait", None),
    (2, "account_limit", None),
    (3, "checks", None),
    (3, "other", "outage_pause"),
    (4, "landing", None),
    (5, "checks", None),
    (6, "development", None),
    (6, "review", None),
    (6, "plan", None),
    (6, "other", "legacy_role"),
    (6, "other", "reflector"),
    (7, "queue", None),
]
ROLE_STAGES = {"landing", "queue", "development", "plan", "definition", "review"}


def random_claims(rng, length):
    """Claims over `[0, length]` seconds on integer instants, some empty, some touching."""
    found = []
    for _ in range(rng.randint(0, 12)):
        start = rng.randint(0, length)
        end = min(length, start + rng.choice([0, 0, 1, 5, 20, length]))
        rank, stage, cause = rng.choice(POOL)
        found.append(
            Claim(
                ORIGIN + timedelta(seconds=start),
                ORIGIN + timedelta(seconds=end),
                rank,
                stage,
                cause,
            )
        )
    return found


def oracle(found, length):
    """The owner of every second by brute force: the covering claim with the smallest key."""
    owners = []
    for second in range(length):
        lo, hi = ORIGIN + timedelta(seconds=second), ORIGIN + timedelta(seconds=second + 1)
        covering = [c for c in found if c.start <= lo and hi <= c.end]
        owners.append(min(covering, key=stages.key) if covering else None)
    return owners


@pytest.mark.parametrize("seed", range(300))
def test_the_sweep_matches_a_one_second_oracle(seed, monkeypatch):
    rng = random.Random(seed)
    length = rng.randint(1, 120)
    found = random_claims(rng, length)

    def run_split(claim_list):
        monkeypatch.setattr(stages, "claims", lambda document, **_: list(claim_list))
        return stages.stage_split(
            {},
            issue_created_at=ORIGIN,
            pr_created_at=ORIGIN,
            pr_merged_at=ORIGIN + timedelta(seconds=length),
        )

    result = run_split(found)
    assert isinstance(result, StageSplit)
    owners = oracle(found, length)
    want = dict.fromkeys(STAGES, 0.0)
    want_causes = dict.fromkeys(CAUSES, 0.0)
    for owner in owners:
        if owner is None or owner.stage == "other":
            want_causes[(owner.cause if owner else None) or "gap"] += 1
        else:
            want[owner.stage] += 1
    want["other"] = sum(want_causes.values())
    assert result.seconds == want
    assert result.other_by_cause == want_causes
    assert sum(result.seconds.values()) == length
    assert min(result.seconds.values()) >= 0
    assert min(result.other_by_cause.values()) >= 0
    # no second under a pause (rank 1-3) is landing, queue or a role stage
    free = [t for t, o in enumerate(owners) if not (o and o.rank <= 3)]
    for stage in ROLE_STAGES:
        covered = sum(
            1
            for t in free
            if any(
                c.stage == stage
                and c.start <= ORIGIN + timedelta(seconds=t)
                and ORIGIN + timedelta(seconds=t + 1) <= c.end
                for c in found
            )
        )
        assert result.seconds[stage] <= covered
    shuffled = rng.sample(found, len(found))
    assert run_split(shuffled) == result
