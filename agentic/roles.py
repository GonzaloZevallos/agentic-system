"""Role definitions and prompt loading.

Prompts live in `agentic/prompts/<slug>.md`. A product may override one with
`.agentic/prompts/<slug>.md` in its own repo (ADR-0005). Each task records
the short hash of every prompt it ran, so a tuned prompt is traceable even
before it is committed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .configuration import RoleKind
from .schemas import Classification, Definition, RoleOutput, Verdict

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
OVERLAY_SUBDIR = Path(".agentic/prompts")
# Pseudo-section every role may declare: the task as it entered.
ORIGINAL = "Original request"


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
    # Sections of the task document this role reads, in full (DISENO.md, 6).
    # Declarative, so PjM can record it and see what a failing role was missing.
    # A role that lists itself reads its own previous section on a return.
    reads: tuple[str, ...] = (ORIGINAL,)
    effort: str = "medium"
    max_turns: int = 4

    def prompt(self, product_dir: Path, **placeholders: object) -> Prompt:
        """Load the prompt, product overlay first, and fill `{{name}}` placeholders."""
        source = product_dir / OVERLAY_SUBDIR / f"{self.slug}.md"
        if not source.exists():
            source = PROMPTS_DIR / f"{self.slug}.md"
        raw = source.read_text(encoding="utf-8")
        text = raw
        for key, value in {"date": date.today().isoformat(), **placeholders}.items():
            text = text.replace("{{" + key + "}}", str(value))
        return Prompt(text=text, hash=prompt_hash(raw), source=source)


def prompt_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


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
)

PM = Role(
    name="PM",
    slug="pm",
    kind="validator",
    schema=Verdict,
    reads=(ORIGINAL, "Product Analyst", "PM"),
)

PAIR_PM_PA = f"{PM.name}→{PRODUCT_ANALYST.name}"
