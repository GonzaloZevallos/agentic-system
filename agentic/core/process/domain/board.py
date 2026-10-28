"""The board mirror's pure core (GH-375, ADR-0071): what stage a GitHub Project card shows
for an issue, and what has to be written to put it there.

The Project is an output. Nothing here, and nothing that calls it, reads a card's value
back: the only inputs are facts the poller already holds (the issue's labels and state, the
live registry, the `BlockerReport` of an issue with no document, the task document, the
evaluation set). `card` is the one place Etapa and Bloqueo are computed; no clock is read
here, `now` always arrives as an argument.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, assert_never, get_args

from . import rules
from .task import Status

Etapa = Literal["Corriendo", "Trabada", "Necesita humano", "En cola", "Fuera del sistema", "Hecha"]
"""The Project's `Status` field, renamed Etapa; the six options a person created."""

Field = Literal["etapa", "bloqueo", "costo", "inicio", "fin"]
"""The five fields the system writes. Bloque and Hito are never in this list (ADR-0071)."""
FIELDS: tuple[Field, ...] = ("etapa", "bloqueo", "costo", "inicio", "fin")

AGENTIC_LABEL = "agentic"
NEEDS_HUMAN_LABEL = "needs-human"
READY_FOR_HUMAN_LABEL = "ready-for-human"

BOARD_RETRY_AFTER = timedelta(minutes=15)
"""How long the mirror stays away from GitHub after a Project call failed whole."""

BLOQUEO_MAX = 200
NO_LIVE_PROCESS = "sin proceso vivo: retomar o esperar cupo"
PR_WAITING = "PR esperando merge"
MERGE_REFUSED = "merge rechazado: esperando aterrizaje"
DISCARDED = "descartada: cerrar el issue"
SPLIT = "partida: cerrar el issue"
TRACKER_ONLY = "lista de tracker pendiente"
UNKNOWN_STATUS = "documento con estado desconocido"

_STATUSES: dict[str, Status] = {status: status for status in get_args(Status)}


@dataclass(frozen=True)
class IssueFacts:
    """Everything `card` reads about one issue. `blocker` is only meaningful for an issue
    without a document; `in_eval_set` is `None` when the evaluation set could not be read."""

    number: int
    closed: bool
    closed_at: date | None
    labels: frozenset[str]
    live: bool
    blocker: rules.BlockerReport | None
    document: Mapping[str, object] | None
    in_eval_set: bool | None


@dataclass(frozen=True)
class Card:
    """One issue's card: the five values the Project shows. `None` is "no value"."""

    number: int
    etapa: Etapa
    bloqueo: str | None
    cost_usd: float | None
    started: date | None
    finished: date | None


def _first_line(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:BLOQUEO_MAX]
    return None


def _blockers(report: rules.BlockerReport) -> str:
    numbers = {
        *report.open,
        *report.unknown,
        *(number for number, _ in report.closed_unresolved),
    }
    return ", ".join(f"#{n}" for n in sorted(numbers))


def _merge_refused(document: Mapping[str, object]) -> bool:
    """A refused merge is on record: the merge verb wrote `merge_failures`, or the last
    `landing` entry is still open (the landing owes the pull request a fix)."""
    if document.get("merge_failures"):
        return True
    entries = document.get("landing")
    if not isinstance(entries, list) or not entries:
        return False
    last = entries[-1]
    return isinstance(last, Mapping) and not rules.entry_closed(last) and not rules.before_pr(last)


def _mapped(facts: IssueFacts) -> tuple[Etapa, str | None]:
    """The map for an open, `agentic` issue with no live process."""
    document = facts.document
    if document is None:
        if facts.blocker:
            return "Trabada", _blockers(facts.blocker)
        return "En cola", None
    status = _STATUSES.get(str(document.get("status")))
    if status is None:
        return "Necesita humano", UNKNOWN_STATUS
    match status:
        case "open" | "defined" | "returned" | "definition_ready" | "planned" | "developing":
            return "Trabada", NO_LIVE_PROCESS
        case "waiting_on_human":
            return "Necesita humano", _first_line(document.get("escalated"))
        case "ready_for_review":
            if facts.in_eval_set is not False:
                return "Necesita humano", PR_WAITING
            if _merge_refused(document):
                return "Trabada", MERGE_REFUSED
            return "En cola", None
        case "discarded":
            return "Necesita humano", DISCARDED
        case "split":
            return "Necesita humano", SPLIT
        case "tracker_only":
            return "Necesita humano", TRACKER_ONLY
        case _:
            assert_never(status)


