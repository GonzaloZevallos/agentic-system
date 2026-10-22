"""The scorecard aggregate and the verdict: `agentic.core.measurement.domain.scorecard`."""

import pytest
import yaml

from agentic.core.measurement.domain import metrics, scorecard


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


def judged(outcome, n):
    return [scorecard.task_figures(doc(closed_at="t", delivery_outcome=outcome)) for _ in range(n)]


# --- rework: `docs/product/rework.md`'s own acceptance case, moved onto the documents
# (ADR-0037: no telemetry backend left to derive it from a live reader's own samples;
# `task_figures.rework_usd` and `metrics.run_rework_usd` are the one implementation both
# `agentic close` and `agentic eval compare` read, never a second one) ---


def test_task_figures_rework_equals_the_sum_of_its_runs_own_rework_plus_the_residual():
    """A synthetic document with one return (`version=2`, all of it rework), one
    first-delivery run whose own retried attempt cost something already inside its
    `cost_usd` (`failed_cost_usd`, only that slice is rework), and a residual --
    `cost_usd` money no run accounts for at all, itself rework by `rework_cost_usd`'s
    own definition. `task_figures(document).rework_usd` must equal the document-only
    sum this test computes independently: `metrics.run_rework_usd` over every run in
    `document["runs"]`, plus whatever of `cost_usd` those runs do not add up to."""
    returned_run = {
        "role": "Tech Lead",
        "version": 2,
        "session_id": "s1",
        "model": "m",
        "cost_usd": 0.2,
        "turns": 2,
    }
    retried_run = {
        "role": "Product Analyst",
        "version": 1,
        "session_id": "s2",
        "model": "m",
        "cost_usd": 0.05,
        "turns": 1,
        "failed_cost_usd": 0.02,
    }
    document = doc(
        status="ready_for_review",
        cost_usd=0.33,  # 0.2 + 0.05 delivered by runs, 0.08 an escalation residual
        runs=[returned_run, retried_run],
    )

    figures = scorecard.task_figures(document)

    runs_rework = sum(metrics.run_rework_usd(run) for run in document["runs"])
    runs_cost = sum(run["cost_usd"] for run in document["runs"])
    residual = max(document["cost_usd"] - runs_cost, 0.0)
    expected = runs_rework + residual

    assert figures.rework_usd == pytest.approx(expected)
    # Named against the fixture's own numbers too, so a future change to
    # `rework_cost_usd`'s formula that still happens to match `runs_rework + residual`
    # by coincidence cannot pass this test silently.
    assert expected == pytest.approx(0.2 + 0.02 + 0.08)


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


def test_external_failure_reasons_excludes_account_limit_upstream_and_network():
    assert set(scorecard.EXTERNAL_FAILURE_REASONS) == {"account_limit", "upstream", "network"}


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
                {
                    "role": "R",
                    "attempt": 5,
                    "error": "e",
                    "cost_usd": 0.0,
                    "session_id": "",
                    "reason": "network",
                },
            ]
        )
    )
    built = scorecard.build([figures])
    # Two own failures (limit, sdk) over one terminated Corrida: the three external
    # ones (account_limit, upstream, network) are excluded from the numerator, never
    # from the count of terminated documents itself.
    assert built.own_failures_per_task.value == pytest.approx(2.0)
    assert built.own_failures_per_task.denominator == 1


# --- build: denominators, exclusions, n/a on an empty denominator ---


def test_build_autonomy_is_clean_merges_over_judged_deliveries():
    figures = [
        *judged("merged", 3),
        *judged("merged_with_changes", 3),
        scorecard.task_figures(doc()),  # not judged: excluded from autonomy's denominator
    ]
    built = scorecard.build(figures)
    assert built.autonomy.value == pytest.approx(0.5)
    assert built.autonomy.denominator == 6
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
            *judged("merged", 5),
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
            *judged("merged", 5),
            scorecard.task_figures(doc()),
        ]
    )
    result = scorecard.verdict(frm, "pre-baseline", to, "bl-2026-09-23")
    assert result.outcome == "improves"


