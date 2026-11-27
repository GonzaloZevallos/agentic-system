"""The board mirror's pure core (GH-375, ADR-0073): what stage a GitHub Project card shows
for an issue, and what has to be written to put it there.

The Project is an output. Nothing here, and nothing that calls it, reads a card's value
back: the only inputs are facts about the issue itself (its labels and state, the live
registry, the `BlockerReport` of an issue with no document, the task document) and `today`.
No round scheduling result is an input. `card` is the one place Etapa and Motivo are
computed; no clock is read here, `today` and `now` always arrive as arguments.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol, get_args

from . import hold, rules, tracker_items
from .task import Status

Etapa = Literal["Te necesita", "En curso", "Próximo", "En pausa", "Hecho"]
"""The Project's `Status` field, renamed Etapa; the five options a person created."""

Field = Literal["etapa", "motivo", "costo", "inicio", "fin"]
"""The five fields the system writes. Bloque and Hito are never in this list (ADR-0073)."""
FIELDS: tuple[Field, ...] = ("etapa", "motivo", "costo", "inicio", "fin")

AGENTIC_LABEL = "agentic"
NEEDS_HUMAN_LABEL = "needs-human"
READY_FOR_HUMAN_LABEL = "ready-for-human"

BOARD_RETRY_AFTER = timedelta(minutes=15)
"""How long the mirror stays away from GitHub after a Project call failed whole."""

HECHO_DAYS = 7
"""How many days after its close day an issue stays a Hecho card; later it has no Etapa."""
EXPIRY_READS_MAX = 10
"""The most remembered closed issues one round re-reads to see them expire."""
UNREAD_MAX = 3
"""Consecutive failed reads after which an issue leaves the memory."""

MOTIVO_MAX = 200
RUNNING = "corriendo"
IN_PR = "en PR: espera CI y merge"
RETAKES_ITSELF = "se retoma sola"
WAITING_ANSWER = "esperando tu respuesta"
NEEDS_ATTENTION = "necesita tu atención"
READY_FOR_REVIEW = "lista para tu revisión"
DISCARDED = "descartada: cerrar el issue"
TRACKER_ONLY = "lista de tracker pendiente"
UNKNOWN_STATUS = "documento con estado desconocido"
NO_LIVE_PROCESS = "sin proceso vivo"
WAITING_PLACE = "esperando lugar"
HELD = "retenida: agentic-hold"
NEEDS_TRIAGE = "sin triage"
OUTSIDE = "fuera de la bandeja"
BLOCKER_PREFIX = "espera "
FALLBACK_MOTIVO: dict[Etapa, str] = {
    "Te necesita": NEEDS_ATTENTION,
    "En curso": RUNNING,
    "Próximo": WAITING_PLACE,
    "En pausa": OUTSIDE,
}
"""One non-empty Motivo per Etapa that carries one. Hecho has none."""

_STATUSES: dict[str, Status] = {status: status for status in get_args(Status)}
_IN_FLIGHT: frozenset[Status] = frozenset(
    {"open", "defined", "returned", "definition_ready", "planned", "developing"}
)
_ENDS: dict[Status, str] = {
    "discarded": DISCARDED,
    "tracker_only": TRACKER_ONLY,
    "evaluated": UNKNOWN_STATUS,
}
"""The statuses that need the human whatever else is true, and their Motivo."""


@dataclass(frozen=True)
class IssueFacts:
    """Everything `card` reads about one issue: its own state and `today`. `blocker` is only
    meaningful for an issue without a document; `resumes_itself` says the poller retakes this
    pause on its own (`rules.poller_retakes`)."""

    number: int
    closed: bool
    closed_at: date | None
    labels: frozenset[str]
    live: bool
    blocker: rules.BlockerReport | None
    document: Mapping[str, object] | None
    today: date
    resumes_itself: bool


@dataclass(frozen=True)
class Card:
    """One issue's card: the five values the Project shows. `None` is "no value"; an `etapa`
    of `None` is a card that is cleared and never added."""

    number: int
    etapa: Etapa | None
    motivo: str | None
    cost_usd: float | None
    started: date | None
    finished: date | None


def hecho_until(closed_at: date) -> date:
    """The last day a card closed on `closed_at` is Hecho (inclusive)."""
    return closed_at + timedelta(days=HECHO_DAYS)


def hecho_expired(until: date, today: date) -> bool:
    """Whether a Hecho card whose last day is `until` is gone on `today`. The one comparison."""
    return today > until


def last_hecho_day(value: Card) -> date | None:
    """The last day `value` is Hecho, or None when it is not a Hecho card with a close day.
    The one place that says which Etapa carries an expiry, so no caller names the option."""
    if value.etapa == "Hecho" and value.finished is not None:
        return hecho_until(value.finished)
    return None


def _first_line(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:MOTIVO_MAX]
    return None


def _blockers(report: rules.BlockerReport) -> str:
    numbers = {
        *report.open,
        *report.unknown,
        *(number for number, _ in report.closed_unresolved),
    }
    return BLOCKER_PREFIX + ", ".join(f"#{n}" for n in sorted(numbers))


def _needs_you(facts: IssueFacts) -> str:
    if facts.resumes_itself:
        return RETAKES_ITSELF
    line = _first_line((facts.document or {}).get("escalated"))
    return NEEDS_ATTENTION if line is None else f"{WAITING_ANSWER}: {line}"


