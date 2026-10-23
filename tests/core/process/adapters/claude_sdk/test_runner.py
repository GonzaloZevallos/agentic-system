"""The SDK adapter with `query` replaced. No model involved."""

import asyncio
import io
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from pathlib import Path

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agentic.core.process.adapters.claude_sdk import runner as module
from agentic.core.process.adapters.claude_sdk.runner import ClaudeSdkRunner, options_for
from agentic.core.process.adapters.shell import ShellReaper
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain import reaping
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import DEVELOPER, PJM, ROLES, TECH_LEAD_REVIEWER
from agentic.shared.environment import RESOURCE_ROLE_SLUG_KEY, RESOURCE_TRACE_ID_KEY
from tests.fakes import FakeReaper

pytestmark = pytest.mark.anyio

DELIVERY = {"markdown": "ok", "type": "feature", "confidence": "high", "reason": "r"}


class _FakeTelemetry:
    """A `Telemetry` whose `traceparent()` is fixed; `run_once` never calls
    `role_span`, so this double needs no more than the one method it uses."""

    def __init__(self, traceparent: str | None):
        self._traceparent = traceparent

    def traceparent(self) -> str | None:
        return self._traceparent


def result(**overrides) -> ResultMessage:
    fields = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="sess-123",
        total_cost_usd=0.02,
        structured_output=DELIVERY,
    )
    return ResultMessage(**{**fields, **overrides})


PRODUCT_KEY = "abcdef012345"


def make_runner(**kwargs) -> ClaudeSdkRunner:
    """A runner with a recording reaper that touches no process (GH-282); a test of the
    reaping itself passes its own `reaper` and `product_key`."""
    kwargs.setdefault("reaper", FakeReaper())
    kwargs.setdefault("product_key", PRODUCT_KEY)
    return ClaudeSdkRunner(**kwargs)


def fake_query(monkeypatch, messages):
    seen = []

    async def query(prompt, options):
        seen.append((prompt, options))
        for message in messages:
            yield message

    monkeypatch.setattr(module, "query", query)
    return seen


async def run_once(runner: ClaudeSdkRunner, tmp_path):
    return await runner.run_once(
        PJM,
        "p",
        system_prompt="s",
        model="model-x",
        json_schema={"type": "object"},
        cwd=tmp_path,
        owner_task="GH-1",
    )


async def test_raw_result_carries_session_cost_and_turns(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.structured_output == DELIVERY
    assert (raw.session_id, raw.turns, raw.cost_usd) == ("sess-123", 2, 0.02)
    prompt, options = seen[0]
    assert prompt == "p"
    assert options.model == "model-x" and options.cwd == tmp_path
    assert options.tools == [] and options.setting_sources == []
    assert options.effort == PJM.effort and options.max_turns == PJM.max_turns
    assert options.include_partial_messages is False


async def test_no_role_sees_the_users_mcp_servers(monkeypatch, tmp_path):
    # Without `strict_mcp_config` the CLI loads the user's own MCP servers into every
    # role, and no role uses them (README.md, 14, Observabilidad; issue #18).
    seen = fake_query(monkeypatch, [result()])
    await run_once(make_runner(), tmp_path)
    options = seen[0][1]
    assert options.strict_mcp_config is True
    assert options.mcp_servers == {}


async def test_raw_result_splits_the_cost_by_model(monkeypatch, tmp_path):
    # The SDK's total covers the sub-agents a role spawns; `model_usage` says how much
    # went to each model, which is how a Sonnet sub-agent shows next to an Opus parent.
    usage = {
        "claude-opus-5": {"costUSD": 0.5, "inputTokens": 10},
        "claude-sonnet-5": {"costUSD": 0.25, "inputTokens": 20},
    }
    fake_query(monkeypatch, [result(total_cost_usd=0.75, model_usage=usage)])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.cost_usd == 0.75
    assert raw.cost_by_model == {"claude-opus-5": 0.5, "claude-sonnet-5": 0.25}
    fake_query(monkeypatch, [result(model_usage=None)])
    assert (await run_once(make_runner(), tmp_path)).cost_by_model == {}


async def test_raw_result_carries_usage_calls_and_tool_calls(monkeypatch, tmp_path):
    usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 40,
    }
    first = AssistantMessage(
        content=[
            ToolUseBlock(id="1", name="Read", input={}),
            ToolUseBlock(id="2", name="Bash", input={}),
        ],
        model="m",
    )
    second = AssistantMessage(
        content=[
            ToolUseBlock(id="3", name="Read", input={}),
            ToolUseBlock(id="4", name="mcp__weird__tool", input={}),
            ToolUseBlock(id="5", name="StructuredOutput", input={"markdown": "ok"}),
        ],
        model="m",
    )
    fake_query(monkeypatch, [first, second, result(usage=usage)])
    raw = await run_once(make_runner(), tmp_path)
    # Normalised to the closed `kind` set `agentic.role.tokens` groups by, off the SDK's
    # own snake_case keys.
    assert raw.usage == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}
    assert raw.calls == 2  # one per AssistantMessage seen before the delivery
    assert raw.tool_calls == {"Read": 2, "Bash": 1, "other": 1, "StructuredOutput": 1}


async def test_raw_result_usage_defaults_when_the_sdk_reports_none(monkeypatch, tmp_path):
    fake_query(monkeypatch, [result(usage=None)])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.usage == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    assert raw.calls == 0
    assert raw.tool_calls == {}


async def test_usage_of_a_resumed_session_from_an_earlier_node_is_not_adjusted(
    monkeypatch, tmp_path
):
    # The probe (observability/README.md, 14) resumed a session across two separate
    # `query()` calls and printed the second call's own `usage`: {"input_tokens": 2,
    # "cache_creation_input_tokens": 63, "cache_read_input_tokens": 1862,
    # "output_tokens": 4}, not the first call's added in. `run_once` has no session
    # this runner opened itself to compare against here either way — `resume` may name
    # a session an earlier node opened — so there is no baseline it could subtract even
    # if the SDK's own figure needed one, which the probe found it does not.
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 63,
        "cache_read_input_tokens": 1862,
        "output_tokens": 4,
    }
    fake_query(monkeypatch, [result(usage=usage)])
    raw = await make_runner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume="session-opened-by-an-earlier-node",
        owner_task="GH-1",
    )
    assert raw.usage == {"input": 2, "output": 4, "cache_read": 1862, "cache_write": 63}


async def test_nested_schemas_are_inlined_for_the_sdk(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    schema = {
        "$defs": {"Item": {"type": "object", "properties": {"a": {"type": "string"}}}},
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
    }
    await make_runner().run_once(
        PJM, "p", system_prompt="s", model="m", json_schema=schema, cwd=tmp_path, owner_task="GH-1"
    )
    sent = seen[0][1].output_format["schema"]
    assert "$defs" not in sent and "$ref" not in str(sent)
    assert sent["properties"]["items"]["items"]["properties"]["a"] == {"type": "string"}
    assert "$defs" in schema  # the caller's schema is not mutated


async def test_missing_result_and_error_results_are_attempt_failures(monkeypatch, tmp_path):
    fake_query(monkeypatch, [])
    with pytest.raises(AttemptFailed, match="without a result") as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.reason == "sdk"

    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed, match="max_turns \\(2\\) exhausted") as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.cost_usd == 0.5
    assert info.value.reason == "max_turns"

    fake_query(monkeypatch, [result(is_error=True, subtype="error", errors=["boom"])])
    with pytest.raises(AttemptFailed, match="boom") as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.reason == "sdk"


async def test_a_failed_attempt_keeps_the_session_it_left_behind(monkeypatch, tmp_path):
    # The money cap cuts the run: the work is done, the session is alive, and a retry
    # that resumes it delivers instead of reading everything again.
    cut = result(is_error=True, subtype="error_max_budget_usd", total_cost_usd=3.05)
    fake_query(monkeypatch, [cut])
    with pytest.raises(AttemptFailed, match="error_max_budget_usd") as info:
        await run_once(make_runner(), tmp_path)
    assert (info.value.cost_usd, info.value.session_id) == (3.05, "sess-123")
    # A per-attempt budget cap is `limit` (issue #66), not a generic `sdk` failure.
    assert info.value.reason == "limit"

    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.session_id == "sess-123"

    fake_query(monkeypatch, [])  # no result at all: nothing to resume
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.session_id == ""


