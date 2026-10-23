"""Performance measurement, pure (GH-318, ADR-0059): the types, the contract of a `measure`
command, every statistic and the verdict. Nothing here touches a process, a file or a clock.

**The `measure` contract.** A product's `measure` command prints, as the last non-empty
line of its stdout, one JSON object::

    {"volume": 1000, "volume_unit": "rows", "metrics": {"list_ms": 12.5, "list_queries": 3}}

`volume` and `volume_unit` are what the command saw (the system hands it `PERF_VOLUME` and
`PERF_VOLUME_UNIT`), `metrics` maps a name to a non-negative finite number. The command only
prints; it never judges. `parse_line` is the only reader of that line, and `verdict` the only
place a measured value meets a limit.

**Statistics.** Percentiles are nearest-rank (rank = ceil(q * n)); the median is the middle
element of the sorted values. With n = 5 or 9 the p10 and p90 are the smallest and, for 5, the
largest value, so the spread (p90 - p10) / median is a range over the median, not a
percentile band. A median of 0 with a band wider than 0 has no relative spread and is
`inconclusive/spread`.

**Outcomes.** A scenario is `within`, `over` or `inconclusive`; the last always carries one
reason of `spread | broken | timeout | busy | volume`. Reason precedence: `timeout`,
`broken`, `volume`, `spread`.

`command_refusal` is a string guard against a command that is plainly outside what a
measurement may do. It is not a sandbox: a command can spell any of those words in a way it
does not see, and the network stays open. The runner's scrubbed environment is the barrier.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

FIRST_RUNS = 5
RETRY_RUNS = 9
UNIT_SUFFIX = {"ms": "_ms", "queries": "_queries", "kb": "_kb"}

Unit = Literal["ms", "queries", "kb"]
VolumeUnit = Literal["rows", "days", "months", "years"]
Outcome = Literal["within", "over", "inconclusive"]
Reason = Literal["spread", "broken", "timeout", "busy", "volume"]


@dataclass(frozen=True)
class Limit:
    limit: float
    unit: Unit


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: str
    measure: str
    volume: float
    volume_unit: VolumeUnit
    limits: Mapping[str, Limit]


@dataclass(frozen=True)
class Measurement:
    """What one `measure` line says. `volume` and `volume_unit` are `None` when the line
    does not carry a usable one: the verdict reads that as a volume that is missing."""

    volume: float | None
    volume_unit: str | None
    metrics: Mapping[str, float]


@dataclass(frozen=True)
class Run:
    """One command's outcome. `measurement` is `None` when stdout held no valid line (a seed
    never has one). The load averages are informational."""

    measurement: Measurement | None
    exit_code: int
    timed_out: bool
    load_before: float | None
    load_after: float | None


@dataclass(frozen=True)
class MetricResult:
    metric: str
    median: float | None
    spread: float | None
    limit: Limit
    outcome: Outcome
    reason: Reason | None


@dataclass(frozen=True)
class Result:
    scenario: str
    outcome: Outcome
    reason: Reason | None
    metrics: tuple[MetricResult, ...]
    started: bool
    runs: int
    load_before: float | None
    load_after: float | None


def parse_line(stdout: str) -> Measurement | None:
    """The `measure` contract's only parser: the last non-empty line, one JSON object.

    `None` is a broken command: no line, not JSON, `NaN`/`Infinity`, no `metrics` mapping, or
    a metric that is a bool, not a number, not finite or negative. A `volume` or
    `volume_unit` that is absent or of the wrong type is not broken: it is a volume the
    verdict reads as missing."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        data = json.loads(lines[-1], parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("metrics")
    if not isinstance(raw, dict):
        return None
    metrics: dict[str, float] = {}
    for name, value in raw.items():
        number = _number(value)
        if number is None or number < 0:
            return None
        metrics[str(name)] = number
    volume_unit = data.get("volume_unit")
    return Measurement(
        volume=_number(data.get("volume")),
        volume_unit=volume_unit if isinstance(volume_unit, str) else None,
        metrics=metrics,
    )


