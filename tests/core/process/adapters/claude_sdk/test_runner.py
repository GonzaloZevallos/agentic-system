"""The SDK adapter with `query` replaced. No model involved."""

import io
from dataclasses import replace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock

from agentic.core.process.adapters.claude_sdk import runner as module
from agentic.core.process.adapters.claude_sdk.runner import ClaudeSdkRunner
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain.roles import PJM

pytestmark = pytest.mark.anyio

DELIVERY = {"markdown": "ok", "type": "feature", "confidence": "high", "reason": "r"}


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
        PJM, "p", system_prompt="s", model="model-x", json_schema={"type": "object"}, cwd=tmp_path
    )


async def test_raw_result_carries_session_cost_and_turns(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
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
    await run_once(ClaudeSdkRunner(), tmp_path)
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
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
    assert raw.cost_usd == 0.75
    assert raw.cost_by_model == {"claude-opus-5": 0.5, "claude-sonnet-5": 0.25}
    fake_query(monkeypatch, [result(model_usage=None)])
    assert (await run_once(ClaudeSdkRunner(), tmp_path)).cost_by_model == {}


async def test_nested_schemas_are_inlined_for_the_sdk(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    schema = {
        "$defs": {"Item": {"type": "object", "properties": {"a": {"type": "string"}}}},
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
    }
    await ClaudeSdkRunner().run_once(
        PJM, "p", system_prompt="s", model="m", json_schema=schema, cwd=tmp_path
    )
    sent = seen[0][1].output_format["schema"]
    assert "$defs" not in sent and "$ref" not in str(sent)
    assert sent["properties"]["items"]["items"]["properties"]["a"] == {"type": "string"}
    assert "$defs" in schema  # the caller's schema is not mutated


async def test_missing_result_and_error_results_are_attempt_failures(monkeypatch, tmp_path):
    fake_query(monkeypatch, [])
    with pytest.raises(AttemptFailed, match="without a result"):
        await run_once(ClaudeSdkRunner(), tmp_path)

    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed, match="max_turns \\(2\\) exhausted") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.cost_usd == 0.5

    fake_query(monkeypatch, [result(is_error=True, subtype="error", errors=["boom"])])
    with pytest.raises(AttemptFailed, match="boom"):
        await run_once(ClaudeSdkRunner(), tmp_path)


async def test_a_failed_attempt_keeps_the_session_it_left_behind(monkeypatch, tmp_path):
    # The money cap cuts the run: the work is done, the session is alive, and a retry
    # that resumes it delivers instead of reading everything again.
    cut = result(is_error=True, subtype="error_max_budget_usd", total_cost_usd=3.05)
    fake_query(monkeypatch, [cut])
    with pytest.raises(AttemptFailed, match="error_max_budget_usd") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert (info.value.cost_usd, info.value.session_id) == (3.05, "sess-123")

    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed) as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.session_id == "sess-123"

    fake_query(monkeypatch, [])  # no result at all: nothing to resume
    with pytest.raises(AttemptFailed) as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
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
    raw = await run_once(ClaudeSdkRunner(verbose=True, stream=buffer), tmp_path)
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
    empty = result(structured_output=None, num_turns=1)
    fake_query(monkeypatch, [empty, result(num_turns=2)])
    buffer = io.StringIO()
    raw = await run_once(ClaudeSdkRunner(stream=buffer), tmp_path)
    assert raw.structured_output == DELIVERY and raw.turns == 2
    assert buffer.getvalue() == ""

    fake_query(monkeypatch, [empty])
    raw = await run_once(ClaudeSdkRunner(stream=buffer), tmp_path)
    assert raw.structured_output is None  # the use case's failure, not the runner's


async def test_sdk_exceptions_are_attempt_failures(monkeypatch, tmp_path):
    async def query(prompt, options):
        raise RuntimeError("socket closed")
        yield  # pragma: no cover

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed, match="SDK error: socket closed"):
        await run_once(ClaudeSdkRunner(), tmp_path)


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
    raw = await run_once(ClaudeSdkRunner(verbose=True, stream=buffer), tmp_path)
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
    await ClaudeSdkRunner().run_once(
        developer,
        "p",
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        max_budget_usd=3.0,
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
    await ClaudeSdkRunner().run_once(
        reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, resume="sess-1"
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
    await ClaudeSdkRunner(env=identity).run_once(
        developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    options = seen[0][1]
    # The SDK merges `env` over the inherited environment, so every `git` the role
    # runs commits as the system, whatever `user.*` the product's config carries.
    assert options.env == identity
    # Deny rules hold even in `bypassPermissions`.
    assert options.disallowed_tools == ["Bash(git config:*)"]


async def test_a_role_that_executes_runs_inside_the_hooks_cage(monkeypatch, tmp_path):
    # Deny rules match a prefix; the hooks see the parsed call (ADR-0011, addendum).
    seen = fake_query(monkeypatch, [result()])
    developer = replace(PJM, tools=("Bash",), executes=True)
    await ClaudeSdkRunner().run_once(
        developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    matchers = seen[0][1].hooks["PreToolUse"]
    assert {m.matcher for m in matchers} == {"Bash", "Edit|Write"}
    seen = fake_query(monkeypatch, [result()])
    await run_once(ClaudeSdkRunner(), tmp_path)
    assert seen[0][1].hooks is None


async def test_the_delivery_gate_becomes_a_hook_on_the_delivery_call(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])

    class Gate:
        timeout_s = 300.0

        def __call__(self):
            return None

    await ClaudeSdkRunner().run_once(
        PJM, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path, gate=Gate()
    )
    (matcher,) = seen[0][1].hooks["PreToolUse"]
    assert matcher.matcher == "StructuredOutput" and matcher.timeout > 300.0
