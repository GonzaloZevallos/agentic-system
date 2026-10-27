"""GH-375, ADR-0069: the board mirror's pure core. `card` maps the facts the poller reads to
the stage a Project card shows; `fingerprint`, `due`, `writes` and `retry_due` decide what is
sent and when."""

import itertools
from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.domain import board, rules
from agentic.core.process.domain.board import Card, IssueFacts
from agentic.core.process.domain.task import Status

AGENTIC = frozenset({"agentic"})


def _facts(**kwargs) -> IssueFacts:
    base = {
        "number": 7,
        "closed": False,
        "closed_at": None,
        "labels": AGENTIC,
        "live": False,
        "blocker": None,
        "document": None,
        "in_eval_set": False,
    }
    return IssueFacts(**{**base, **kwargs})


def _doc(status, **extra) -> dict:
    return {"status": status, **extra}


# -- the request's table, one row each ---------------------------------------------------


def test_a_live_process_is_corriendo():
    assert board.card(_facts(live=True)).etapa == "Corriendo"


def test_waiting_on_human_is_necesita_humano_with_the_first_line_of_the_reason():
    reason = "needs a decision\nsecond line\nthird"
    got = board.card(_facts(document=_doc("waiting_on_human", escalated=reason)))
    assert (got.etapa, got.bloqueo) == ("Necesita humano", "needs a decision")


def test_the_needs_human_label_is_necesita_humano_without_a_reason_to_assert():
    got = board.card(_facts(labels=AGENTIC | {"needs-human"}))
    assert (got.etapa, got.bloqueo) == ("Necesita humano", None)


def test_a_paused_task_with_no_live_process_is_trabada_with_a_neutral_text():
    got = board.card(_facts(document=_doc("developing")))
    assert (got.etapa, got.bloqueo) == ("Trabada", "sin proceso vivo: retomar o esperar cupo")


def test_open_agentic_nothing_live_not_blocked_is_en_cola():
    got = board.card(_facts())
    assert (got.etapa, got.bloqueo) == ("En cola", None)


def test_an_issue_without_agentic_is_fuera_del_sistema():
    assert board.card(_facts(labels=frozenset())).etapa == "Fuera del sistema"


def test_ready_for_human_is_fuera_del_sistema():
    got = board.card(_facts(labels=AGENTIC | {"ready-for-human"}))
    assert got.etapa == "Fuera del sistema"


def test_a_closed_issue_is_hecha_with_its_close_day_as_fin():
    got = board.card(_facts(closed=True, closed_at=date(2026, 10, 20)))
    assert (got.etapa, got.finished) == ("Hecha", date(2026, 10, 20))


# -- the map for an open agentic issue with nothing live ---------------------------------


def test_a_blocked_issue_with_no_document_lists_every_blocker_it_waits_on():
    report = rules.BlockerReport(
        open=(12,), unknown=(5,), closed_unresolved=((9, "no_merge_recorded"),)
    )
    got = board.card(_facts(blocker=report))
    assert (got.etapa, got.bloqueo) == ("Trabada", "#5, #9, #12")


def test_an_empty_blocker_report_does_not_block():
    assert board.card(_facts(blocker=rules.BlockerReport())).etapa == "En cola"


@pytest.mark.parametrize(
    "status", ["open", "defined", "returned", "definition_ready", "planned", "developing"]
)
def test_every_in_flight_status_with_no_process_is_trabada(status):
    got = board.card(_facts(document=_doc(status)))
    assert (got.etapa, got.bloqueo) == ("Trabada", board.NO_LIVE_PROCESS)


def test_the_reason_is_cut_to_200_characters_and_one_line():
    got = board.card(_facts(document=_doc("waiting_on_human", escalated="\n" + "x" * 500)))
    assert got.bloqueo == "x" * 200


@pytest.mark.parametrize("in_eval", [True, None])
def test_ready_for_review_a_human_merges_or_cannot_be_ruled_out_is_necesita_humano(in_eval):
    got = board.card(_facts(document=_doc("ready_for_review"), in_eval_set=in_eval))
    assert (got.etapa, got.bloqueo) == ("Necesita humano", "PR esperando merge")


def test_ready_for_review_with_a_merge_failure_on_record_is_trabada():
    document = _doc("ready_for_review", merge_failures={"head": "a", "count": 1, "failure": "x"})
    got = board.card(_facts(document=document))
    assert (got.etapa, got.bloqueo) == ("Trabada", "merge rechazado: esperando aterrizaje")


def test_ready_for_review_with_an_open_landing_entry_is_trabada():
    document = _doc("ready_for_review", landing=[{"head": "a", "step": "fix_important"}])
    assert board.card(_facts(document=document)).etapa == "Trabada"


def test_ready_for_review_with_every_landing_entry_closed_is_en_cola():
    landing = [{"head": "a", "step": "fix_important", "resolved_at": "2026-10-20T00:00:00Z"}]
    got = board.card(_facts(document=_doc("ready_for_review", landing=landing)))
    assert (got.etapa, got.bloqueo) == ("En cola", None)