def _stage(facts: IssueFacts) -> tuple[Etapa, str | None]:
    if facts.closed:
        return "Hecha", None
    if facts.live:
        return "Corriendo", None
    if NEEDS_HUMAN_LABEL in facts.labels:
        return "Necesita humano", _first_line((facts.document or {}).get("escalated"))
    if AGENTIC_LABEL not in facts.labels or READY_FOR_HUMAN_LABEL in facts.labels:
        return "Fuera del sistema", None
    return _mapped(facts)


def _cost(document: Mapping[str, object] | None) -> float | None:
    return None if document is None else round(rules.task_spent(document), 2)


def _utc_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).date()


def _first_run_day(document: Mapping[str, object] | None) -> date | None:
    runs = (document or {}).get("runs")
    days = [
        day
        for run in (runs if isinstance(runs, list) else [])
        if isinstance(run, Mapping) and (day := _utc_date(run.get("started_at"))) is not None
    ]
    return min(days, default=None)


def card(facts: IssueFacts) -> Card:
    """The card for `facts`. Total: precedence Hecha > Corriendo > Necesita humano > Fuera
    del sistema > Trabada > En cola. Bloqueo is set only on Trabada and Necesita humano."""
    etapa, bloqueo = _stage(facts)
    return Card(
        number=facts.number,
        etapa=etapa,
        bloqueo=bloqueo if etapa in ("Trabada", "Necesita humano") else None,
        cost_usd=_cost(facts.document),
        started=_first_run_day(facts.document),
        finished=facts.closed_at,
    )


def fingerprint(value: Card) -> str:
    """A stable hash of the five values (not the number): equal cards, equal strings."""
    payload = json.dumps(
        [
            value.etapa,
            value.bloqueo,
            value.cost_usd,
            value.started.isoformat() if value.started else None,
            value.finished.isoformat() if value.finished else None,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def present(value: Card) -> frozenset[Field]:
    """The fields that carry a value on `value`: what the memory keeps beside the hash, so a
    later card without one knows whether there is something on the board to clear."""
    have: dict[Field, bool] = {
        "etapa": True,
        "bloqueo": value.bloqueo is not None,
        "costo": value.cost_usd is not None,
        "inicio": value.started is not None,
        "fin": value.finished is not None,
    }
    return frozenset(name for name, there in have.items() if there)


def due(cards: Sequence[Card], sent: Mapping[int, str]) -> list[Card]:
    """The cards whose fingerprint differs from what was last sent (or was never sent)."""
    return [c for c in cards if sent.get(c.number) != fingerprint(c)]


def retry_due(failed_at: datetime | None, now: datetime) -> bool:
    """Whether the mirror may call GitHub: no whole-call failure on record, or the wait
    (`BOARD_RETRY_AFTER`) is over. A `failed_at` in the future (a clock set back) waits."""
    return failed_at is None or now - failed_at >= BOARD_RETRY_AFTER


@dataclass(frozen=True)
class FieldWrite:
    """One field of one card: `value` set, or `None` to clear it."""

    issue: int
    field: Field
    value: str | float | date | None


def writes(value: Card, previous: frozenset[Field] | None) -> list[FieldWrite]:
    """What puts `value` on the board. Etapa is always set; another field is set when it has
    a value and cleared only when `previous` (the fields the last send put there) had one.
    `previous=None`, a card never sent, clears nothing."""
    values: dict[Field, str | float | date | None] = {
        "etapa": value.etapa,
        "bloqueo": value.bloqueo,
        "costo": value.cost_usd,
        "inicio": value.started,
        "fin": value.finished,
    }
    return [
        FieldWrite(value.number, name, values[name])
        for name in FIELDS
        if values[name] is not None or (previous is not None and name in previous)
    ]