async def test_the_run_ends_at_the_first_delivery(monkeypatch, tmp_path):
    # A sub-agent left in the background reopens the session when it returns and the
    # model delivers again, one ResultMessage per notification (issue #16). The first
    # delivery is the verdict: the runner leaves the stream there, which ends the
    # process, and the printer shows one `done`.
    launched = AssistantMessage(
        content=[
            ToolUseBlock(
                id="a1",
                name="Agent",
                input={"prompt": "standards", "run_in_background": True},
            ),
            ToolUseBlock(id="a2", name="Agent", input={"prompt": "spec"}),
        ],
        model="m",
    )
    first = result(num_turns=16, total_cost_usd=2.53, structured_output={"markdown": "v1"})
    repeat = result(num_turns=3, total_cost_usd=2.92, structured_output={"markdown": "v2"})
    consumed = []

    async def query(prompt, options):
        for message in (launched, first, repeat):
            consumed.append(message)
            yield message

    monkeypatch.setattr(module, "query", query)
    buffer = io.StringIO()
    raw = await run_once(make_runner(verbose=True, stream=buffer), tmp_path)
    assert raw.structured_output == {"markdown": "v1"}
    assert (raw.turns, raw.cost_usd) == (16, 2.53)
    assert repeat not in consumed  # the generator was closed before it
    assert buffer.getvalue().count("done:") == 1
    # The log says why the verdict came out without its sub-agents' reports.
    assert "delivered with 1 sub-agent(s) launched in the background" in buffer.getvalue()


async def test_a_result_without_a_delivery_does_not_end_the_run(monkeypatch, tmp_path):
    # Only a delivery ends the run early: a result without one leaves the stream to
    # the SDK, as before, and the last result is what counts. Without background
    # sub-agents there is nothing to warn about, verbose or not.
    #
    # Behaviour changed here (#33): a sink now decides whether the stream is observed,
    # not `verbose` -- so `stream=buffer` with `verbose=False` (the default) still gets
    # a `done:` line per result, where it used to get nothing.
    empty = result(structured_output=None, num_turns=1)
    fake_query(monkeypatch, [empty, result(num_turns=2)])
    buffer = io.StringIO()
    raw = await run_once(make_runner(stream=buffer), tmp_path)
    assert raw.structured_output == DELIVERY and raw.turns == 2
    assert "[PjM] done: 1 turns" in buffer.getvalue()
    assert "[PjM] done: 2 turns" in buffer.getvalue()

    fake_query(monkeypatch, [empty])
    raw = await run_once(make_runner(stream=buffer), tmp_path)
    assert raw.structured_output is None  # the use case's failure, not the runner's


async def test_a_sink_observes_the_stream_even_when_verbose_is_false(monkeypatch, tmp_path):
    # The sink decides whether the stream is watched (module docstring, #33); `verbose`
    # only decides `include_partial_messages`, the fine-grained deltas. A role's tool
    # lines, its `done:` line and the sub-agent warning all reach the sink either way.
    launched = AssistantMessage(
        content=[
            ToolUseBlock(id="a1", name="Agent", input={"prompt": "x", "run_in_background": True}),
            ToolUseBlock(id="a2", name="Read", input={"path": "y"}),
        ],
        model="m",
    )
    seen = fake_query(
        monkeypatch,
        [launched, result(structured_output=DELIVERY, num_turns=3, total_cost_usd=0.1)],
    )
    buffer = io.StringIO()
    raw = await run_once(make_runner(stream=buffer, verbose=False), tmp_path)
    assert raw.structured_output == DELIVERY
    assert seen[0][1].include_partial_messages is False  # verbose alone gates deltas
    written = buffer.getvalue()
    assert '[PjM] tool Read {"path": "y"}' in written
    assert "[PjM] done: 3 turns, $0.1000" in written
    assert "delivered with 1 sub-agent(s) launched in the background" in written


async def test_verbose_also_mirrors_the_sink_on_stderr(monkeypatch, tmp_path, capsys):
    # `--verbose` is additive on top of the sink (PM's decision, README.md, 14): a log
    # file always gets a role's tool lines and its `done:` line; `--verbose` decides
    # only whether the same lines also reach the terminal.
    messages = [
        AssistantMessage(
            content=[ToolUseBlock(id="t", name="Read", input={"path": "x"})], model="m"
        ),
        result(num_turns=1, total_cost_usd=0.02),
    ]
    fake_query(monkeypatch, messages)
    buffer = io.StringIO()
    await run_once(make_runner(verbose=True, stream=buffer), tmp_path)
    written = buffer.getvalue()
    assert '[PjM] tool Read {"path": "x"}' in written and "[PjM] done: 1 turns" in written
    err = capsys.readouterr().err
    assert '[PjM] tool Read {"path": "x"}' in err and "[PjM] done: 1 turns" in err


async def test_verbose_without_a_sink_still_streams_to_stderr_alone(monkeypatch, tmp_path, capsys):
    # No log file (the `langgraph dev` door, or `AGENTIC_VERBOSE` with no task under
    # the CLI): `--verbose` keeps its old, only destination.
    messages = [
        AssistantMessage(
            content=[ToolUseBlock(id="t", name="Read", input={"path": "x"})], model="m"
        ),
        result(num_turns=1, total_cost_usd=0.02),
    ]
    fake_query(monkeypatch, messages)
    await run_once(make_runner(verbose=True), tmp_path)
    err = capsys.readouterr().err
    assert '[PjM] tool Read {"path": "x"}' in err and "[PjM] done: 1 turns" in err


async def test_a_cancelled_stream_writes_the_token_line_once_and_reraises_unchanged(
    monkeypatch, tmp_path
):
    # SIGTERM/SIGHUP cancel the invocation (the `signals` subtask); the cancellation
    # must never become an `AttemptFailed`, a retry or a `Failure` entry (invariant 9).
    # The token figure is a read off the last main-thread `AssistantMessage.usage`, fed
    # through the same normaliser `_usage` uses -- never a sum, sub-agents never counted
    # (invariant 10).
    main_usage = {"input_tokens": 5, "output_tokens": 7}
    main = AssistantMessage(content=[], model="m", parent_tool_use_id=None, usage=main_usage)
    sub_agent = AssistantMessage(
        content=[],
        model="m",
        parent_tool_use_id="sub-1",
        usage={"input_tokens": 999, "output_tokens": 999},
    )

    async def query(prompt, options):
        yield main
        yield sub_agent
        raise anyio.get_cancelled_exc_class()()

    monkeypatch.setattr(module, "query", query)
    buffer = io.StringIO()
    with pytest.raises(anyio.get_cancelled_exc_class()):
        await run_once(make_runner(stream=buffer), tmp_path)
    written = buffer.getvalue()
    assert written.count("interrupted") == 1
    assert "[PjM] interrupted after 2 turns, 5 input / 7 output tokens seen on the main " in written
    assert "sub-agents not counted, cost unknown" in written
    assert "999" not in written  # the sub-agent's usage never counted


async def test_sdk_exceptions_are_attempt_failures(monkeypatch, tmp_path):
    async def query(prompt, options):
        raise RuntimeError("socket closed")
        yield  # pragma: no cover

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed, match="SDK error: socket closed") as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.reason == "sdk"


async def test_an_sdk_exception_with_no_message_names_its_type_instead_of_nothing(
    monkeypatch, tmp_path
):
    async def query(prompt, options):
        raise RuntimeError
        yield  # pragma: no cover

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed, match="SDK error: RuntimeError, with no message"):
        await run_once(make_runner(), tmp_path)


LIMIT_PROSE = "API Error: You've hit your session limit · resets 11:20pm"


async def test_a_result_error_with_success_and_no_errors_still_names_the_limit(
    monkeypatch, tmp_path
):
    # The 2026-09-17 incident (issue #66): the CLI reports the limit in `result`, with
    # `subtype="success"` and `errors=[]`; the old f-string produced `run failed
    # (success): `.
    from claude_agent_sdk import ResultError

    async def query(prompt, options):
        yield result(is_error=True, subtype="success", total_cost_usd=0.0)
        raise ResultError(
            "run failed", data={"subtype": "success", "errors": [], "result": LIMIT_PROSE}
        )

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert LIMIT_PROSE in str(info.value)
    assert "success" not in str(info.value)