def test_ready_for_review_the_system_merges_is_en_cola():
    assert board.card(_facts(document=_doc("ready_for_review"))).etapa == "En cola"


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("discarded", "descartada: cerrar el issue"),
        ("split", "partida: cerrar el issue"),
        ("tracker_only", "lista de tracker pendiente"),
    ],
)
def test_the_ends_without_a_pull_request_wait_for_the_human_to_close_the_issue(status, text):
    got = board.card(_facts(document=_doc(status)))
    assert (got.etapa, got.bloqueo) == ("Necesita humano", text)


def test_a_status_the_map_does_not_know_asks_a_human_rather_than_guessing():
    got = board.card(_facts(document=_doc("from-the-future")))
    assert got.etapa == "Necesita humano"


def test_every_status_has_an_explicit_etapa():
    seen = {}
    for status in get_args(Status):
        seen[status] = board.card(_facts(document=_doc(status), in_eval_set=False)).etapa
    assert set(seen) == set(get_args(Status))
    assert set(seen.values()) <= set(get_args(board.Etapa))


# -- one Etapa and a Bloqueo only where it means something --------------------------------


def test_every_combination_of_facts_gives_one_etapa_and_bloqueo_only_where_it_applies():
    labels = [AGENTIC, frozenset(), AGENTIC | {"needs-human"}, AGENTIC | {"ready-for-human"}]
    reports = [None, rules.BlockerReport(open=(3,))]
    documents = [None, *(_doc(s, escalated="why") for s in get_args(Status))]
    count = 0
    for closed, live, label, report, document, in_eval in itertools.product(
        [False, True], [False, True], labels, reports, documents, [True, False, None]
    ):
        got = board.card(
            _facts(
                closed=closed,
                live=live,
                labels=label,
                blocker=report,
                document=document,
                in_eval_set=in_eval,
            )
        )
        assert got.etapa in get_args(board.Etapa)
        if got.etapa not in ("Trabada", "Necesita humano"):
            assert got.bloqueo is None
        if closed:
            assert got.etapa == "Hecha"
        elif live:
            assert got.etapa == "Corriendo"
        count += 1
    assert count == 2 * 2 * 4 * 2 * len(documents) * 3


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
        "etapa": "En cola",
        "bloqueo": None,
        "cost_usd": None,
        "started": None,
        "finished": None,
    }
    return Card(**{**base, **kwargs})


def test_the_fingerprint_is_stable_and_changes_with_any_of_the_five_fields():
    base = _card()
    assert board.fingerprint(base) == board.fingerprint(_card())
    changed = [
        _card(etapa="Trabada"),
        _card(bloqueo="x"),
        _card(cost_usd=0.01),
        _card(started=date(2026, 1, 1)),
        _card(finished=date(2026, 1, 1)),
    ]
    assert len({board.fingerprint(c) for c in [base, *changed]}) == 6


def test_due_returns_only_the_cards_that_differ_from_what_was_sent():
    same, different, new = _card(number=1), _card(number=2, etapa="Hecha"), _card(number=3)
    sent = {1: board.fingerprint(same), 2: board.fingerprint(_card(number=2))}
    assert board.due([same, different, new], sent) == [different, new]


def test_nothing_is_due_when_everything_was_sent():
    cards = [_card(number=n) for n in range(4)]
    assert board.due(cards, {c.number: board.fingerprint(c) for c in cards}) == []


def test_a_new_card_sets_only_the_fields_it_has_and_clears_nothing():
    got = board.writes(_card(cost_usd=1.5), None)
    assert [(w.field, w.value) for w in got] == [("etapa", "En cola"), ("costo", 1.5)]


def test_a_field_that_lost_its_value_is_cleared_only_if_the_last_send_had_one():
    now = _card(etapa="En cola")
    had = board.present(_card(etapa="Trabada", bloqueo="x"))
    assert [(w.field, w.value) for w in board.writes(now, had)] == [
        ("etapa", "En cola"),
        ("bloqueo", None),
    ]
    assert [w.field for w in board.writes(now, board.present(now))] == ["etapa"]


def test_the_system_writes_no_field_outside_the_five():
    assert set(board.FIELDS) == {"etapa", "bloqueo", "costo", "inicio", "fin"}
    everything = _card(
        bloqueo="b", cost_usd=1.0, started=date(2026, 1, 1), finished=date(2026, 1, 2)
    )
    assert {w.field for w in board.writes(everything, None)} == set(board.FIELDS)


def test_the_retry_wait_is_fifteen_minutes_after_a_whole_failure():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert board.retry_due(None, now)
    assert not board.retry_due(now - timedelta(minutes=14), now)
    assert board.retry_due(now - timedelta(minutes=15), now)
    assert not board.retry_due(now + timedelta(minutes=1), now)
