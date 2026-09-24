"""Cross-checks `observability/grafana/dashboard.json` against `observability/README.md`
(issue #24, subtask `dashboard-v3`; issue GH-114, ADR-0028, subtask `dashboard-rows`).

A dashboard has no other automatic check: this parses every PromQL `expr` in the JSON
and asserts that every metric name it queries is one the README's metric table
documents, and that every label key it queries on a given metric is one that same
metric's own row documents -- paired metric by metric, not as two table-wide flat sets,
so a panel querying a label its own metric does not carry is caught even when some
other metric in the table happens to carry that label. It also asserts the JSON parses
(Grafana would reject anything else) and that no panel uses `increase()` (ADR-0008: the
metrics are events, read with `sum_over_time`/`count_over_time`, never a counter's
rate).

ADR-0028 reorganised the dashboard into four rows (Scorecard, Experimentos,
Diagnóstico, Control), each with its own comparison axis. This file grew three
mechanisms for that, each named for the problem it solves:

- `_panels()`, the recursive panel walk with a count-two-ways assertion
  (`test_panel_walk_is_exhaustive`): a Grafana row with `"collapsed": true` hides its
  children inside `row["panels"]`, and a one-level walk would pass *vacuously* over
  half a four-row dashboard.
- `ROW_AXIS`, the row-keyed axis table with a two-way partition
  (`test_every_row_title_is_in_row_axis_and_vice_versa`,
  `test_every_non_row_panel_is_attributed_to_exactly_one_row`): row membership is
  derived from `gridPos`, never from list position.
- `tests.readme_metrics._not_read()` beside `_documented()`: one reader of the README
  for two consumers, this file and `test_telemetry.py`.

The README table itself is read through `tests.readme_metrics`, shared with
`test_telemetry.py`'s own cross-check of the same table against what the code emits.
"""

import json
import re
from pathlib import Path

from agentic.core.measurement.domain.scorecard import EXTERNAL_FAILURE_REASONS
from tests.readme_metrics import _documented, _not_read

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "observability" / "grafana" / "dashboard.json"


def _load() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _row_titles(data: dict) -> list[tuple[str, int]]:
    """Every row panel's title and `gridPos.y`, sorted by `y`: the ordering
    `_row_for_y` needs to find "the row with the greatest `y` not above" a sibling's
    own `y` (Tech Lead plan, `Subtasks`)."""
    rows = [(p["title"], p["gridPos"]["y"]) for p in data["panels"] if p.get("type") == "row"]
    return sorted(rows, key=lambda kv: kv[1])


def _row_for_y(rows: list[tuple[str, int]], y: int) -> str | None:
    candidates = [title for title, row_y in rows if row_y <= y]
    return candidates[-1] if candidates else None


def _panels(data: dict) -> list[tuple[str | None, dict]]:
    """Every leaf (non-row) panel with the title of the row it belongs to: a row's own
    `panels` list (present when Grafana would collapse it) is descended into and
    attributed to that row directly; a top-level sibling is attributed by `gridPos` to
    the row with the greatest `y` not above its own, never by its position in the
    list. Recurses arbitrarily deep so a panel double-nested by a future edit is still
    visited rather than silently dropped."""
    rows = _row_titles(data)
    out: list[tuple[str | None, dict]] = []

    def walk(panel: dict, row_title: str | None) -> None:
        for child in panel.get("panels", []):
            out.append((row_title, child))
            walk(child, row_title)

    for panel in data["panels"]:
        if panel.get("type") == "row":
            walk(panel, panel["title"])
            continue
        row_title = _row_for_y(rows, panel["gridPos"]["y"])
        out.append((row_title, panel))
        walk(panel, row_title)
    return out


def _exprs(data: dict | None = None) -> list[str]:
    data = data if data is not None else _load()
    out = []
    for _row, panel in _panels(data):
        for target in panel.get("targets", []):
            if "expr" in target:
                out.append(target["expr"])
    return out


METRIC_WITH_SELECTOR = re.compile(r"(agentic_[a-z0-9_]+)\{([^}]*)\}")
BARE_METRIC = re.compile(r"\bagentic_[a-z0-9_]+\b")


