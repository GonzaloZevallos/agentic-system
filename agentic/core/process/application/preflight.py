"""Fail before spending money if a product overlay is off."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain.roles import ROLES
from .ports import Ports
from .run_role import load_prompt

# Every placeholder any prompt may use, with a value good enough to render. A prompt
# that names one outside this set fails here, whatever role it belongs to.
PLACEHOLDERS = {
    "remaining_changes": 2,
    "max_rounds": 4,
    "round": 1,
    "subtask": "subtask",
    "base": "HEAD",
    "review": "a first review",
    "branches": "- none",
    "checks": "- `tests`: `pytest`",
}


def preflight(cfg: Configuration, ports: Ports) -> None:
    """Load the vocabulary and render every role's prompt: a bad flags file or an
    overlay with a stray placeholder fails here, not in the middle of a task."""
    vocabulary = ports.flags.load()
    for role in ROLES:
        load_prompt(role, cfg, ports, vocabulary, **PLACEHOLDERS)
