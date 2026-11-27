"""GH-375, ADR-0073: the board mirror's pure core. `card` maps the facts the poller reads to
the stage a Project card shows; `fingerprint`, `due`, `writes` and `retry_due` decide what is
sent and when."""

import itertools
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.domain import board, rules
from agentic.core.process.domain.board import Card, IssueFacts
from agentic.core.process.domain.task import Status

AGENTIC = frozenset({"agentic"})
TODAY = date(2026, 11, 27)


def _facts(**kwargs) -> IssueFacts:
    base = {
        "number": 7,
        "closed": False,
        "closed_at": None,
        "labels": AGENTIC,
        "live": False,
        "blocker": None,
        "document": None,
        "today": TODAY,
        "resumes_itself": False,
    }
    return IssueFacts(**{**base, **kwargs})


def _doc(status, **extra) -> dict:
    return {"status": status, **extra}


def _got(**kwargs) -> tuple[str | None, str | None]:
    got = board.card(_facts(**kwargs))
    return got.etapa, got.motivo


BLOCKED = rules.BlockerReport(
    open=(12,), unknown=(5,), closed_unresolved=((9, "no_merge_recorded"),)
)
NEEDS_HUMAN = AGENTIC | {"needs-human"}
WAITING = _doc("waiting_on_human", escalated="needs a decision\nsecond line")

# -- the request's table, one row each ---------------------------------------------------

ROWS = [
    ({"live": True}, ("En curso", "corriendo")),
    ({"document": _doc("ready_for_review")}, ("En curso", "en PR: espera CI y merge")),
    (
        {"document": WAITING},
        ("Te necesita", "esperando tu respuesta: needs a decision"),
    ),
    ({"document": WAITING, "resumes_itself": True}, ("Te necesita", "se retoma sola")),
    ({"document": _doc("waiting_on_human")}, ("Te necesita", "necesita tu atención")),
    ({"labels": NEEDS_HUMAN}, ("Te necesita", "necesita tu atención")),
    (
        {"labels": NEEDS_HUMAN, "document": _doc("developing", escalated="stuck\nmore")},
        ("Te necesita", "esperando tu respuesta: stuck"),
    ),
    (
        {"labels": NEEDS_HUMAN, "resumes_itself": True},
        ("Te necesita", "se retoma sola"),
    ),
    ({"labels": AGENTIC | {"ready-for-human"}}, ("Te necesita", "lista para tu revisión")),
    ({"document": _doc("discarded")}, ("Te necesita", "descartada: cerrar el issue")),
    ({"document": _doc("tracker_only")}, ("Te necesita", "lista de tracker pendiente")),
    ({"document": _doc("from-the-future")}, ("Te necesita", "documento con estado desconocido")),
    ({"document": _doc("evaluated")}, ("Te necesita", "documento con estado desconocido")),
    ({"document": _doc("developing")}, ("Te necesita", "sin proceso vivo")),
    ({"blocker": BLOCKED}, ("Próximo", "espera #5, #9, #12")),
    (
        {"document": _doc("definition_ready"), "blocker": rules.BlockerReport(open=(12,))},
        ("Próximo", "espera #12"),
    ),
    ({}, ("Próximo", "esperando lugar")),
    ({"blocker": rules.BlockerReport()}, ("Próximo", "esperando lugar")),
    ({"labels": AGENTIC | {"agentic-hold"}}, ("En pausa", "retenida: agentic-hold")),
    ({"labels": AGENTIC | {"needs-triage"}}, ("En pausa", "sin triage")),
    ({"labels": frozenset()}, ("En pausa", "fuera de la bandeja")),
    ({"closed": True, "closed_at": TODAY - timedelta(days=7)}, ("Hecho", None)),
    ({"closed": True}, ("Hecho", None)),
    ({"closed": True, "closed_at": TODAY - timedelta(days=8)}, (None, None)),
    ({"document": _doc("split")}, (None, None)),
]


