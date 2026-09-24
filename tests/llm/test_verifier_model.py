"""Spike (subtask `verifier-model`): does the pinned SDK and CLI accept the identifier
`Configuration.verifier_model` now defaults to, and bill a call on it? Excluded from
CI: `uv run pytest -m llm`.

`pytest -m "not llm"`, ruff, ruff-format and mypy cannot tell a real model identifier
from a typo; `preflight` never reads `verifier_model`; and this task's own Developer
runs on `actor_model` (Sonnet), never the model this default ships. So the fact has to
come from actually running the harness once, against the model the shipping default
names -- resolved as `Configuration.model_fields["verifier_model"].default`, never a
constructed `Configuration()` (which raises: `product_dir` has no default) and never
the environment (`from_sources` reads `AGENTIC_VERIFIER_MODEL` ahead of the default, so
a machine where someone pulled the rollback lever would prove a model that is not
shipping, green and silent).

`TOY_ROLE` is built here, not in `agentic/domain/roles.py` (invariant: no subtask adds
a `Role` to the domain, not even for a test), with `schemas.RoleOutput` itself as its
schema -- the one-field `markdown` shape, never a real verifier's schema
(`Review`/`PlatformAnswers`): an unsatisfied multi-field schema reads exactly like a
rejected identifier, the confound `test_delivery_rejection_visibility.py` already
worked around the same way.

Options go through `options_for`, the same function production calls, so the kwarg set
this spike sends is the one a real verifier run sends -- never a hand-built
`ClaudeAgentOptions`. Bounded: `max_budget_usd=1.0`, `max_turns=4`, capped at two
attempts total (this file's own `RETRY_BUDGET`), the spend outside every cap and every
series (`RoleRun.cost_usd`, `agentic.role.*`) this repo's telemetry knows about --
pasted verbatim into the Developer's section with its measured cost, the same shape
ADR-0020 wrote down rather than discovered.

On a rejection, `_reported_result`/`_reported_error` (the adapter's own, imported here
on purpose: a second mapping is a second reading that could disagree with production,
which is what ADR-0029 removed) build the `ReportedFailure` `rules.failure_reason`
classifies. Only `sdk` or `limit` (this spike's own budget cap) mean the identifier was
rejected; `account_limit`, `upstream` and `schema` get one retry before being read the
same way, since they are not evidence about the identifier."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query

from agentic.core.process.adapters.claude_sdk.runner import (
    _reported_error,
    _reported_result,
    options_for,
)
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import Role
from agentic.core.process.domain.rules import ReportedFailure, failure_reason
from agentic.shared.configuration import Configuration

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

# Built here, never in `agentic/domain/roles.py`: a role of the spike, not of the
# product (invariant: no subtask adds a `Role` to the domain, not even for a test).
TOY_ROLE = Role(
    name="Toy Verifier",
    slug="toy_verifier_model_spike",
    kind="verifier",
    schema=schemas.RoleOutput,
    executes=False,
    max_turns=4,
)

TOY_SCHEMA = schemas.json_schema(schemas.RoleOutput, {})

PROMPT = (
    "Call the `StructuredOutput` tool exactly once, with a single top-level JSON key "
    "`markdown` whose value is the short string '### Changes\\nProbe, nothing built.'. "
    "Then stop."
)

# At most two attempts, spelled out in the module docstring: withholding on a quota
# blip costs one re-run of this task; a third attempt would not.
RETRY_BUDGET = 2


def _spike_options(tmp_path: Path, model: str) -> ClaudeAgentOptions:
    return options_for(
        TOY_ROLE,
        system_prompt="You are a probe for a harness spike. Follow the user's "
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


async def _attempt(tmp_path: Path, model: str) -> tuple[ResultMessage | None, Exception | None]:
    options = _spike_options(tmp_path, model)
    result: ResultMessage | None = None
    try:
        async for message in query(prompt=PROMPT, options=options):
            if isinstance(message, ResultMessage):
                result = message
                if not message.is_error and message.structured_output is not None:
                    break
    except ResultError as exc:
        return None, exc
    except Exception as exc:  # the SDK raises its own hierarchy; production catches all of it
        return None, exc
    return result, None


async def test_verifier_model_is_accepted_and_billed(tmp_path):
    """The gate: does the pinned SDK and CLI accept the shipping `verifier_model`
    default and bill a call on it? Retries once on a facts-not-about-the-identifier
    classification (`account_limit`, `upstream`, `schema`); anything classified `sdk`
    or `limit` on the last attempt is the identifier itself being rejected."""
    model = Configuration.model_fields["verifier_model"].default
    print(f"resolved verifier_model default: {model}")

    result: ResultMessage | None = None
    exc: Exception | None = None
    reason: str | None = None
    for attempt in range(1, RETRY_BUDGET + 1):
        result, exc = await _attempt(tmp_path, model)
        if result is not None and not result.is_error and result.total_cost_usd:
            print(f"attempt {attempt}: green, cost {result.total_cost_usd}")
            break
        report = (
            _reported_error(exc)
            if isinstance(exc, ResultError)
            else _reported_result(result)
            if result is not None
            # Neither a ResultMessage nor a ResultError: the same shape production's
            # own `if result is None:` branch reasons about (runner.py). An empty
            # `ReportedFailure` carries no budget subtype, no 429, no 5xx and no
            # structured-output-exhaustion marker, so `failure_reason` falls through
            # its own precedence to `sdk` -- the classifier is exercised, not bypassed.
            else ReportedFailure()
        )
        reason = failure_reason(report)
        print(f"attempt {attempt}: classified {reason!r}")
        if reason not in ("account_limit", "upstream", "schema") or attempt == RETRY_BUDGET:
            break

    assert result is not None, f"no ResultMessage on any attempt (last classified: {reason!r})"
    print("ResultMessage.is_error:", result.is_error)
    print("ResultMessage.total_cost_usd:", result.total_cost_usd)
    print("ResultMessage.structured_output:", result.structured_output)
    assert not result.is_error, f"run failed, classified {reason!r}"
    assert result.total_cost_usd is not None and result.total_cost_usd > 0