def _queried_by_metric(exprs: list[str]) -> dict[str, set[str]]:
    """Metric name -> label keys queried on it, paired within the same expression: a
    selector's labels (`metric{label="x"}`) attach only to that metric, and a `by(...)`
    grouping attaches to every metric the same expression reads (a division of two
    series grouped the same way on both sides, which is every case in this dashboard
    today) rather than to the whole dashboard's label vocabulary. Kept exactly as it
    was before ADR-0028 (module docstring): both the dashboard-wide label-vocabulary
    check and the new per-row `ROW_AXIS` check below reuse it unchanged, the latter by
    calling it on one panel's own expressions instead of the whole dashboard's."""
    result: dict[str, set[str]] = {}
    for expr in exprs:
        metrics_in_expr = set(BARE_METRIC.findall(expr))
        for metric, body in METRIC_WITH_SELECTOR.findall(expr):
            selector_labels = set(re.findall(r'([a-z_][a-z0-9_]*)\s*(?:=~|=|!=|!~)\s*"', body))
            result.setdefault(metric, set()).update(selector_labels)
        for group in re.findall(r"\bby\s*\(([^)]*)\)", expr):
            by_labels = {tok.strip() for tok in group.split(",") if tok.strip()}
            for metric in metrics_in_expr:
                result.setdefault(metric, set()).update(by_labels)
    return result


def test_dashboard_json_is_valid():
    _load()


def test_no_panel_uses_increase():
    # The metrics are events (ADR-0008): every panel reads them with sum_over_time
    # or count_over_time, never increase(), which assumes a monotonic counter.
    assert "increase(" not in DASHBOARD.read_text(encoding="utf-8")


def test_negative_matcher_is_recognised_as_a_queried_label():
    # `model!=""` is the dashboard's first negative matcher (the `$ / 1k tokens de
    # salida` panel excludes the residual sample): the label-key regex must see it,
    # or both this and the subset assertion below would be blind to it.
    expr = 'sum(agentic_role_cost_usd{product=~"$product", model!=""})'
    assert "model" in _queried_by_metric([expr])["agentic_role_cost_usd"]


