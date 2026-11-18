"""The `LessonDecider` port through its fake (GH-918): the fake answers what `settle` makes of
its script, the shared consistency checker accepts it and rejects a broken `Settled`, and
`Decided.ok` is true only for a settled answer with no error and no negative cost."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from agentic.core.process.application.ports import Decided
from agentic.core.process.domain.lesson_decision import (
    Cluster,
    ClusterVerdict,
    Decision,
    DecisionRequest,
    NewCandidate,
    RawDecision,
    RawDecisions,
    RawVerdict,
    Settled,
    Similar,
    settle,
)
from tests.fakes import FakeLessonDecider
from tests.lesson_decision_checks import settled_is_consistent

REQUEST = DecisionRequest(
    "proj",
    (
        NewCandidate("a", "text of a", (Similar("act", "active text", "active"),)),
        NewCandidate("b", "text of b", (Similar("pen", "pending text", "candidate"),)),
        NewCandidate("c", "text of c", ()),
    ),
    (Cluster("k", (Similar("m1", "one", "candidate"), Similar("m2", "two", "archived"))),),
)

SCRIPTS: dict[str, RawDecisions] = {
    "full": RawDecisions(
        (
            RawDecision("a", "MERGE", "act", None),
            RawDecision("b", "MERGE", "pen", "pending text"),
            RawDecision("c", "ADD", None, "text of c"),
        ),
        (RawVerdict("k", "confirm", "two"),),
    ),
    "partial": RawDecisions((RawDecision("b", "NOOP", "pen", None),), ()),
    "duplicate": RawDecisions(
        (RawDecision("c", "ADD", None, None), RawDecision("c", "NOOP", "pen", None)),
        (RawVerdict("k", "split", None), RawVerdict("k", "confirm", "one")),
    ),
    "cross-candidate id": RawDecisions(
        (RawDecision("a", "MERGE", "pen", "pending text"), RawDecision("zz", "ADD", None, None)),
        (RawVerdict("nope", "split", None),),
    ),
    "active target": RawDecisions(
        (RawDecision("a", "MERGE", "act", "active text"), RawDecision("c", "NOOP", None, "x")),
        (RawVerdict("k", "confirm", "not a member"),),
    ),
}


def _decide(fake: FakeLessonDecider) -> Decided:
    return asyncio.run(fake.decide(REQUEST))


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_the_fakes_answer_is_consistent_and_equals_settle_over_its_script(name: str) -> None:
    script = SCRIPTS[name]
    fake = FakeLessonDecider(script, cost_usd=0.25)
    decided = _decide(fake)
    assert decided.ok
    assert decided.settled is not None
    assert settled_is_consistent(REQUEST, decided.settled) == []
    assert decided == Decided(settle(REQUEST, script), 0.25, None)
    assert fake.requests == [REQUEST]


def test_the_fake_fails_with_its_script_when_that_is_an_error() -> None:
    decided = _decide(FakeLessonDecider("the model timed out", cost_usd=0.5))
    assert decided == Decided(None, 0.5, "the model timed out")
    assert not decided.ok


# ---- the checker rejects what it must ---------------------------------------------------


def _good() -> Settled:
    return settle(REQUEST, SCRIPTS["full"])


def _with_decision(settled: Settled, index: int, **changes: Any) -> Settled:
    decisions = list(settled.decisions)
    decisions[index] = dataclasses.replace(decisions[index], **changes)
    return dataclasses.replace(settled, decisions=tuple(decisions))


def _with_verdicts(settled: Settled, *verdicts: ClusterVerdict) -> Settled:
    return dataclasses.replace(settled, verdicts=verdicts)


BROKEN: dict[str, Callable[[Settled], Settled]] = {
    "a foreign candidate key": lambda s: dataclasses.replace(
        s, decisions=(*s.decisions, Decision("zz", "ADD", None, None, None))
    ),
    "a candidate decided twice": lambda s: dataclasses.replace(
        s, decisions=(*s.decisions, s.decisions[0])
    ),
    "a candidate neither decided nor undecided": lambda s: dataclasses.replace(
        s, decisions=s.decisions[1:]
    ),
    "a candidate both decided and undecided": lambda s: dataclasses.replace(s, undecided=("a",)),
    "a target outside the candidate's own similar": lambda s: _with_decision(
        s, 0, target_id="pen", target_shelf="candidate"
    ),
    "a target shelf that is not the entry's": lambda s: _with_decision(
        s, 1, target_shelf="archived"
    ),
    "an ADD with a target": lambda s: _with_decision(s, 2, target_id="act"),
    "an ADD with a shelf": lambda s: _with_decision(s, 2, target_shelf="active"),
    "a NOOP with a wording": lambda s: _with_decision(s, 1, verb="NOOP", wording="pending text"),
    "a wording that is not a source": lambda s: _with_decision(s, 1, wording="invented"),
    "a MERGE onto an active target with a wording": lambda s: _with_decision(
        s, 0, wording="text of a"
    ),
    "a verdict for a foreign cluster": lambda s: _with_verdicts(
        s, ClusterVerdict("zz", "split", None)
    ),
    "a cluster with two verdicts": lambda s: _with_verdicts(
        s, *s.verdicts, ClusterVerdict("k", "split", None)
    ),
    "a split with a canonical": lambda s: _with_verdicts(s, ClusterVerdict("k", "split", "one")),
    "a confirm whose canonical is no member": lambda s: _with_verdicts(
        s, ClusterVerdict("k", "confirm", "invented")
    ),
    "a confirm with no canonical": lambda s: _with_verdicts(
        s, ClusterVerdict("k", "confirm", None)
    ),
}


def test_a_good_settled_has_no_violations() -> None:
    assert settled_is_consistent(REQUEST, _good()) == []


@pytest.mark.parametrize("name", list(BROKEN))
def test_the_checker_reports_a_broken_settled(name: str) -> None:
    assert settled_is_consistent(REQUEST, BROKEN[name](_good())), name


def test_the_checker_names_each_violation_on_its_own_line() -> None:
    broken = _with_verdicts(
        _with_decision(_good(), 2, target_id="act"), ClusterVerdict("k", "split", "one")
    )
    assert len(settled_is_consistent(REQUEST, broken)) == 2


# ---- Decided.ok ------------------------------------------------------------------------


def test_decided_is_ok_only_for_a_settled_answer_without_error_or_negative_cost() -> None:
    settled = _good()
    assert Decided(settled, 0.0, None).ok
    assert not Decided(None, 1.0, None).ok
    assert not Decided(settled, 1.0, "x").ok
    assert not Decided(settled, -0.01, None).ok
    assert not Decided(None, 1.0, "x").ok
    assert not Decided(settled, float("nan"), None).ok
