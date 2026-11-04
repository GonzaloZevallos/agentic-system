"""The flake wiring of ADR-0066 across the gates that rerun a red check: what the Developer
run's patch carries, what the merge gate and `full_checks` record, which command each rerun
uses, and what the merge decision files. Over the in-memory ports."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, cast

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.domain.ci import CiUnreadable
from agentic.core.process.domain.presubmit import RerunForm
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import (
    CommandResult,
    FakeCiLogs,
    MemoryCommands,
    MemoryStack,
    ScriptedRunner,
    delivered,
    full_checks_in_task_checkout,
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


def test_a_flake_at_the_merge_gate_is_recorded_as_a_flake_and_files_no_issue_below_the_threshold(
    tmp_path,
):
    commands = MemoryCommands(**{"run tests": [RED, GREEN]})
    ports = merge_world(tmp_path, commands)
    result, _ = merge_once(ports, tmp_path)
    document = ports.documents.load("GH-5") or {}
    assert result.kind == "merged"
    flaked = {**document["merge_checks"]["checks"][-1]}
    assert flaked.pop("master") == document["merge_checks"]["master"]
    assert isinstance(flaked.pop("duration_s"), float)
    assert flaked == {"name": "tests", "passed": True, "flake": True}
    # The head the gate ran on: the checkout of the pull request's head merged with master.
    [flake] = document["flakes"]
    assert isinstance(flake.pop("at"), str)  # dated by the gate (GH-464)
    assert flake == {"check": "tests", "head": "base", "gate": "merge", "tests": []}
    assert ports.tracker.opened_issues == [] and ports.inbox.refs_calls == 0


def test_a_failure_that_repeats_at_the_merge_gate_refuses_and_files_nothing(tmp_path):
    ports = merge_world(tmp_path, MemoryCommands(**{"run tests": [RED, RED]}))
    result, _ = merge_once(ports, tmp_path)
    assert result.kind == "refused"
    assert "flakes" not in (ports.documents.load("GH-5") or {})
    assert ports.tracker.opened_issues == []


def test_the_merge_gate_adds_to_the_flakes_already_recorded_and_runs_the_form(tmp_path):
    earlier = {"check": "lint", "head": "old", "gate": "delivery", "tests": []}
    ports = merge_world(
        tmp_path,
        MemoryCommands(**{"run tests": [RED], "pytest --lf": [GREEN]}),
        delivery_checks=("lint",),
        rerun_checks={"tests": FORM},
    )
    ports.documents.record_closure("GH-5", {"flakes": [earlier]})
    merge_once(ports, tmp_path)
    flakes = (ports.documents.load("GH-5") or {})["flakes"]
    assert [(f["check"], f["gate"]) for f in flakes] == [("lint", "delivery"), ("tests", "merge")]
    # GH-464: the form applies at the merge gate too; complete ids, so no whole-command rerun.
    assert "pytest --lf" in [c for _, c in ports.commands.ran]
    assert [c for _, c in ports.commands.ran].count("run tests") == 1
    assert flakes[1]["tests"] == ["tests/a.py::one", "tests/b.py::two"]


# `full_checks`.


async def test_a_flake_in_full_checks_passes_and_is_recorded(cfg):
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    commands = MemoryCommands(pytest=[RED], **{"pytest --lf": [GREEN]})
    ports = ports_of(commands, rerun_checks={"tests": FORM})

    patch = await full_checks_in_task_checkout(state_of(), CFG, ports)
    assert patch["full_checks"]["passed"] is True
    # Invariant 2: the record and the section say flake, never plain passed.
    [entry] = [e for e in patch["full_checks"]["results"] if e["name"] == "tests"]
    assert entry.get("flake") is True
    assert "tests (flake)" in patch["sections"]["Full checks"]
    [record] = patch["flakes"]
    assert (record["check"], record["gate"]) == ("tests", "full_checks")
    assert record["tests"] == ["tests/a.py::one", "tests/b.py::two"]
    # GH-464: the form at every gate, once; the whole command ran only for the first run.
    assert [c for _, c in ports.commands.ran].count("pytest") == 1
    assert "pytest --lf" in [c for _, c in ports.commands.ran]


async def test_a_green_full_checks_carries_no_flakes(cfg):
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    patch = await full_checks_in_task_checkout(state_of(), CFG, ports_of())
    assert "flakes" not in patch


async def test_a_first_run_that_stopped_early_reruns_the_form_in_full_checks_as_at_delivery(cfg):
    """GH-464: the form applies at `full_checks` as at delivery. A suite behind an early stop
    that is red only shows past the threshold, which bounds the risk (ADR)."""
    from tests.core.process.application.test_full_checks import CFG, ports_of, state_of

    one_id = CommandResult(1, "FAILED tests/a.py::one\n")
    patch, _, _, _ = await delivery(
        cfg,
        MemoryCommands(pytest=[one_id], **{"pytest --lf": [GREEN]}),
        delivery_checks=("tests",),
        rerun_checks={"tests": FORM},
    )
    assert [f["tests"] for f in patch["flakes"]] == [["tests/a.py::one"]]
    commands = MemoryCommands(pytest=[one_id], **{"pytest --lf": [GREEN]})
    full = await full_checks_in_task_checkout(
        state_of(), CFG, ports_of(commands, rerun_checks={"tests": FORM})
    )
    assert full["full_checks"]["passed"] is True
    assert "pytest --lf" in [c for _, c in commands.ran]


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


# GH-464: the form at every site that builds a gate, and the CI flake block on the judged head.


async def test_the_delivery_gate_dates_its_flake_and_reruns_the_form(cfg):
    commands = MemoryCommands(pytest=[RED], **{"pytest --lf": [GREEN]})
    patch, ports, _, _ = await delivery(
        cfg, commands, delivery_checks=("tests",), rerun_checks={"tests": FORM}
    )
    [record] = patch["flakes"]
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    assert "pytest --lf" in [c for _, c in ports.commands.ran]


def test_the_slice_gate_reruns_the_form_with_complete_ids_and_dates_the_flake():
    from tests.core.process.application.test_landing_slice import (
        SLICE_HEAD,
        SLICE_URL,
        FakeLauncher,
        _doc,
        _land,
        _world,
    )

    ports = _world(frozenset({"integration.py"}))
    ports = dataclasses.replace(
        ports,
        stack=MemoryStack(
            checks={"tests": "run tests"}, delivery_checks=("tests",), rerun_checks={"tests": FORM}
        ),
        commands=MemoryCommands(**{"run tests": [RED], "pytest --lf": [GREEN]}),
    )

    _land(ports, FakeLauncher())

    assert ports.tracker.merge_calls == [(SLICE_URL, SLICE_HEAD)]
    [record] = _doc(ports)["flakes"]
    assert record["tests"] == ["tests/a.py::one", "tests/b.py::two"]
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    ran = [c for _, c in ports.commands.ran]
    assert ran.count("run tests") == 1 and "pytest --lf" in ran


BLOCK = 'agentic-flakes: {"v":1,"check":"tests","tests":["tests/a.py::one"]}'
FORGED = 'agentic-flakes: {"v":1,"check":"head-only","tests":["x"]}'


class HeadDeclaresMore(MemoryStack):
    """A stack that declares `head-only` at every revision but the task's base (`HEAD` in the
    fake workspace): what a pull request adding a check to its own stack would show."""

    def checks(self, revision):
        declared = super().checks(revision)
        return declared if revision == "HEAD" else {**declared, "head-only": "x"}


async def full_ci(*lines, blocks=None):
    from tests.core.process.application.test_full_checks_ci import CHECKS, ports_of, run, state_of
    from tests.core.process.application.test_full_checks_ci import GREEN as CI_GREEN

    logs = FakeCiLogs()
    logs.blocks = blocks if blocks is not None else [("unit", list(lines)), ("web", [])]
    ports = ports_of(CI_GREEN, ci_logs=logs)
    ports = dataclasses.replace(
        ports,
        stack=HeadDeclaresMore(checks=CHECKS, delivery_checks=("tests",), ci=("unit", "web")),
    )
    return await run(state_of(), ports), logs


async def test_full_checks_records_the_ci_flake_block_of_the_judged_head_with_its_date():
    patch, logs = await full_ci(BLOCK)
    assert patch["full_checks"]["passed"] is True
    [record] = patch["flakes"]
    assert (record["check"], record["head"], record["gate"]) == ("tests", "base", "full_checks")
    assert record["tests"] == ["tests/a.py::one"]
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    # The CI ref and the head it judged, and the jobs the stack's `ci:` names.
    assert logs.flake_asked == [("agentic-ci/GH-1", "base", ("unit", "web"))]


async def test_full_checks_drops_a_block_naming_a_check_only_the_head_declares():
    patch, _ = await full_ci(FORGED)
    assert "flakes" not in patch
    assert patch["full_checks"]["passed"] is True


async def test_full_checks_with_an_unreadable_block_records_nothing_and_changes_no_verdict():
    patch, _ = await full_ci(blocks=CiUnreadable("gh_unreadable"))
    assert "flakes" not in patch
    assert patch["full_checks"]["passed"] is True and patch["full_checks"]["ran_on"] == "ci"


def merge_ci(blocks, tmp_path):
    from tests.core.process.application.test_merge_ci import _check, _report, _run, _world

    ports = _world(_report(_check()))
    logs = FakeCiLogs()
    logs.blocks = blocks
    ports = dataclasses.replace(
        ports,
        ci_logs=logs,
        stack=HeadDeclaresMore(checks={"tests": "run tests"}, ci=("web",)),
    )
    result, _ = _run(ports, tmp_path)
    return result, ports, logs


def test_the_merge_gate_records_the_ci_flake_block_of_the_head_it_judged_with_its_date(tmp_path):
    result, ports, logs = merge_ci([("web", [BLOCK])], tmp_path)
    assert result.kind == "merged"
    [record] = (ports.documents.load("GH-5") or {})["flakes"]
    assert (record["check"], record["gate"]) == ("tests", "merge")
    assert record["tests"] == ["tests/a.py::one"]
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    [(_, head, jobs)] = logs.flake_asked
    assert head == record["head"] and jobs == ("web",)


def test_the_merge_gate_drops_a_block_naming_a_check_only_the_head_declares(tmp_path):
    result, ports, _ = merge_ci([("web", [FORGED])], tmp_path)
    assert result.kind == "merged"
    assert "flakes" not in (ports.documents.load("GH-5") or {})


def test_the_merge_gate_with_an_unreadable_block_records_nothing_and_still_merges(tmp_path):
    result, ports, _ = merge_ci(CiUnreadable("gh_unreadable"), tmp_path)
    assert result.kind == "merged"
    assert "flakes" not in (ports.documents.load("GH-5") or {})


def test_a_branch_reference_reaches_the_branch_report_and_a_pull_request_url_the_pr_report():
    """Production: `GhCiLogs` reads through this function, and `full_checks` asks for the
    `agentic-ci/<id>` ref, which `ci_report` alone would turn into `CiUnreadable`."""
    from agentic.composition import ci_reader

    class Tracker:
        def __init__(self):
            self.asked = []

        def ci_report(self, reference):
            self.asked.append(("pr", reference))
            return CiUnreadable("gh_unreadable")

        def branch_ci(self, branch):
            self.asked.append(("branch", branch))
            return CiUnreadable("gh_unavailable")

    tracker = Tracker()
    read = ci_reader(cast(Any, tracker))
    assert read("agentic-ci/GH-1") == CiUnreadable("gh_unavailable")
    read("https://github.com/o/r/pull/7")
    assert tracker.asked == [("branch", "agentic-ci/GH-1"), ("pr", "https://github.com/o/r/pull/7")]
