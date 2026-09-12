"""`run` retry semantics with `_once` replaced. No model involved."""

import pytest

from agentic import agent
from agentic.agent import Output, RoleFailure, _AttemptFailed
from agentic.roles import PJM

pytestmark = pytest.mark.anyio


def scripted(monkeypatch, outcomes):
    calls = []

    async def fake_once(role, prompt, system_prompt, model, flags, cwd):
        calls.append(prompt)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(agent, "_once", fake_once)
    return calls


async def run(retries=1):
    return await agent.run(
        PJM, "task", system_prompt="s", model="m", retries=retries, flags={}, cwd=None
    )


async def test_retry_passes_the_error_and_sums_cost(monkeypatch):
    ok = Output(text="fine", data={}, cost_usd=0.03)
    calls = scripted(monkeypatch, [_AttemptFailed("structured output missing", 0.05), ok])
    out = await run()
    assert out.cost_usd == pytest.approx(0.08)
    assert out.failures == [
        {"role": "PjM", "attempt": 1, "error": "structured output missing", "cost_usd": 0.05}
    ]
    assert "<PreviousAttemptFailed>\nstructured output missing" in calls[1]
    assert calls[0] == "task"


async def test_second_failure_raises_role_failure_with_cost(monkeypatch):
    scripted(monkeypatch, [_AttemptFailed("a", 0.05), _AttemptFailed("b", 0.04)])
    with pytest.raises(RoleFailure) as info:
        await run()
    assert info.value.cost_usd == pytest.approx(0.09)
    assert str(info.value) == "PjM failed 2 times: b"


async def test_zero_retries_means_one_attempt(monkeypatch):
    scripted(monkeypatch, [_AttemptFailed("a")])
    with pytest.raises(RoleFailure) as info:
        await run(retries=0)
    assert len(info.value.failures) == 1