async def test_the_same_payload_as_a_result_message_still_names_the_limit(monkeypatch, tmp_path):
    # The `ResultMessage` shape the module docstring says the CLI may never actually
    # produce (the error result is followed by a non-zero exit, `ResultError`'s path):
    # the branch stays, and its motive is held to the same rule.
    fake_query(
        monkeypatch,
        [result(is_error=True, subtype="success", errors=[], result=LIMIT_PROSE)],
    )
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert LIMIT_PROSE in str(info.value)
    assert "run ended in error: success" not in str(info.value)


async def test_max_turns_motive_carries_what_the_sdk_reported(monkeypatch, tmp_path):
    # Issues #69/#81: a role that spent its turns fighting a denial deserves more than
    # "max_turns (N) exhausted"; the model's own prose (here, a stand-in for the CLI's
    # report of what it was doing) travels with it, and `subtype` is not repeated since
    # it is exactly how this branch is reached in the first place.
    fake_query(
        monkeypatch,
        [
            result(
                subtype="error_max_turns",
                total_cost_usd=0.5,
                result="wrote a heredoc to /tmp/test_repro.py; denied",
            )
        ],
    )
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert str(info.value) == (
        "max_turns (2) exhausted without a result: wrote a heredoc to /tmp/test_repro.py; denied"
    )
    assert "subtype" not in str(info.value)
    assert info.value.reason == "max_turns"


async def test_max_turns_motive_is_bare_when_the_sdk_reports_nothing_else(monkeypatch, tmp_path):
    # Tier 3 of the definition (`docs/product/role-failures.md`): nothing beyond the
    # subtype that put us in this branch is not new information, so the fixed sentence
    # stands alone rather than being followed by a tautological "no cause reported".
    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert str(info.value) == "max_turns (2) exhausted without a result"


async def test_max_turns_motive_via_result_error_carries_what_the_sdk_reported(
    monkeypatch, tmp_path
):
    from claude_agent_sdk import ResultError

    async def query(prompt, options):
        yield result(is_error=True, subtype="error_max_turns", total_cost_usd=0.5)
        raise ResultError(
            "run failed",
            data={
                "subtype": "error_max_turns",
                "errors": ["cat > /tmp/test_repro.py <<EOF: permission denied"],
            },
        )

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert str(info.value) == (
        "max_turns (2) exhausted without a result: "
        "cat > /tmp/test_repro.py <<EOF: permission denied"
    )
    assert info.value.reason == "max_turns"


async def test_a_result_error_with_a_budget_subtype_is_the_limit_reason(monkeypatch, tmp_path):
    from claude_agent_sdk import ResultError

    async def query(prompt, options):
        yield result(is_error=True, subtype="error_max_budget_usd", total_cost_usd=1.0)
        raise ResultError(
            "run failed", data={"subtype": "error_max_budget_usd", "errors": ["over budget"]}
        )

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed, match="error_max_budget_usd") as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.reason == "limit"


async def test_verbose_streams_the_generation_through_the_printer(monkeypatch, tmp_path):
    sid = "s"

    def event(uuid, **event):
        return StreamEvent(uuid=uuid, session_id=sid, event=event)

    messages = [
        event("1", type="content_block_start", content_block={"type": "thinking"}),
        event("2", type="content_block_delta", delta={"type": "thinking_delta", "thinking": "hmm"}),
        event("3", type="content_block_stop"),
        event("4", type="content_block_start", content_block={"type": "text"}),
        event("5", type="content_block_delta", delta={"type": "text_delta", "text": "Clear "}),
        event("6", type="content_block_delta", delta={"type": "text_delta", "text": "feature."}),
        event("7", type="content_block_stop"),
        event(
            "8",
            type="content_block_start",
            content_block={"type": "tool_use", "name": "StructuredOutput", "input": {}},
        ),
        event(
            "9",
            type="content_block_delta",
            delta={"type": "input_json_delta", "partial_json": '{"markdown": "### Verdict\\n'},
        ),
        event(
            "10",
            type="content_block_delta",
            delta={"type": "input_json_delta", "partial_json": 'ok"}'},
        ),
        event("11", type="content_block_stop"),
        AssistantMessage(
            content=[ToolUseBlock(id="t", name="Read", input={"path": "x"})], model="m"
        ),
        AssistantMessage(
            content=[ToolUseBlock(id="t2", name="StructuredOutput", input={"markdown": "x"})],
            model="m",
        ),
        result(session_id=sid, num_turns=1, total_cost_usd=0.5),
    ]
    seen = fake_query(monkeypatch, messages)
    buffer = io.StringIO()
    raw = await run_once(make_runner(verbose=True, stream=buffer), tmp_path)
    assert raw.session_id == "s"
    assert seen[0][1].include_partial_messages is True
    assert buffer.getvalue() == (
        "[PjM] thinking:\nhmm\n[PjM]:\nClear feature.\n"
        '[PjM] delivery:\n{"markdown": "### Verdict\nok"}\n'
        '[PjM] tool Read {"path": "x"}\n[PjM] done: 1 turns, $0.5000\n'
    )


async def test_a_role_that_executes_runs_unattended_with_a_budget(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    developer = replace(
        PJM, slug="developer", tools=("Read", "Edit", "Bash"), skills=("tdd",), executes=True
    )
    await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        max_budget_usd=3.0,
        owner_task="GH-1",
    )
    options = seen[0][1]
    assert options.permission_mode == "bypassPermissions"
    assert options.max_budget_usd == 3.0
    assert options.tools == ["Read", "Edit", "Bash", "Skill"]  # skills need the Skill tool
    assert options.skills == ["mattpocock-skills:tdd"]
    assert options.plugins == [{"type": "local", "path": str(module.PLUGIN_DIR)}]
    assert (
        module.PLUGIN_DIR.name == "mattpocock-skills" and module.PLUGIN_DIR.parent.name == "plugins"
    )
    assert options.resume is None


