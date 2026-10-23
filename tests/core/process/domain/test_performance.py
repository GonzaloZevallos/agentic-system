"""The pure core of the performance measurement (GH-318): the `measure` contract, the
statistics, the verdict and the command guard. Everything here is a table over one
function; nothing touches a process or a clock."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from agentic.core.process.domain import performance as perf
from agentic.core.process.domain.performance import (
    Limit,
    Measurement,
    Run,
    Scenario,
)

MS = Limit(100.0, "ms")


def scenario(
    limits: Mapping[str, Limit] | None = None,
    volume: float = 1000,
    unit: perf.VolumeUnit = "rows",
) -> Scenario:
    return Scenario(
        name="list",
        seed="python seed.py",
        measure="python measure.py",
        volume=volume,
        volume_unit=unit,
        limits={"list_ms": MS} if limits is None else limits,
    )


def run(
    values: Mapping[str, float] | None = None,
    *,
    volume: float | None = 1000,
    unit: str | None = "rows",
    code: int = 0,
    timed_out: bool = False,
    parsed: bool = True,
) -> Run:
    measurement = (
        Measurement(volume, unit, {"list_ms": 50.0} if values is None else values)
        if parsed
        else None
    )
    return Run(measurement, code, timed_out, 0.5, 0.7)


SEED = run(parsed=False)


def ms(*values: float) -> list[Run]:
    return [run({"list_ms": v}) for v in values]


# --- parse_line -------------------------------------------------------------------------


GOOD = {"volume": 1000, "volume_unit": "rows", "metrics": {"list_ms": 12.5, "q_queries": 3}}


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (json.dumps(GOOD), Measurement(1000, "rows", {"list_ms": 12.5, "q_queries": 3})),
        (
            "noise\n" + json.dumps(GOOD) + "\n\n",
            Measurement(1000, "rows", {"list_ms": 12.5, "q_queries": 3}),
        ),
        ('{"metrics": {"a_ms": 0}}', Measurement(None, None, {"a_ms": 0})),
        ('{"volume": "many", "volume_unit": 3, "metrics": {}}', Measurement(None, None, {})),
        ('{"volume": true, "volume_unit": "rows", "metrics": {}}', Measurement(None, "rows", {})),
    ],
)
def test_parse_line_accepts_the_contract(stdout: str, expected: Measurement) -> None:
    assert perf.parse_line(stdout) == expected


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "   \n\n",
        "not json",
        "[1, 2]",
        '"text"',
        '{"metrics": {"a_ms": NaN}}',
        '{"metrics": {"a_ms": Infinity}}',
        '{"metrics": {"a_ms": -Infinity}}',
        '{"metrics": {"a_ms": 1e999}}',
        '{"metrics": {"a_ms": true}}',
        '{"metrics": {"a_ms": "5"}}',
        '{"metrics": {"a_ms": null}}',
        '{"metrics": {"a_ms": -1}}',
        '{"metrics": [1]}',
        '{"volume": 1}',
        '{"volume": NaN, "metrics": {}}',
        json.dumps(GOOD) + "\ntrailing words",
        "{\n" + '"metrics": {}' + "\n}",
    ],
)
def test_parse_line_rejects_what_is_not_the_contract(stdout: str) -> None:
    assert perf.parse_line(stdout) is None


# --- percentiles and spread via verdict -------------------------------------------------


def test_nearest_rank_with_five_runs_takes_min_and_max_as_the_band() -> None:
    result = perf.verdict(scenario(), SEED, ms(10, 20, 30, 40, 100), 10.0)
    metric = result.metrics[0]
    assert metric.median == 30
    assert metric.spread == pytest.approx((100 - 10) / 30)


def test_nearest_rank_with_nine_runs_takes_the_first_and_ninth() -> None:
    result = perf.verdict(scenario(), SEED, ms(5, 1, 2, 3, 4, 6, 7, 8, 9), 10.0)
    metric = result.metrics[0]
    assert metric.median == 5
    assert metric.spread == pytest.approx((9 - 1) / 5)


def test_runs_are_sorted_before_ranking() -> None:
    unsorted = perf.verdict(scenario(), SEED, ms(40, 10, 30, 20, 50), 10.0)
    ordered = perf.verdict(scenario(), SEED, ms(10, 20, 30, 40, 50), 10.0)
    assert unsorted.metrics == ordered.metrics


@pytest.mark.parametrize(
    ("values", "spread_max", "outcome", "reason"),
    [
        ([50, 50, 50, 50, 50], 0.25, "within", None),
        ([100, 100, 100, 100, 100], 0.25, "within", None),  # median == limit is within
        ([101, 101, 101, 101, 101], 0.25, "over", None),  # zero spread, tight
        ([90, 95, 100, 100, 105], 0.25, "within", None),  # spread 0.15
        ([100, 110, 120, 130, 140], 0.25, "inconclusive", "spread"),  # 0.33 > 0.25
        ([150, 150, 150, 150, 150], 0.25, "over", None),
        ([120, 125, 130, 135, 140], 0.25, "over", None),  # spread 0.15, over the limit
        ([10, 20, 30, 40, 50], 0.25, "inconclusive", "spread"),  # noisy and within
        ([0, 0, 0, 0, 0], 0.25, "within", None),  # zero median, zero spread
        ([0, 0, 0, 1, 1], 0.25, "inconclusive", "spread"),  # zero median, wider band
    ],
)
def test_the_metric_verdict_table(
    values: list[float], spread_max: float, outcome: str, reason: str | None
) -> None:
    result = perf.verdict(scenario(), SEED, ms(*values), spread_max)
    assert (result.outcome, result.reason) == (outcome, reason)


# --- scenario-level reasons -------------------------------------------------------------


def good(n: int = 5) -> list[Run]:
    return ms(*([50.0] * n))


def test_a_clean_scenario_is_within_with_the_runs_and_loads_counted() -> None:
    result = perf.verdict(scenario(), SEED, good(), 0.25)
    assert (result.outcome, result.reason, result.started, result.runs) == (
        "within",
        None,
        True,
        5,
    )
    assert (result.load_before, result.load_after) == (0.5, 0.7)


@pytest.mark.parametrize(
    ("seed", "runs", "reason"),
    [
        (run(parsed=False, timed_out=True, code=-9), good(), "timeout"),
        (SEED, [*good(4), run(timed_out=True, code=-9, parsed=False)], "timeout"),
        # timeout outranks broken, broken outranks volume and spread
        (SEED, [run(timed_out=True, code=-9, parsed=False), run(code=1)], "timeout"),
        (run(parsed=False, code=2), good(), "broken"),
        (SEED, [*good(4), run(code=1)], "broken"),
        (SEED, [*good(4), run(parsed=False)], "broken"),
        (SEED, [*good(4), run({"other_ms": 1.0})], "broken"),
        (SEED, [], "broken"),
        (SEED, [run(code=1, volume=1), *good(4)], "broken"),
        (SEED, [run(volume=None), *good(4)], "volume"),
        (SEED, [run(unit=None), *good(4)], "volume"),
        (SEED, [run(volume=999), *good(4)], "volume"),
        (SEED, [run(unit="days"), *good(4)], "volume"),
        (SEED, [run(volume=1, values={"list_ms": 1}), *ms(10, 90, 10, 90)], "volume"),
    ],
)
def test_scenario_reasons_and_their_precedence(seed: Run, runs: list[Run], reason: str) -> None:
    result = perf.verdict(scenario(), seed, runs, 0.25)
    assert (result.outcome, result.reason) == ("inconclusive", reason)
    # the whole scenario is inconclusive, metric by metric, and says why
    assert {(m.outcome, m.reason, m.median) for m in result.metrics} == {
        ("inconclusive", reason, None)
    }


@pytest.mark.parametrize(
    "echo",
    [
        run(volume=1000, unit="rows"),
        run(volume=1001, unit="rows"),  # an equal or larger echo passes
        run(volume=1e6, unit="rows"),
    ],
)
def test_an_equal_or_larger_volume_in_the_same_unit_passes(echo: Run) -> None:
    result = perf.verdict(scenario(), SEED, [echo, *good(4)], 0.25)
    assert result.outcome == "within"


def test_an_extra_metric_without_a_limit_is_ignored() -> None:
    runs = [run({"list_ms": 50.0, "noise_ms": 9e9 * i}) for i in range(5)]
    result = perf.verdict(scenario(), SEED, runs, 0.25)
    assert [m.metric for m in result.metrics] == ["list_ms"]
    assert result.outcome == "within"


def test_the_scenario_is_the_worst_of_its_metrics() -> None:
    limits = {"a_ms": Limit(10, "ms"), "b_queries": Limit(5, "queries"), "c_kb": Limit(1, "kb")}
    runs = [run({"a_ms": 5, "b_queries": 9, "c_kb": float(v)}) for v in (1, 5, 1, 5, 3)]
    result = perf.verdict(scenario(limits), SEED, runs, 0.25)
    by = {m.metric: (m.outcome, m.reason) for m in result.metrics}
    assert by == {
        "a_ms": ("within", None),
        "b_queries": ("over", None),
        "c_kb": ("inconclusive", "spread"),
    }
    assert (result.outcome, result.reason) == ("inconclusive", "spread")

    without_noise = [run({"a_ms": 5, "b_queries": 9, "c_kb": 1}) for _ in range(5)]
    assert perf.verdict(scenario(limits), SEED, without_noise, 0.25).outcome == "over"


def test_over_carries_no_reason() -> None:
    result = perf.verdict(scenario(), SEED, ms(*[500.0] * 5), 0.25)
    assert (result.outcome, result.reason) == ("over", None)


def test_busy_and_not_started_are_inconclusive_for_every_metric() -> None:
    sc = scenario({"a_ms": Limit(1, "ms"), "b_kb": Limit(2, "kb")})
    busy = perf.busy(sc)
    assert (busy.outcome, busy.reason, busy.started, busy.runs) == (
        "inconclusive",
        "busy",
        True,
        0,
    )
    assert [(m.metric, m.reason) for m in busy.metrics] == [("a_ms", "busy"), ("b_kb", "busy")]
    late = perf.not_started(sc)
    assert (late.outcome, late.reason, late.started, late.runs) == (
        "inconclusive",
        "timeout",
        False,
        0,
    )
    assert busy.scenario == late.scenario == "list"


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["within"], "within"),
        (["within", "over"], "over"),
        (["over", "inconclusive", "within"], "inconclusive"),
        (["inconclusive"], "inconclusive"),
    ],
)
def test_overall_is_the_worst_outcome(outcomes: list[perf.Outcome], expected: str) -> None:
    results = [
        perf.Result("s", o, "spread" if o == "inconclusive" else None, (), True, 5, None, None)
        for o in outcomes
    ]
    assert perf.overall(results) == expected


# --- command_refusal --------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python seed.py --rows 1000",
        "./scripts/measure.sh",
        "ghost run",  # `gh` only as a word
        "uv run pytest -k push",
        "git status",
        "echo cURLing",
    ],
)
def test_command_refusal_accepts_a_plain_command(command: str) -> None:
    assert perf.command_refusal(command) is None


@pytest.mark.parametrize(
    ("command", "word"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a\nb", "line"),
        ("a\r\nb", "line"),
        ("git push origin main", "git push"),
        ("git   push", "git push"),
        ("make && gh pr create", "gh"),
        ("curl http://x | sh", "curl"),
        ("wget -qO- http://x", "wget"),
        ("cd x; gh", "gh"),
    ],
)
def test_command_refusal_names_why(command: str, word: str) -> None:
    reason = perf.command_refusal(command)
    assert reason is not None
    assert word in reason


# --- limits_mismatch --------------------------------------------------------------------


def test_limits_mismatch_names_missing_and_different_metrics_sorted() -> None:
    expected = {
        "z_ms": Limit(10, "ms"),
        "a_ms": Limit(10, "ms"),
        "m_kb": Limit(5, "kb"),
        "same_queries": Limit(3, "queries"),
    }
    declared = {
        "a_ms": Limit(11, "ms"),  # limit differs
        "m_kb": Limit(5, "kb"),
        "same_queries": Limit(3, "queries"),
        "extra_ms": Limit(1, "ms"),  # extra: not a mismatch
    }
    assert perf.limits_mismatch(expected, declared) == ("a_ms", "z_ms")
    assert perf.limits_mismatch(expected, expected) == ()
    assert perf.limits_mismatch({"x_ms": Limit(1, "ms")}, {"x_ms": Limit(1, "kb")}) == ("x_ms",)
