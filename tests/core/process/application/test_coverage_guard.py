"""`InvariantCoverageGuard` and its place in `run_with_retry`'s guard chain (GH-174): the
schema guard asks first, then the map check, and the presubmit only after both, so a
bad map never costs a run of the product's checks. On fakes: the runner below models
the delivery hook's order (`_delivery_gate`) over scripted emissions."""

from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application.coverage_guard import InvariantCoverageGuard, checkout_view
from agentic.core.process.application.ports import GitError, RawResult
from agentic.core.process.application.run_role import run_with_retry
from agentic.core.process.application.schema_guard import RoleSchemaGuard
from agentic.core.process.domain import coverage
from agentic.core.process.domain.roles import DEVELOPER, PJM
from tests.fakes import MemoryWorkspace, ScriptedRunner, classified, delivered

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


class View:
    """A `LocationView` over fixed files: records the paths it was asked about."""

    def __init__(self, lengths: dict[str, int], diff: set[str]):
        self.lengths, self.diff = lengths, diff
        self.asked: list[set[str]] = []

    def __call__(self, paths: set[str]) -> tuple[dict[str, int], set[str]]:
        self.asked.append(paths)
        return self.lengths, self.diff


def test_denies_a_location_that_is_not_in_the_diff_with_the_reason_then_passes():
    guard = InvariantCoverageGuard(1, View({"a.py": 5}, set()))
    reason = guard(MAPPED)
    assert reason is not None and "a.py:1" in reason and "not in your diff" in reason
    assert guard.refusals == 1
    assert guard(MAPPED) is None  # the one refusal is spent, stale or not
    assert guard.refusals == coverage.REFUSALS


def test_a_gap_and_a_stale_location_share_one_refusal_and_one_message():
    guard = InvariantCoverageGuard(2, View({}, set()))
    reason = guard({"coverage": ["invariant 1: nope.py:3"]})
    assert reason is not None and "invariant(s) 2." in reason and "nope.py:3" in reason
    assert guard.refusals == 1
    assert guard({"coverage": ["invariant 1: other.py:3"]}) is None  # no second denial
    assert guard.refusals == 1


def test_a_map_of_real_locations_in_the_diff_is_accepted_and_the_view_is_asked_its_paths():
    look = View({"a.py": 5}, {"a.py"})
    guard = InvariantCoverageGuard(1, look)
    assert guard(MAPPED) is None
    assert guard.refusals == 0
    assert look.asked == [{"a.py"}]


def nothing(paths: set[str]) -> None:
    return None


def test_without_a_view_or_when_the_view_cannot_answer_the_locations_are_not_checked():
    assert InvariantCoverageGuard(1)(MAPPED) is None
    assert InvariantCoverageGuard(1, nothing)(MAPPED) is None


def test_a_map_without_locations_never_asks_the_View():
    look = View({}, set())
    guard = InvariantCoverageGuard(1, look)
    assert guard({"coverage": ["invariant 1: unsatisfiable, see Decisions"]}) is None
    assert look.asked == []


def test_the_stale_reasons_are_capped_and_the_rest_counted():
    lines = [f"invariant 1: n{i}.py:1" for i in range(8)]
    reason = InvariantCoverageGuard(1, View({}, set()))({"coverage": lines})
    assert reason is not None
    assert "n0.py:1" in reason and "n7.py:1" not in reason and "and 3 more" in reason


class BrokenDiff(MemoryWorkspace):
    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        raise GitError("boom")


def test_the_checkout_view_reads_lengths_at_the_head_and_the_diff_since_the_base():
    workspace = MemoryWorkspace()
    workspace.moved.add(Path("/p"))
    workspace.files[("head1", "a.py")] = "x\ny\nz\n"
    workspace.files[("head1", "empty.py")] = ""
    workspace.diff_paths = ("a.py",)
    look = checkout_view(workspace, Path("/p"), "base", "base")
    assert look({"a.py", "gone.py", "empty.py"}) == ({"a.py": 3, "empty.py": 0}, {"a.py"})
    assert workspace.diff_reads == [("base", "head1")]


def test_the_checkout_view_has_no_answer_before_a_commit_or_when_git_fails():
    unmoved = MemoryWorkspace()  # head stays "base", the commit the run started from
    assert checkout_view(unmoved, Path("/p"), "base", "base")({"a.py"}) is None
    broken = BrokenDiff()
    broken.moved.add(Path("/p"))
    assert checkout_view(broken, Path("/p"), "base", "base")({"a.py"}) is None


def test_a_plan_with_no_invariants_still_denies_a_brief_location_that_is_not_in_the_diff():
    guard = InvariantCoverageGuard(0, View({"a.py": 5}, set()))
    reason = guard({**BARE, "coverage": ["brief: add the parser -> a.py:1"]})
    assert reason is not None and "a.py:1" in reason and "not in your diff" in reason
    assert guard.refusals == 1
    assert (
        InvariantCoverageGuard(0, View({"a.py": 5}, {"a.py"}))(
            {**BARE, "coverage": ["brief: add the parser -> a.py:1"]}
        )
        is None
    )
