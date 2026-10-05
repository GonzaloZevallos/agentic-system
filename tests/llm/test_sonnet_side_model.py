"""Gate (subtask `sonnet-side`, ADR-0042): does the pinned SDK and CLI accept
`claude-sonnet-5-5` on both paths `Configuration.actor_model` and
`Configuration.subagent_model` reach the CLI through, and bill a call on it?
Excluded from CI: `uv run pytest -m llm`.

Green: the decision run proved both paths accept the candidate (see the Developer's
section and ADR-0042's `## Gate result`), so both defaults moved in this same diff and
this file now resolves the model off `Configuration.model_fields[...].default` --
never a literal, and never the environment (`from_sources` reads
`AGENTIC_ACTOR_MODEL`/`AGENTIC_SUBAGENT_MODEL` ahead of the default, so a machine where
someone pulled the rollback lever would prove a model that is not shipping, silently).

Same shape as `test_verifier_model.py`, not a refactor of it. Toy roles are built here,
never added to `agentic/core/process/domain/roles.py` (invariant: no subtask adds a
`Role` to the domain, not even for a test), with `schemas.RoleOutput` itself as their
schema -- the one-field `markdown` shape, never a real actor's.

Options go through `options_for`, the same function production calls. Bounded:
`max_budget_usd=1.0`, `max_turns=4`, capped at two attempts per case (`RETRY_BUDGET`),
the spend outside every cap and every series this repo's telemetry knows about --
pasted verbatim into the Developer's section and the ADR with its measured cost.

On a rejection, `_reported_result`/`_reported_error` (the adapter's own) build the
`ReportedFailure` `rules.failure_reason` classifies. Only `sdk` or `limit` mean the
identifier itself was rejected; `account_limit`, `upstream`, `network` and `schema` get
one retry before being read the same way, since they are not evidence about the
identifier.

Two functions, because the two defaults reach the CLI by different paths:

- The actor path: `actor_model` goes in as `options.model`, parametrized over
  `sorted({r.effort for r in ROLES if r.kind == "actor"})` -- derived from the domain,
  never typed by hand.
- The sub-agent path: `subagent_model` goes in through `Configuration(...).role_env` as
  `CLAUDE_CODE_SUBAGENT_MODEL` plus `_FORCE=1`, built directly, never through
  `from_sources` (no file spells either variable by hand). The parent runs on
  `Configuration.model_fields["verifier_model"].default`, never on the candidate, and
  must have `Agent` in both `tools` and `allowed` -- otherwise `_deny_writes`
  (`runner.py`) refuses the call before it ever reaches a sub-agent. Before reading
  `model_usage`, the test asserts a main-thread `Agent` `ToolUseBlock` and zero denials
  (counted through `on_denied`): either assertion failing says the sub-agent never ran,
  which is a broken test, not a red result about the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultError,
    ResultMessage,
    ToolUseBlock,
    query,
)

from agentic.core.process.adapters.claude_sdk.runner import (
    _reported_error,
    _reported_result,
    options_for,
)
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import ROLES, Role
from agentic.core.process.domain.rules import ReportedFailure, failure_reason
from agentic.shared.configuration import Configuration

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

TOY_SCHEMA = schemas.json_schema(schemas.RoleOutput, {})

# At most two attempts per case, spelled out in the module docstring.
RETRY_BUDGET = 2

# Facts not about the identifier: retried once before being read as a rejection.
_RETRIABLE = ("account_limit", "upstream", "network", "schema")

DELIVER_PROMPT = (
    "Call the `StructuredOutput` tool exactly once, with a single top-level JSON key "
    "`markdown` whose value is the short string '### Changes\\nProbe, nothing built.'. "
    "Then stop."
)


def _classify(result: ResultMessage | None, exc: Exception | None) -> str:
    report = (
        _reported_error(exc)
        if isinstance(exc, ResultError)
        else _reported_result(result)
        if result is not None
        else ReportedFailure()
    )
    return failure_reason(report)


# --- actor path ---------------------------------------------------------------------

# `sorted({r.effort for r in ROLES if r.kind == "actor"})`: derived from the domain,
# never typed by hand. Today `high`, `low`, `medium`.
ACTOR_EFFORTS = sorted({r.effort for r in ROLES if r.kind == "actor"})


def _toy_actor_role(effort: str) -> Role:
    return Role(
        name="Toy Actor",
        slug=f"toy_actor_model_spike_{effort}",
        kind="actor",
        schema=schemas.RoleOutput,
        executes=False,
        effort=effort,
        max_turns=4,
    )


def _actor_options(tmp_path: Path, role: Role, model: str) -> ClaudeAgentOptions:
    return options_for(
        role,
        system_prompt="You are a probe for a harness gate. Follow the user's "
        "instructions on how to call `StructuredOutput` exactly.",
        model=model,
        json_schema=TOY_SCHEMA,
        cwd=tmp_path,
        resume=None,
        max_budget_usd=1.0,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
    )


async def _actor_attempt(
    tmp_path: Path, role: Role, model: str
) -> tuple[ResultMessage | None, Exception | None]:
    options = _actor_options(tmp_path, role, model)
    result: ResultMessage | None = None
    try:
        async for message in query(prompt=DELIVER_PROMPT, options=options):
            if isinstance(message, ResultMessage):
                result = message
                if not message.is_error and message.structured_output is not None:
                    break
    except ResultError as exc:
        return None, exc
    except Exception as exc:  # the SDK raises its own hierarchy; production catches all of it
        return None, exc
    return result, None


@pytest.mark.parametrize("effort", ACTOR_EFFORTS)
async def test_sonnet_5_5_is_accepted_as_actor_model(effort: str, tmp_path: Path) -> None:
    """The actor-path gate, one case per `effort` a `kind="actor"` role runs at: does
    the shipping `actor_model` default get accepted as `options.model` and bill a
    call?"""
    model = Configuration.model_fields["actor_model"].default
    role = _toy_actor_role(effort)
    print(f"effort={effort!r} resolved actor_model default: {model}")

    result: ResultMessage | None = None
    exc: Exception | None = None
    reason: str | None = None
    for attempt in range(1, RETRY_BUDGET + 1):
        result, exc = await _actor_attempt(tmp_path, role, model)
        if result is not None:
            print(
                f"effort={effort!r} attempt {attempt}: ResultMessage.is_error="
                f"{result.is_error} total_cost_usd={result.total_cost_usd} "
                f"model_usage={result.model_usage}"
            )
        else:
            print(f"effort={effort!r} attempt {attempt}: no ResultMessage, exception={exc!r}")
        if result is not None and not result.is_error and result.total_cost_usd:
            print(f"effort={effort!r} attempt {attempt}: green, cost {result.total_cost_usd}")
            break
        reason = _classify(result, exc)
        print(f"effort={effort!r} attempt {attempt}: classified {reason!r}")
        if reason not in _RETRIABLE or attempt == RETRY_BUDGET:
            break

    assert result is not None, (
        f"effort={effort!r}: no ResultMessage on any attempt (last classified: {reason!r})"
    )
    assert not result.is_error, f"effort={effort!r}: run failed, classified {reason!r}"
    assert result.total_cost_usd is not None and result.total_cost_usd > 0


# --- sub-agent path ------------------------------------------------------------------

# The parent never runs on the candidate: `verifier_model`'s shipping default
# (`claude-opus-5-5`), so a green result is evidence about the sub-agent's model and
# never about the parent's.
PARENT_MODEL = Configuration.model_fields["verifier_model"].default
SUBAGENT_MODEL = Configuration.model_fields["subagent_model"].default
assert PARENT_MODEL != SUBAGENT_MODEL, "the parent model must differ from the candidate"

PARENT_ROLE = Role(
    name="Toy Parent",
    slug="toy_subagent_model_spike_parent",
    kind="actor",
    schema=schemas.RoleOutput,
    executes=False,
    # `Agent` in both, or `_deny_writes` (runner.py) refuses the call before a
    # sub-agent is ever launched (module docstring).
    tools=("Agent",),
    allowed=("Agent",),
    max_turns=4,
)

SUBAGENT_PROMPT = (
    "Use the Agent tool exactly once: launch a general-purpose sub-agent with the "
    "prompt 'Reply in plain text with exactly the word: done. Call no tools.' Wait "
    "for it to finish, then call the `StructuredOutput` tool exactly once, with a "
    "single top-level JSON key `markdown` whose value is the short string "
    "'### Changes\\nProbe, nothing built.'. Then stop."
)


async def _subagent_attempt(
    tmp_path: Path, env: dict[str, str]
) -> tuple[ResultMessage | None, Exception | None, bool, int]:
    denials = 0

    def _count_denied() -> None:
        nonlocal denials
        denials += 1

    options = options_for(
        PARENT_ROLE,
        system_prompt="You are a probe for a harness gate. Follow the user's "
        "instructions on how to use the Agent tool and call `StructuredOutput` exactly.",
        model=PARENT_MODEL,
        json_schema=TOY_SCHEMA,
        cwd=tmp_path,
        resume=None,
        max_budget_usd=1.0,
        traceparent=None,
        partial=False,
        env=env,
        resource_attributes=None,
        on_denied=_count_denied,
    )

    agent_seen = False
    result: ResultMessage | None = None
    try:
        async for message in query(prompt=SUBAGENT_PROMPT, options=options):
            if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "Agent":
                        agent_seen = True
            if isinstance(message, ResultMessage):
                result = message
                if not message.is_error and message.structured_output is not None:
                    break
    except ResultError as exc:
        return None, exc, agent_seen, denials
    except Exception as exc:  # the SDK raises its own hierarchy; production catches all of it
        return None, exc, agent_seen, denials
    return result, None, agent_seen, denials


async def test_sonnet_5_5_is_accepted_as_subagent_model(tmp_path: Path) -> None:
    """The sub-agent-path gate: a parent on `verifier_model` launches one sub-agent
    through the `Agent` tool, its model forced by the shipping `subagent_model`
    default through `Configuration(...).role_env` (built directly, never through
    `from_sources`, and no file spells `CLAUDE_CODE_SUBAGENT_MODEL`/`_FORCE` by hand).
    Does the default get accepted as the sub-agent's model and bill a call?"""
    env = Configuration(product_dir=tmp_path).role_env
    print("parent model (verifier_model default):", PARENT_MODEL)
    print("resolved subagent_model default:", SUBAGENT_MODEL)

    result: ResultMessage | None = None
    exc: Exception | None = None
    reason: str | None = None
    agent_seen = False
    denials = 0
    for attempt in range(1, RETRY_BUDGET + 1):
        result, exc, agent_seen, denials = await _subagent_attempt(tmp_path, env)
        if result is not None:
            print(
                f"attempt {attempt}: ResultMessage.is_error={result.is_error} "
                f"total_cost_usd={result.total_cost_usd} model_usage={result.model_usage} "
                f"(agent_seen={agent_seen}, denials={denials})"
            )
        else:
            print(
                f"attempt {attempt}: no ResultMessage, exception={exc!r} "
                f"(agent_seen={agent_seen}, denials={denials})"
            )
        if result is not None and not result.is_error and result.total_cost_usd:
            print(f"attempt {attempt}: green, cost {result.total_cost_usd}")
            break
        reason = _classify(result, exc)
        print(f"attempt {attempt}: classified {reason!r}")
        if reason not in _RETRIABLE or attempt == RETRY_BUDGET:
            break

    assert result is not None, f"no ResultMessage on any attempt (last classified: {reason!r})"

    # The sub-agent must actually have run, or neither this assertion nor the one on
    # `model_usage` below is evidence about the model at all (module docstring): a
    # test bug to fix here, never a red result.
    assert agent_seen, (
        "no main-thread `Agent` ToolUseBlock seen: the sub-agent never ran, this test "
        "proved nothing about the model"
    )
    assert denials == 0, (
        f"{denials} call(s) were denied: the sub-agent never ran as intended, this "
        "test proved nothing about the model"
    )

    assert not result.is_error, f"run failed, classified {reason!r}"
    usage = result.model_usage or {}
    assert SUBAGENT_MODEL in usage, f"no key {SUBAGENT_MODEL!r} in model_usage: {usage}"
    assert usage[SUBAGENT_MODEL].get("costUSD", 0) > 0
