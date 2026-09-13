"""Product knowledge: what the system knows about the product, distilled and current.

It lives in the product repo with the human docs, not under `.agentic/` (ADR-0006):
`docs/product/<capability>.md`, one file per capability named after a glossary
concept, and `CONTEXT.md`, the glossary. Product Analyst and PM read all of it on
every task. On approval PM returns the complete updated file of each capability the
task touched plus the new or changed glossary terms, and the graph writes them here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

PRODUCT_SUBDIR = Path("docs/product")
GLOSSARY = "CONTEXT.md"
HEADING = "## Product knowledge"
LANGUAGE_HEADING = "## Language"

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_H2 = re.compile(r"^## ", re.M)


def read(product_dir: Path) -> str:
    """Everything a product role reads before the task document; empty if nothing exists."""
    parts = []
    glossary = product_dir / GLOSSARY
    if glossary.is_file():
        parts.append(_fenced(GLOSSARY, glossary.read_text(encoding="utf-8")))
    for path in sorted((product_dir / PRODUCT_SUBDIR).glob("*.md")):
        parts.append(_fenced(f"{PRODUCT_SUBDIR}/{path.name}", path.read_text(encoding="utf-8")))
    if not parts:
        return ""
    return HEADING + "\n\n" + "\n\n".join(parts)


def _fenced(name: str, text: str) -> str:
    # Fenced, so the files' own `##` headings never look like task sections.
    return f"`{name}`:\n\n````markdown\n{text.strip()}\n````"


def write_definitions(product_dir: Path, definitions: dict[str, str]) -> list[Path]:
    """Replace each capability file whole. Creates `docs/product/` on first use."""
    written = []
    for slug, content in definitions.items():
        if not _SLUG.match(slug):
            raise ValueError(f"capability {slug!r} is not a slug (lower-case, digits, hyphens)")
        path = product_dir / PRODUCT_SUBDIR / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")
        written.append(path)
    return written


def upsert_terms(product_dir: Path, terms: dict[str, dict[str, str]]) -> Path | None:
    """Add or replace `**Term**:` entries under `## Language` of the glossary.

    Existing entries keep their place; new ones go at the end of the section. Nothing
    else in the file is touched. Returns the path, or None when there is nothing to do.
    """
    if not terms:
        return None
    path = product_dir / GLOSSARY
    text = (
        path.read_text(encoding="utf-8")
        if path.is_file()
        else f"# {product_dir.name}\n\n{LANGUAGE_HEADING}\n"
    )
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
    path.write_text(text, encoding="utf-8")
    return path


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
            if not _SLUG.match(item["capability"]):
                raise ValueError(
                    f"capability {item['capability']!r} must be a slug: lower-case, digits, "
                    "hyphens, named after a glossary concept (e.g. `check-ins`)"
                )

    return check
