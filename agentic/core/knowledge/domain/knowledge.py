"""Product knowledge: what the system knows about the product, distilled and current.

It lives in the product repo with the human docs, not under `.agentic/` (ADR-0006):
`docs/product/<capability>.md`, one file per capability named after a glossary
concept, and `CONTEXT.md`, the glossary. Product Analyst and PM read all of it on
every task. On approval PM returns the complete updated file of each capability the
task touched plus the new or changed glossary terms, and the system writes them.

This module shapes the text: how the knowledge is presented to a role, how a
glossary is edited, what an approval must carry. The `ProductKnowledge` port reads
and writes the files.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from agentic.core.process.domain.schemas import SLUG

PRODUCT_SUBDIR = Path("docs/product")
GLOSSARY = "CONTEXT.md"
HEADING = "## Product knowledge"
LANGUAGE_HEADING = "## Language"
# The engineering standards (ADR-0012): the product's file at its root replaces the
# system's default whole, the same pattern as the flag vocabulary. Named so that the
# `code-review` skill finds it by itself.
STANDARDS = "CODING_STANDARDS.md"
DEFAULT_STANDARDS = Path(__file__).resolve().parent / STANDARDS
STANDARDS_HEADING = "## Engineering standards"

_SLUG = SLUG  # one slug rule for the whole system: capabilities, subtasks
_H2 = re.compile(r"^## ", re.M)
_TERM = re.compile(r"^\*\*(.+?)\*\*[^\n]*:$", re.M)


def terms(glossary: str) -> list[str]:
    """The terms a glossary defines, in order: the `**Term**...:` lines `upsert_terms` writes."""
    return _TERM.findall(glossary)


def check_slug(capability: str) -> None:
    if not _SLUG.match(capability):
        raise ValueError(
            f"capability {capability!r} must be a slug: lower-case, digits, hyphens, "
            "named after a glossary concept (e.g. `check-ins`)"
        )


def assemble(glossary: str | None, capabilities: dict[str, str]) -> str:
    """Everything a product role reads before the task document: the glossary, then
    each capability file by name, sorted. Empty if nothing exists."""
    parts = []
    if glossary is not None:
        parts.append(_fenced(GLOSSARY, glossary))
    for name in sorted(capabilities):
        parts.append(_fenced(f"{PRODUCT_SUBDIR}/{name}", capabilities[name]))
    if not parts:
        return ""
    return HEADING + "\n\n" + "\n\n".join(parts)


def assemble_standards(text: str) -> str:
    """The standards as a role reads them, before the product knowledge."""
    return STANDARDS_HEADING + "\n\n" + _fenced(STANDARDS, text)


def _fenced(name: str, text: str) -> str:
    # Fenced, so the files' own `##` headings never look like task sections.
    return f"`{name}`:\n\n````markdown\n{text.strip()}\n````"


def upsert_terms(text: str | None, terms: dict[str, dict[str, str]], product_name: str) -> str:
    """The glossary with `**Term**:` entries added or replaced under `## Language`.

    Existing entries keep their place; new ones go at the end of the section. Nothing
    else is touched. Without a glossary (`None`) a minimal one is started.
    """
    if text is None:
        text = f"# {product_name}\n\n{LANGUAGE_HEADING}\n"
    if LANGUAGE_HEADING not in text:
        text = text.rstrip("\n") + f"\n\n{LANGUAGE_HEADING}\n"
    for term, entry in terms.items():
        block = _entry(term, entry)
        # An entry is its `**Term**...:` line plus everything up to the next blank line.
        pattern = re.compile(rf"^\*\*{re.escape(term)}\*\*[^\n]*:\n.*?(?=\n\n|\Z)", re.M | re.S)
        if pattern.search(text):
            text = pattern.sub(lambda _: block, text, count=1)
        else:
            text = _append_to_language(text, block)
    return text if text.endswith("\n") else text + "\n"


def _entry(term: str, entry: dict[str, str]) -> str:
    lines = [f"**{term}**:", entry["definition"].strip()]
    if entry.get("avoid", "").strip():
        lines.append(f"_Avoid_: {entry['avoid'].strip()}")
    return "\n".join(lines)


def _append_to_language(text: str, block: str) -> str:
    start = text.index(LANGUAGE_HEADING) + len(LANGUAGE_HEADING)
    following = _H2.search(text, start)
    end = following.start() if following else len(text)
    section = text[start:end].rstrip("\n")
    return text[:start] + section + "\n\n" + block + "\n" + ("\n" if following else "") + text[end:]


def distillation(verdict: dict) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """What an approved verdict leaves in the knowledge: capability files by slug and
    glossary terms by name, as the `ProductKnowledge` port takes them."""
    definitions = {d["capability"]: d["content"] for d in verdict["definitions"]}
    terms = {
        t["term"]: {"definition": t["definition"], "avoid": t["avoid"]} for t in verdict["glossary"]
    }
    return definitions, terms


def approval_check(task_type: str) -> Callable[[dict], None]:
    """The check a PM output must pass before it counts: an approved feature ships
    its definitions, and every capability is a valid file name."""

    def check(data: dict) -> None:
        if data["verdict"] != "approved":
            return
        definitions = data.get("definitions", [])
        if task_type == "feature" and not definitions:
            raise ValueError(
                "approved a feature without `definitions`: return the complete updated "
                "file of every capability this task touches (docs/product/<capability>.md)"
            )
        for item in definitions:
            check_slug(item["capability"])

    return check