def test_verdict_worse_when_autonomy_drops_and_own_failures_rise():
    frm = scorecard.build(
        [
            *judged("merged", 5),
            scorecard.task_figures(doc()),
        ]
    )
    to = scorecard.build(
        [
            *judged("closed", 5),
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
    to = scorecard.build(judged("merged", 5))
    result = scorecard.verdict(empty, "pre-baseline", to, "bl-2026-09-23")
    assert result.outcome == "undecidable"
    assert "pre-baseline" in result.reason


def test_verdict_ties_when_nothing_moves():
    same = scorecard.build(judged("merged", 5))
    result = scorecard.verdict(same, "a", same, "b")
    assert result.outcome == "ties"


# --- auto_merged (ADR-0045): landed without a human verdict ---


def test_autonomy_is_over_the_human_judged_tasks_and_leaves_the_auto_merged_out():
    built = scorecard.build(
        [*judged("merged", 3), *judged("merged_with_changes", 2), *judged("auto_merged", 3)]
    )
    assert built.autonomy.value == pytest.approx(3 / 5)
    assert built.autonomy.denominator == 5
    assert built.autonomy.excluded == 3
    assert built.judged == 5


def test_cost_per_delivery_leaves_the_auto_merged_ones_out():
    cheap = [
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged", cost_usd=2.0))
        for _ in range(5)
    ]
    dear = [
        scorecard.task_figures(doc(closed_at="t", delivery_outcome="auto_merged", cost_usd=50.0))
        for _ in range(3)
    ]
    built = scorecard.build([*cheap, *dear])
    assert built.cost_per_delivery.value == pytest.approx(2.0)
    assert built.cost_per_delivery.denominator == 5
    assert built.cost_per_delivery.excluded == 3


def test_own_failures_and_rework_still_count_the_auto_merged_ones():
    failure = {
        "role": "R",
        "attempt": 1,
        "error": "e",
        "cost_usd": 0.0,
        "session_id": "",
        "reason": "other",
    }
    clean = [scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged"))]
    auto = [
        scorecard.task_figures(
            doc(closed_at="t", delivery_outcome="auto_merged", failures=[failure, failure])
        )
    ]
    built = scorecard.build([*clean, *auto])
    assert built.own_failures_per_task.denominator == 2
    assert built.own_failures_per_task.value == pytest.approx(1.0)
    assert built.rework_per_task.denominator == 2


def test_autonomy_prints_no_rate_below_the_evaluation_set_size_but_keeps_n_over_m():
    built = scorecard.build([*judged("merged", 4), *judged("auto_merged", 3)])
    assert scorecard.AUTONOMY_MIN_JUDGED == 5
    assert built.autonomy.value is None
    assert built.autonomy.denominator == 4


def test_verdict_is_undecidable_while_autonomy_is_below_the_minimum_sample():
    thin = scorecard.build(judged("merged", 4))
    enough = scorecard.build(judged("merged", 5))
    assert scorecard.verdict(thin, "a", enough, "b").outcome == "undecidable"
    assert scorecard.verdict(enough, "a", thin, "b").outcome == "undecidable"


# --- bucket_by_baseline: every bucket, including `untagged` and `unreadable`, rejects ---


def test_bucket_by_baseline_untagged_is_its_own_bucket_not_folded_into_pre_baseline_or_unknown():
    # The rejecting input for this bucket: a document whose own `system_baseline` is
    # the literal `untagged` (`telemetry.system_baseline`'s own fact -- git answered
    # and no `bl-*` tag reaches this commit). An implementation that treats a falsy
    # check loosely, or that consults `resolve` for it, is exactly what this test
    # catches: `resolve` here raises if it is ever called.
    def resolve(sha):
        raise AssertionError("a document with its own system_baseline must not consult git")

    parsed = [("GH-1", doc(system_baseline="untagged"))]
    buckets, unreadable = scorecard.bucket_by_baseline(parsed, resolve)
    assert set(buckets) == {"untagged"}
    assert unreadable == 0


def test_bucket_by_baseline_falls_back_to_resolve_with_no_system_baseline_of_its_own():
    parsed = [("GH-1", doc(system_commit="abc1234"))]
    buckets, unreadable = scorecard.bucket_by_baseline(parsed, resolve=lambda sha: "pre-baseline")
    assert set(buckets) == {"pre-baseline"}
    assert unreadable == 0


def test_bucket_by_baseline_counts_and_excludes_an_unreadable_document():
    # The rejecting input this bucket needs: a document `document.parse` itself could
    # not read. Malformed frontmatter YAML, not merely a missing one -- `document.parse`
    # raises `yaml.YAMLError` for it (from its own unguarded `yaml.safe_load`), a
    # different exception than the plain `ValueError`/`OSError` an absent frontmatter
    # block raises; `cli._bucket_documents` must catch all three into the `None`
    # sentinel this test feeds `bucket_by_baseline` below, or a comparison ends on the
    # first half-written document instead of counting it.
    from agentic.core.process.domain import document as task_document

    malformed = "---\nid: [unterminated\n---\n\n# t\n"
    with pytest.raises(yaml.YAMLError):
        task_document.parse(malformed, "GH-2.md")

    parsed = [
        ("GH-1", doc(closed_at="t", delivery_outcome="merged")),
        None,  # `cli._bucket_documents`'s own sentinel for the document above
    ]
    buckets, unreadable = scorecard.bucket_by_baseline(parsed, resolve=lambda sha: "pre-baseline")
    assert unreadable == 1
    assert sum(len(tasks) for tasks in buckets.values()) == 1


# --- actual/estimate (ADR-0054): median, over the plan's own estimate ---


def _closed(**overrides):
    return scorecard.task_figures(doc(closed_at="t", delivery_outcome="merged", **overrides))


def _plan(cost, files):
    return {"cost_usd": cost, "files": files}


def test_task_figures_reads_the_plans_estimate_and_the_actual_files_none_when_absent():
    bare = scorecard.task_figures(doc())
    assert (bare.estimate_cost_usd, bare.estimate_files, bare.actual_files) == (None, None, None)
    figures = scorecard.task_figures(doc(estimate=_plan(40.0, 10), actual_files=12))
    assert (figures.estimate_cost_usd, figures.estimate_files, figures.actual_files) == (
        40.0,
        10,
        12,
    )


def test_the_ratio_uses_the_plans_estimate_and_never_the_revisions():
    revised = _closed(
        cost_usd=20.0,
        estimate=_plan(10.0, 5),
        estimate_revisions=[{"cost_usd": 80.0, "files": 20}],
    )

    assert scorecard.build([revised]).cost_over_estimate.value == 2.0


def test_the_figures_are_medians_not_means():
    figures = [
        _closed(cost_usd=c, estimate=_plan(10.0, 10), actual_files=f)
        for c, f in ((10.0, 10), (10.0, 10), (100.0, 100))
    ]

    card = scorecard.build(figures)

    assert card.cost_over_estimate.value == 1.0 and card.cost_over_estimate.denominator == 3
    assert card.files_over_estimate.value == 1.0 and card.files_over_estimate.denominator == 3


def test_a_document_without_actual_files_stays_out_of_the_files_figure_only():
    figures = [
        _closed(cost_usd=20.0, estimate=_plan(10.0, 5), actual_files=10),
        _closed(cost_usd=30.0, estimate=_plan(10.0, 5)),  # no actual_files
        _closed(cost_usd=99.0),  # no estimate: out of both
        scorecard.task_figures(doc(estimate=_plan(10.0, 5), actual_files=1)),  # not closed
    ]

    card = scorecard.build(figures)

    assert (card.cost_over_estimate.value, card.cost_over_estimate.denominator) == (2.5, 2)
    assert (card.files_over_estimate.value, card.files_over_estimate.denominator) == (2.0, 1)
    assert card.files_over_estimate.excluded == 3


def test_the_figures_are_n_a_when_no_closed_document_has_a_positive_estimate():
    card = scorecard.build([_closed(), _closed(estimate=_plan(0.0, 0), actual_files=3)])

    assert card.cost_over_estimate.value is None and card.cost_over_estimate.denominator == 0
    assert card.files_over_estimate.value is None and card.files_over_estimate.denominator == 0
