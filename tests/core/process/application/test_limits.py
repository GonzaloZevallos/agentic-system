"""The run as a process: how many roles run at once, and what they all spent
(ADR-0004, fifth addendum)."""

import asyncio
from pathlib import Path

import pytest

from agentic.core.process.application.limits import RunLimits
from agentic.core.process.application.ports import AttemptFailed, RawResult
from agentic.core.process.application.run_role import TaskCapReached, run_with_retry
from agentic.core.process.domain.roles import PJM
from tests.fakes import ScriptedRunner, classified

pytestmark = pytest.mark.anyio

OK = classified(cost=0.03)


async def run_pjm(runner, limits, spent_usd=0.0, task_cap_usd=None):
    return await run_with_retry(
        PJM,
        "task",
        system_prompt="s",
        model="m",
        retries=1,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
        spent_usd=spent_usd,
        task_cap_usd=task_cap_usd,
        limits=limits,
    )


async def test_the_cap_is_read_against_what_every_branch_spent():
    # A branch of the fan-out carries a copy of the state, so `spent_usd` is what the
    # task had spent at the fan-out. `RunLimits` is what the branches share.
    limits = RunLimits()
    limits.record(10.4)
    with pytest.raises(TaskCapReached) as info:
        await run_pjm(ScriptedRunner(pjm=[OK]), limits, spent_usd=1.0, task_cap_usd=10.0)
    assert "10.40 of 10.00 USD spent" in str(info.value)


async def test_the_state_still_wins_when_it_is_the_larger_view():
    # A resumed run starts the counter at zero and the state carries the history.
    limits = RunLimits()
    with pytest.raises(TaskCapReached) as info:
        await run_pjm(ScriptedRunner(pjm=[OK]), limits, spent_usd=50.0, task_cap_usd=10.0)
    assert "50.00 of 10.00 USD spent" in str(info.value)


async def test_a_failed_attempt_is_recorded_too():
    limits = RunLimits()
    runner = ScriptedRunner(pjm=[AttemptFailed("cut", 1.5, session_id="s"), OK])
    await run_pjm(runner, limits)
    assert limits.spent_usd == pytest.approx(1.53)


async def test_a_role_already_running_is_never_cut_off():
    """The counter only refuses to start: work already paid for is never thrown away."""
    limits = RunLimits()

    class Slow:
        async def run_once(self, role, prompt, **kwargs):
            limits.record(100.0)  # a sister branch blows the cap while this one runs
            await asyncio.sleep(0)
            return RawResult(
                {"markdown": "m", "type": "bug", "confidence": "high", "reason": "r"}, 0.03
            )

    out = await run_pjm(Slow(), limits, task_cap_usd=10.0)
    assert out.cost_usd == pytest.approx(0.03)


async def test_the_semaphore_holds_a_slot_for_as_long_as_a_role_runs():
    limits = RunLimits().sized(1)
    inside, peak = 0, 0

    class Counting:
        async def run_once(self, role, prompt, **kwargs):
            nonlocal inside, peak
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0)
            inside -= 1
            return RawResult(
                {"markdown": "m", "type": "bug", "confidence": "high", "reason": "r"}, 0.01
            )

    await asyncio.gather(*(run_pjm(Counting(), limits) for _ in range(4)))
    assert peak == 1


async def test_unsized_limits_do_not_hold_anything_back():
    # A use case called outside a graph, or a test: there is no configuration to size it.
    out = await run_pjm(ScriptedRunner(pjm=[OK]), RunLimits())
    assert out.cost_usd == pytest.approx(0.03)


def test_the_first_configuration_sizes_the_semaphore_and_later_ones_get_the_same():
    limits = RunLimits()
    assert limits.sized(3) is limits
    first = limits.slot()
    assert limits.sized(9).slot() is first
