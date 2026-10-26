"""GH-362, Rule 1 (ADR-0045 addendum): Espera de aterrizaje. A product whose model land waits
for a slot publishes the fact in the run registry, and every other product holds its counted
starts below a land until the land has started or the record has expired. `run_once` over two
products on one shared registry."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from agentic.core.process.application.ports import IssueState
from agentic.core.process.domain import rules
from tests.core.process.application.test_poll import (
    ESCALATED_AT,
    _cfg,
    _round,
    _seed_body,
)
from tests.core.process.application.test_poll_exempt import (
    LIVE,
    _child,
    _land_argv,
    _owed_lands,
)
from tests.core.process.application.test_poll_merge import _ports
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryStack

A_WAITS = "agentic: poll: land #5 waits: no_slot"


def _two_products(tmp_path):
    """Product A owes a model land (issue 5, cap 2); product B has a launch candidate
    (issue 9, cap 3); one registry, three live role children of B."""
    a_ports = dataclasses.replace(_owed_lands(), stack=MemoryStack(product="a"))
    b_ports = dataclasses.replace(_ports(), stack=MemoryStack(product="b"))
    b_ports.tracker._queue = [9]
    b_ports.tracker.states[9] = IssueState("OPEN", "", None, None)
    _seed_body(b_ports, 9)
    registry = MemoryRunRegistry([_child("role", f"GH-{n}", "b", pid=n) for n in (1, 2, 3)])
    a_cfg = _cfg(tmp_path / "a", max_concurrent_runs=2)
    b_cfg = _cfg(tmp_path / "b", max_concurrent_runs=3)
    return a_ports, b_ports, registry, a_cfg, b_cfg


def _lines(cfg, ports, registry, launcher, now=ESCALATED_AT):
    return _round(cfg, ports, registry, launcher, now, live_steps=LIVE)


def _only_lands(lines):
    return [line for line in lines if " land #" in line]


def test_a_waiting_land_holds_the_other_products_launch_until_it_starts(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_products(tmp_path)
    a_launcher, b_launcher = FakeLauncher(), FakeLauncher()

    first = _lines(a_cfg, a_ports, registry, a_launcher)
    assert _only_lands(first) == [A_WAITS]
    assert [w.product for w in registry.land_waits(ESCALATED_AT)[0]] == ["a"]

    registry.entries.pop()  # one of B's children finished: one slot is free
    held = _lines(b_cfg, b_ports, registry, b_launcher)
    assert b_launcher.spawned == []
    assert not any("launch" in line for line in held)

    started = _lines(a_cfg, a_ports, registry, a_launcher)
    assert a_launcher.spawned == [_land_argv(5, tmp_path / "a")]
    assert _only_lands(started) == ["agentic: poll: land #5 -> pid 4242"]
    assert registry.land_waits(ESCALATED_AT) == ([], [])


def test_a_record_older_than_the_ttl_holds_nothing(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_products(tmp_path)
    _lines(a_cfg, a_ports, registry, FakeLauncher())
    registry.entries.pop()
    later = ESCALATED_AT + rules.LAND_WAIT_TTL + timedelta(seconds=1)
    launcher = FakeLauncher()

    lines = _lines(b_cfg, b_ports, registry, launcher, later)

    assert [argv[1] for argv in launcher.spawned] == ["run"]
    assert "agentic: poll: launch #9 -> pid 4242" in lines


def test_a_product_never_holds_itself(tmp_path):
    a_ports, _, registry, a_cfg, _ = _two_products(tmp_path)
    _lines(a_cfg, a_ports, registry, FakeLauncher())
    registry.entries.clear()  # every slot is free: the land and a launch both fit
    a_ports.tracker._queue = [9]
    a_ports.tracker.states[9] = IssueState("OPEN", "", None, None)
    _seed_body(a_ports, 9)
    launcher = FakeLauncher()

    lines = _lines(a_cfg, a_ports, registry, launcher)

    assert _land_argv(5, tmp_path / "a") in launcher.spawned
    # Its own wait record is still fresh when the round reads it: if the product held itself,
    # the launch would be held (GH-362, integration).
    assert "agentic: poll: launch #9 -> pid 4242" in lines


def test_the_notes_of_an_unreadable_wait_file_open_the_round_output(tmp_path):
    _, b_ports, registry, _, b_cfg = _two_products(tmp_path)
    registry.land_wait_notes = ["agentic: poll: land wait file x.json unreadable, skipped"]

    lines = _lines(b_cfg, b_ports, registry, FakeLauncher())

    assert lines[0] == "agentic: poll: land wait file x.json unreadable, skipped"


# First come, first landed (GH-362): two products owing a model land, one headroom between them.
T0 = ESCALATED_AT
T1 = T0 + timedelta(minutes=2)
T2 = T0 + timedelta(minutes=4)
T3 = T0 + timedelta(minutes=6)


def _two_land_products(tmp_path):
    """Both products owe a model land (issue 5), both with cap 2, and three role children
    hold the cap and the headroom on one shared registry."""
    a_ports = dataclasses.replace(_owed_lands(), stack=MemoryStack(product="a"))
    b_ports = dataclasses.replace(_owed_lands(), stack=MemoryStack(product="b"))
    registry = MemoryRunRegistry([_child("role", f"GH-{n}", "x", pid=n) for n in (1, 2, 3)])
    a_cfg = _cfg(tmp_path / "a", max_concurrent_runs=2)
    b_cfg = _cfg(tmp_path / "b", max_concurrent_runs=2)
    return a_ports, b_ports, registry, a_cfg, b_cfg


def _since_of(registry, product):
    (wait,) = [w for w in registry.land_waits(T2)[0] if w.product == product]
    return wait.since


def test_the_later_wait_yields_the_land_and_the_earlier_one_starts_first(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_land_products(tmp_path)
    a_launcher, b_launcher = FakeLauncher(), FakeLauncher()
    assert _only_lands(_lines(a_cfg, a_ports, registry, a_launcher, T0)) == [A_WAITS]

    registry.entries.pop()  # one slot is free: the headroom could take a land
    queued = _lines(b_cfg, b_ports, registry, b_launcher, T1)
    assert b_launcher.spawned == []
    assert _only_lands(queued) == ["agentic: poll: land #5 waits: land_queued"]

    started = _lines(a_cfg, a_ports, registry, a_launcher, T2)
    assert a_launcher.spawned == [_land_argv(5, tmp_path / "a")]
    assert _only_lands(started) == ["agentic: poll: land #5 -> pid 4242"]

    after = _lines(b_cfg, b_ports, registry, b_launcher, T3)
    assert b_launcher.spawned == [_land_argv(5, tmp_path / "b")]
    assert _only_lands(after) == ["agentic: poll: land #5 -> pid 4242"]
    assert registry.land_waits(T3) == ([], [])


def test_a_queued_land_publishes_a_record_that_keeps_its_place_in_line(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_land_products(tmp_path)
    _lines(a_cfg, a_ports, registry, FakeLauncher(), T0)
    registry.entries.pop()

    _lines(b_cfg, b_ports, registry, FakeLauncher(), T1)
    _lines(b_cfg, b_ports, registry, FakeLauncher(), T2)

    assert _since_of(registry, "b") == T1
    assert _since_of(registry, "a") == T0


def test_the_oldest_wait_is_never_queued_so_two_waiting_products_do_not_deadlock(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_land_products(tmp_path)
    # B waited first this time: it has the earlier record, A the later one.
    _lines(b_cfg, b_ports, registry, FakeLauncher(), T0)
    _lines(a_cfg, a_ports, registry, FakeLauncher(), T1)
    registry.entries.pop()
    a_launcher, b_launcher = FakeLauncher(), FakeLauncher()

    a_lines = _lines(a_cfg, a_ports, registry, a_launcher, T2)
    b_lines = _lines(b_cfg, b_ports, registry, b_launcher, T2)

    assert _only_lands(a_lines) == ["agentic: poll: land #5 waits: land_queued"]
    assert _only_lands(b_lines) == ["agentic: poll: land #5 -> pid 4242"]
    assert b_launcher.spawned == [_land_argv(5, tmp_path / "b")]


def test_a_queued_record_that_expired_frees_the_land_of_the_other_product(tmp_path):
    a_ports, b_ports, registry, a_cfg, b_cfg = _two_land_products(tmp_path)
    _lines(a_cfg, a_ports, registry, FakeLauncher(), T0)  # A's poller then dies
    registry.entries.pop()
    later = T0 + rules.LAND_WAIT_TTL + timedelta(seconds=1)
    launcher = FakeLauncher()

    lines = _lines(b_cfg, b_ports, registry, launcher, later)

    assert launcher.spawned == [_land_argv(5, tmp_path / "b")]
    assert _only_lands(lines) == ["agentic: poll: land #5 -> pid 4242"]
