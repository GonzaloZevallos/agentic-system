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


def test_external_failure_reasons_excludes_account_limit_upstream_network_and_disk_full():
    assert set(scorecard.EXTERNAL_FAILURE_REASONS) == {
        "account_limit",
        "upstream",
        "network",
        "disk_full",
    }


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


def _failure(reason: str | None, attempt: int = 1) -> dict:
    failure = {"role": "R", "attempt": attempt, "error": "e", "cost_usd": 0.0, "session_id": ""}
    return {**failure, "reason": reason} if reason is not None else failure


def test_a_disk_full_failure_is_not_an_own_failure():
    # GH-391: the machine's disk, not the role, broke the attempt. The same document with a
    # `schema` failure instead does count, which is the input this test rejects.
    full = scorecard.build([scorecard.task_figures(doc(failures=[_failure("disk_full")]))])
    own = scorecard.build([scorecard.task_figures(doc(failures=[_failure("schema")]))])
    assert full.own_failures_per_task.value == pytest.approx(0.0)
    assert own.own_failures_per_task.value == pytest.approx(1.0)


def test_own_failures_of_a_document_with_no_disk_full_are_what_they_were():
    # Documents written before GH-391 have no `disk_full`: their count is unchanged.
    legacy = doc(failures=[_failure("limit"), _failure(None, 2), _failure("upstream", 3)])
    built = scorecard.build([scorecard.task_figures(legacy)])
    assert built.own_failures_per_task.value == pytest.approx(2.0)


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


def test_a_task_closed_by_its_slices_counts_as_no_delivery():
    closed = scorecard.task_figures(doc(closed_at="t", closed_by_slices=["https://x/pull/3"]))
    assert closed.closed_at is None and closed.delivery_outcome is None
    built = scorecard.build([*judged("merged", 2), closed])
    assert built.autonomy.denominator == 2


def test_a_tracker_only_document_does_not_crash_the_scorecard_and_counts_as_excluded():
    """GH-336: `tracker_only` is outside `SCOREABLE_STATUSES` and `TERMINATED_STATUSES`,
    the same as `split` (ADR-0062: a known gap); it is counted in `excluded`."""
    ended = scorecard.task_figures(
        doc(id="GH-307", status="tracker_only", tracker_checklist=["Close #12"])
    )
    card = scorecard.build([ended, *judged("merged", 1)])
    assert card.cost_per_delivery.denominator == 1 and card.cost_per_delivery.excluded == 1
    assert card.own_failures_per_task.excluded == 1


# --- the cycle split (GH-467): `cycle.time_split` computes, the scorecard only medians ---


def _at(clock: str) -> str:
    return f"2026-10-20T{clock}.000Z"


def _timed_document(*, pause_log=None, full_checks=None):
    """A 10:00-14:00 cycle: 1 h of run, a 30 min failure, 30 min of CI wait (12:00-12:30) and a
    1 h external pause that starts at the failure's end and closes at 12:30."""
    run = {
        "role": "Developer",
        "slug": "developer",
        "section": "Developer: a",
        "version": 1,
        "cost_usd": 1.0,
        "started_at": _at("10:00:00"),
        "ended_at": _at("11:00:00"),
    }
    failure = {
        "role": "Developer",
        "attempt": 1,
        "error": "e",
        "cost_usd": 0.0,
        "session_id": "",
        "reason": "account_limit",
        "section": "Developer: a",
        "started_at": _at("11:00:00"),
        "ended_at": _at("11:30:00"),
    }
    timing = {
        "pushed_at": _at("12:00:00"),
        "node_started_at": _at("12:00:00"),
        "run_started_at": _at("12:00:00"),
        "run_ended_at": _at("12:30:00"),
        "verdict": "green",
    }
    default_pause_log = [
        {
            "kind": "failure",
            "value": "account_limit",
            "section": "Developer: a",
            "at": _at("11:30:00"),
        },
        {"kind": "resumed", "value": "resumed", "section": "Developer: a", "at": _at("12:30:00")},
    ]
    return doc(
        closed_at=_at("14:00:00"),
        runs=[run],
        failures=[failure],
        full_checks={"ci_timing": timing} if full_checks is None else full_checks,
        pause_log=default_pause_log if pause_log is None else pause_log,
    )


def test_task_figures_carries_the_split_and_paused_is_external_plus_human():
    figures = scorecard.task_figures(_timed_document())

    assert figures.failed_s == pytest.approx(1800)
    # The CI wait sits inside the pause, and waiting is subtracted before paused: it counts once.
    assert figures.waiting_s == pytest.approx(1800)
    assert figures.paused_external_s == pytest.approx(1800)
    assert figures.paused_human_s == 0
    assert figures.paused_s == figures.paused_external_s
    cycle_s = figures.cycle_s or 0.0
    parts = [figures.active_s, figures.failed_s, figures.waiting_s, figures.paused_s]
    assert figures.unattributed_s == pytest.approx(cycle_s - sum(p or 0.0 for p in parts))
    assert figures.unattributed_s == pytest.approx(5400)