@pytest.mark.parametrize(("kwargs", "expected"), ROWS)
def test_each_row_of_the_table_has_the_facts_that_put_a_card_there(kwargs, expected):
    assert _got(**kwargs) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # cleared > Hecho
        ({"closed": True, "closed_at": TODAY, "document": _doc("split")}, (None, None)),
        # Hecho > En curso
        ({"closed": True, "live": True, "labels": NEEDS_HUMAN}, ("Hecho", None)),
        # En curso > Te necesita
        ({"live": True, "labels": NEEDS_HUMAN}, ("En curso", "corriendo")),
        ({"document": _doc("ready_for_review"), "labels": NEEDS_HUMAN}, ("En curso", board.IN_PR)),
        # Te necesita > En pausa
        ({"labels": NEEDS_HUMAN | {"agentic-hold"}}, ("Te necesita", "necesita tu atención")),
        ({"document": _doc("discarded"), "labels": frozenset()}, ("Te necesita", board.DISCARDED)),
        # En pausa > Próximo
        ({"labels": AGENTIC | {"agentic-hold"}, "blocker": BLOCKED}, ("En pausa", board.HELD)),
        ({"labels": frozenset(), "blocker": BLOCKED}, ("En pausa", board.OUTSIDE)),
    ],
)
def test_the_precedence_is_cleared_hecho_en_curso_te_necesita_en_pausa_proximo(kwargs, expected):
    assert _got(**kwargs) == expected


def test_a_closed_issue_is_hecho_with_its_close_day_as_fin():
    got = board.card(_facts(closed=True, closed_at=date(2026, 11, 20)))
    assert (got.etapa, got.finished) == ("Hecho", date(2026, 11, 20))


def test_the_reason_is_cut_to_200_characters_and_one_line():
    got = board.card(_facts(document=_doc("waiting_on_human", escalated="\n" + "x" * 500)))
    assert got.motivo == "esperando tu respuesta: " + "x" * 200


def test_every_status_is_handled_by_the_tables_or_by_name():
    named = {"waiting_on_human", "ready_for_review", "split"}
    assert set(get_args(Status)) == named | board._IN_FLIGHT | set(board._ENDS)


def test_the_hecho_day_is_the_last_one_inclusive_and_expiry_has_one_comparison():
    until = board.hecho_until(date(2026, 11, 20))
    assert until == date(2026, 11, 27)
    assert not board.hecho_expired(until, until)
    assert board.hecho_expired(until, until + timedelta(days=1))


def test_the_motivo_fallbacks_are_non_empty_and_cover_every_etapa_but_hecho():
    assert set(board.FALLBACK_MOTIVO) == set(get_args(board.Etapa)) - {"Hecho"}
    assert all(board.FALLBACK_MOTIVO.values())


# -- one Etapa and a Motivo on every card that is not Hecho ---------------------------------


def test_every_combination_of_facts_gives_an_etapa_or_none_and_a_motivo_unless_hecho():
    labels = [
        AGENTIC,
        frozenset(),
        NEEDS_HUMAN,
        AGENTIC | {"ready-for-human"},
        AGENTIC | {"agentic-hold"},
        AGENTIC | {"needs-triage"},
    ]
    reports = [None, rules.BlockerReport(), rules.BlockerReport(open=(3,))]
    documents = [
        None,
        _doc("from-the-future"),
        *(_doc(s, escalated="why") for s in get_args(Status)),
    ]
    closings = [
        (False, None),
        (True, None),
        (True, TODAY - timedelta(days=7)),
        (True, TODAY - timedelta(days=8)),
    ]
    count = 0
    for (closed, closed_at), live, label, report, document, resumes in itertools.product(
        closings, [False, True], labels, reports, documents, [False, True]
    ):
        got = board.card(
            _facts(
                closed=closed,
                closed_at=closed_at,
                live=live,
                labels=label,
                blocker=report,
                document=document,
                resumes_itself=resumes,
            )
        )
        assert got.etapa in (*get_args(board.Etapa), None)
        if got.etapa not in ("Hecho", None):
            assert got.motivo
        else:
            assert got.motivo is None
        if closed and got.etapa is not None:
            assert got.etapa == "Hecho"
        count += 1
    assert count == 4 * 2 * len(labels) * len(reports) * len(documents) * 2


