"""The lesson decision domain (GH-918): the caps, the ids, `settle` and `request_text`."""

from __future__ import annotations

import dataclasses
import json
from typing import get_args, get_type_hints

import pytest
from pydantic import BaseModel

from agentic.core.process.domain import lesson_decision as ld
from agentic.core.process.domain.lesson_decision import (
    Cluster,
    DecisionRequest,
    DuplicateKey,
    NewCandidate,
    OverCap,
    RawDecision,
    RawDecisions,
    RawVerdict,
    Report,
    Similar,
    decision_request,
    pending_id,
    request_text,
    settle,
    similar_of,
)
from agentic.core.process.domain.playbook import Archived, Item, Pending
from agentic.core.process.domain.schemas import LessonDecisions


def _sim(n: int, shelf: ld.Shelf = "candidate", text: str | None = None) -> Similar:
    return Similar(f"s{n}", text or f"similar text {n}", shelf)


def _cand(key: str, *similar: Similar, text: str | None = None) -> NewCandidate:
    return NewCandidate(key, text or f"text of {key}", tuple(similar))


def _req(*candidates: NewCandidate, clusters: tuple[Cluster, ...] = ()) -> DecisionRequest:
    return DecisionRequest("proj", candidates, clusters)


def _add(key: str, wording: str | None = None) -> RawDecision:
    return RawDecision(key, "ADD", None, wording)


def _run(
    request: DecisionRequest, *decisions: RawDecision, verdicts: tuple[RawVerdict, ...] = ()
) -> ld.Settled:
    return settle(request, RawDecisions(tuple(decisions), verdicts))


def _item(item_id: str = "L3", lesson: str = "Keep tests small") -> Item:
    return Item(item_id, lesson, "tests", "empty", ("GH-1",), "2026-11-01")


def _pending(lesson: str = "Keep tests small", category: str = "tests") -> Pending:
    return Pending("proj", category, lesson, "low", ("GH-1",), ("review",), "2026-11-01")


# ---- caps and duplicates ---------------------------------------------------------------


def test_a_request_within_every_cap_is_built() -> None:
    cands = [_cand(f"c{i}", *[_sim(j) for j in range(ld.SIMILAR_MAX)]) for i in range(20)]
    clusters = [Cluster(f"k{i}", tuple(_sim(j) for j in range(6))) for i in range(5)]
    out = decision_request("proj", cands, clusters)
    assert isinstance(out, DecisionRequest)
    assert len(out.candidates) == ld.DECIDE_CANDIDATES_MAX
    assert len(out.clusters) == ld.CLUSTERS_MAX


@pytest.mark.parametrize(
    ("cands", "clusters", "expected"),
    [
        ([_cand(f"c{i}") for i in range(21)], [], OverCap("candidates", 20, 21)),
        ([_cand("c", *[_sim(j) for j in range(6)])], [], OverCap("similar", 5, 6)),
        ([], [Cluster(f"k{i}", ()) for i in range(6)], OverCap("clusters", 5, 6)),
        ([], [Cluster("k", tuple(_sim(j) for j in range(7)))], OverCap("members", 6, 7)),
    ],
)
def test_each_cap_gives_over_cap(
    cands: list[NewCandidate], clusters: list[Cluster], expected: OverCap
) -> None:
    assert decision_request("proj", cands, clusters) == expected


@pytest.mark.parametrize(
    ("cands", "clusters", "expected"),
    [
        ([_cand("c"), _cand("c")], [], DuplicateKey("candidate", "c")),
        ([], [Cluster("k", ()), Cluster("k", ())], DuplicateKey("cluster", "k")),
        ([_cand("c", _sim(1), _sim(1))], [], DuplicateKey("similar", "s1")),
    ],
)
def test_a_repeated_key_gives_duplicate_key(
    cands: list[NewCandidate], clusters: list[Cluster], expected: DuplicateKey
) -> None:
    assert decision_request("proj", cands, clusters) == expected


def test_decision_request_is_a_three_way_return() -> None:
    results = [
        decision_request("p", [_cand("c")], []),
        decision_request("p", [_cand(f"c{i}") for i in range(21)], []),
        decision_request("p", [_cand("c"), _cand("c")], []),
    ]
    assert [type(r) for r in results] == [DecisionRequest, OverCap, DuplicateKey]


