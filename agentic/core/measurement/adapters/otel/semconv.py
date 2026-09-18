"""The OpenTelemetry GenAI semantic convention names the role span adopts (ADR-0008,
addendum). Literals, not an import of `opentelemetry.semconv`: that module is private,
transitively pulled in by `opentelemetry-sdk` rather than declared in `pyproject.toml`,
and its GenAI attributes are Development status — a dependency the product would carry
by accident. Copying the strings by hand, pinned by `test_semconv.py`, is what keeps
this adapter working if that package's shape moves under it.

`invoke_agent` is the only operation this system's role span ever names: a role spends
across models within one run (`RoleRun.finish` stamps `agentic.role.model` from the
last one), so no `chat`-level attribute — token counts, request or response model,
sampling parameters — belongs on it.
"""

from __future__ import annotations

OPERATION_NAME = "gen_ai.operation.name"
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
PROVIDER_NAME = "gen_ai.provider.name"
SYSTEM = "gen_ai.system"

INVOKE_AGENT = "invoke_agent"
ANTHROPIC = "anthropic"

# Span-attribute keys kept bare on purpose: each is already spelled the same on the
# span and on a metric label (the parity rule, ADR-0008 addendum), or, for
# `langfuse.observation.type`, it is a vendor key that is not ours to namespace either
# way. Every other application attribute on the role span takes `agentic.`.
EXCEPTIONS = frozenset({"product", "role", "role.outcome", "langfuse.observation.type"})


def span_name(role_name: str) -> str:
    """`invoke_agent <role>`, the convention's span-name shape for the `invoke_agent`
    operation. `role_name` is `Role.name`, verbatim (invariant 6)."""
    return f"{INVOKE_AGENT} {role_name}"
