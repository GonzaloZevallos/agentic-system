"""Pins the OpenTelemetry GenAI semantic convention names this adapter depends on
(invariant 7). `opentelemetry.semconv` ships these as a private, incubating module
transitively pulled in by `opentelemetry-sdk`; `pyproject.toml` does not declare it and
its GenAI attributes are Development status, so the strings are literals here instead,
copied from the convention by hand and pinned so a typo is a red test, not a silent
mismatch with whatever reads the trace."""

import ast
from pathlib import Path

import agentic
from agentic.core.measurement.adapters.otel import semconv


def test_the_gen_ai_attribute_names_match_the_convention():
    assert semconv.OPERATION_NAME == "gen_ai.operation.name"
    assert semconv.AGENT_NAME == "gen_ai.agent.name"
    assert semconv.AGENT_ID == "gen_ai.agent.id"
    assert semconv.PROVIDER_NAME == "gen_ai.provider.name"
    assert semconv.SYSTEM == "gen_ai.system"


def test_the_operation_and_provider_values_match_the_convention():
    assert semconv.INVOKE_AGENT == "invoke_agent"
    assert semconv.ANTHROPIC == "anthropic"


def test_span_name_is_invoke_agent_followed_by_the_role_name():
    assert semconv.span_name("Tech Lead") == "invoke_agent Tech Lead"


def test_exceptions_are_exactly_the_keys_kept_bare_by_the_parity_rule():
    # `product`, `role`, `role.outcome`: already spelled the same on the span and on a
    # metric label, so they keep that spelling instead of taking `agentic.` (parity
    # rule, ADR-0008). `langfuse.observation.type` is a vendor key, not ours to
    # namespace either way.
    assert semconv.EXCEPTIONS == frozenset(
        {"product", "role", "role.outcome", "langfuse.observation.type"}
    )


def test_nothing_imports_opentelemetry_semconv():
    """Invariant 7: the convention's names are copied by hand, never imported from the
    private module that carries them."""
    package = Path(agentic.__file__).parent
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n.startswith("opentelemetry.semconv") for n in names), path