# ---- similar_of and pending_id ---------------------------------------------------------


def test_similar_of_puts_a_playbook_value_on_its_shelf() -> None:
    item, pending = _item(), _pending("another lesson")
    archived = Archived(_item("L9", "old lesson"), "unused", "2026-10-01")
    assert similar_of(item) == Similar("L3", "Keep tests small", "active")
    assert similar_of(pending) == Similar(pending_id(pending), "another lesson", "candidate")
    assert similar_of(archived) == Similar("L9", "old lesson", "archived")
    assert similar_of(pending).id.startswith("candidate:")
    assert all(similar_of(v).id for v in (item, pending, archived))


def test_pending_id_ignores_case_and_whitespace_but_not_category() -> None:
    base = pending_id(_pending("Keep tests small"))
    assert pending_id(_pending("  keep   TESTS\nsmall ")) == base
    assert pending_id(_pending("Keep tests small", category="other")) != base
    assert pending_id(dataclasses.replace(_pending(), slug="other")) != base
    assert pending_id(_pending("Keep tests tiny")) != base


# ---- request_text ----------------------------------------------------------------------


def test_request_text_escapes_quotes_braces_and_newlines() -> None:
    nasty = 'say "hi" {{x}}\nand {"a": 1} é'
    request = _req(
        _cand("c1", Similar("s1", nasty, "active"), text=nasty),
        clusters=(Cluster("k1", (Similar("m1", nasty, "candidate"),)),),
    )
    text = request_text(request)
    assert nasty not in text
    assert "é" in text
    loaded = json.loads(text)
    assert loaded["slug"] == "proj"
    assert loaded["candidates"][0]["text"] == nasty
    assert loaded["candidates"][0]["similar"][0] == {"id": "s1", "text": nasty, "shelf": "active"}
    assert loaded["clusters"][0]["members"][0]["text"] == nasty


# ---- settle: ids, unanswered, doubled ---------------------------------------------------


def test_three_of_five_answered_leaves_two_undecided_and_no_extra_add() -> None:
    request = _req(*[_cand(f"c{i}") for i in range(5)])
    settled = _run(request, _add("c0"), _add("c2"), _add("c4"))
    assert [d.candidate_key for d in settled.decisions] == ["c0", "c2", "c4"]
    assert settled.undecided == ("c1", "c3")
    assert settled.reports == (
        Report("candidate", "c1", "missing"),
        Report("candidate", "c3", "missing"),
    )


def test_a_valid_merge_carries_its_target_and_its_shelf() -> None:
    request = _req(_cand("c", _sim(1, "archived")))
    settled = _run(request, RawDecision("c", "MERGE", "s1", None))
    assert settled.decisions[0].target_id == "s1"
    assert settled.decisions[0].target_shelf == "archived"
    assert settled.reports == ()


def test_an_id_offered_for_another_candidate_gives_add_and_unknown_id() -> None:
    request = _req(_cand("a", _sim(1)), _cand("b", _sim(2)))
    settled = _run(request, RawDecision("a", "NOOP", "s2", None), _add("b"))
    first = settled.decisions[0]
    assert (first.verb, first.target_id, first.target_shelf, first.wording) == (
        "ADD",
        None,
        None,
        None,
    )
    assert settled.reports == (Report("candidate", "a", "unknown_id"),)


def test_a_missing_target_id_on_a_merge_is_an_unknown_id() -> None:
    settled = _run(_req(_cand("a", _sim(1))), RawDecision("a", "MERGE", None, None))
    assert settled.decisions[0].verb == "ADD"
    assert settled.reports == (Report("candidate", "a", "unknown_id"),)


def test_a_decision_for_a_foreign_key_is_dropped_without_echoing_the_key() -> None:
    settled = _run(_req(_cand("a")), _add("a"), _add("not a key {{x}}"))
    assert [d.candidate_key for d in settled.decisions] == ["a"]
    assert settled.reports == (Report("candidate", "", "unknown_id"),)


