"""The system stops knowing Python (issue #38): no role prompt may spell a
language-specific token — a file extension, a comment syntax, a tool name, a linter
directive. Which tool meets a practice is the product's to declare, through
`.agentic/stack.yaml`, never a word baked into a prompt.

Seam: the role prompt text as `run_role.load_prompt` renders it through the `Prompts`
port (`agentic.core.process.application.ports.Prompts`), the same call `preflight` and
every node make before a role runs.
"""

from __future__ import annotations

from agentic.core.process.application.preflight import PLACEHOLDERS
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.domain.roles import PJM, ROLES
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryPrompts, memory_ports

# The task's own inventory of what a Python-flavoured prompt spelled, plus the sweep
# list the brief names. Matched case-insensitively, as a substring, so "Python" and
# "HTTP" are caught the same as their lowercase spellings, a silencing directive is
# caught by its keyword alone, and ".py" also catches a `conftest.py`-shaped example.
STACK_TOKENS = (
    "http",
    "database",
    "pytest",
    "type hint",
    ".py",
    "mypy",
    "ruff",
    "ignore",
    "noqa",
    "python",
)


def _violations(text: str) -> list[str]:
    lowered = text.lower()
    return [token for token in STACK_TOKENS if token in lowered]


# `PLACEHOLDERS` is `preflight`'s own stand-in set, good enough to render, not a source
# of truth for a prompt's own words: its `checks` value still names `pytest` today (the
# `suppressions` subtask's edit, out of scope here), so this test fills that one
# placeholder with a stack-agnostic stand-in of its own and leaves every other value
# untouched, to isolate what the prompts themselves say from what a caller substitutes.
_RENDER_WITH = {**PLACEHOLDERS, "checks": "- `tests`: `the product's declared test command`"}


def test_no_role_prompt_names_a_stack_specific_token(tmp_path):
    # The default prompts, as `Prompts.raw` returns them with no product overlay: the
    # accepting case, every role in the roster.
    cfg = Configuration(product_dir=tmp_path)
    ports = memory_ports()
    violations = {
        role.slug: _violations(load_prompt(role, cfg, ports, {}, **_RENDER_WITH).text)
        for role in ROLES
    }
    assert violations == {role.slug: [] for role in ROLES}


def test_a_stack_specific_token_in_a_prompt_is_caught(tmp_path):
    # The rejecting case: a synthetic prompt smuggling back the flavour this task
    # removes, rendered through the same seam, must not pass unnoticed.
    cfg = Configuration(product_dir=tmp_path)
    tuned = "Today is {{date}}. Silence mypy: tell the linter to noqa and move on."
    ports = memory_ports(prompts=MemoryPrompts(pjm=tuned))
    prompt = load_prompt(PJM, cfg, ports, {}, **_RENDER_WITH)
    assert _violations(prompt.text) == ["mypy", "noqa"]
