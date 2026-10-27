"""The flake wiring of ADR-0066 across the gates that rerun a red check: what the Developer
run's patch carries, what the merge gate and `full_checks` record, which command each rerun
uses, and what the merge decision files. Over the in-memory ports."""

from __future__ import annotations

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.domain.presubmit import RerunForm
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import (
    CommandResult,
    MemoryCommands,
    MemoryStack,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

CHECKS = {"tests": "pytest", "lint": "ruff"}
FORM = RerunForm(command="pytest --lf", test_id=r"(?m)^FAILED (\S+)")
RED = CommandResult(1, "FAILED tests/a.py::one\nFAILED tests/b.py::two\n")
GREEN = CommandResult(0, "ok")


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


# The Developer run's patch.


async def delivery(cfg, commands, **stack):
    runner = ScriptedRunner(developer=[delivered(), delivered()])
    ports = memory_ports(runner, stack=MemoryStack(checks=CHECKS, **stack), commands=commands)
    state, checkout = building(ports)
    return await develop(state, cfg, ports), ports, checkout, state


async def test_a_flake_at_delivery_travels_in_the_developer_patch_with_the_forms_ids(cfg):
    commands = MemoryCommands(pytest=[RED], **{"pytest --lf": [GREEN]})
    patch, ports, _, _ = await delivery(
        cfg, commands, delivery_checks=("tests",), rerun_checks={"tests": FORM}
    )
    [record] = patch["flakes"]
    assert record["head"]  # the fake workspace moves its head on every read
    assert record == {**record, "check": "tests", "gate": "delivery"}
    assert record["tests"] == ["tests/a.py::one", "tests/b.py::two"]
    assert [c for _, c in ports.commands.ran] == ["pytest", "pytest --lf"]


async def test_a_flake_at_delivery_without_a_form_reruns_the_full_command(cfg):
    commands = MemoryCommands(pytest=[RED, GREEN])
    patch, ports, _, _ = await delivery(cfg, commands)
    assert [f["tests"] for f in patch["flakes"]] == [[]]
    assert [c for _, c in ports.commands.ran].count("pytest") == 2


async def test_a_delivery_with_no_red_check_carries_no_flakes(cfg):
    patch, _, _, _ = await delivery(cfg, MemoryCommands())
    assert "flakes" not in patch


async def test_two_deliveries_of_one_head_with_one_flake_leave_one_record_after_the_fan_in(cfg):
    first, _, _, _ = await delivery(cfg, MemoryCommands(pytest=[RED, GREEN]))
    # The re-delivery of the same head reports the same flake again.
    joined = apply(apply({"flakes": []}, first), {"flakes": list(first["flakes"])})
    assert joined["flakes"] == first["flakes"]


# The merge gate and the merge decision.


def merge_world(tmp_path, commands, **stack):
    from tests.core.process.application.test_merge_evidence import _world

    return _world(
        tmp_path, commands, MemoryStack(checks={"lint": "run lint", "tests": "run tests"}, **stack)
    )


def merge_once(ports, tmp_path):
    from tests.core.process.application.test_merge_evidence import _run

    return _run(ports, tmp_path)


def test_a_flake_at_the_merge_gate_is_recorded_as_a_flake_then_filed_at_the_decision(tmp_path):
    commands = MemoryCommands(**{"run tests": [RED, GREEN]})
    ports = merge_world(tmp_path, commands)
    result, _ = merge_once(ports, tmp_path)
    document = ports.documents.load("GH-5") or {}
    assert result.kind == "merged"
    flaked = {**document["merge_checks"]["checks"][-1]}
    assert flaked.pop("master") == document["merge_checks"]["master"]
    assert flaked == {"name": "tests", "passed": True, "flake": True}
    # The head the gate ran on: the checkout of the pull request's head merged with master.
    assert document["flakes"] == [{"check": "tests", "head": "base", "gate": "merge", "tests": []}]
    [(title, body)] = ports.tracker.opened_issues
    assert title == "Flaky check: tests" and "<!-- agentic:flake check=tests -->" in body
    assert ports.tracker.opened_labels == ["agentic"]


def test_a_failure_that_repeats_at_the_merge_gate_refuses_and_files_nothing(tmp_path):
    ports = merge_world(tmp_path, MemoryCommands(**{"run tests": [RED, RED]}))
    result, _ = merge_once(ports, tmp_path)
    assert result.kind == "refused"
    assert "flakes" not in (ports.documents.load("GH-5") or {})
    assert ports.tracker.opened_issues == []


def test_the_merge_gate_adds_to_the_flakes_already_recorded_and_never_runs_the_form(tmp_path):
    earlier = {"check": "lint", "head": "old", "gate": "delivery", "tests": []}
    ports = merge_world(
        tmp_path,
        MemoryCommands(**{"run tests": [RED, GREEN]}),
        delivery_checks=("lint",),
        rerun_checks={"tests": FORM},
    )
    ports.documents.record_closure("GH-5", {"flakes": [earlier]})
    merge_once(ports, tmp_path)
    flakes = (ports.documents.load("GH-5") or {})["flakes"]
    assert [(f["check"], f["gate"]) for f in flakes] == [("lint", "delivery"), ("tests", "merge")]
    assert "pytest --lf" not in [c for _, c in ports.commands.ran]
    assert [c for _, c in ports.commands.ran].count("run tests") == 2


# `full_checks`.


async def test_a_flake_in_full_checks_passes_and_is_recorded(cfg):
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    ports = ports_of(MemoryCommands(pytest=[RED, GREEN]), rerun_checks={"tests": FORM})
    from agentic.core.process.application.full_checks import full_checks

    patch = await full_checks(state_of(), CFG, ports)
    assert patch["full_checks"]["passed"] is True
    # Invariant 2: the record and the section say flake, never plain passed.
    [entry] = [e for e in patch["full_checks"]["results"] if e["name"] == "tests"]
    assert entry.get("flake") is True
    assert "tests (flake)" in patch["sections"]["Full checks"]
    [record] = patch["flakes"]
    assert (record["check"], record["gate"], record["tests"]) == ("tests", "full_checks", [])
    # The full command again, never the form: only a full run covers the suite.
    assert [c for _, c in ports.commands.ran].count("pytest") == 2
    assert "pytest --lf" not in [c for _, c in ports.commands.ran]


async def test_a_green_full_checks_carries_no_flakes(cfg):
    from agentic.core.process.application.full_checks import full_checks
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    patch = await full_checks(state_of(), CFG, ports_of())
    assert "flakes" not in patch


async def test_a_first_run_that_stopped_early_passes_in_the_form_at_delivery_but_not_in_full_checks(
    cfg,
):
    """The form reruns one failed id and passes; the suite behind the early stop is red, and
    only `full_checks` runs it all: it reruns the full command and refuses."""
    from agentic.core.process.application.full_checks import full_checks
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    one_id = CommandResult(1, "FAILED tests/a.py::one\n")
    patch, _, _, _ = await delivery(
        cfg,
        MemoryCommands(pytest=[one_id], **{"pytest --lf": [GREEN]}),
        delivery_checks=("tests",),
        rerun_checks={"tests": FORM},
    )
    assert [f["tests"] for f in patch["flakes"]] == [["tests/a.py::one"]]
    red_suite = MemoryCommands(pytest=[CommandResult(1, "FAILED tests/z.py::late\n")] * 2)
    full = await full_checks(state_of(), CFG, ports_of(red_suite, rerun_checks={"tests": FORM}))
    assert full["full_checks"]["passed"] is False
    assert "pytest --lf" not in [c for _, c in red_suite.ran]


def test_the_flakes_survive_a_save_and_the_merge_gate_may_replace_them_whole():
    """`render` persists `flakes` after a delivery, so the next save does not lose them; the
    closure that adds the merge gate's flake rewrites the record whole."""
    from agentic.core.process.domain import document

    record = {"check": "tests", "head": "h1", "gate": "delivery", "tests": []}
    raw = document.render(
        {"id": "GH-1", "title": "t", "description": "d", "type": "feature", "flakes": [record]}
    )
    assert document.parse(raw)["flakes"] == [record]
    more = {"check": "lint", "head": "h2", "gate": "merge", "tests": []}
    written = document.record_closure(raw, {"flakes": [record, more]})
    assert document.parse(written)["flakes"] == [record, more]
