"""Role definitions and prompt rendering.

A role's prompt is part of its definition, like the sections it reads: the defaults
live next to this module in `prompts/<slug>.md`. Where a prompt is loaded from (the
product overlay of ADR-0005) is the `Prompts` port's business; rendering the
placeholders and hashing the raw text is the domain's, so a tuned prompt is
traceable even before it is committed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agentic.shared.configuration import RoleKind

from .document import ORIGINAL  # pseudo-section every role may declare: the task as it entered
from .schemas import Classification, Definition, RoleOutput, Verdict

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    text: str
    hash: str
    source: Path


@dataclass(frozen=True)
class Role:
    name: str
    slug: str
    kind: RoleKind
    schema: type[RoleOutput]
    # Sections of the task document this role reads, in full (README.md, 6).
    # Declarative, so PjM can record it and see what a failing role was missing.
    # A role that lists itself reads its own previous section on a return.
    reads: tuple[str, ...] = (ORIGINAL,)
    # Product roles also receive the product knowledge, whole (ADR-0006).
    reads_knowledge: bool = False
    # Budgets (README.md, 14): how hard the role thinks and how many turns it gets.
    effort: str = "medium"
    max_turns: int = 4


def render(raw: str, source: Path, **placeholders: object) -> Prompt:
    """Fill `{{name}}` placeholders; the hash is of the raw text, before filling."""
    values = {"date": date.today().isoformat(), **placeholders}

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"{source}: no value for placeholder {{{{{key}}}}}")
        return str(values[key])

    return Prompt(text=_PLACEHOLDER.sub(fill, raw), hash=prompt_hash(raw), source=source)


def prompt_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def describe_flags(vocabulary: dict[str, str]) -> str:
    """The flag vocabulary as the prompt shows it. Where the vocabulary comes from is
    the `FlagVocabulary` port's business (`core.knowledge`)."""
    return "\n".join(f"- `{name}`: {desc}" for name, desc in vocabulary.items())


PJM = Role(
    name="PjM",
    slug="pjm",
    kind="thinker",
    schema=Classification,
    effort="low",
    max_turns=2,
)

PRODUCT_ANALYST = Role(
    name="Product Analyst",
    slug="product_analyst",
    kind="thinker",
    schema=Definition,
    reads=(ORIGINAL, "PjM", "Product Analyst", "PM"),
    reads_knowledge=True,
)

PM = Role(
    name="PM",
    slug="pm",
    kind="validator",
    schema=Verdict,
    reads=(ORIGINAL, "Product Analyst", "PM"),
    reads_knowledge=True,
)

ROLES = (PJM, PRODUCT_ANALYST, PM)


@dataclass(frozen=True)
class Pair:
    """Who returns and who receives the return; the limit counts per pair (README.md, 3).
    Not validator→thinker: QA returns to PM, to Tech Lead or to a Developer's subtask."""

    by: Role
    to: Role
    subtask: str | None = None

    @property
    def key(self) -> str:
        base = f"{self.by.name}→{self.to.name}"
        return base if self.subtask is None else f"{base}:{self.subtask}"


PAIR_PM_PA = Pair(by=PM, to=PRODUCT_ANALYST)
