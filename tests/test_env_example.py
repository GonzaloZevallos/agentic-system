"""`.env.example` documents `AGENTIC_<FIELD>` overrides (ADR-0008, 2026-09-16 addendum):
a token that names no `Configuration` field and is read by nothing is a dead knob,
silently inert the moment its field is renamed. Three such tokens (`AGENTIC_THINKER_
MODEL`, `AGENTIC_VALIDATOR_MODEL`, `AGENTIC_MAX_RETURNS_PER_PAIR`) survived a rename
this way; this file is the artefact that keeps the drift from recurring.

Every `AGENTIC_<FIELD>` token the example file carries, commented or not, must either
name a field of `Configuration.model_fields` or appear in `ALLOWLIST` below, each entry
naming the reader that still consumes it outside `Configuration` (ADR-0030's baseline
and version labels, read by `measurement`'s telemetry, `agentic/core/measurement/
adapters/otel/telemetry.py`'s `system_version`/`system_baseline`)."""

import re
from pathlib import Path

from agentic.shared.configuration import ENV_PREFIX, Configuration

REPO = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO / ".env.example"

# Tokens read outside Configuration.model_fields, each naming its reader. Without this,
# the test would fail on day one against a correct file -- the allowlist is part of the
# pin, not an exemption from it.
ALLOWLIST = {
    # agentic/core/measurement/adapters/otel/telemetry.py, system_version()
    "AGENTIC_SYSTEM_VERSION",
    # agentic/core/measurement/adapters/otel/telemetry.py, system_baseline()
    "AGENTIC_SYSTEM_BASELINE",
    # agentic/composition.py, task_scorer(): the Langfuse key pair, read once from the
    # environment and handed to the scores adapter alone -- deliberately not fields.
    "AGENTIC_LANGFUSE_HOST",
    "AGENTIC_LANGFUSE_PUBLIC_KEY",
    "AGENTIC_LANGFUSE_SECRET_KEY",
}

_TOKEN = re.compile(rf"{ENV_PREFIX}[A-Z0-9_]+")


def _tokens(text: str) -> set[str]:
    """Every `AGENTIC_<...>` token appearing anywhere in `text`, commented or not."""
    return set(_TOKEN.findall(text))


def _undeclared(tokens: set[str]) -> set[str]:
    """The subset of `tokens` that names no `Configuration` field and is not
    allowlisted."""
    declared_fields = {ENV_PREFIX + name.upper() for name in Configuration.model_fields}
    return tokens - declared_fields - ALLOWLIST


def test_env_example_has_no_undeclared_agentic_token():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert _undeclared(_tokens(text)) == set()


def test_undeclared_rejects_a_token_naming_no_field():
    # The enforcement artefact must fail on an input it should reject, not only accept
    # a correct file (ADR-0023): a token naming no Configuration field and absent from
    # the allowlist is undeclared.
    bad = _tokens("# AGENTIC_TOTALLY_MADE_UP_KNOB=1\n")
    assert _undeclared(bad) == {"AGENTIC_TOTALLY_MADE_UP_KNOB"}


def test_undeclared_accepts_a_real_field_and_the_allowlist():
    good = _tokens("AGENTIC_VERIFIER_MODEL=x\n# AGENTIC_SYSTEM_BASELINE=\n")
    assert _undeclared(good) == set()
