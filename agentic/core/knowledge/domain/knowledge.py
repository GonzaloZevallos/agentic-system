"""Product knowledge: what the system knows about the product, consolidated and current.

It lives in the product repo with the human docs, not under `.agentic/` (ADR-0006):
`docs/product/<capability>.md`, one file per capability named after a glossary
concept, `CONTEXT.md`, the glossary, and `docs/adr/`, the decisions Tech Lead (or the
human) already took. A role reads the Brief and the glossaries whole and the rest through
an index (`knowledge_index`, GH-292). On approval PM
returns the complete updated file of each capability the task touched plus the new or
changed glossary terms, and the system writes them; the ADRs it only reads.

This module shapes the text: how the knowledge is presented to a role, how a
glossary is edited, what an approval must carry. The `ProductKnowledge` port reads
and writes the files.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from agentic.core.process.domain.schemas import SLUG

PRODUCT_SUBDIR = Path("docs/product")
ADR_SUBDIR = Path("docs/adr")
GLOSSARY = "CONTEXT.md"
# A product with more than one bounded context maps them here instead of holding one
# glossary at the root (ADR-0010 is this repo's own case, issue #45).
CONTEXT_MAP = "CONTEXT-MAP.md"
HEADING = "## Product knowledge"
LANGUAGE_HEADING = "## Language"
# The engineering standards (ADR-0012): the product's file at its root replaces the
# system's default whole, the same pattern as the flag vocabulary. Named so that the
# `code-review` skill finds it by itself.
STANDARDS = "CODING_STANDARDS.md"
DEFAULT_STANDARDS = Path(__file__).resolve().parent / STANDARDS
STANDARDS_HEADING = "## Engineering standards"

# The design standards (ADR-0055): the criteria of the four design roles, a file at the
# product's root. Unlike the engineering standards the system ships no default: a product
# without the file has an empty design block.
DESIGN_STANDARDS = "DESIGN_STANDARDS.md"
DESIGN_HEADING = "## Design standards"
# The writing standards: the criteria of the two roles that write and review a product's
# person-facing texts (voice, tone, register, vocabulary, length limits, patterns per kind
# of text, and the message catalog when the product has one). A file at the product's root,
# served whole, with no system default: a product without it has an empty block and the
# role says so.
WRITING_STANDARDS = "WRITING_STANDARDS.md"
WRITING_HEADING = "## Writing standards"
# A criteria document never grows silently: past this many lines it is reported, never
# refused or rewritten (ADR-0055).
CRITERIA_MAX_LINES = 300

# The Brief (ADR-0025): what the human writes once per product, before the first task
# -- the problem, the jobs and the constraints. A root-level artefact, next to
# `CONTEXT.md` and `CODING_STANDARDS.md`, never under `PRODUCT_SUBDIR`: there it would
# be read as a capability, `write_definitions` would replace it whole on the first PM
# approval naming the slug `brief`, and its `## Jobs` heading would satisfy
# `JOBS_HEADING` in `check_definitions`. The system ships no default text a run falls
# back to -- an absent Brief is a fact `cli.py` prints, never this module's problem.
BRIEF = "BRIEF.md"
# Its own tuple, deliberately not built from `JOBS_HEADING` below: the `## Jobs`
# coincidence is intentional (job stories are the shared format) but the two contracts
# must stay uncoupled, so a future PE moving one never silently moves the other.
BRIEF_HEADINGS = (
    "## Problem and who has it",
    "## Jobs",
    "## Constraints",
    "## Out of scope",
)
# One page. A constant, not a guess: `preflight` spends nothing before this is checked,
# and every role that reads the product knowledge pays this cost on every call (ADR-0025).
BRIEF_MAX_WORDS = 500
# What a human copies into `BRIEF.md` and commits: a shipped, valid example, the same
# `TEMPLATE`/`not_declared` pattern `stack.py` already uses (ADR-0016, second addendum).
DEFAULT_BRIEF_TEMPLATE = Path(__file__).resolve().parent / "brief.md"

_BRIEF_HEADING_LINE = re.compile(r"^##[ \t]+(?P<title>.*?)[ \t]*$", re.M)
_FENCED_BLOCK = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)

_SLUG = SLUG  # one slug rule for the whole system: capabilities, subtasks
_H2 = re.compile(r"^## ", re.M)
_TERM = re.compile(r"^\*\*(.+?)\*\*[^\n]*:$", re.M)
_MAP_LINK = re.compile(rf"^- \[([^\]]+)\]\((\S+?/{re.escape(GLOSSARY)})\)", re.M)
# An entry's `_Key_: value` lines. The writer of a definition owns `_Avoid_`; any other
# key (`_Campo_`, whatever a product adds) belongs to whoever wrote it and survives a
# rewrite. Italic prose inside a definition is not a key and does not survive (issue #45).
_KEY_LINE = re.compile(r"^_[^_]+_:")
_OWNED = ("_Avoid_:",)


def terms(glossary: str) -> list[str]:
    """The terms a glossary defines, in order: the `**Term**...:` lines `upsert_terms` writes."""
    return _TERM.findall(glossary)


def contexts(context_map: str) -> dict[str, str]:
    """Each context the map links, by the name the map gives it, in the map's order.

    The name and not the directory: `<module>/domain/CONTEXT.md` is a plausible layout,
    and keying by the parent would collapse every context into one `domain` and lose
    every glossary but the last."""
    found: dict[str, str] = {}
    for name, path in _MAP_LINK.findall(context_map):
        if name in found:
            raise ValueError(f"{CONTEXT_MAP} names {name!r} twice; each context once")
        if not _inside(path):
            raise ValueError(
                f"{CONTEXT_MAP} links {path!r}, outside the product; a glossary lives "
                "in the product, and the map cannot reach out of it"
            )
        found[name] = path.lstrip("./")
    return found


def _inside(path: str) -> bool:
    """A link that climbs out of the product root. The map is a file a role can edit,
    so this is the difference between writing in its worktree and outside it."""
    depth = 0
    for part in PurePosixPath(path).parts:
        if part == "..":
            depth -= 1
        elif part not in (".", ""):
            depth += 1
        if depth < 0:
            return False
    return not PurePosixPath(path).is_absolute()


def context_path(context: str, found: dict[str, str]) -> str:
    """The glossary of one context, by the name the map gives it or by its directory."""
    if context in found:
        return found[context]
    by_dir = [p for p in found.values() if p.rsplit("/", 2)[-2] == context]
    if len(by_dir) == 1:
        return by_dir[0]
    raise ValueError(f"context {context!r} is not in {CONTEXT_MAP}; it names: {', '.join(found)}")


def check_contexts(terms: list[dict[str, Any]], found: dict[str, str]) -> None:
    """Every term of a mapped product says which context it belongs to, and the context
    exists. A term belongs to a context, not to a file: a task that changes behaviour
    across modules leaves terms in each of their glossaries (issue #45)."""
    if not found:
        return
    for term in terms:
        if not (context := term.get("context", "").strip()):
            raise ValueError(
                f"this product is multi-context ({', '.join(found)}): term "
                f"{term['term']!r} must say in `context` which one it belongs to"
            )
        context_path(context, found)


def check_slug(capability: str) -> None:
    if not _SLUG.match(capability):
        raise ValueError(
            f"capability {capability!r} must be a slug: lower-case, digits, hyphens, "
            "named after a glossary concept (e.g. `check-ins`)"
        )


def criteria_over_cap(texts: dict[str, str]) -> list[str]:
    """One message per criteria document strictly over `CRITERIA_MAX_LINES`, in input order.

    `texts` is file name -> text and holds only files the product has: an absent, deleted
    or system-default file is never measured, and the caller never passes it. Lines are
    counted per document, never summed across them."""
    return [
        f"`{name}`: {count} lines (cap {CRITERIA_MAX_LINES}); cut the file or split it into "
        "`## ` sections"
        for name, text in texts.items()
        if (count := len(text.splitlines())) > CRITERIA_MAX_LINES
    ]


def fenced(name: str, text: str) -> str:
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
        if found := pattern.search(text):
            block = _entry(term, entry, _kept(found.group(0)))
            text = pattern.sub(lambda _: block, text, count=1)
        else:
            text = _append_to_language(text, block)
    return text if text.endswith("\n") else text + "\n"


def _entry(term: str, entry: dict[str, str], kept: list[str] | None = None) -> str:
    lines = [f"**{term}**:", entry["definition"].strip()]
    if entry.get("avoid", "").strip():
        lines.append(f"_Avoid_: {entry['avoid'].strip()}")
    return "\n".join(lines + (kept or []))


def _kept(block: str) -> list[str]:
    """The `_Key_:` lines of an existing entry that a rewrite does not own."""
    return [
        line
        for line in block.split("\n")[1:]
        if _KEY_LINE.match(line) and not line.startswith(_OWNED)
    ]


def _append_to_language(text: str, block: str) -> str:
    start = text.index(LANGUAGE_HEADING) + len(LANGUAGE_HEADING)
    following = _H2.search(text, start)
    end = following.start() if following else len(text)
    section = text[start:end].rstrip("\n")
    return text[:start] + section + "\n\n" + block + "\n" + ("\n" if following else "") + text[end:]


def consolidation(verdict: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """What an approved verdict leaves in the knowledge: capability files by slug and
    glossary terms by name, as the `ProductKnowledge` port takes them."""
    definitions = {d["capability"]: d["content"] for d in verdict["definitions"]}
    terms = {
        t["term"]: {
            "definition": t["definition"],
            "avoid": t["avoid"],
            "context": t.get("context", ""),
        }
        for t in verdict["glossary"]
    }
    return definitions, terms


JOBS_HEADING = "## Jobs"


def approval_check(
    task_type: str, contexts: dict[str, str] | None = None
) -> Callable[[dict[str, Any]], None]:
    """The check a PM output must pass before it counts: an approved feature ships
    its definitions, every capability is a valid file name, and every definition
    opens with the jobs it serves."""

    def check(data: dict[str, Any]) -> None:
        if data["verdict"] != "approved":
            return
        definitions = data.get("definitions", [])
        if task_type == "feature" and not definitions:
            raise ValueError(
                "approved a feature without `definitions`: return the complete updated "
                "file of every capability this task touches (docs/product/<capability>.md)"
            )
        check_definitions(definitions)
        check_contexts(data.get("glossary", []), contexts or {})

    return check


def clarification_check(
    contexts: dict[str, str] | None = None,
) -> Callable[[dict[str, Any]], None]:
    """The check a PM consultation must pass: whatever it consolidates is a valid file that
    opens with its jobs, and every term says its context. Consolidating nothing is fine:
    the answers may change no text."""

    def check(data: dict[str, Any]) -> None:
        check_definitions(data.get("definitions", []))
        check_contexts(data.get("glossary", []), contexts or {})

    return check


def check_definitions(definitions: list[dict[str, Any]]) -> None:
    for item in definitions:
        check_slug(item["capability"])
        if JOBS_HEADING not in item["content"]:
            raise ValueError(
                f"definition of {item['capability']!r} has no `{JOBS_HEADING}` section: "
                "open the file with the job stories the capability serves"
            )


def _brief_sections(text: str) -> dict[str, str]:
    """Top-level `## ` headings, never `###` (the regex only matches right after a
    heading's own `##` at the start of a line, with a `#` third character failing the
    following `[ \\t]+`). Keyed by title with trailing whitespace and at most one
    trailing `:` stripped, case-folded; valued by everything up to the next `##` or the
    end of the file."""
    matches = list(_BRIEF_HEADING_LINE.finditer(text))
    found: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group("title").rstrip()
        if title.endswith(":"):
            title = title[:-1].rstrip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        found[title.casefold()] = text[m.end() : end]
    return found


def _brief_heading_key(heading: str) -> str:
    title = heading.removeprefix("## ").rstrip()
    if title.endswith(":"):
        title = title[:-1].rstrip()
    return title.casefold()


def _has_content(section: str) -> bool:
    """At least one non-whitespace line: line-based, not substring containment, which
    would pass a heading followed only by blank or whitespace-only lines."""
    return any(line.strip() for line in section.split("\n"))


def _brief_word_count(text: str) -> int:
    """`len(text.split())` over the text with fenced code blocks stripped first, so a
    pasted snippet does not count against the cap it is quoting."""
    return len(_FENCED_BLOCK.sub("", text).split())


def brief_refusal(text: str) -> str | None:
    """Why `BRIEF.md` cannot be used, or None when its shape is valid (ADR-0025): the
    four headings present as top-level `##` lines, each followed by at least one
    non-whitespace line, and the whole file under `BRIEF_MAX_WORDS`. A pure function
    over text -- no filesystem, no port -- so `cli.py` is free to join it with whatever
    read `ProductKnowledge.brief` returns."""
    sections = _brief_sections(text)
    missing = [
        heading
        for heading in BRIEF_HEADINGS
        if not _has_content(sections.get(_brief_heading_key(heading), ""))
    ]
    template = DEFAULT_BRIEF_TEMPLATE.read_text(encoding="utf-8")
    if missing:
        return (
            f"{BRIEF} is missing or empty: {', '.join(missing)}. All four sections are "
            f"mandatory, each with at least one line of content:\n\n{template}"
        )
    words = _brief_word_count(text)
    if words > BRIEF_MAX_WORDS:
        return f"{BRIEF}: {words} words (cap {BRIEF_MAX_WORDS}). Trim it to one page:\n\n{template}"
    return None
