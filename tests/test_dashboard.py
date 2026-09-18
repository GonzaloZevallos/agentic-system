"""Cross-checks `observability/grafana/dashboard.json` against `observability/README.md`
(issue #24, subtask `dashboard-v3`).

A dashboard has no other automatic check: this parses every PromQL `expr` in the JSON
and asserts that every metric name and label key it queries is one the README's metric
table documents, so a panel can never query a series or label nobody emits. It also
asserts the JSON parses (Grafana would reject anything else) and that no panel uses
`increase()` (ADR-0008: the metrics are events, read with `sum_over_time`/
`count_over_time`, never a counter's rate).
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "observability" / "grafana" / "dashboard.json"
README = REPO / "observability" / "README.md"

# One row per metric: `` `agentic.foo.bar` `` | labels... | value | unit.
ROW = re.compile(r"^\|\s*`(agentic\.[a-z_.]+)`\s*\|(.*)\|([^|]*)\|([^|]*)\|\s*$", re.M)
BACKTICK = re.compile(r"`([a-z_.]+)`")

# Same suffixing rule the OTel-to-Prometheus bridge applies: a unit becomes a name
# suffix unless the metric's own name already ends with it (`agentic.role.duration`
# with unit `s` lands as `agentic_role_duration_seconds`; `agentic.role.tokens` with
# unit `tokens` stays `agentic_role_tokens`, no double suffix).
UNIT_SUFFIX = {"s": "seconds", "tokens": "tokens"}


def _prom_name(dotted: str, unit: str) -> str:
    name = dotted.replace(".", "_")
    suffix = UNIT_SUFFIX.get(unit.strip())
    if suffix and not name.endswith(suffix):
        name += "_" + suffix
    return name


def _documented() -> tuple[set[str], set[str]]:
    text = README.read_text(encoding="utf-8")
    metrics: set[str] = set()
    labels: set[str] = set()
    for dotted, label_col, _value_col, unit_col in ROW.findall(text):
        metrics.add(_prom_name(dotted, unit_col))
        labels.update(tok.replace(".", "_") for tok in BACKTICK.findall(label_col))
    return metrics, labels


def _exprs() -> list[str]:
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    out = []
    for panel in data["panels"]:
        for target in panel.get("targets", []):
            if "expr" in target:
                out.append(target["expr"])
    return out


def _queried(exprs: list[str]) -> tuple[set[str], set[str]]:
    metrics: set[str] = set()
    labels: set[str] = set()
    for expr in exprs:
        metrics.update(re.findall(r"\bagentic_[a-z0-9_]+", expr))
        labels.update(re.findall(r'([a-z_][a-z0-9_]*)\s*(?:=~|=)\s*"', expr))
        for group in re.findall(r"\bby\s*\(([^)]*)\)", expr):
            labels.update(tok.strip() for tok in group.split(",") if tok.strip())
    return metrics, labels


def test_dashboard_json_is_valid():
    json.loads(DASHBOARD.read_text(encoding="utf-8"))


def test_no_panel_uses_increase():
    # The metrics are events (ADR-0008): every panel reads them with sum_over_time
    # or count_over_time, never increase(), which assumes a monotonic counter.
    assert "increase(" not in DASHBOARD.read_text(encoding="utf-8")


def test_every_queried_metric_and_label_is_documented():
    documented_metrics, documented_labels = _documented()
    queried_metrics, queried_labels = _queried(_exprs())

    undocumented_metrics = queried_metrics - documented_metrics
    assert not undocumented_metrics, (
        f"dashboard.json queries metrics observability/README.md does not document: "
        f"{sorted(undocumented_metrics)}"
    )

    undocumented_labels = queried_labels - documented_labels
    assert not undocumented_labels, (
        f"dashboard.json queries label keys observability/README.md does not document: "
        f"{sorted(undocumented_labels)}"
    )
