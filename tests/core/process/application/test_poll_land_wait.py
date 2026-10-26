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
    registry.entries.pop()
    a_ports.tracker._queue = [9]
    a_ports.tracker.states[9] = IssueState("OPEN", "", None, None)
    _seed_body(a_ports, 9)
    launcher = FakeLauncher()

    _lines(a_cfg, a_ports, registry, launcher)

    assert _land_argv(5, tmp_path / "a") in launcher.spawned


def test_the_notes_of_an_unreadable_wait_file_open_the_round_output(tmp_path):
    _, b_ports, registry, _, b_cfg = _two_products(tmp_path)
    registry.land_wait_notes = ["agentic: poll: land wait file x.json unreadable, skipped"]

    lines = _lines(b_cfg, b_ports, registry, FakeLauncher())

    assert lines[0] == "agentic: poll: land wait file x.json unreadable, skipped"