def test_the_first_of_two_decisions_stands() -> None:
    request = _req(_cand("a", _sim(1)))
    settled = _run(request, RawDecision("a", "NOOP", "s1", None), _add("a"))
    assert [d.verb for d in settled.decisions] == ["NOOP"]
    assert settled.reports == (Report("candidate", "a", "duplicate"),)


def test_an_add_that_names_a_target_loses_it_silently() -> None:
    settled = _run(_req(_cand("a", _sim(1))), RawDecision("a", "ADD", "s1", None))
    decision = settled.decisions[0]
    assert (decision.target_id, decision.target_shelf) == (None, None)
    assert settled.reports == ()


# ---- settle: wordings -------------------------------------------------------------------


def test_add_may_carry_only_the_candidates_own_text() -> None:
    request = _req(_cand("a", _sim(1)))
    own = _run(request, _add("a", "  text of a  "))
    assert own.decisions[0].wording == "  text of a  "
    assert own.reports == ()
    other = _run(request, _add("a", "similar text 1"))
    assert other.decisions[0].wording is None
    assert other.reports == (Report("candidate", "a", "wording_not_a_source"),)


def test_merge_may_carry_the_candidates_text_or_the_targets() -> None:
    request = _req(_cand("a", _sim(1, "candidate")))
    for source in ("text of a", "similar text 1"):
        settled = _run(request, RawDecision("a", "MERGE", "s1", source))
        assert settled.decisions[0].wording == source
        assert settled.reports == ()
    stray = _run(request, RawDecision("a", "MERGE", "s1", "something else"))
    assert stray.decisions[0].verb == "MERGE"
    assert stray.decisions[0].wording is None
    assert stray.reports == (Report("candidate", "a", "wording_not_a_source"),)


@pytest.mark.parametrize("verb", ["NOOP", "CONTRADICTS"])
def test_noop_and_contradicts_carry_no_wording(verb: ld.Verb) -> None:
    request = _req(_cand("a", _sim(1)))
    settled = _run(request, RawDecision("a", verb, "s1", "text of a"))
    assert settled.decisions[0].verb == verb
    assert settled.decisions[0].wording is None
    assert settled.reports == (Report("candidate", "a", "wording_not_a_source"),)


def test_a_blank_wording_is_no_wording() -> None:
    settled = _run(_req(_cand("a", _sim(1))), RawDecision("a", "NOOP", "s1", "  \n"))
    assert settled.decisions[0].wording is None
    assert settled.reports == ()


def test_a_merge_with_an_unknown_id_and_a_target_wording_gives_two_reports() -> None:
    request = _req(_cand("a", _sim(1)))
    settled = _run(request, RawDecision("a", "MERGE", "nope", "similar text 1"))
    assert settled.decisions[0].verb == "ADD"
    assert settled.decisions[0].wording is None
    assert settled.reports == (
        Report("candidate", "a", "unknown_id"),
        Report("candidate", "a", "wording_not_a_source"),
    )


def test_a_refused_wording_is_dropped_and_the_verb_stays() -> None:
    long = "x" * 300
    request = _req(_cand("a", _sim(1, "candidate"), text=long))
    settled = _run(request, _add("a", long))
    assert settled.decisions[0].verb == "ADD"
    assert settled.decisions[0].wording is None
    assert settled.reports == (Report("candidate", "a", "wording_refused"),)


def test_a_merge_onto_an_active_target_clears_its_wording() -> None:
    request = _req(_cand("a", _sim(1, "active")))
    settled = _run(request, RawDecision("a", "MERGE", "s1", "text of a"))
    decision = settled.decisions[0]
    assert (decision.verb, decision.wording, decision.target_shelf) == ("MERGE", None, "active")
    assert settled.reports == (Report("candidate", "a", "wording_on_active"),)


def test_a_refused_wording_on_an_active_merge_gives_only_wording_refused() -> None:
    bad = "has `backticks`"
    request = _req(_cand("a", _sim(1, "active"), text=bad))
    settled = _run(request, RawDecision("a", "MERGE", "s1", bad))
    assert settled.decisions[0].wording is None
    assert settled.reports == (Report("candidate", "a", "wording_refused"),)


# ---- settle: clusters -------------------------------------------------------------------