# --- The recursive panel walk (ADR-0028): a synthetic input a shallow, one-level
# walk would reject -- i.e. silently fail to visit -- so the mechanism is exercised on
# the case it exists for, not only on the real dashboard where every row today happens
# to ship "collapsed": false with no nested children.
def test_panel_walk_descends_into_a_collapsed_row():
    fake = {
        "panels": [
            {"type": "row", "title": "Fila", "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}},
            {
                "type": "row",
                "title": "Colapsada",
                "collapsed": True,
                "gridPos": {"x": 0, "y": 1, "w": 24, "h": 1},
                "panels": [
                    {
                        "type": "stat",
                        "title": "Escondido",
                        "gridPos": {"x": 0, "y": 2, "w": 6, "h": 4},
                        "targets": [
                            {
                                "refId": "A",
                                "expr": 'sum(agentic_task_finished{product=~"$product"})',
                            }
                        ],
                    }
                ],
            },
        ]
    }
    visited_titles = {panel["title"] for _row, panel in _panels(fake)}
    assert "Escondido" in visited_titles, (
        "a collapsed row's nested panels must be visited by the walk, or a panel "
        "hidden that way would never have its PromQL checked at all"
    )
    # The row it was attributed to is the enclosing row, never derived from gridPos.y
    # (its nested gridPos.y=2 would otherwise misattribute it to "Fila" at y=0/1).
    attributed = {panel["title"]: row for row, panel in _panels(fake)}
    assert attributed["Escondido"] == "Colapsada"


def test_panel_walk_is_exhaustive():
    """Count-two-ways: the walked count must equal every top-level non-row panel plus
    every nested panel list's length, so a panel the walk did not visit is a failure,
    never a silent vacuous pass (ADR-0028, `Subtasks`)."""
    data = _load()
    top = data["panels"]
    num_rows = sum(1 for p in top if p.get("type") == "row")
    nested_total = sum(len(p.get("panels", [])) for p in top)
    expected = (len(top) - num_rows) + nested_total
    assert len(_panels(data)) == expected


# --- Row-keyed axis table (ADR-0028): every row's required grouping/selector axis.
# "Control" is not required on its one panel that groups by product instead
# (`ROW_AXIS_EXCEPTIONS`): the product/type mix is the one exception the plan itself
# names, never a second, undocumented one.
ROW_AXIS = {
    "Scorecard": "system_baseline",
    "Experimentos": "arm",
    "Diagnóstico": "system_baseline",
    "Control": "system_baseline",
}
ROW_AXIS_EXCEPTIONS = {"Mezcla de tareas por producto y versión (control, no eje)"}


def test_every_row_title_is_in_row_axis_and_vice_versa():
    data = _load()
    row_titles = {title for title, _y in _row_titles(data)}
    missing_from_table = row_titles - set(ROW_AXIS)
    missing_from_dashboard = set(ROW_AXIS) - row_titles
    assert not missing_from_table, f"row(s) with no ROW_AXIS entry: {sorted(missing_from_table)}"
    assert not missing_from_dashboard, (
        f"ROW_AXIS entry naming no row in the dashboard: {sorted(missing_from_dashboard)}"
    )


def test_every_non_row_panel_is_attributed_to_exactly_one_row():
    data = _load()
    unattributed = [
        panel.get("title", panel.get("id")) for row, panel in _panels(data) if row is None
    ]
    assert not unattributed, f"panel(s) attributed to no row: {unattributed}"
    seen: dict[str, int] = {}
    for _row, panel in _panels(data):
        key = panel.get("title") or str(panel.get("id"))
        seen[key] = seen.get(key, 0) + 1
    duplicated = [title for title, count in seen.items() if count > 1]
    assert not duplicated, f"panel(s) attributed to more than one row: {duplicated}"


def test_row_axis_is_used_by_every_panel_in_its_row():
    """Every documented metric an expression in a row reads must carry that row's axis
    label -- via a `by(...)` grouping or a bare selector matcher, `_queried_by_metric`
    does not distinguish the two, and Scorecard/Experimentos filter to a single
    baseline/arm through the selector rather than grouping by it (Tech Lead plan)."""
    data = _load()
    documented = _documented()
    for row_title, panel in _panels(data):
        if panel.get("title") in ROW_AXIS_EXCEPTIONS:
            continue
        axis = ROW_AXIS.get(row_title)
        if axis is None:
            continue
        exprs = [t["expr"] for t in panel.get("targets", []) if "expr" in t]
        queried = _queried_by_metric(exprs)
        for metric, labels in queried.items():
            if metric not in documented:
                continue
            assert axis in labels, (
                f"panel {panel.get('title')!r} in row {row_title!r} reads {metric} "
                f"without {axis!r} in its by(...)/selector: {exprs!r}"
            )


def test_panels_gridpos_y_is_non_decreasing():
    """A UI save reorders `data["panels"]` freely; this is the one property that makes
    row membership derivable from `gridPos` at all instead of from list position
    (ADR-0028, `Subtasks`)."""
    data = _load()
    ys = [p["gridPos"]["y"] for p in data["panels"]]
    for i in range(1, len(ys)):
        assert ys[i] >= ys[i - 1], f"gridPos.y decreases at panel index {i}: {ys[i - 1]} -> {ys[i]}"


# The metric families this task adds `system.version` to (ADR-0008, 2026-09-21 addendum):
# tokens, calls, turns, tool_calls and subagent_cost_usd of a role run, its failures,
# presubmit checks and interview counts. `agentic_role_duration_seconds` (node grain) is
# deliberately absent: it stays frozen and unversioned (ADR-0008, 2026-09-18), and the
# panel that wants a version reads `agentic_role_run_duration_seconds`/`_queue_wait_seconds`
# instead, which already carried `system.version` before this task.
VERSIONED_FAMILIES = [
    "agentic_role_tokens",
    "agentic_role_calls",
    "agentic_role_turns",
    "agentic_role_max_turns",
    "agentic_role_tool_calls",
    "agentic_role_subagent_cost_usd",
    "agentic_role_failures",
    "agentic_role_failure_cost_usd",
    "agentic_presubmit_checks",
    "agentic_presubmit_duration_seconds",
    "agentic_interview_rounds",
    "agentic_interview_questions",
    "agentic_interview_invariants",
]

DELIVERY_PREFIX = "agentic_delivery_"


def test_versioned_families_are_grouped_and_filtered_by_system_version():
    """ADR-0028 folded the old per-metric axis check into the row-keyed rule
    (`test_row_axis_is_used_by_every_panel_in_its_row`): a versioned family's panel now
    lives in Diagnóstico and groups by `system_baseline`, not `system_version`. What
    survives here, amended rather than deleted, is the part `ROW_AXIS` does not cover:
    every occurrence of a versioned family anywhere in the dashboard still carries
    `system_version=~"$version"` in its own selector -- the filter this task keeps
    alongside the new axis (Tech Lead plan, `Subtasks`: "keeping system_version=~$version
    ... in every selector")."""
    exprs = _exprs()
    exprs_by_metric: dict[str, list[str]] = {}
    for expr in exprs:
        for metric in set(BARE_METRIC.findall(expr)):
            exprs_by_metric.setdefault(metric, []).append(expr)

    for metric in VERSIONED_FAMILIES:
        for expr in exprs_by_metric.get(metric, []):
            assert re.search(rf'{metric}\{{[^}}]*system_version=~"\$version"', expr), (
                f'{metric} is queried without system_version=~"$version" in its selector: {expr!r}'
            )
            assert re.search(rf'{metric}\{{[^}}]*system_baseline=~"\$baseline"', expr), (
                f'{metric} is queried without system_baseline=~"$baseline" in its selector '
                f"(ADR-0028's universal selector rule): {expr!r}"
            )


def test_every_queried_metric_and_label_is_documented():
    documented = _documented()
    queried = _queried_by_metric(_exprs())

    undocumented_metrics = set(queried) - set(documented)
    assert not undocumented_metrics, (
        f"dashboard.json queries metrics observability/README.md does not document: "
        f"{sorted(undocumented_metrics)}"
    )

    mismatches = {
        metric: sorted(extra)
        for metric, labels in queried.items()
        if (extra := labels - documented.get(metric, set()))
    }
    assert not mismatches, (
        f"dashboard.json queries label keys the metric's own README row does not "
        f"document: {mismatches}"
    )


def test_delivery_panels_are_grouped_and_filtered_by_system_version():
    """ADR-0028 folds this into the row-keyed rule the same way as the versioned-family
    test above (amended, not deleted): every `agentic.delivery.*` expression keeps
    `system_version=~"$version"` in its selector wherever it appears (Scorecard,
    Experimentos and Diagnóstico's new panels alike) plus the new
    `system_baseline=~"$baseline"` the universal selector rule adds; which axis a given
    panel *groups* by is `ROW_AXIS`'s job, not this test's."""
    exprs = [e for e in _exprs() if DELIVERY_PREFIX in e]
    assert exprs, "no panel reads an agentic.delivery.* family"
    for expr in exprs:
        for metric in set(BARE_METRIC.findall(expr)):
            if not metric.startswith(DELIVERY_PREFIX):
                continue
            assert re.search(rf'{metric}\{{[^}}]*system_version=~"\$version"', expr), (
                f'{metric} is queried without system_version=~"$version" in its selector: {expr!r}'
            )
            assert re.search(rf'{metric}\{{[^}}]*system_baseline=~"\$baseline"', expr), (
                f'{metric} is queried without system_baseline=~"$baseline" in its '
                f"selector: {expr!r}"
            )


def test_unmergeable_pauses_are_broken_out_by_version():
    """gym-saas #16 (GH-91): two of seven escalations were `unmergeable` pauses a
    Developer turn would have settled by hand. `escalation_class` already ships on
    `agentic_task_paused` and "Pausas por motivo de escalado" already stacks every
    class together, but that buries the one class this task's fix targets inside six
    others. Amended for ADR-0028 (not deleted): this panel now lives in Diagnóstico,
    whose axis moved to `system_baseline`, so the regex that used to require
    `by (system_version)` and nothing else now requires `by (system_baseline)` and
    nothing else -- the same intent (the unmergeable count stays visible per this
    panel's own row axis, not buried in a bigger group), rewritten against the new
    axis rather than the old one."""
    exprs = [e for e in _exprs() if 'escalation_class="unmergeable"' in e]
    assert exprs, 'no panel isolates escalation_class="unmergeable" on its own'
    for expr in exprs:
        assert "agentic_task_paused" in expr
        assert re.search(r'agentic_task_paused\{[^}]*product=~"\$product"', expr), expr
        assert re.search(r'agentic_task_paused\{[^}]*system_version=~"\$version"', expr), expr
        assert re.search(r"by\s*\(\s*system_baseline\s*\)", expr), expr


# --- ADR-0029: "own failures" excludes exactly the two external reasons, and the
# matcher is asserted against the imported constant, never retyped.
def test_own_failures_matcher_excludes_exactly_the_external_reasons():
    data = _load()
    exprs = [e for e in _exprs(data) if "agentic_role_failures" in e]
    assert exprs, "no panel reads agentic_role_failures"
    matchers = [m.group(1) for e in exprs for m in re.finditer(r'reason!~"([^"]*)"', e)]
    assert matchers, 'no panel excludes failure reasons with reason!~"..."'
    for alternation in matchers:
        assert set(alternation.split("|")) == set(EXTERNAL_FAILURE_REASONS), (
            f"reason!~ alternation {alternation!r} does not match "
            f"scorecard.EXTERNAL_FAILURE_REASONS {EXTERNAL_FAILURE_REASONS!r}"
        )
    for e in exprs:
        assert 'reason!="limit"' not in e, (
            f'reason!="limit" is the inversion of the glossary rule (ADR-0029): {e!r}'
        )


# --- $baseline and $baseline_prev are single-select.
def test_baseline_variables_are_single_select():
    data = _load()
    by_name = {v["name"]: v for v in data["templating"]["list"]}
    for name in ("baseline", "baseline_prev"):
        assert name in by_name, f"no ${name} template variable"
        v = by_name[name]
        assert v.get("multi") is False, f"${name} must be multi: false"
        assert v.get("includeAll") is False, f"${name} must be includeAll: false"
        assert "allValue" not in v, f"${name} must not declare allValue"


# --- Every Scorecard/Experimentos panel, and every panel whose expression divides,
# names its baseline resolution and its denominator population in `description`.
def test_scorecard_experimentos_and_dividing_panels_have_a_description():
    data = _load()
    for row_title, panel in _panels(data):
        exprs = [t.get("expr", "") for t in panel.get("targets", [])]
        divides = any(" / " in e for e in exprs)
        if row_title in ("Scorecard", "Experimentos") or divides:
            desc = panel.get("description", "")
            assert desc.strip(), (
                f"panel {panel.get('title')!r} (row {row_title!r}) must carry a non-empty "
                "description naming its baseline resolution and its denominator population"
            )


# --- No literal range selector exceeds 32 days (Grafana Cloud's per-query ceiling,
# ADR-0008's first addendum), and the dashboard default is no wider.
RANGE_LITERAL = re.compile(r"\[(\d+)([smhdwy])\]")
DAY_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
MAX_RANGE_DAYS = 32


def test_no_literal_range_selector_exceeds_32_days():
    data = _load()
    for expr in _exprs(data):
        for value, unit in RANGE_LITERAL.findall(expr):
            days = int(value) * DAY_SECONDS[unit] / 86400
            assert days <= MAX_RANGE_DAYS, (
                f"literal range selector [{value}{unit}] in {expr!r} exceeds the "
                f"{MAX_RANGE_DAYS}-day Grafana Cloud ceiling ($__range is not a literal "
                "and is not checked here)"
            )
    default_from = data["time"]["from"]
    m = re.match(r"now-(\d+)([smhdwy])$", default_from)
    assert m, f'dashboard default "from" is not a relative literal: {default_from!r}'
    value, unit = m.groups()
    days = int(value) * DAY_SECONDS[unit] / 86400
    assert days <= MAX_RANGE_DAYS, (
        f'dashboard default "from" ({default_from}) exceeds {MAX_RANGE_DAYS} days'
    )


# --- The README partition: every documented metric is either queried or listed as
# unread, in both directions.
def test_readme_partition_of_documented_metrics_is_queried_or_not_read():
    documented = set(_documented())
    queried = set(_queried_by_metric(_exprs()))
    not_read = set(_not_read())

    undocumented_not_read = not_read - documented
    assert not undocumented_not_read, (
        f"'## Métricas emitidas y no leídas' names metric(s) not in the metric table: "
        f"{sorted(undocumented_not_read)}"
    )
    queried_and_not_read = queried & not_read
    assert not queried_and_not_read, (
        f"metric(s) both queried by a panel and listed as not read: {sorted(queried_and_not_read)}"
    )
    neither = documented - queried - not_read
    assert not neither, (
        f"documented metric(s) neither queried by any panel nor listed as not "
        f"read: {sorted(neither)}"
    )