async def test_a_reader_role_is_caged_by_permission_rules(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    reader = replace(
        PJM, tools=("Read", "Bash"), allowed=("Bash(git diff:*)", "Read"), skills=("code-review",)
    )
    await make_runner().run_once(
        reader,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        resume="sess-1",
        owner_task="GH-1",
    )
    options = seen[0][1]
    assert options.permission_mode == "default"  # anything outside the rules is denied
    assert options.allowed_tools == ["Bash(git diff:*)", "Read", "Skill"]  # its skills included
    assert options.tools == ["Read", "Bash", "Skill"]
    assert options.skills == ["mattpocock-skills:code-review"]
    assert options.max_budget_usd is None
    assert options.resume == "sess-1"


async def test_the_git_identity_and_the_deny_rules_reach_the_role(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    identity = {"GIT_AUTHOR_NAME": "agentic-system", "GIT_COMMITTER_NAME": "agentic-system"}
    developer = replace(PJM, tools=("Bash",), executes=True, denied=("Bash(git config:*)",))
    await make_runner(env=identity).run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    options = seen[0][1]
    # The SDK merges `env` over the inherited environment, so every `git` the role
    # runs commits as the system, whatever `user.*` the product's config carries.
    assert {k: v for k, v in options.env.items() if k != reaping.MARK_NAME} == identity
    # Deny rules hold even in `bypassPermissions`.
    assert options.disallowed_tools == ["Bash(git config:*)"]


def test_options_for_marks_the_child_env_with_role_and_trace(tmp_path):
    # GH-70: the marker identifies whose run a spawned process's spans belong to.
    # `with_resource_marker`/`trace_id_of` are the pure functions `gate` shipped in
    # `agentic/shared/environment.py`; this is the wiring that reaches every role.
    # `resource_attributes` stands for the real ambient `os.environ` value, kept
    # apart from `env` (the runner's own git-identity overlay, which never carries
    # it in production) so the two sources cannot be confused.
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    options = options_for(
        PJM,
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=traceparent,
        partial=False,
        env={"GIT_AUTHOR_NAME": "agentic-system"},
        resource_attributes="deployment.environment=prod",
    )
    attrs = options.env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "deployment.environment=prod" in attrs  # the inherited entry survives
    assert f"{RESOURCE_ROLE_SLUG_KEY}={PJM.slug}" in attrs
    assert f"{RESOURCE_TRACE_ID_KEY}=4bf92f3577b34da6a3ce929d0e0e4736" in attrs
    assert "service." not in attrs  # never a service.* key (invariant 5)
    assert options.env["TRACEPARENT"] == traceparent  # unchanged (module docstring)
    assert options.env["GIT_AUTHOR_NAME"] == "agentic-system"  # env's own entries survive too


def test_options_for_adds_no_marker_without_a_traceparent(tmp_path):
    # No trace, nothing to mark: `test_the_git_identity_and_the_deny_rules_reach_the_role`
    # already pins `options.env == identity` for `NoTelemetry`'s bare `None`.
    options = options_for(
        PJM,
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes="deployment.environment=prod",
    )
    assert "OTEL_RESOURCE_ATTRIBUTES" not in options.env
    assert "TRACEPARENT" not in options.env


@pytest.mark.parametrize("role", ROLES, ids=lambda role: role.slug)
def test_options_for_sets_max_buffer_size_for_every_role(role, tmp_path):
    # GH-160: the SDK's `_DEFAULT_MAX_BUFFER_SIZE` (1 MiB) is the cap on one JSON
    # message off the CLI's stdout; a `Read` on a screenshot over ~750 KB (base64 in
    # the tool result) exceeded it and killed the SDK's reader (apostrophe GH-58).
    # `options_for` is the one site in `agentic/` that builds `ClaudeAgentOptions`, so
    # every role, parametrized over the whole closed set, must get the raised value.
    options = options_for(
        role,
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
    )
    assert options.max_buffer_size == module.MAX_BUFFER_BYTES


async def test_run_once_marks_against_the_real_ambient_resource_attributes(monkeypatch, tmp_path):
    # Review finding (blocking): the composed marker must preserve the process's own
    # ambient `OTEL_RESOURCE_ATTRIBUTES` (`os.environ`), not just whatever `env=` the
    # runner was constructed with (the git identity, which never carries it in
    # production, `agentic/composition.py`) -- invariant 8. This exercises the wiring
    # through `ClaudeSdkRunner.run_once`, the seam that was actually wrong.
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=prod")
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    seen = fake_query(monkeypatch, [result()])
    identity = {"GIT_AUTHOR_NAME": "agentic-system"}
    await make_runner(env=identity, telemetry=_FakeTelemetry(traceparent)).run_once(
        PJM, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, owner_task="GH-1"
    )
    attrs = seen[0][1].env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "deployment.environment=prod" in attrs  # the real ambient entry survives
    assert f"{RESOURCE_ROLE_SLUG_KEY}={PJM.slug}" in attrs
    assert f"{RESOURCE_TRACE_ID_KEY}=4bf92f3577b34da6a3ce929d0e0e4736" in attrs


async def test_a_role_that_executes_runs_inside_the_hooks_cage(monkeypatch, tmp_path):
    # Deny rules match a prefix; the hooks see the parsed call (ADR-0011, addendum).
    seen = fake_query(monkeypatch, [result()])
    developer = replace(PJM, tools=("Bash",), executes=True)
    await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    matchers = seen[0][1].hooks["PreToolUse"]
    assert {m.matcher for m in matchers} == {"Bash", "Edit|Write"}
    seen = fake_query(monkeypatch, [result()])
    await run_once(make_runner(), tmp_path)
    assert seen[0][1].hooks is None


async def test_a_non_executing_role_gets_a_can_use_tool_denial_callback(monkeypatch, tmp_path):
    # A role that does not execute runs in `permission_mode="default"`: anything its
    # `allowed` rules do not cover reaches the SDK's permission layer, which is where
    # the role's own rule (`denial_for`) has to land instead of the SDK's generic text.
    seen = fake_query(monkeypatch, [result()])
    reader = replace(PJM, tools=("Read",), allowed=("Read",))
    await make_runner().run_once(
        reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, owner_task="GH-1"
    )
    assert seen[0][1].can_use_tool is not None


async def test_an_executing_role_gets_no_can_use_tool_callback(monkeypatch, tmp_path):
    # `bypassPermissions` already shadows `can_use_tool` entirely (the SDK's own
    # warning); wiring one anyway would be dead code pretending to cage a role the
    # hooks cage already covers (ADR-0011, addendum).
    seen = fake_query(monkeypatch, [result()])
    developer = replace(PJM, tools=("Bash",), executes=True)
    await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    assert seen[0][1].can_use_tool is None


async def test_the_denial_callback_names_the_role_rule_and_is_counted(monkeypatch, tmp_path):
    reader = replace(PJM, tools=("Read", "Write"), allowed=("Read",))

    async def query(prompt, options):
        outcome = await options.can_use_tool(
            "Write", {"file_path": "x"}, ToolPermissionContext(tool_use_id="t1")
        )
        assert outcome.behavior == "deny"
        assert outcome.message == denial_for(reader)
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await make_runner().run_once(
        reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, owner_task="GH-1"
    )
    assert raw.denials == 1


async def test_a_denial_during_a_failed_attempt_still_reaches_attemptfailed(monkeypatch, tmp_path):
    reader = replace(PJM, tools=("Read", "Write"), allowed=("Read",))

    async def query(prompt, options):
        await options.can_use_tool(
            "Write", {"file_path": "x"}, ToolPermissionContext(tool_use_id="t1")
        )
        yield result(subtype="error_max_turns", total_cost_usd=0.1)

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await make_runner().run_once(
            reader,
            "p",
            system_prompt="s",
            model="m",
            json_schema={},
            cwd=tmp_path,
            owner_task="GH-1",
        )
    assert info.value.denials == 1


def _main_thread_calls(n: int) -> list[AssistantMessage]:
    """`n` main-thread `AssistantMessage`s with no tool call: what the stream loop's
    own `turns.value += 1` counts (`runner.py`'s `parent_tool_use_id is None` branch),
    the seam the notice's threshold is read against -- never a `PostToolUse` tally the
    hook keeps on its own any more."""
    return [AssistantMessage(content=[], model="m") for _ in range(n)]


async def test_a_fired_turn_notice_is_carried_on_the_raw_result(monkeypatch, tmp_path):
    # First position 45 (`turn_notice.TURN_NOTICE_AT`): the 45th main-thread call
    # brings `turns.value` to the position, so the next `PostToolUse` event fires --
    # and carries `main_thread_turns`, the counter's own final value, alongside it.
    developer = replace(DEVELOPER, max_turns=200)

    async def query(prompt, options):
        for message in _main_thread_calls(45):
            yield message
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    assert raw.turn_notice is True
    assert raw.turn_notice_firings == 1
    assert raw.main_thread_turns == 45


async def test_a_second_posttooluse_event_reaching_the_second_window_raises_the_firing_count(
    monkeypatch, tmp_path
):
    # The collapsing predicate's own point: a second event only fires again once it
    # crosses a *new* position (65, not merely a later call still inside window 1).
    developer = replace(DEVELOPER, max_turns=200)

    async def query(prompt, options):
        for message in _main_thread_calls(45):
            yield message
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        for message in _main_thread_calls(20):  # turns.value now 65
            yield message
        await hook({"tool_name": "Bash"}, "t2", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    assert raw.turn_notice_firings == 2
    assert raw.main_thread_turns == 65


async def test_no_turn_notice_when_the_threshold_is_never_crossed(monkeypatch, tmp_path):
    developer = replace(DEVELOPER, max_turns=200)

    async def query(prompt, options):
        for message in _main_thread_calls(44):  # one short of the first position
            yield message
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await make_runner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        owner_task="GH-1",
    )
    assert raw.turn_notice is False
    assert raw.turn_notice_firings == 0
    assert raw.main_thread_turns == 44


async def test_a_fired_turn_notice_during_a_failed_attempt_still_reaches_attemptfailed(
    monkeypatch, tmp_path
):
    developer = replace(DEVELOPER, max_turns=200)

    async def query(prompt, options):
        for message in _main_thread_calls(45):
            yield message
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result(subtype="error_max_turns", total_cost_usd=0.1)

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await make_runner().run_once(
            developer,
            "p",
            system_prompt="s",
            model="m",
            json_schema={},
            cwd=tmp_path,
            owner_task="GH-1",
        )
    assert info.value.turn_notice is True
    assert info.value.turn_notice_firings == 1
    assert info.value.main_thread_turns == 45


async def test_main_thread_turns_is_carried_on_every_other_raise_site_too(monkeypatch, tmp_path):
    # Acceptance criteria: written at every raise site, not only the `max_turns` one.
    # `run ended without a result message` is the site with no `ResultMessage` at all.
    async def query(prompt, options):
        for message in _main_thread_calls(7):
            yield message

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await make_runner().run_once(
            PJM, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, owner_task="GH-1"
        )
    assert info.value.main_thread_turns == 7


async def test_raw_result_carries_the_delivery_tool_emissions(monkeypatch, tmp_path):
    # The runner's own `tool_calls` counter for `StructuredOutput`, exposed as a
    # dedicated field (glossary: Emisión de entrega): #32's repeated invalid re-emits
    # inside one attempt are what this counts.
    calls = AssistantMessage(
        content=[
            ToolUseBlock(id="1", name="StructuredOutput", input={"markdown": "a"}),
            ToolUseBlock(id="2", name="StructuredOutput", input={"markdown": "b"}),
        ],
        model="m",
    )
    fake_query(monkeypatch, [calls, result()])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.deliveries == 2


async def test_raw_result_deliveries_default_when_the_tool_was_never_called(monkeypatch, tmp_path):
    fake_query(monkeypatch, [result()])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.deliveries == 0


@pytest.mark.parametrize(
    "build_messages",
    [
        # is_error
        lambda extra: [*extra, result(is_error=True, subtype="error", errors=["boom"])],
        # error_max_turns, as a `ResultMessage.subtype`
        lambda extra: [*extra, result(subtype="error_max_turns", total_cost_usd=0.5)],
        # error_max_budget_usd
        lambda extra: [
            *extra,
            result(is_error=True, subtype="error_max_budget_usd", total_cost_usd=3.05),
        ],
        # no result message at all
        lambda extra: extra,
    ],
)
async def test_attemptfailed_carries_deliveries_on_every_result_based_failure(
    monkeypatch, tmp_path, build_messages
):
    extra = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="1", name="StructuredOutput", input={}),
                ToolUseBlock(id="2", name="StructuredOutput", input={}),
            ],
            model="m",
        )
    ]
    fake_query(monkeypatch, build_messages(extra))
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.deliveries == 2


