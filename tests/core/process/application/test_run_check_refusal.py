"""A hand run the delivery gate would repeat is refused before anything runs (ADR-0065,
addendum 2026-11-25, GH-1070): `RunCheck.run` over fake ports."""

from pathlib import Path

from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.application.run_check import RunCheck
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.run_check import Verification
from tests.fakes import (
    FakeCheckScheduler,
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
SETUP = ["uv sync"]
STAGE0 = "a" * 40
CHECKS = {"tests": "pytest", "lint": "ruff", "e2e": "playwright"}
AFFECTED = {"tests": "pytest --affected {base} -n {workers}"}


def build(*, committed=True, clean=True, witness=None, **inputs):
    workspace = MemoryWorkspace()
    if committed:
        workspace.moved.add(PATH)
    if not clean:
        workspace.dirty.add(PATH)
    workspace.record_setup_tree(PATH)
    commands = MemoryCommands()
    store = MemoryCheckResults()
    scheduler = FakeCheckScheduler(6)
    ports = memory_ports(
        workspace=workspace, commands=commands, check_results=store, check_scheduler=scheduler
    )
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        CHECKS,
        30.0,
        priority=CheckPriority.SUBTASK,
        witness=witness,
        reuse=Reuse("shop", tuple(SETUP), STAGE0),
    )
    defaults = {"delivery": ("tests", "lint"), "affected": AFFECTED, "base": "abc"}
    handler = RunCheck(
        gate,
        clock=lambda: 1_000_000.0,
        verifications={"compare": Verification("compare.sh", 60.0)},
        **(defaults | inputs),
    )
    return handler, store, commands, scheduler


def assert_nothing_happened(handler, store, commands, scheduler):
    assert commands.ran == []
    assert store.gets == [] and store.puts == []
    assert scheduler.requests == [] and scheduler.released == []
    assert handler.calls == []


def test_a_check_with_an_affected_form_is_refused_on_a_committed_clean_tree_and_nothing_runs():
    handler, store, commands, scheduler = build(witness="witness")
    run = handler.run("tests")
    assert run.refused is not None and "pytest --affected abc -n 1" in run.refused
    assert not run.passed and run.output == ""
    assert_nothing_happened(handler, store, commands, scheduler)
    assert handler("tests") == run.refused


def test_a_check_with_no_affected_form_runs_and_is_stored_on_a_committed_clean_tree():
    handler, store, commands, _ = build()
    run = handler.run("lint")
    assert run.refused is None and run.passed and run.unrecorded is None
    assert commands.ran == [(PATH, "ruff")] and len(store.puts) == 1


def test_a_check_with_no_affected_form_is_refused_on_a_dirty_tree():
    handler, store, commands, scheduler = build(clean=False)
    run = handler.run("lint")
    assert run.refused is not None and "Commit" in run.refused
    assert_nothing_happened(handler, store, commands, scheduler)


def test_a_check_with_no_affected_form_is_refused_before_the_first_commit():
    handler, store, commands, scheduler = build(committed=False)
    assert handler.run("lint").refused is not None
    assert_nothing_happened(handler, store, commands, scheduler)


def test_a_check_in_the_red_set_runs_in_full_and_the_set_is_read_at_every_call():
    red: set[str] = set()
    handler, store, commands, _ = build(red=lambda: frozenset(red))
    assert handler.run("tests").refused is not None
    red.add("tests")
    run = handler.run("tests")
    assert run.refused is None and run.passed
    assert commands.ran == [(PATH, "pytest")]


def test_a_check_outside_the_delivery_tier_and_a_verification_are_never_refused():
    handler, _, commands, _ = build(clean=False)
    assert handler.run("e2e").refused is None
    assert handler.run("compare").refused is None
    assert [command for _, command in commands.ran] == ["playwright", "compare.sh"]


def test_an_unknown_name_is_still_answered_by_the_declared_list():
    handler, store, commands, scheduler = build()
    run = handler.run("nope")
    assert run.refused is not None and "unknown check" in run.refused
    assert commands.ran == [] and store.gets == []


def test_with_the_default_inputs_every_name_runs_as_it_always_did():
    plain, store, commands, _ = build(delivery=(), affected={}, base="")
    assert plain.run("tests").passed and plain.run("lint").passed
    assert [command for _, command in commands.ran] == ["pytest", "ruff"]
    assert len(store.puts) == 2