def test_build_medians_each_split_figure_in_hours_over_the_tasks_where_it_is_known():
    known = scorecard.task_figures(_timed_document())
    open_pause = scorecard.task_figures(
        _timed_document(
            pause_log=[{"kind": "failure", "value": "other", "section": "s", "at": _at("11:30:00")}]
        )
    )
    unreadable = scorecard.task_figures(doc())  # no run: no cycle, nothing known
    card = scorecard.build([known, open_pause, unreadable])

    assert (card.failed_h.value, card.failed_h.denominator, card.failed_h.excluded) == (0.5, 2, 1)
    assert (card.paused_h.denominator, card.paused_h.excluded) == (
        1,
        2,
    )  # the open pause leaves it out
    assert card.paused_external_h.value == pytest.approx((known.paused_external_s or 0) / 3600)
    assert (card.unattributed_h.denominator, card.unattributed_h.excluded) == (1, 2)
    assert card.waiting_h.denominator == 2


def test_the_split_figures_never_move_the_verdict():
    base = judged("merged", 5)
    with_split = scorecard.build([*base, scorecard.task_figures(_timed_document())])
    # The same task with only the split's own fields removed: the figures differ, the verdict not.
    stripped = scorecard.build(
        [
            *base,
            scorecard.task_figures(_timed_document(pause_log=[{"kind": "lapse", "value": "x"}])),
        ]
    )
    assert with_split.paused_h != stripped.paused_h or with_split.waiting_h != stripped.waiting_h
    for frm, to in ((with_split, stripped), (stripped, with_split), (with_split, with_split)):
        got = scorecard.verdict(frm, "a", to, "b")
        assert (got.outcome, got.reason) == ("ties", "no figure moved enough to decide")


def test_a_document_without_the_split_keys_reads_the_same_as_one_with_no_events():
    """The system writes `failures`, `pause_log` and `ci_timing` only when they hold something, so
    a document from before GH-467 and a task with no failure, wait or pause are the same shape:
    both read as zero failed, waiting and paused time, never as unknown. `n/a` is for a figure
    whose own instants are missing or whose pause is open."""
    old = _timed_document(pause_log=[{"kind": "lapse", "value": "x"}], full_checks={})
    old.pop("failures")
    quiet = {**old, "failures": []}
    for key in ("pause_log", "full_checks"):
        old.pop(key), quiet.pop(key)
    a, b = scorecard.task_figures(old), scorecard.task_figures(quiet)

    assert (a.failed_s, a.waiting_s, a.paused_s) == (0.0, 0.0, 0.0)
    assert (a.failed_s, a.waiting_s, a.paused_s, a.unattributed_s) == (
        b.failed_s,
        b.waiting_s,
        b.paused_s,
        b.unattributed_s,
    )
    assert a.unattributed_s == pytest.approx(10800)


def test_the_ratio_reads_the_declared_figure_when_the_system_raised_the_estimate():
    raised = _closed(
        cost_usd=20.0,
        estimate={"cost_usd": 40.0, "declared_usd": 10.0, "files": 5},
        actual_files=5,
    )
    assert raised.estimate_cost_usd == 10.0
    assert scorecard.build([raised]).cost_over_estimate.value == 2.0
    assert _closed(estimate=_plan(40.0, 5)).estimate_cost_usd == 40.0


def _sizes(planned, actual):
    from agentic.core.process.domain import size_budget

    return {
        **size_budget.PlanSize(*planned).to_frontmatter(),
        **size_budget.ActualSize(*actual).to_frontmatter(),
    }


def test_the_scorecard_reports_the_median_divergence_of_the_hand_written_size_outside_the_verdict():
    card = scorecard.build(
        [
            _closed(**_sizes((10, 100, 0), (20, 100, 0, 0))),
            _closed(**_sizes((10, 100, 0), (10, 300, 0, 0))),
            _closed(**_sizes((0, 0, 0), (4, 50, 0, 0))),  # planned nothing: no ratio, excluded
            _closed(),  # a document from before the keys
        ]
    )

    assert (card.hand_files_over_plan.value, card.hand_files_over_plan.denominator) == (1.5, 2)
    assert (card.hand_lines_over_plan.value, card.hand_lines_over_plan.denominator) == (2.0, 2)
    assert card.hand_files_over_plan.excluded == 2


def test_a_document_without_the_size_keys_reads_none_and_leaves_the_figure_empty():
    figures = scorecard.task_figures(doc())

    assert (figures.planned_size, figures.actual_size) == (None, None)
    assert scorecard.build([_closed()]).hand_lines_over_plan.value is None