async def test_attemptfailed_carries_deliveries_via_result_error(monkeypatch, tmp_path):
    from claude_agent_sdk import ResultError

    extra = AssistantMessage(
        content=[ToolUseBlock(id="1", name="StructuredOutput", input={})], model="m"
    )

    async def query(prompt, options):
        yield extra
        yield result(is_error=True, subtype="error_max_turns", total_cost_usd=0.5)
        raise ResultError("run failed", data={"subtype": "error_max_turns", "errors": ["x"]})

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.deliveries == 1


async def test_attemptfailed_carries_deliveries_on_an_sdk_exception(monkeypatch, tmp_path):
    extra = AssistantMessage(
        content=[ToolUseBlock(id="1", name="StructuredOutput", input={})], model="m"
    )

    async def query(prompt, options):
        yield extra
        raise RuntimeError("socket closed")

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(), tmp_path)
    assert info.value.deliveries == 1


async def test_a_presubmit_denial_is_counted_on_the_raw_result(monkeypatch, tmp_path):
    class FakePresubmit:
        timeout_s = 1.0

        def __call__(self):
            return "suite red"

    async def query(prompt, options):
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "StructuredOutput", "tool_input": {}}, "t1", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await make_runner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        presubmit=FakePresubmit(),
        owner_task="GH-1",
    )
    assert raw.presubmit_denials == 1
    assert raw.denials == 0  # a different fact: the SDK's own permission layer


async def test_a_presubmit_denial_during_a_failed_attempt_still_reaches_attemptfailed(
    monkeypatch, tmp_path
):
    class FakePresubmit:
        timeout_s = 1.0

        def __call__(self):
            return "suite red"

    async def query(prompt, options):
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "StructuredOutput", "tool_input": {}}, "t1", {"signal": None})
        yield result(subtype="error_max_turns", total_cost_usd=0.1)

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await make_runner().run_once(
            PJM,
            "p",
            system_prompt="s",
            model="m",
            json_schema={},
            cwd=tmp_path,
            presubmit=FakePresubmit(),
            owner_task="GH-1",
        )
    assert info.value.presubmit_denials == 1


async def test_a_role_with_no_margin_never_gets_the_turn_notice_hook_wired(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    await run_once(make_runner(), tmp_path)
    assert "PostToolUse" not in (seen[0][1].hooks or {})


async def test_the_presubmit_becomes_a_hook_on_the_delivery_call(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])

    class FakePresubmit:
        timeout_s = 300.0

        def __call__(self):
            return None

    await make_runner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        presubmit=FakePresubmit(),
        owner_task="GH-1",
    )
    (matcher,) = seen[0][1].hooks["PreToolUse"]
    assert matcher.matcher == "StructuredOutput" and matcher.timeout > 300.0


class FakeGuard:
    def __init__(self, *reasons):
        self.reasons = list(reasons)
        self.refusals = 0

    def __call__(self, tool_input):
        return self.reasons.pop(0) if self.reasons else None


async def test_the_guard_becomes_a_hook_on_the_delivery_call(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    await make_runner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        guard=FakeGuard(),
        owner_task="GH-1",
    )
    (matcher,) = seen[0][1].hooks["PreToolUse"]
    assert matcher.matcher == "StructuredOutput"


def _callable_marker(value):
    """A stable stand-in for a function object: whether one is wired, and by which
    function, never the object itself -- `options_for` builds a fresh closure on every
    call, so identity across two calls would never match even when nothing changed."""
    return None if value is None else ("present", value.__qualname__)


def _normalised_options(options: ClaudeAgentOptions, fixture_dir: Path) -> dict:
    """The whole `ClaudeAgentOptions` `options_for` returned, as a plain dict with one
    entry per `dataclasses.fields(ClaudeAgentOptions)` -- so a field the SDK adds, or a
    kwarg `options_for` starts passing, shows up here (and has to be named in
    `test_options_for_tech_lead_reviewer_is_pinned` below) rather than being missed.

    Four fields do not compare as themselves, and this is the whole of this pin's
    normalisation, not a loosening of it:
    - `cwd` is rendered relative to `fixture_dir` (the pytest tmp dir): the absolute
      path is different on every run and every machine, so a literal expected value
      could never be written for it.
    - `can_use_tool` and `stderr` are callables `options_for` builds fresh on every
      call (`_deny_writes` returns a new closure each time); `_callable_marker` reduces
      either to "wired, and by which function" -- the only fact that can be pinned.
    - `debug_stderr` is the SDK's own default, a live reference to `sys.stderr` set
      once when `claude_agent_sdk.types` is imported; the comparable fact is only
      whether it is still that object, not its identity value.
    """
    raw = {f.name: getattr(options, f.name) for f in dataclass_fields(ClaudeAgentOptions)}
    raw["cwd"] = None if raw["cwd"] is None else str(Path(raw["cwd"]).relative_to(fixture_dir))
    raw["can_use_tool"] = _callable_marker(raw["can_use_tool"])
    raw["stderr"] = _callable_marker(raw["stderr"])
    raw["debug_stderr"] = raw["debug_stderr"] is sys.stderr
    return raw


