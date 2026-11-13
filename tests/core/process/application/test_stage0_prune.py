"""`make_prune`: the `Prune` rotation plugs in (GH-632, part 2b), with doubles of
`Stage0Store` and `Stage0Users`. Nothing here touches the disk."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.application.stage0_prune import make_prune
from agentic.core.process.domain.stage0_rotation import Unreadable

NAMES = [c * 40 for c in "0123456789"]
START = datetime(2026, 11, 1, tzinfo=UTC)
ROOT = Path("/stage0-root")


class Store:
    def __init__(self, *, current=None, record=None, failing=(), invalid=()):
        self._current, self._record = current, record
        self.failing, self.invalid = set(failing), set(invalid)
        self.removed: list[str] = []

    def stages(self):
        return [(name, START + timedelta(hours=i)) for i, name in enumerate(NAMES)]

    def current(self):
        return self._current

    def switch_record(self):
        return self._record

    def remove(self, name):
        if name in self.invalid:
            raise ValueError(name)
        if name in self.failing:
            raise OSError(name)
        self.removed.append(name)


class Users:
    def __init__(self, result):
        self._result = result

    def users(self):
        return self._result


def run(store, users, own=ROOT / "none"):
    lines: list[str] = []
    make_prune(ROOT)(store, Users(users), own, lines.append)
    return lines


def test_it_spares_in_use_current_own_previous_new_and_the_two_newest():
    record = (NAMES[3], NAMES[4], START)
    store = Store(current=NAMES[1], record=record)
    lines = run(store, frozenset({NAMES[0]}), own=ROOT / NAMES[2] / "sub")
    assert sorted(store.removed) == NAMES[5:8]
    assert lines == ["pruned: 3"]


def test_it_is_the_prune_shape_rotate_takes():
    from agentic.core.process.application.stage0_rotate import Prune

    prune: Prune = make_prune(ROOT)
    assert callable(prune)


def test_an_unreadable_proc_prunes_nothing_and_says_so_in_fixed_words():
    store = Store(current=NAMES[1])
    assert run(store, Unreadable()) == ["prune: unreadable"]
    assert store.removed == []


def test_a_missing_switch_record_protects_only_what_the_rules_do():
    store = Store(current=NAMES[9])
    assert run(store, frozenset()) == ["pruned: 8"]
    assert sorted(store.removed) == NAMES[:8]


def test_a_failing_delete_is_counted_and_the_rest_are_still_tried():
    store = Store(current=NAMES[9], failing={NAMES[0]})
    store._record = (NAMES[8], NAMES[9], START)
    lines = run(store, frozenset({NAMES[7], NAMES[6], NAMES[5], NAMES[4]}))
    assert sorted(store.removed) == NAMES[1:4]
    assert lines == ["prune: failed 1", "pruned: 3"]


def test_a_name_that_fails_validation_propagates():
    store = Store(current=NAMES[9], invalid={NAMES[0]})
    with pytest.raises(ValueError):
        run(store, frozenset())


def test_an_own_checkout_outside_the_root_protects_nothing():
    store = Store(current=NAMES[9])
    run(store, frozenset(), own=Path("/elsewhere") / NAMES[0])
    assert NAMES[0] in store.removed


def test_the_root_itself_as_own_protects_nothing():
    store = Store(current=NAMES[9])
    run(store, frozenset(), own=ROOT)
    assert sorted(store.removed) == NAMES[:8]
