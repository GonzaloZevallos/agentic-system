"""The scorecard aggregate and the verdict: `agentic.core.measurement.domain.scorecard`."""

import pytest

from agentic.core.measurement.domain import scorecard


def doc(**overrides):
    base = {
        "id": "GH-10",
        "title": "t",
        "description": "d",
        "status": "ready_for_review",
        "cost_usd": 10.0,
    }
    base.update(overrides)
    return base


# --- task_figures: read from the document alone, absent fields get a documented default ---


def test_task_figures_reads_every_field_from_the_document():
    document = doc(
        cost_usd=12.5,
        failures=[
            {"role": "Developer", "attempt": 1, "error": "e", "cost_usd": 0.1, "session_id": ""}
        ],
        closed_at="2026-09-01T00:00:00.000Z",
        delivery_outcome="merged",
        human_commits="2",
        human_lines_changed="40",
        time_to_merge_seconds="3600",
        system_commit="abc1234",
        system_baseline="bl-2026-09-01",
    )
    figures = scorecard.task_figures(document)
    assert figures.cost_usd == 12.5
    assert figures.failures_by_reason == {"other": 1}
    assert figures.status == "ready_for_review"
    assert figures.closed_at == "2026-09-01T00:00:00.000Z"
    assert figures.delivery_outcome == "merged"
    assert figures.human_commits == 2
    assert figures.human_lines_changed == 40
    assert figures.time_to_merge_seconds == pytest.approx(3600.0)
    assert figures.system_commit == "abc1234"
    assert figures.system_baseline == "bl-2026-09-01"


def test_task_figures_defaults_absent_closure_fields_to_none_not_zero():
    figures = scorecard.task_figures(doc())
    assert figures.closed_at is None
    assert figures.delivery_outcome is None
    assert figures.human_commits is None
    assert figures.human_lines_changed is None
    assert figures.time_to_merge_seconds is None
    assert figures.system_commit is None
    assert figures.system_baseline is None


# --- EXTERNAL_FAILURE_REASONS: the one enumeration in this diff, with a rejecting case ---


def test_external_failure_reasons_excludes_only_account_limit_and_upstream():
    assert set(scorecard.EXTERNAL_FAILURE_REASONS) == {"account_limit", "upstream"}


def test_own_failures_excludes_the_external_reasons_but_keeps_limit_and_sdk():
    # `limit` is our own per-attempt cap (ADR-0029), never external; a scorecard build
    # that folded it into `EXTERNAL_FAILURE_REASONS` is exactly the input this test
    # rejects.
    figures = scorecard.task_figures(
        doc(
            failures=[
                {
                    "role": "R",
                    "attempt": 1,
                    "error": "e",
                    "cost_usd": 0.0,
                    "session_id": "",
                    "reason": "limit",
                },
                {
                    "role": "R",
                    "attempt": 2,
                    "error": "e",
                    "cost_usd": 0.0,
                    "session_id": "",
                    "reason": "sdk",
                },
                {
                    "role": "R",
                    "attempt": 3,
                    "error": "e",
                    "cost_usd": 0.0,
                    "session_id": "",
                    "reason": "account_limit",
                },
                {
                    "role": "R",
                    "attempt": 4,
                    "error": "e",
                    "cost_usd": 0.0,
                    "session_id": "",
                    "reason": "upstream",
                },
            ]
        )
    )
    built = scorecard.build([figures])
    # Two own failures (limit, sdk) over one terminated Corrida: the two external ones
    # (account_limit, upstream) are excluded from the numerator, never from the count
    # of terminated documents itself.
    assert built.own_failures_per_task.value == pytest.approx(2.0)
    assert built.own_failures_per_task.denominator == 1


# --- build: denominators, exclusions, n/a on an empty denominator ---


def test_build_autonomy_is_clean_merges_over_judged_deliveries():
    figures = [
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged")),
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged_with_changes")),
        scorecard.task_figures(doc()),  # not judged: excluded from autonomy's denominator
    ]
    built = scorecard.build(figures)
    assert built.autonomy.value == pytest.approx(0.5)
    assert built.autonomy.denominator == 2
    assert built.autonomy.excluded == 1


def test_build_reports_n_a_and_excludes_nothing_judged_with_an_empty_denominator():
    figures = [scorecard.task_figures(doc())]  # never closed
    built = scorecard.build(figures)
    assert built.autonomy.value is None
    assert built.autonomy.denominator == 0
    assert built.autonomy.excluded == 1
    assert built.cost_per_delivery.value is None


def test_build_cost_and_rework_are_averaged_over_their_own_denominators():
    figures = [
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged", cost_usd=10.0)),
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="closed", cost_usd=30.0)),
    ]
    built = scorecard.build(figures)
    assert built.cost_per_delivery.value == pytest.approx(20.0)
    assert built.cost_per_delivery.denominator == 2


def test_build_time_to_merge_known_counts_only_judged_deliveries_that_carry_it():
    figures = [
        scorecard.task_figures(
            doc(closed_at="t", delivery_outcome="merged", time_to_merge_seconds="120")
        ),
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="closed")),
    ]
    built = scorecard.build(figures)
    assert built.time_to_merge_known == 1
    assert built.judged == 2


# --- verdict: improves, worse, ties, undecidable (autonomy n/a) ---


def test_verdict_improves_when_autonomy_holds_and_own_failures_drop():
    frm = scorecard.build(
        [
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged")),
            scorecard.task_figures(
                doc(
                    failures=[
                        {
                            "role": "R",
                            "attempt": 1,
                            "error": "e",
                            "cost_usd": 0.0,
                            "session_id": "",
                            "reason": "other",
                        }
                    ]
                )
            ),
        ]
    )
    to = scorecard.build(
        [
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged")),
            scorecard.task_figures(doc()),
        ]
    )
    result = scorecard.verdict(frm, "pre-baseline", to, "bl-2026-09-23")
    assert result.outcome == "improves"


def test_verdict_worse_when_autonomy_drops_and_own_failures_rise():
    frm = scorecard.build(
        [
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged")),
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged")),
            scorecard.task_figures(doc()),
        ]
    )
    to = scorecard.build(
        [
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="closed")),
            scorecard.task_figures(doc(closed_at="t", delivery_outcome="closed")),
            scorecard.task_figures(
                doc(
                    failures=[
                        {
                            "role": "R",
                            "attempt": 1,
                            "error": "e",
                            "cost_usd": 0.0,
                            "session_id": "",
                            "reason": "other",
                        }
                    ]
                )
            ),
        ]
    )
    result = scorecard.verdict(frm, "pre-baseline", to, "bl-2026-09-23")
    assert result.outcome == "worse"


def test_verdict_is_undecidable_when_autonomy_has_no_judged_delivery():
    empty = scorecard.build([scorecard.task_figures(doc())])
    to = scorecard.build([scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged"))])
    result = scorecard.verdict(empty, "pre-baseline", to, "bl-2026-09-23")
    assert result.outcome == "undecidable"
    assert "pre-baseline" in result.reason


def test_verdict_ties_when_nothing_moves():
    same = scorecard.build([scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged"))])
    result = scorecard.verdict(same, "a", same, "b")
    assert result.outcome == "ties"