def test_options_for_tech_lead_reviewer_is_pinned(tmp_path):
    # Items 1 and 3 of the task's request ask to confirm that no path sets `thinking`
    # (Opus 5.5 cannot turn it off; ADR-0023's own point) or forces `tool_choice`.
    # Neither string exists under `agentic/` today, so an assertion over that absence
    # would be the enforcement artefact ADR-0023 names as one whose tests can never
    # fail -- forbidden by this task's own invariants. This pins the whole object
    # `options_for` returns instead: a field it is found to set that this test does not
    # name is a failure to look at, not an omission (`ClaudeAgentOptions` carries no
    # `tool_choice` field at all in the pinned SDK, so an unnamed key catches that one
    # exactly the same way it would catch a `thinking` key).
    #
    # `TECH_LEAD_REVIEWER` is the widest verifier surface: the only role that exercises
    # `options_for`'s own skill branches (`SKILL_TOOL` appended to `tools` and to
    # `allowed`, `plugins` populated) with `can_use_tool` wired (it does not execute) --
    # exactly where a stray `thinking` or a forced `tool_choice` kwarg would most
    # plausibly land, and a toy role built just for this test would leave unpinned.
    schema = {
        "type": "object",
        "properties": {"markdown": {"type": "string"}},
        "required": ["markdown"],
    }
    options = options_for(
        TECH_LEAD_REVIEWER,
        system_prompt="s",
        model="model-x",  # arbitrary: no test this task adds names a real model
        json_schema=schema,
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
    )

    # Projections of `TECH_LEAD_REVIEWER` itself, not copied literals: an unrelated
    # task that adds one permission, one skill or raises `max_turns` on this role
    # re-pins nothing here, while a new `ClaudeAgentOptions` key still fails below.
    expected_tools = list(TECH_LEAD_REVIEWER.tools)
    if TECH_LEAD_REVIEWER.skills and module.SKILL_TOOL not in expected_tools:
        expected_tools.append(module.SKILL_TOOL)
    expected_allowed = list(TECH_LEAD_REVIEWER.allowed)
    if TECH_LEAD_REVIEWER.skills and expected_allowed and module.SKILL_TOOL not in expected_allowed:
        expected_allowed.append(module.SKILL_TOOL)
    expected_permission_mode = "bypassPermissions" if TECH_LEAD_REVIEWER.executes else "default"
    expected_skills = [f"{module.PLUGIN}:{skill}" for skill in TECH_LEAD_REVIEWER.skills] or None
    expected_plugins = (
        [{"type": "local", "path": str(module.PLUGIN_DIR)}] if TECH_LEAD_REVIEWER.skills else []
    )

    actual = _normalised_options(options, tmp_path)
    expected = {
        "tools": expected_tools,
        "allowed_tools": expected_allowed,
        "system_prompt": "s",
        "mcp_servers": {},
        "strict_mcp_config": True,
        "permission_mode": expected_permission_mode,
        "continue_conversation": False,
        "resume": None,
        "session_id": None,
        "max_turns": TECH_LEAD_REVIEWER.max_turns,
        "max_budget_usd": None,
        "disallowed_tools": list(TECH_LEAD_REVIEWER.denied),
        "model": "model-x",
        "fallback_model": None,
        "betas": [],
        "permission_prompt_tool_name": None,
        "cwd": ".",  # `tmp_path` itself, relative to itself (`_normalised_options`)
        "cli_path": None,
        "settings": None,
        "add_dirs": [],
        "env": {},  # no traceparent, no ambient marker to add (`_child_env`)
        "extra_args": {},
        "max_buffer_size": module.MAX_BUFFER_BYTES,
        "debug_stderr": True,  # still the SDK's own default `sys.stderr`
        "stderr": None,
        "can_use_tool": ("present", "_deny_writes.<locals>.deny"),  # the role does not execute
        "hooks": None,  # no cage (does not execute), no guard, no presubmit, no turn notice
        "user": None,
        "include_partial_messages": False,
        "include_hook_events": False,
        "forward_subagent_text": False,
        # New in claude-agent-sdk 0.2.159 (GH-131, subtask `harness`; bundled CLI
        # rose from 2.1.259 to 2.1.281). Default `False`; `options_for` does not
        # pass it, so it flows through unchanged. Inert for this harness: `False`
        # is one of the four inert shapes the plan names (None, False, {}, or the
        # value `options_for` already passes), and its only documented effect
        # (verbatim vs. rewritten prompt text sent to the model) is off by
        # default -- no role's prompt handling in this repo depends on it.
        "verbatim_prompts": False,
        "fork_session": False,
        "resume_session_at": None,
        "resume_drops_turn": None,
        "agents": None,
        "setting_sources": [],
        "skills": expected_skills,
        "sandbox": None,
        "plugins": expected_plugins,
        "max_thinking_tokens": None,
        "thinking": None,  # item 1 of the task's request: nobody sets this
        "effort": "medium",  # `Role`'s own default; `TECH_LEAD_REVIEWER` does not override it
        "output_format": {"type": "json_schema", "schema": schema},
        "enable_file_checkpointing": False,
        "session_store": None,
        "session_store_flush": "batched",
        "load_timeout_ms": 60000,
        "task_budget": None,
    }
    # The one exhaustiveness gate: `expected` (hand-written) against the SDK's own true
    # field set, never against `actual` -- `_normalised_options` builds `actual`'s keys
    # from that same `dataclasses.fields(ClaudeAgentOptions)` call, so comparing `actual`
    # to it can never fail; it would only restate this line under a different name. A
    # field `options_for` sets that this pin does not list is new; re-pin deliberately.
    all_fields = {f.name for f in dataclass_fields(ClaudeAgentOptions)}
    assert set(expected) == all_fields, (
        "this pin's own expected dict is missing or over-naming a "
        "dataclasses.fields(ClaudeAgentOptions) key -- fix the pin, not the assertion below"
    )
    assert actual == expected, (
        "options_for's ClaudeAgentOptions for TECH_LEAD_REVIEWER changed shape -- "
        "look at the diff between this and `expected` before re-pinning"
    )


def test_options_for_wires_the_guard_ahead_of_the_presubmit(tmp_path):
    class FakePresubmit:
        timeout_s = 1.0

        def __call__(self):
            return "suite red"

    options = options_for(
        PJM,
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
        presubmit=FakePresubmit(),
        guard=FakeGuard("missing `summary`"),
    )
    (matcher,) = options.hooks["PreToolUse"]
    assert matcher.matcher == "StructuredOutput"


# --- GH-282: every attempt is marked, and its processes are reaped when it ends -------


