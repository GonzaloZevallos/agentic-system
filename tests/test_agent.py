"""`run` retry semantics with `_once` replaced. No model involved."""

import pytest

from agentic import agent
from agentic.agent import Output, RoleFailure, _AttemptFailed
from agentic.roles import PJM

pytestmark = pytest.mark.anyio


def scripted(monkeypatch, outcomes):
    calls = []

    async def fake_once(role, prompt, system_prompt, model, flags, cwd, check=None, observer=None):
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


async def test_once_keeps_the_session_id_and_model(monkeypatch):
    """`_once` with the SDK's `query` replaced: the result's session id lands in `Output`."""
    from claude_agent_sdk import ResultMessage

    result = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="sess-123",
        total_cost_usd=0.02,
        structured_output={
            "markdown": "ok",
            "type": "feature",
            "confidence": "high",
            "reason": "r",
        },
    )

    async def fake_query(prompt, options):
        yield result

    monkeypatch.setattr(agent, "query", fake_query)
    out = await agent._once(PJM, "p", "s", "model-x", {}, None)
    assert out.session_id == "sess-123"
    assert out.model == "model-x"
    assert out.turns == 2


async def test_check_failure_is_a_role_failure_with_the_error_in_the_retry(monkeypatch):
    """The node's own rule over the output goes back to the model like a schema error."""
    from claude_agent_sdk import ResultMessage

    seen_prompts = []

    async def fake_query(prompt, options):
        seen_prompts.append(prompt)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            total_cost_usd=0.01,
            structured_output={
                "markdown": "ok",
                "type": "feature",
                "confidence": "high",
                "reason": "r",
            },
        )

    def check(data):
        if data["confidence"] == "high":
            raise ValueError("be humble")

    monkeypatch.setattr(agent, "query", fake_query)
    with pytest.raises(RoleFailure) as info:
        await agent.run(PJM, "p", system_prompt="s", model="m", retries=1, flags={}, check=check)
    assert "be humble" in str(info.value)
    assert "be humble" in seen_prompts[1] and "be humble" not in seen_prompts[0]


async def test_observer_sees_the_stream_and_the_printer_renders_it(monkeypatch):
    import io

    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock

    from agentic.observe import Printer

    seen_options = []

    async def fake_query(prompt, options):
        seen_options.append(options)
        sid = "s"
        yield StreamEvent(
            uuid="1",
            session_id=sid,
            event={"type": "content_block_start", "content_block": {"type": "thinking"}},
        )
        yield StreamEvent(
            uuid="2",
            session_id=sid,
            event={
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        )
        yield StreamEvent(uuid="3", session_id=sid, event={"type": "content_block_stop"})
        yield StreamEvent(
            uuid="4",
            session_id=sid,
            event={"type": "content_block_start", "content_block": {"type": "text"}},
        )
        yield StreamEvent(
            uuid="5",
            session_id=sid,
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Clear "},
            },
        )
        yield StreamEvent(
            uuid="6",
            session_id=sid,
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "feature."},
            },
        )
        yield StreamEvent(uuid="7", session_id=sid, event={"type": "content_block_stop"})
        yield StreamEvent(
            uuid="8",
            session_id=sid,
            event={
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "StructuredOutput", "input": {}},
            },
        )
        for n, chunk in enumerate(('{"markdown": "### Verdict\\n', 'ok"}')):
            yield StreamEvent(
                uuid=str(9 + n),
                session_id=sid,
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": chunk},
                },
            )
        yield StreamEvent(uuid="11", session_id=sid, event={"type": "content_block_stop"})
        yield AssistantMessage(
            content=[ToolUseBlock(id="t", name="Read", input={"path": "x"})], model="m"
        )
        yield AssistantMessage(
            content=[ToolUseBlock(id="t2", name="StructuredOutput", input={"markdown": "x"})],
            model="m",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id=sid,
            total_cost_usd=0.5,
            structured_output={
                "markdown": "ok",
                "type": "feature",
                "confidence": "high",
                "reason": "r",
            },
        )

    monkeypatch.setattr(agent, "query", fake_query)
    buffer = io.StringIO()
    out = await agent._once(PJM, "p", "s", "m", {}, None, observer=Printer("PjM", buffer))
    assert out.session_id == "s"
    assert seen_options[0].include_partial_messages is True
    assert buffer.getvalue() == (
        "[PjM] thinking:\nhmm\n[PjM]:\nClear feature.\n"
        '[PjM] delivery:\n{"markdown": "### Verdict\nok"}\n'
        '[PjM] tool Read {"path": "x"}\n[PjM] done: 1 turns, $0.5000\n'
    )


def test_printer_skips_empty_thinking():
    import io

    from claude_agent_sdk import StreamEvent

    from agentic.observe import Printer

    buffer = io.StringIO()
    printer = Printer("PM", buffer)
    for event in (
        {"type": "content_block_start", "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": ""}},
        {"type": "content_block_delta", "delta": {"type": "signature_delta", "signature": "x"}},
        {"type": "content_block_stop"},
    ):
        printer(StreamEvent(uuid="u", session_id="s", event=event))
    assert buffer.getvalue() == ""


async def test_without_observer_no_partial_messages(monkeypatch):
    from claude_agent_sdk import ResultMessage

    seen_options = []

    async def fake_query(prompt, options):
        seen_options.append(options)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            structured_output={
                "markdown": "ok",
                "type": "feature",
                "confidence": "high",
                "reason": "r",
            },
        )

    monkeypatch.setattr(agent, "query", fake_query)
    await agent._once(PJM, "p", "s", "m", {}, None)
    assert seen_options[0].include_partial_messages is False


async def test_zero_retries_means_one_attempt(monkeypatch):
    scripted(monkeypatch, [_AttemptFailed("a")])
    with pytest.raises(RoleFailure) as info:
        await run(retries=0)
    assert len(info.value.failures) == 1