def _attention(facts: IssueFacts, status: Status | None) -> str | None:
    """The Motivo of a Te necesita card, `None` when the issue does not need the person."""
    if status == "waiting_on_human" or NEEDS_HUMAN_LABEL in facts.labels:
        return _needs_you(facts)
    if READY_FOR_HUMAN_LABEL in facts.labels:
        return READY_FOR_REVIEW
    if facts.document is None:
        return None
    if status is None:
        return UNKNOWN_STATUS
    if status in _ENDS:
        return _ENDS[status]
    if status in _IN_FLIGHT and not facts.blocker:
        return NO_LIVE_PROCESS
    return None


def _paused(facts: IssueFacts) -> str | None:
    if hold.HOLD_LABEL in facts.labels:
        return HELD
    if tracker_items.TRIAGE_LABEL in facts.labels:
        return NEEDS_TRIAGE
    if AGENTIC_LABEL not in facts.labels:
        return OUTSIDE
    return None


def _stage(facts: IssueFacts) -> tuple[Etapa | None, str | None]:
    status = None if facts.document is None else _STATUSES.get(str(facts.document.get("status")))
    if status == "split":
        return None, None
    if facts.closed:
        if facts.closed_at is not None and hecho_expired(hecho_until(facts.closed_at), facts.today):
            return None, None
        return "Hecho", None
    if facts.live:
        return "En curso", RUNNING
    if status == "ready_for_review":
        return "En curso", IN_PR
    if (attention := _attention(facts, status)) is not None:
        return "Te necesita", attention
    if (paused := _paused(facts)) is not None:
        return "En pausa", paused
    if facts.blocker:
        return "Próximo", _blockers(facts.blocker)
    return "Próximo", WAITING_PLACE


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
    """The card for `facts`. Total, and a function of the issue's own state and `today`:
    precedence cleared (no Etapa) > Hecho > En curso > Te necesita > En pausa > Próximo.
    Motivo is set on every card but Hecho, and falls back to its Etapa's constant when the
    rule that put it there has none. A closed issue with no close day stays Hecho."""
    etapa, motivo = _stage(facts)
    if etapa == "Hecho" or etapa is None:
        motivo = None
    elif not motivo:
        motivo = FALLBACK_MOTIVO[etapa]
    return Card(
        number=facts.number,
        etapa=etapa,
        motivo=motivo,
        cost_usd=_cost(facts.document),
        started=_first_run_day(facts.document),
        finished=facts.closed_at,
    )


def fingerprint(value: Card) -> str:
    """A stable hash of the five values (not the number): equal cards, equal strings."""
    payload = json.dumps(
        [
            value.etapa,
            value.motivo,
            value.cost_usd,
            value.started.isoformat() if value.started else None,
            value.finished.isoformat() if value.finished else None,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def present(value: Card) -> frozenset[Field]:
    """The fields that carry a value on `value`: what the memory keeps beside the hash, so a
    later card without one knows whether there is something on the board to clear. A card
    with no Etapa reports no `etapa`."""
    have: dict[Field, bool] = {
        "etapa": value.etapa is not None,
        "motivo": value.motivo is not None,
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
    """What puts `value` on the board. Etapa is set when it has a value; another field is set
    when it has a value and cleared only when `previous` (the fields the last send put there)
    had one. `previous=None`, a card never sent, clears nothing.

    A card with no Etapa never adds an item: with nothing on the board for it, nothing is
    written. When `previous` had an Etapa it is cleared, with every field `previous` had and
    the card no longer carries; costo, inicio and fin are not set again."""
    if value.etapa is None:
        if previous is None or "etapa" not in previous:
            return []
        kept = present(value)
        return [
            FieldWrite(value.number, name, None)
            for name in FIELDS
            if name in previous and (name == "etapa" or name not in kept)
        ]
    values: dict[Field, str | float | date | None] = {
        "etapa": value.etapa,
        "motivo": value.motivo,
        "costo": value.cost_usd,
        "inicio": value.started,
        "fin": value.finished,
    }
    return [
        FieldWrite(value.number, name, values[name])
        for name in FIELDS
        if values[name] is not None or (previous is not None and name in previous)
    ]


class Remembered(Protocol):
    """What `expiry_due` reads of a remembered issue: the last day it is Hecho, when it was
    sent as a Hecho card, and how many reads in a row failed."""

    @property
    def hecho_until(self) -> date | None: ...

    @property
    def unread(self) -> int: ...


def expiry_due(entries: Mapping[int, Remembered], today: date) -> list[int]:
    """The remembered issues whose Hecho card is gone on `today` and so must be re-read to
    clear it: oldest `hecho_until` first, then issue number, those that failed to read last,
    at most `EXPIRY_READS_MAX`."""
    due = [
        (entry.unread > 0, entry.hecho_until, number)
        for number, entry in entries.items()
        if entry.hecho_until is not None and hecho_expired(entry.hecho_until, today)
    ]
    ordered = sorted(due, key=lambda item: (item[0], item[1] or date.min, item[2]))
    return [number for _, _, number in ordered[:EXPIRY_READS_MAX]]


def dropped(unread: int) -> bool:
    """Whether an issue that failed to read `unread` times in a row leaves the memory."""
    return unread >= UNREAD_MAX