async def test_each_attempt_is_marked_with_its_task_run_and_a_fresh_token(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    reaper = FakeReaper()
    runner = make_runner(reaper=reaper)
    await run_once(runner, tmp_path)
    await run_once(runner, tmp_path)

    marks = [reaping.parse_mark(options.env[reaping.MARK_NAME]) for _, options in seen]
    assert all(m is not None and m.product == PRODUCT_KEY for m in marks)
    assert [(m.task, m.run) for m in marks if m] == [("GH-1", os.getpid())] * 2
    assert marks[0] is not None and marks[1] is not None and marks[0].token != marks[1].token
    # the reap that follows names the same attempt, once each
    assert reaper.calls == [("attempt", "GH-1", os.getpid(), m.token) for m in marks if m]


async def test_the_attempt_is_reaped_on_the_error_path_too(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [])  # no result message: an AttemptFailed
    reaper = FakeReaper()
    with pytest.raises(AttemptFailed):
        await run_once(make_runner(reaper=reaper), tmp_path)
    mark = reaping.parse_mark(seen[0][1].env[reaping.MARK_NAME])
    assert mark is not None
    assert reaper.calls == [("attempt", "GH-1", os.getpid(), mark.token)]


async def test_the_mark_never_reaches_the_shared_environment(monkeypatch, tmp_path):
    # A suite run from inside a marked process (a role's checks) inherits the mark; the test
    # is about what the runner adds, so it starts without one.
    monkeypatch.delenv(reaping.MARK_NAME, raising=False)
    fake_query(monkeypatch, [result()])
    runner = make_runner(env={"GIT_AUTHOR_NAME": "system"})
    await run_once(runner, tmp_path)
    assert reaping.MARK_NAME not in runner.env
    assert reaping.MARK_NAME not in os.environ


def _alive(process: subprocess.Popen) -> bool:
    return process.poll() is None


def _spawner(monkeypatch, children: list, script: list[str]):
    """A `query` that starts a real process, detached from the group, with the very env it
    was handed, then waits forever (the role is still working)."""

    async def query(prompt, options):
        children.append(
            subprocess.Popen(
                script,
                env={**os.environ, **options.env},
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        await anyio.sleep_forever()
        yield  # pragma: no cover

    monkeypatch.setattr(module, "query", query)


async def test_a_cancelled_attempt_still_has_its_processes_reaped(monkeypatch, tmp_path):
    children: list[subprocess.Popen] = []
    _spawner(monkeypatch, children, ["sleep", "300"])
    runner = make_runner(reaper=ShellReaper(PRODUCT_KEY, Path("/nonexistent/worktrees")))
    try:
        with anyio.move_on_after(0.5):
            await run_once(runner, tmp_path)
        deadline = time.monotonic() + reaping.REAP_GRACE_S + reaping.REAP_KILL_WAIT_S + 2
        while _alive(children[0]) and time.monotonic() < deadline:
            await anyio.sleep(0.05)
        assert not _alive(children[0])
    finally:
        for child in children:
            child.kill()
            child.wait()


async def test_the_reap_does_not_block_the_loop_while_it_waits_out_the_grace(monkeypatch, tmp_path):
    children: list[subprocess.Popen] = []
    # Ignores SIGTERM, so the reap has to wait the whole grace before it kills.
    _spawner(monkeypatch, children, ["bash", "-c", 'trap "" TERM; while :; do sleep 1; done'])
    runner = make_runner(reaper=ShellReaper(PRODUCT_KEY, Path("/nonexistent/worktrees")))
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await anyio.sleep(0.05)
            ticks += 1

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(tick)
            with anyio.move_on_after(0.5):
                await run_once(runner, tmp_path)
            before = ticks
            deadline = time.monotonic() + reaping.REAP_GRACE_S + reaping.REAP_KILL_WAIT_S + 2
            while _alive(children[0]) and time.monotonic() < deadline:
                await anyio.sleep(0.05)
            assert not _alive(children[0])
            # the loop kept running during the 5 s of grace the child forced
            assert ticks - before > 20
            tg.cancel_scope.cancel()
    finally:
        for child in children:
            child.kill()
            child.wait()


def _await_argv(pid: int, expected: str, deadline_s: float = 5.0) -> None:
    """Wait until `/proc/<pid>` shows `expected` as its argv: a forked `bash` shows the
    parent's argv until it execs, so reading it straight after spawning races the exec."""
    deadline = time.monotonic() + deadline_s
    last = "gone"
    while True:
        try:
            last = reaping.truncate_command(Path(f"/proc/{pid}/cmdline").read_bytes())
        except OSError:
            last = "gone"
        if last == expected:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"pid {pid} never showed argv {expected!r}; last seen: {last!r}")
        time.sleep(0.02)


def _kill_quietly(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_the_reaper_takes_the_escaped_grandchild_of_a_real_attempt_env_and_spares_a_sibling():
    token = "regression"
    mark = reaping.mark(PRODUCT_KEY, "GH-1", os.getpid(), token)
    child_env = module._child_env({**os.environ}, PJM, None, None, mark)
    sibling_env = {k: v for k, v in child_env.items() if k != reaping.MARK_NAME}
    spawned = subprocess.run(
        ["bash", "-c", "setsid nohup sleep 300 >/dev/null 2>&1 & echo $!"],
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
    )
    grandchild = int(spawned.stdout.strip())
    sibling = subprocess.Popen(["sleep", "301"], env=sibling_env, start_new_session=True)
    try:
        _await_argv(grandchild, "sleep 300")
        reaped = ShellReaper(PRODUCT_KEY, Path("/nonexistent/worktrees")).reap_attempt(
            "GH-1", os.getpid(), token
        )
        assert [p["command"] for p in reaped] == ["sleep 300"]
        assert reaped[0]["pid"] == grandchild
        assert reaped[0]["outcome"] in ("term", "kill")
        assert _alive(sibling)  # the same env minus the mark is not the role's
    finally:
        _kill_quietly(grandchild)
        sibling.kill()
        sibling.wait()


def test_the_argv_wait_fails_with_the_last_argv_until_the_exec_and_returns_after_it(tmp_path):
    gate = tmp_path / "gate"
    mark = reaping.mark(PRODUCT_KEY, "GH-1", os.getpid(), "gate")
    env = {**module._child_env({**os.environ}, PJM, None, None, mark), "GATE": str(gate)}
    process = subprocess.Popen(
        ["bash", "-c", 'until [ -e "$GATE" ]; do sleep 0.05; done; exec setsid nohup sleep 300'],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(AssertionError, match="bash -c"):
            _await_argv(process.pid, "sleep 300", deadline_s=0.3)
        gate.touch()
        _await_argv(process.pid, "sleep 300")
    finally:
        _kill_quietly(process.pid)
        process.wait()


async def test_what_the_reaper_stopped_comes_back_on_the_result_and_on_the_failure(
    monkeypatch, tmp_path
):
    leak: tuple[reaping.ReapedProcess, ...] = (
        {"pid": 7, "command": "sleep 300", "outcome": "term"},
    )
    fake_query(monkeypatch, [result()])
    raw = await run_once(make_runner(reaper=FakeReaper(reaped_attempts=leak)), tmp_path)
    assert raw.reaped == list(leak)
    fake_query(monkeypatch, [])
    with pytest.raises(AttemptFailed) as info:
        await run_once(make_runner(reaper=FakeReaper(reaped_attempts=leak)), tmp_path)
    assert info.value.reaped == list(leak)
    fake_query(monkeypatch, [result()])
    assert (await run_once(make_runner(), tmp_path)).reaped == []


# --- GH-299: a hung attempt is detected, reaped by its mark and failed as a stall ----

TIMEOUT_S = 600.0


class FakeClock:
    """The runner's monotonic clock, moved by the test and by nothing else."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class HungReaper(FakeReaper):
    """A reaper whose `reap_attempt` runs `on_reap` (in the reap's thread) and whose
    `child_activity` is whatever `ticks` says; `survivors` is what the reap leaves."""

    def __init__(self, on_reap=lambda: None, ticks=None, survivors=(), running=False):
        super().__init__(reaped_attempts=survivors)
        self.on_reap = on_reap
        self.ticks = ticks or (lambda scan: 0)
        self.running = running

    def reap_attempt(self, task_id, run, token):
        self.on_reap()
        return super().reap_attempt(task_id, run, token)

    def child_activity(self, task_id, run, token, parent_pid):
        super().child_activity(task_id, run, token, parent_pid)
        return module.ChildActivity(cpu_ticks=self.ticks(self.scans), running=self.running)


SURVIVOR = (reaping.ReapedProcess(pid=7, command="sleep", outcome="survived"),)
INIT = SystemMessage(subtype="init", data={"session_id": "sess-init"})


def tool_call(tool_id: str, parent: str | None = None) -> AssistantMessage:
    block = ToolUseBlock(id=tool_id, name="Bash", input={})
    return AssistantMessage(content=[block], model="m", parent_tool_use_id=parent)


def tool_result(tool_id: str) -> UserMessage:
    return UserMessage(content=[ToolResultBlock(tool_use_id=tool_id, content="ok")])


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    monkeypatch.setattr(module, "CHECK_EVERY_S", 0.01)
    return FakeClock()


def stalling_runner(clock, reaper, buffer=None, **kwargs) -> ClaudeSdkRunner:
    return make_runner(
        clock=clock, stall_timeout_s=TIMEOUT_S, reaper=reaper, stream=buffer, **kwargs
    )


async def until(condition, seconds: float = 5.0) -> None:
    with anyio.fail_after(seconds):
        while not condition():
            await anyio.sleep(0.005)


def silent_after(clock, messages, killed: threading.Event, *, before_silence=None):
    """A query that yields `messages`, lets the fake clock run past the threshold, then
    stays silent until `killed` is set -- the CLI dying under the reap -- and ends (EOF)."""

    async def query(prompt, options):
        for message in messages:
            yield message
        if before_silence is not None:
            await before_silence(options)
        while not killed.is_set():
            clock.now += 5 * TIMEOUT_S  # the silence goes on, whatever the first scan saw
            await anyio.sleep(0.005)

    return query


async def test_a_silent_attempt_is_reaped_and_fails_as_a_stall_on_the_eof_path(
    monkeypatch, tmp_path, clock
):
    killed = threading.Event()
    reaper = HungReaper(on_reap=killed.set)
    monkeypatch.setattr(module, "query", silent_after(clock, [INIT], killed))
    buffer = io.StringIO()
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, reaper, buffer), tmp_path)
    failure = caught.value
    assert failure.reason == "stall" and failure.session_id == "sess-init"
    assert failure.cost_usd == 0.0
    assert [call[0] for call in reaper.calls][:1] == ["attempt"]
    written = buffer.getvalue()
    assert "interrupted" not in written
    assert written.count("stalled") == 1
    assert "stalled 3000s, last event system" in written
    assert "cost unknown" in written


async def test_the_attempt_is_reaped_before_any_scope_is_cancelled_when_the_cli_survives(
    monkeypatch, tmp_path, clock
):
    order: list[str] = []
    reaper = HungReaper(on_reap=lambda: order.append("reap"), survivors=SURVIVOR)

    async def query(prompt, options):
        yield INIT
        clock.now += 5 * TIMEOUT_S
        try:
            await anyio.sleep_forever()  # the CLI that ignores the reap
        finally:
            order.append("cancelled")

    monkeypatch.setattr(module, "query", query)
    buffer = io.StringIO()
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, reaper, buffer), tmp_path)
    assert caught.value.reason == "stall" and caught.value.session_id == "sess-init"
    assert order[:2] == ["reap", "cancelled"]
    assert "interrupted" not in buffer.getvalue()
    assert "stalled" in buffer.getvalue()


async def test_a_stall_before_the_init_message_leaves_no_session(monkeypatch, tmp_path, clock):
    killed = threading.Event()
    monkeypatch.setattr(module, "query", silent_after(clock, [], killed))
    buffer = io.StringIO()
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, HungReaper(on_reap=killed.set), buffer), tmp_path)
    assert caught.value.session_id == ""
    assert "last event none" in buffer.getvalue()


async def test_a_stall_surfacing_as_an_sdk_error_is_still_the_stall(monkeypatch, tmp_path, clock):
    killed = threading.Event()

    async def query(prompt, options):
        yield INIT
        clock.now += 5 * TIMEOUT_S
        while not killed.is_set():
            await anyio.sleep(0.005)
        raise RuntimeError("Command failed with exit code -15")  # the SDK noticing the kill

    monkeypatch.setattr(module, "query", query)
    buffer = io.StringIO()
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, HungReaper(on_reap=killed.set), buffer), tmp_path)
    assert caught.value.reason == "stall"
    assert "exit code" not in buffer.getvalue()  # the log never copies a message


async def test_a_marked_child_burning_cpu_does_not_hold_an_attempt_with_no_pending_tool(
    monkeypatch, tmp_path, clock
):
    killed = threading.Event()
    reaper = HungReaper(on_reap=killed.set, ticks=lambda scan: scan * 100, running=True)
    monkeypatch.setattr(module, "query", silent_after(clock, [INIT], killed))
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, reaper), tmp_path)
    assert caught.value.reason == "stall"


async def _run_with_silence(monkeypatch, tmp_path, clock, reaper, messages, steps=4):
    """Runs an attempt that sends `messages`, then stays silent for `steps` periods of
    most of the threshold, then delivers."""

    async def query(prompt, options):
        for message in messages:
            yield message
        for _ in range(steps):
            clock.now += TIMEOUT_S - 100
            await anyio.sleep(0.1)
        yield result()

    monkeypatch.setattr(module, "query", query)
    return await run_once(stalling_runner(clock, reaper), tmp_path)


async def test_a_pending_tool_is_held_by_a_child_burning_cpu_or_running(
    monkeypatch, tmp_path, clock
):
    burning = HungReaper(ticks=lambda scan: scan * 100)
    raw = await _run_with_silence(monkeypatch, tmp_path, clock, burning, [INIT, tool_call("t1")])
    assert raw.session_id == "sess-123"
    running = HungReaper(running=True)
    raw = await _run_with_silence(monkeypatch, tmp_path, clock, running, [INIT, tool_call("t1")])
    assert raw.session_id == "sess-123"


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param([INIT, tool_call("t1")], id="idle child"),
        pytest.param([INIT, tool_call("t1"), tool_result("t1")], id="tool already returned"),
        pytest.param([INIT, tool_call("t1", parent="sub-1")], id="sub-agent's tool"),
    ],
)
async def test_an_idle_child_or_no_pending_main_thread_tool_does_not_hold_the_attempt(
    monkeypatch, tmp_path, clock, messages
):
    killed = threading.Event()
    # The child burns CPU in the last two cases; only a pending main-thread tool counts it.
    idle = messages == [INIT, tool_call("t1")]
    reaper = HungReaper(
        on_reap=killed.set, ticks=(lambda scan: 5) if idle else (lambda scan: scan * 100)
    )
    monkeypatch.setattr(module, "query", silent_after(clock, messages, killed))
    with pytest.raises(AttemptFailed) as caught:
        await run_once(stalling_runner(clock, reaper), tmp_path)
    assert caught.value.reason == "stall"


class SlowPresubmit:
    timeout_s = 1500.0

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()

    def __call__(self):
        self.entered.set()
        self.release.wait(10)
        return None


async def test_a_long_delivery_hook_holds_the_attempt_and_the_whole_threshold_restarts_after_it(
    monkeypatch, tmp_path, clock
):
    presubmit = SlowPresubmit()
    killed = threading.Event()
    reaper = HungReaper(on_reap=killed.set)
    reaped_at: list[int] = []

    async def query(prompt, options):
        yield INIT
        hook = options.hooks["PreToolUse"][0].hooks[0]
        running = asyncio.ensure_future(hook({"tool_input": {}}, None, {"signal": None}))
        await until(lambda: reaper.scans >= 1)
        clock.now += 3 * 1500  # a hook thrice the check timeout, with no message
        scans = reaper.scans
        await until(lambda: reaper.scans >= scans + 3)
        reaped_at.append(len(reaper.calls))  # during the hook: no reap
        presubmit.release.set()
        await running
        clock.now += TIMEOUT_S - 1  # a whole threshold after it, minus a second
        scans = reaper.scans
        await until(lambda: reaper.scans >= scans + 3)
        reaped_at.append(len(reaper.calls))  # still no reap
        clock.now += 2
        while not killed.is_set():
            await anyio.sleep(0.005)

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as caught:
        await stalling_runner(clock, reaper).run_once(
            PJM,
            "p",
            system_prompt="s",
            model="m",
            json_schema={},
            cwd=tmp_path,
            presubmit=presubmit,
            owner_task="GH-1",
        )
    assert caught.value.reason == "stall"
    assert reaped_at == [0, 0]


async def test_the_hook_counter_is_per_attempt_not_shared_between_sessions(
    monkeypatch, tmp_path, clock
):
    # Two attempts at once: one holds a hook open, the other is silent. Only the silent
    # one stalls; a counter shared between sessions would hold both.
    presubmit = SlowPresubmit()
    killed = threading.Event()
    reaper_quiet, reaper_held = HungReaper(on_reap=killed.set), HungReaper()

    async def query(prompt, options):
        yield INIT
        if prompt == "held":
            hook = options.hooks["PreToolUse"][0].hooks[0]
            running = asyncio.ensure_future(hook({"tool_input": {}}, None, {"signal": None}))
            await until(lambda: killed.is_set())
            presubmit.release.set()
            await running
            yield result()
        else:
            await until(presubmit.entered.is_set)  # the other attempt's hook is in flight
            clock.now += 5 * TIMEOUT_S
            while not killed.is_set():
                await anyio.sleep(0.005)

    monkeypatch.setattr(module, "query", query)
    outcomes: dict[str, object] = {}

    async def attempt(prompt: str, reaper: HungReaper) -> None:
        runner = stalling_runner(clock, reaper)
        try:
            outcomes[prompt] = await runner.run_once(
                PJM,
                prompt,
                system_prompt="s",
                model="m",
                json_schema={},
                cwd=tmp_path,
                presubmit=presubmit if prompt == "held" else None,
                owner_task="GH-1",
            )
        except AttemptFailed as exc:
            outcomes[prompt] = exc

    async with anyio.create_task_group() as group:
        group.start_soon(attempt, "held", reaper_held)
        group.start_soon(attempt, "quiet", reaper_quiet)
    quiet = outcomes["quiet"]
    assert isinstance(quiet, AttemptFailed) and quiet.reason == "stall"
    assert not isinstance(outcomes["held"], AttemptFailed)
    assert reaper_held.calls == [call for call in reaper_held.calls if call[0] == "attempt"]
    assert len(reaper_held.calls) == 1  # only run_once's own reap, never the watchdog's


async def test_an_external_cancellation_stays_an_interruption_with_the_watchdog_running(
    monkeypatch, tmp_path, clock
):
    async def query(prompt, options):
        yield INIT
        await anyio.sleep_forever()

    monkeypatch.setattr(module, "query", query)
    buffer = io.StringIO()
    reaper = HungReaper()
    with anyio.CancelScope() as outside:

        async def cancel_soon():
            await anyio.sleep(0.1)
            outside.cancel()

        async with anyio.create_task_group() as group:
            group.start_soon(cancel_soon)
            await run_once(stalling_runner(clock, reaper, buffer), tmp_path)
    assert outside.cancel_called
    assert buffer.getvalue().count("interrupted") == 1
    assert "stalled" not in buffer.getvalue()


async def test_a_default_runner_never_stalls_an_attempt_that_keeps_talking(monkeypatch, tmp_path):
    fake_query(monkeypatch, [INIT, tool_call("t1"), tool_result("t1"), result()])
    raw = await run_once(make_runner(), tmp_path)
    assert raw.session_id == "sess-123"
