"""Cross-checks `observability/grafana/dashboard.json` against `observability/README.md`
(issue #24, subtask `dashboard-v3`).

A dashboard has no other automatic check: this parses every PromQL `expr` in the JSON
and asserts that every metric name it queries is one the README's metric table
documents, and that every label key it queries on a given metric is one that same
metric's own row documents — paired metric by metric, not as two table-wide flat sets,
so a panel querying a label its own metric does not carry is caught even when some
other metric in the table happens to carry that label. It also asserts the JSON parses
(Grafana would reject anything else) and that no panel uses `increase()` (ADR-0008: the
metrics are events, read with `sum_over_time`/`count_over_time`, never a counter's
rate).

The README table itself is read through `tests.readme_metrics`, shared with
`test_telemetry.py`'s own cross-check of the same table against what the code emits.
"""

import json
import re
from pathlib import Path

from tests.readme_metrics import _documented

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "observability" / "grafana" / "dashboard.json"


def _exprs() -> list[str]:
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    out = []
    for panel in data["panels"]:
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
    today) rather than to the whole dashboard's label vocabulary."""
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
    json.loads(DASHBOARD.read_text(encoding="utf-8"))


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
