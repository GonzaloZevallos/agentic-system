"""The hold predicate (GH-839): membership in the round's one held-issues read."""

from agentic.core.process.domain.hold import held


def test_an_issue_in_the_held_set_is_held():
    assert held(7, frozenset({3, 7}))


def test_an_issue_outside_the_held_set_is_not_held():
    assert not held(8, frozenset({3, 7}))


def test_nothing_is_held_against_an_empty_set():
    assert not held(7, frozenset())