def _clustered() -> DecisionRequest:
    cluster = Cluster("k", (_sim(1, text="first member"), _sim(2, text="second member")))
    return _req(clusters=(cluster,))


def _verdict(verdict: ld.VerdictKind, canonical: str | None = None, key: str = "k") -> RawVerdict:
    return RawVerdict(key, verdict, canonical)


def test_a_confirm_with_a_members_text_stands() -> None:
    settled = _run(_clustered(), verdicts=(_verdict("confirm", "second member"),))
    assert settled.verdicts == (ld.ClusterVerdict("k", "confirm", "second member"),)
    assert settled.reports == ()


@pytest.mark.parametrize("canonical", ["a third text", None, "  ", "first member "])
def test_a_confirm_that_is_not_a_member_becomes_a_split(canonical: str | None) -> None:
    settled = _run(_clustered(), verdicts=(_verdict("confirm", canonical),))
    assert settled.verdicts == (ld.ClusterVerdict("k", "split", None),)
    assert settled.reports == (Report("cluster", "k", "canonical_not_a_member"),)


def test_a_refused_canonical_becomes_a_split() -> None:
    cluster = Cluster("k", (_sim(1, text="a `bad` member"), _sim(2)))
    settled = _run(_req(clusters=(cluster,)), verdicts=(_verdict("confirm", "a `bad` member"),))
    assert settled.verdicts == (ld.ClusterVerdict("k", "split", None),)
    assert settled.reports == (Report("cluster", "k", "wording_refused"),)


def test_a_split_with_a_canonical_loses_it_without_a_report() -> None:
    settled = _run(_clustered(), verdicts=(_verdict("split", "first member"),))
    assert settled.verdicts == (ld.ClusterVerdict("k", "split", None),)
    assert settled.reports == ()


def test_an_unknown_cluster_is_dropped_without_echoing_its_key() -> None:
    settled = _run(_clustered(), verdicts=(_verdict("split", key="zzz"), _verdict("split")))
    assert [v.cluster_key for v in settled.verdicts] == ["k"]
    assert settled.reports == (Report("cluster", "", "unknown_cluster"),)


def test_a_cluster_with_two_verdicts_gives_duplicate_and_the_first_stands() -> None:
    settled = _run(_clustered(), verdicts=(_verdict("split"), _verdict("confirm", "first member")))
    assert settled.verdicts == (ld.ClusterVerdict("k", "split", None),)
    assert settled.reports == (Report("cluster", "k", "duplicate"),)


def test_a_cluster_with_no_verdict_gives_missing_and_no_verdict() -> None:
    settled = _run(_clustered())
    assert settled.verdicts == ()
    assert settled.reports == (Report("cluster", "k", "missing"),)


# ---- the closed cause and the schema ----------------------------------------------------


def test_report_causes_are_the_closed_set() -> None:
    assert set(get_args(ld.ReportCause)) == {
        "unknown_id",
        "missing",
        "duplicate",
        "unknown_cluster",
        "wording_not_a_source",
        "wording_refused",
        "wording_on_active",
        "canonical_not_a_member",
    }
    assert [f.name for f in dataclasses.fields(Report)] == ["kind", "subject", "cause"]


def _literal(annotation: object) -> tuple[object, ...]:
    """The literal values of `X` or `X | None`; a plain `str | None` gives `(str,)`."""
    args = tuple(a for a in get_args(annotation) if a is not type(None))
    if len(args) == 1 and get_args(args[0]):
        return tuple(get_args(args[0]))
    return args or (annotation,)


@pytest.mark.parametrize(
    ("field_name", "raw_type"),
    [("decisions", RawDecision), ("verdicts", RawVerdict)],
)
def test_the_schema_rows_match_the_raw_dataclasses(field_name: str, raw_type: type) -> None:
    (row,) = get_args(LessonDecisions.model_fields[field_name].annotation)
    assert issubclass(row, BaseModel)
    hints = get_type_hints(raw_type)
    assert list(row.model_fields) == [f.name for f in dataclasses.fields(raw_type)]
    for name, info in row.model_fields.items():
        assert _literal(info.annotation) == _literal(hints[name]), name