def test_identical_facts_give_an_identical_card_and_fingerprint():
    facts = _facts(document=_doc("developing", cost_usd=1.0), blocker=BLOCKED)
    first, again = board.card(facts), board.card(facts)
    assert first == again and board.fingerprint(first) == board.fingerprint(again)


# -- cost, start, finish -----------------------------------------------------------------


def test_cost_is_the_task_spend_rounded_to_cents_and_carried_cost_counts():
    document = _doc("developing", cost_usd=1.234, landing_cost_carried=2.0)
    got = board.card(_facts(document=document)).cost_usd
    assert got == round(rules.task_spent(document), 2) == 3.23


def test_cost_is_none_without_a_document():
    assert board.card(_facts()).cost_usd is None


def test_inicio_is_the_earliest_run_start_as_a_utc_date():
    runs = [
        {"started_at": "2026-10-21T10:30:00.000-03:00"},
        {"started_at": "2026-10-20T10:00:00Z"},
        {"role": "no start"},
        {"started_at": "garbage"},
    ]
    assert board.card(_facts(document=_doc("developing", runs=runs))).started == date(2026, 10, 20)


def test_inicio_converts_to_utc_before_taking_the_day():
    runs = [{"started_at": "2026-10-21T23:30:00-03:00"}]
    assert board.card(_facts(document=_doc("developing", runs=runs))).started == date(2026, 10, 22)


def test_inicio_is_none_when_no_run_has_a_start():
    assert board.card(_facts(document=_doc("developing", runs=[]))).started is None


# -- what is sent -------------------------------------------------------------------------


def _card(**kwargs) -> Card:
    base = {
        "number": 7,
        "etapa": "Próximo",
        "motivo": None,
        "cost_usd": None,
        "started": None,
        "finished": None,
    }
    return Card(**{**base, **kwargs})


def test_the_fingerprint_is_stable_and_changes_with_any_of_the_five_fields():
    base = _card()
    assert board.fingerprint(base) == board.fingerprint(_card())
    changed = [
        _card(etapa="En pausa"),
        _card(motivo="x"),
        _card(cost_usd=0.01),
        _card(started=date(2026, 1, 1)),
        _card(finished=date(2026, 1, 1)),
        _card(etapa=None),
    ]
    assert len({board.fingerprint(c) for c in [base, *changed]}) == 7


def test_due_returns_only_the_cards_that_differ_from_what_was_sent():
    same, different, new = _card(number=1), _card(number=2, etapa="Hecho"), _card(number=3)
    sent = {1: board.fingerprint(same), 2: board.fingerprint(_card(number=2))}
    assert board.due([same, different, new], sent) == [different, new]


def test_nothing_is_due_when_everything_was_sent():
    cards = [_card(number=n) for n in range(4)]
    assert board.due(cards, {c.number: board.fingerprint(c) for c in cards}) == []


def test_present_reports_etapa_only_when_there_is_one():
    assert "etapa" in board.present(_card())
    assert "etapa" not in board.present(_card(etapa=None, finished=date(2026, 1, 1)))


def test_a_new_card_sets_only_the_fields_it_has_and_clears_nothing():
    got = board.writes(_card(cost_usd=1.5), None)
    assert [(w.field, w.value) for w in got] == [("etapa", "Próximo"), ("costo", 1.5)]


