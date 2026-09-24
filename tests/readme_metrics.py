"""Reads `observability/README.md`'s metric table as data (ADR-0036): one row per
family that used to leave a Prometheus/Grafana Cloud sample, mapped to the span
attribute key that carries the same fact now and the span it lives on ("rol", "tarea"
or "invocación"), or to `—` when the fact has no span home at all -- in which case the
row's own prose names its reader (`agentic eval compare`, the task document, or a query
against the Langfuse API).

The one consumer left is `tests/core/measurement/adapters/otel/test_telemetry.py`:
`test_dashboard.py`'s own cross-check against `dashboard.json` died with the Grafana
dashboard it read (ADR-0036, decision 1)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "observability" / "README.md"

TABLE_HEADING = "## Métricas"
GRAINS = ("rol", "tarea", "invocación", "—")
# `` `agentic.old.name` `` | new cell | grano | qué responde ahora.
ROW = re.compile(
    r"^\|\s*`(agentic\.[a-z_.]+)`\s*\|\s*(.+?)\s*\|\s*(rol|tarea|invocación|—)\s*\|(.*)\|\s*$",
    re.M,
)
ATTRIBUTE = re.compile(r"^`([a-zA-Z_.]+)`$")
# The named readers decision 5 allows for a family with no span home: `agentic eval
# compare` (the task-document corpus), the task document itself, or a query against
# the Langfuse API (a trace's own timestamps, or a count of traces sharing a tag).
READERS = ("agentic eval compare", "documento de tarea", "API de Langfuse")


class Family:
    """One resolved row. Raises, naming the family, on the two shapes decision 5 and
    item 4 rule out: a lost row with no named reader, and a kept row whose cell is not
    a single backtick-quoted attribute key -- the rejecting input this parser exists to
    catch, exercised at `test_telemetry.py`'s own seam
    (`test_a_lost_row_with_no_named_reader_is_rejected`)."""

    def __init__(self, old: str, cell: str, grain: str, note: str) -> None:
        self.old = old
        self.grain = grain
        self.note = note
        if grain == "—":
            if not any(reader in note for reader in READERS):
                raise AssertionError(f"{old!r} under {TABLE_HEADING!r} names no reader")
            self.attribute: str | None = None
        else:
            match = ATTRIBUTE.match(cell)
            if not match:
                raise AssertionError(f"{old!r} under {TABLE_HEADING!r} has no attribute key")
            self.attribute = match.group(1)


def _families() -> list[Family]:
    text = README.read_text(encoding="utf-8")
    if TABLE_HEADING not in text:
        raise AssertionError(f"{TABLE_HEADING!r} heading not found in {README}")
    section = text.split(TABLE_HEADING, 1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    rows = ROW.findall(section)
    if not rows:
        raise AssertionError(f"no family row found under {TABLE_HEADING!r} in {README}")
    return [Family(*row) for row in rows]


def _documented(grain: str) -> set[str]:
    """Every attribute key documented as living on the span named by `grain` ("rol",
    "tarea" or "invocación"). A lost row (`grain == "—"`) never contributes a key: its
    own prose is checked at parse time instead (`Family.__init__`)."""
    if grain not in GRAINS:
        raise ValueError(grain)
    return {f.attribute for f in _families() if f.grain == grain and f.attribute}


def _lost() -> set[str]:
    """The old Prometheus names of every family this task's table marks as lost -- the
    normative list ADR-0036 points at instead of restating (`observability/README.md`)."""
    return {f.old for f in _families() if f.grain == "—"}
