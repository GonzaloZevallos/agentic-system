"""The SDK adapter with `query` replaced. No model involved."""

import io
from dataclasses import replace

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    ToolPermissionContext,
    ToolUseBlock,
)

from agentic.core.process.adapters.claude_sdk import runner as module
from agentic.core.process.adapters.claude_sdk.runner import ClaudeSdkRunner, options_for
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import DEVELOPER, PJM
from agentic.shared.environment import RESOURCE_ROLE_SLUG_KEY, RESOURCE_TRACE_ID_KEY

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
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
    # Normalised to the closed `kind` set `agentic.role.tokens` groups by, off the SDK's
    # own snake_case keys.
    assert raw.usage == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}
    assert raw.calls == 2  # one per AssistantMessage seen before the delivery
    assert raw.tool_calls == {"Read": 2, "Bash": 1, "other": 1, "StructuredOutput": 1}


async def test_raw_result_usage_defaults_when_the_sdk_reports_none(monkeypatch, tmp_path):
    fake_query(monkeypatch, [result(usage=None)])
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
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
    raw = await ClaudeSdkRunner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={"type": "object"},
        cwd=tmp_path,
        resume="session-opened-by-an-earlier-node",
    )
    assert raw.usage == {"input": 2, "output": 4, "cache_read": 1862, "cache_write": 63}


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
    with pytest.raises(AttemptFailed, match="without a result") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.reason == "sdk"

    fake_query(monkeypatch, [result(subtype="error_max_turns", total_cost_usd=0.5)])
    with pytest.raises(AttemptFailed, match="max_turns \\(2\\) exhausted") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.cost_usd == 0.5
    assert info.value.reason == "max_turns"

    fake_query(monkeypatch, [result(is_error=True, subtype="error", errors=["boom"])])
    with pytest.raises(AttemptFailed, match="boom") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.reason == "sdk"


async def test_a_failed_attempt_keeps_the_session_it_left_behind(monkeypatch, tmp_path):
    # The money cap cuts the run: the work is done, the session is alive, and a retry
    # that resumes it delivers instead of reading everything again.
    cut = result(is_error=True, subtype="error_max_budget_usd", total_cost_usd=3.05)
    fake_query(monkeypatch, [cut])
    with pytest.raises(AttemptFailed, match="error_max_budget_usd") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert (info.value.cost_usd, info.value.session_id) == (3.05, "sess-123")
    # A per-attempt budget cap is `limit` (issue #66), not a generic `sdk` failure.
    assert info.value.reason == "limit"

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
    with pytest.raises(AttemptFailed, match="SDK error: socket closed") as info:
        await run_once(ClaudeSdkRunner(), tmp_path)
    assert info.value.reason == "sdk"