def test_a_field_that_lost_its_value_is_cleared_only_if_the_last_send_had_one():
    now = _card(etapa="Próximo")
    had = board.present(_card(etapa="En pausa", motivo="x"))
    assert [(w.field, w.value) for w in board.writes(now, had)] == [
        ("etapa", "Próximo"),
        ("motivo", None),
    ]
    assert [w.field for w in board.writes(now, board.present(now))] == ["etapa"]


def test_a_card_with_no_etapa_never_adds_an_item():
    cleared = _card(etapa=None, finished=date(2026, 1, 1), cost_usd=2.0)
    assert board.writes(cleared, None) == []
    assert board.writes(cleared, frozenset({"costo", "fin"})) == []


def test_a_hecho_that_expires_clears_only_etapa_and_what_the_card_no_longer_carries():
    cleared = _card(etapa=None, finished=date(2026, 1, 1), cost_usd=2.0)
    previous = frozenset({"etapa", "motivo", "costo", "fin"})
    assert [(w.field, w.value) for w in board.writes(cleared, previous)] == [
        ("etapa", None),
        ("motivo", None),
    ]


def test_the_system_writes_no_field_outside_the_five():
    assert set(board.FIELDS) == {"etapa", "motivo", "costo", "inicio", "fin"}
    everything = _card(
        motivo="b", cost_usd=1.0, started=date(2026, 1, 1), finished=date(2026, 1, 2)
    )
    assert {w.field for w in board.writes(everything, None)} == set(board.FIELDS)


def test_the_retry_wait_is_fifteen_minutes_after_a_whole_failure():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert board.retry_due(None, now)
    assert not board.retry_due(now - timedelta(minutes=14), now)
    assert board.retry_due(now - timedelta(minutes=15), now)
    assert not board.retry_due(now + timedelta(minutes=1), now)


def test_the_document_deliver_leaves_with_one_human_item_needs_you_with_its_motivo():
    from agentic.core.process.domain import tracker_items

    item = {"kind": "human", "question": "Ship the flag?", "recommendation": "Yes"}
    state = {"id": "GH-12", "tracker_items": [item], "status": "ready_for_review"}
    patch = tracker_items.escalate_human(state, "2026-11-13T00:00:00+00:00")
    got = board.card(_facts(document={**state, **patch, "pull_request": "https://x/pull/7"}))
    assert got.etapa == "Te necesita"
    assert got.motivo == "esperando tu respuesta: the plan leaves 1 decision(s) to the owner"


# -- the remembered Hecho cards that expire ----------------------------------------------


@dataclass(frozen=True)
class _Entry:
    hecho_until: date | None
    unread: int = 0


def test_expiry_due_lists_only_the_expired_oldest_first_then_by_number():
    entries = {
        5: _Entry(TODAY - timedelta(days=1)),
        3: _Entry(TODAY - timedelta(days=1)),
        9: _Entry(TODAY - timedelta(days=4)),
        1: _Entry(TODAY),  # last day: not due yet
        2: _Entry(None),  # not a Hecho card
    }
    assert board.expiry_due(entries, TODAY) == [9, 3, 5]


def test_fifteen_due_entries_are_read_ten_then_the_other_five():
    entries = {n: _Entry(TODAY - timedelta(days=n)) for n in range(1, 16)}
    first = board.expiry_due(entries, TODAY)
    assert first == list(range(15, 5, -1)) and len(first) == board.EXPIRY_READS_MAX
    rest = {n: e for n, e in entries.items() if n not in first}
    assert board.expiry_due(rest, TODAY) == [5, 4, 3, 2, 1]


def test_an_entry_that_failed_to_read_goes_last():
    entries = {1: _Entry(TODAY - timedelta(days=9), unread=1), 2: _Entry(TODAY - timedelta(days=1))}
    assert board.expiry_due(entries, TODAY) == [2, 1]


def test_an_issue_leaves_the_memory_at_three_failed_reads():
    assert [board.dropped(n) for n in (0, 1, 2, 3, 4)] == [False, False, False, True, True]