def verdict(scenario: Scenario, seed: Run, runs: Sequence[Run], spread_max: float) -> Result:
    """The scenario's result from its seed and its measure runs, in order. No run at all
    (a seed that failed, so nothing was measured) is `broken` unless the seed timed out."""
    every = [seed, *runs]
    reason = _scenario_reason(scenario, seed, runs, every)
    if reason is not None:
        metrics = tuple(
            MetricResult(name, None, None, limit, "inconclusive", reason)
            for name, limit in scenario.limits.items()
        )
    else:
        metrics = tuple(
            _metric(
                name,
                limit,
                [r.measurement.metrics[name] for r in runs if r.measurement],
                spread_max,
            )
            for name, limit in scenario.limits.items()
        )
    outcome = _worst([m.outcome for m in metrics])
    if reason is None and outcome == "inconclusive":
        reason = "spread"
    return Result(
        scenario=scenario.name,
        outcome=outcome,
        reason=reason if outcome == "inconclusive" else None,
        metrics=metrics,
        started=True,
        runs=len(runs),
        load_before=(runs[0] if runs else seed).load_before,
        load_after=(runs[-1] if runs else seed).load_after,
    )


def _scenario_reason(
    scenario: Scenario, seed: Run, runs: Sequence[Run], every: Sequence[Run]
) -> Reason | None:
    if any(r.timed_out for r in every):
        return "timeout"
    if not runs or any(r.exit_code != 0 for r in every):
        return "broken"
    measurements = [r.measurement for r in runs]
    for measurement in measurements:
        if measurement is None or any(name not in measurement.metrics for name in scenario.limits):
            return "broken"
    for measurement in measurements:
        if measurement is not None and _volume_short(scenario, measurement):
            return "volume"
    return None


def _volume_short(scenario: Scenario, measurement: Measurement) -> bool:
    return (
        measurement.volume is None
        or measurement.volume_unit != scenario.volume_unit
        or measurement.volume < scenario.volume
    )


def _metric(name: str, limit: Limit, values: Sequence[float], spread_max: float) -> MetricResult:
    ordered = sorted(values)
    median = _percentile(ordered, 50)
    low = _percentile(ordered, 10)
    high = _percentile(ordered, 90)
    if high == low:
        spread = 0.0
    elif median == 0:
        return MetricResult(name, median, None, limit, "inconclusive", "spread")
    else:
        spread = (high - low) / median
    if spread > spread_max:
        return MetricResult(name, median, spread, limit, "inconclusive", "spread")
    outcome: Outcome = "within" if median <= limit.limit else "over"
    return MetricResult(name, median, spread, limit, outcome, None)


def _percentile(ordered: Sequence[float], percent: int) -> float:
    """Nearest-rank: the value at rank ceil(percent * n / 100), in integers."""
    rank = max(1, -(-percent * len(ordered) // 100))
    return ordered[rank - 1]


def busy(scenario: Scenario) -> Result:
    """The machine-wide lock was not obtained in time: nothing ran, the scenario is not judged."""
    return _unmeasured(scenario, "busy", started=True)


def not_started(scenario: Scenario) -> Result:
    """The node's budget was spent before this scenario could start."""
    return _unmeasured(scenario, "timeout", started=False)


def overall(results: Sequence[Result]) -> Outcome:
    """The worst outcome of the run: inconclusive, then over, then within. Never called with
    no result."""
    return _worst([r.outcome for r in results])


def _unmeasured(scenario: Scenario, reason: Reason, *, started: bool) -> Result:
    metrics = tuple(
        MetricResult(name, None, None, limit, "inconclusive", reason)
        for name, limit in scenario.limits.items()
    )
    return Result(scenario.name, "inconclusive", reason, metrics, started, 0, None, None)


_REFUSED = re.compile(r"(?<![\w-])(git\s+push|gh|curl|wget)(?![\w-])")


def command_refusal(cmd: str) -> str | None:
    """Why a `seed`/`measure` command is refused, or `None`. A guard on the string, not a
    sandbox (module docstring)."""
    if not cmd.strip():
        return "the command is empty"
    if "\n" in cmd or "\r" in cmd:
        return "the command spans more than one line; declare one line"
    found = _REFUSED.search(cmd)
    if found:
        word = " ".join(found.group(1).split())
        return f"the command uses `{word}`, which a measurement may not"
    return None


def limits_mismatch(
    expected: Mapping[str, Limit], declared: Mapping[str, Limit]
) -> tuple[str, ...]:
    """The metrics of `expected` that `declared` lacks or states with another limit or unit,
    sorted. A metric only `declared` has is not a mismatch."""
    return tuple(sorted(name for name, limit in expected.items() if declared.get(name) != limit))


def _worst(outcomes: Sequence[Outcome]) -> Outcome:
    found = set(outcomes)
    if "inconclusive" in found:
        return "inconclusive"
    return "over" if "over" in found else "within"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a measurement")
