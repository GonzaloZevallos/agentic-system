"""`InvariantCoverageGuard` and its place in `run_with_retry`'s guard chain (GH-174): the
schema guard asks first, then the map check, and the presubmit only after both, so a
bad map never costs a run of the product's checks. On fakes: the runner below models
the delivery hook's order (`_delivery_gate`) over scripted emissions."""

from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application.coverage_guard import InvariantCoverageGuard
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.run_role import run_with_retry
from agentic.core.process.application.schema_guard import RoleSchemaGuard
from agentic.core.process.domain import coverage
from agentic.core.process.domain.roles import DEVELOPER, PJM
from tests.fakes import ScriptedRunner, classified, delivered

pytestmark = pytest.mark.anyio

BARE = {"markdown": "x", "summary": "s", "assumptions": []}
MAPPED = {**BARE, "coverage": ["invariant 1: a.py:1"]}


def test_denies_a_map_that_leaves_an_invariant_out_then_passes():
    guard = InvariantCoverageGuard(1)
    reason = guard(BARE)
    assert reason is not None and "invariant(s) 1." in reason
    assert guard.refusals == 1
    # Cap spent: the same gap now passes, the delivery goes through with it.
    assert guard(BARE) is None
    assert guard.refusals == coverage.REFUSALS


def test_accepts_a_complete_map_without_counting_a_refusal():
    guard = InvariantCoverageGuard(1)
    assert guard(MAPPED) is None
    assert guard.refusals == 0


def test_never_raises_on_a_payload_it_cannot_read():
    guard = InvariantCoverageGuard(2)
    assert guard({"coverage": {"x": 1}}) is not None
    assert guard({"coverage": 3}) is None  # cap spent, still no exception


class HookOrderRunner:
    """What the harness does on each emission: the guard first, the presubmit second,
    the next emission only after a denial."""

    def __init__(self, emissions: list[dict[str, Any]]):
        self.emissions = emissions
        self.guards: list[Any] = []
        self.denials: list[str] = []

    async def run_once(self, role, prompt, **kwargs) -> RawResult:
        guard, presubmit = kwargs.get("guard"), kwargs.get("presubmit")
        self.guards.append(guard)
        for emission in self.emissions:
            reason = guard(emission) if guard is not None else None
            if reason is None and presubmit is not None:
                reason = presubmit()
            if reason is None:
                return RawResult(emission, 0.1, 5, "s")
            self.denials.append(reason)
        raise AssertionError("every emission was denied")


class CountingPresubmit:
    timeout_s = 1.0
    wait_max_s = 0.0

    def __init__(self) -> None:
        self.runs = 0

    def __call__(self) -> str | None:
        self.runs += 1
        return None


class Denies:
    """A stub `delivery_check` that denies its first call."""

    refusals = 0

    def __call__(self, tool_input: dict[str, Any]) -> str | None:
        self.refusals += 1
        return "map denied" if self.refusals == 1 else None


async def run(runner, **more):
    return await run_with_retry(
        DEVELOPER,
        "task",
        system_prompt="s",
        model="m",
        retries=0,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
        **more,
    )


async def test_the_schema_guard_denies_before_the_map_check():
    check = InvariantCoverageGuard(1)
    runner = HookOrderRunner([{"markdown": "x"}, BARE, MAPPED])
    await run(runner, delivery_check=check)
    assert "`summary`" in runner.denials[0]  # schema first, though the map is also empty
    assert "invariant(s) 1." in runner.denials[1]
    assert len(runner.denials) == 2 and check.refusals == 1


async def test_a_denied_map_never_costs_a_run_of_the_checks():
    presubmit = CountingPresubmit()
    runner = HookOrderRunner([MAPPED, MAPPED])
    await run(runner, presubmit=presubmit, delivery_check=Denies())
    assert runner.denials == ["map denied"]
    assert presubmit.runs == 1  # once, for the emission that passed both guards


async def test_the_map_check_and_the_schema_guard_keep_their_own_counters():
    check = InvariantCoverageGuard(1)
    runner = HookOrderRunner([{"markdown": "x"}, BARE, MAPPED])
    await run(runner, delivery_check=check)
    (composed,) = {id(g) for g in runner.guards}
    assert composed != id(check)
    assert check.refusals == 1


async def test_without_a_delivery_check_the_guard_is_todays_object():
    runner = ScriptedRunner(developer=[delivered()])
    await run(runner)
    guard = runner.calls[0]["guard"]
    assert isinstance(guard, RoleSchemaGuard)


async def test_a_delivery_check_alone_reaches_a_role_with_no_schema_guard():
    check = Denies()
    runner = ScriptedRunner(pjm=[classified()])
    await run_with_retry(
        PJM,
        "task",
        system_prompt="s",
        model="m",
        retries=0,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
        delivery_check=check,
    )
    assert runner.calls[0]["guard"] is check
