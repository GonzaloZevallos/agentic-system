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

# A label cell that inherits another row's set instead of spelling out its own:
# "ídem" (the row right above), "ídem que `metric.name`" (a named row, used when the
# row above is not the real base — issue #24 found that chaining through an
# intermediate "ídem" row silently drops or leaks a label), each optionally followed
# by "sin `x`, `y`" (removed) and/or "+ `x`" (added).
IDEM_OF = re.compile(r"^ídem que `([a-z_.]+)`\s*(.*)$")
SIN = re.compile(r"sin\s+`([a-z_.]+)`")

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


def _resolve_labels(cell: str, previous: set[str] | None, by_name: dict[str, set[str]]) -> set[str]:
    """One row's label cell to its own resolved set of Prometheus label names, given
    the row immediately above it (for a bare "ídem") and every row already resolved
    (for "ídem que `metric`"). A cell with no "ídem" at all is a full, explicit list:
    it never inherits, so it cannot be broken by an edit to an unrelated earlier row."""
    text = cell.strip()
    if m := IDEM_OF.match(text):
        base, rest = set(by_name[m.group(1)]), m.group(2)
    elif text == "ídem" or text.startswith("ídem "):
        base, rest = set(previous or set()), text[len("ídem") :].strip()
    else:
        base, rest = set(), text
    removed = {tok.replace(".", "_") for tok in SIN.findall(rest)}
    kept = {tok.replace(".", "_") for tok in BACKTICK.findall(rest)} - removed
    return (base - removed) | kept


def _documented() -> dict[str, set[str]]:
    """Prometheus metric name -> its own resolved label set, one entry per README row,
    in table order so "ídem" always resolves against something already computed."""
    text = README.read_text(encoding="utf-8")
    by_dotted: dict[str, set[str]] = {}
    by_prom: dict[str, set[str]] = {}
    previous: set[str] | None = None
    for dotted, label_col, _value_col, unit_col in ROW.findall(text):
        labels = _resolve_labels(label_col, previous, by_dotted)
        by_dotted[dotted] = labels
        by_prom[_prom_name(dotted, unit_col)] = labels
        previous = labels
    return by_prom


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
            selector_labels = set(re.findall(r'([a-z_][a-z0-9_]*)\s*(?:=~|=)\s*"', body))
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