async def test_an_sdk_exception_with_no_message_names_its_type_instead_of_nothing(
    monkeypatch, tmp_path
):
    async def query(prompt, options):
        raise RuntimeError
        yield  # pragma: no cover

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed, match="SDK error: RuntimeError, with no message"):
        await run_once(ClaudeSdkRunner(), tmp_path)


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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
    await ClaudeSdkRunner(env=identity, telemetry=_FakeTelemetry(traceparent)).run_once(
        PJM, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    attrs = seen[0][1].env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "deployment.environment=prod" in attrs  # the real ambient entry survives
    assert f"{RESOURCE_ROLE_SLUG_KEY}={PJM.slug}" in attrs
    assert f"{RESOURCE_TRACE_ID_KEY}=4bf92f3577b34da6a3ce929d0e0e4736" in attrs


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


async def test_a_non_executing_role_gets_a_can_use_tool_denial_callback(monkeypatch, tmp_path):
    # A role that does not execute runs in `permission_mode="default"`: anything its
    # `allowed` rules do not cover reaches the SDK's permission layer, which is where
    # the role's own rule (`denial_for`) has to land instead of the SDK's generic text.
    seen = fake_query(monkeypatch, [result()])
    reader = replace(PJM, tools=("Read",), allowed=("Read",))
    await ClaudeSdkRunner().run_once(
        reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    assert seen[0][1].can_use_tool is not None


async def test_an_executing_role_gets_no_can_use_tool_callback(monkeypatch, tmp_path):
    # `bypassPermissions` already shadows `can_use_tool` entirely (the SDK's own
    # warning); wiring one anyway would be dead code pretending to cage a role the
    # hooks cage already covers (ADR-0011, addendum).
    seen = fake_query(monkeypatch, [result()])
    developer = replace(PJM, tools=("Bash",), executes=True)
    await ClaudeSdkRunner().run_once(
        developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
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
    raw = await ClaudeSdkRunner().run_once(
        reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
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
        await ClaudeSdkRunner().run_once(
            reader, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
        )
    assert info.value.denials == 1


async def test_a_fired_turn_notice_is_carried_on_the_raw_result(monkeypatch, tmp_path):
    # `max_turns=40`, margin 39: the very first `PostToolUse` call already crosses it.
    developer = replace(DEVELOPER, max_turns=40)

    async def query(prompt, options):
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await ClaudeSdkRunner().run_once(
        developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    assert raw.turn_notice is True


async def test_no_turn_notice_when_the_margin_is_never_crossed(monkeypatch, tmp_path):
    developer = replace(DEVELOPER, max_turns=200)

    async def query(prompt, options):
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result()

    monkeypatch.setattr(module, "query", query)
    raw = await ClaudeSdkRunner().run_once(
        developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
    )
    assert raw.turn_notice is False


async def test_a_fired_turn_notice_during_a_failed_attempt_still_reaches_attemptfailed(
    monkeypatch, tmp_path
):
    developer = replace(DEVELOPER, max_turns=40)

    async def query(prompt, options):
        (matcher,) = options.hooks["PostToolUse"]
        (hook,) = matcher.hooks
        await hook({"tool_name": "Bash"}, "t1", {"signal": None})
        yield result(subtype="error_max_turns", total_cost_usd=0.1)

    monkeypatch.setattr(module, "query", query)
    with pytest.raises(AttemptFailed) as info:
        await ClaudeSdkRunner().run_once(
            developer, "p", system_prompt="s", model="m", json_schema={}, cwd=tmp_path
        )
    assert info.value.turn_notice is True


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
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
    assert raw.deliveries == 2


async def test_raw_result_deliveries_default_when_the_tool_was_never_called(monkeypatch, tmp_path):
    fake_query(monkeypatch, [result()])
    raw = await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
        await run_once(ClaudeSdkRunner(), tmp_path)
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
    raw = await ClaudeSdkRunner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        presubmit=FakePresubmit(),
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
        await ClaudeSdkRunner().run_once(
            PJM,
            "p",
            system_prompt="s",
            model="m",
            json_schema={},
            cwd=tmp_path,
            presubmit=FakePresubmit(),
        )
    assert info.value.presubmit_denials == 1


async def test_a_role_with_no_margin_never_gets_the_turn_notice_hook_wired(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])
    await run_once(ClaudeSdkRunner(), tmp_path)
    assert "PostToolUse" not in (seen[0][1].hooks or {})


async def test_the_presubmit_becomes_a_hook_on_the_delivery_call(monkeypatch, tmp_path):
    seen = fake_query(monkeypatch, [result()])

    class FakePresubmit:
        timeout_s = 300.0

        def __call__(self):
            return None

    await ClaudeSdkRunner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        presubmit=FakePresubmit(),
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
    await ClaudeSdkRunner().run_once(
        PJM,
        "p",
        system_prompt="s",
        model="m",
        json_schema={},
        cwd=tmp_path,
        guard=FakeGuard(),
    )
    (matcher,) = seen[0][1].hooks["PreToolUse"]
    assert matcher.matcher == "StructuredOutput"


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
