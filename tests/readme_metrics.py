"""Reads `observability/README.md`'s metric table as data: one entry per row, the
Prometheus name the OTel-to-Prometheus bridge would give the metric, mapped to that
row's own resolved set of Prometheus label names.

Shared by `tests/test_dashboard.py` (README row vs. `dashboard.json` query) and
`tests/core/measurement/adapters/otel/test_telemetry.py` (README row vs. what
`telemetry.py` actually emits): both cross-check the same table against a different
half of the system, so both read it through the one function that resolves a row.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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
