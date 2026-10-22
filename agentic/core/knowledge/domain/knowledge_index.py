"""The two-level index of product knowledge (GH-292, ADR-0001 of this module).

A role no longer receives the whole corpus. It receives what `always_whole` says goes
whole -- the Brief, the glossaries and the sections a document marks as overriding
everything -- and an index: one line per area, each naming the file that lists that
area's entries. From there it opens an entry with `Read`, only when it needs it.

Pure: nothing here touches disk. `build_index` returns the texts and the files; the
`ProductKnowledge` adapter and `Workspace` are who write them under `.agentic/index/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from agentic.core.process.domain import roles

from . import knowledge

# The closed set of areas. A path outside them has no entry and raises (invariant: tags
# come from closed sets declared in one place).
AREAS = ("product", "adr", "standards")
AREA_SUMMARY = {
    "product": "capabilities: what the product does, one file per glossary concept",
    "adr": "decisions already taken, superseded ones left out",
    "standards": "engineering standards, one file per section",
}
INDEX_DIR = ".agentic/index"
# What goes whole by name, never by size. `CONTEXT.md` matches by its file name because
# a mapped product keeps one per context, at the path its `CONTEXT-MAP.md` links.
ALWAYS_FILES = (knowledge.BRIEF, knowledge.CONTEXT_MAP)
ALWAYS_MARKER = "<!-- agentic:always -->"
INDEX_SENTENCE = (
    "Paths are relative to your working directory; open an area's index file, and an "
    "entry's file, with `Read` only when you need it."
)

_H2_LINE = re.compile(r"^## +(?P<title>.*?)\s*$")
_H1_LINE = re.compile(r"^# +(?P<title>.*?)\s*$", re.M)
_FENCE = re.compile(r"^\s*(```|~~~)")
_NUMBERING = re.compile(r"^\d+[.)]?\s+")
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(?P<body>.*?)^---[ \t]*$", re.M | re.S)


@dataclass(frozen=True)
class KnowledgeIndex:
    """What a role receives and what must exist on disk for it to follow the index."""

    always: str  # fenced Brief, glossaries and marked sections; no heading
    areas: str  # one line per area plus the sentence; no heading
    files: dict[str, str]  # relative path -> content, under `.agentic/index/`
    standards: str = ""  # the whole `## Engineering standards` block, or "" when by index

    @property
    def text(self) -> str:
        """The `## Product knowledge` block; empty when there is nothing to say."""
        parts = [part for part in (self.always, self.areas) if part]
        return knowledge.HEADING + "\n\n" + "\n\n".join(parts) if parts else ""


def always_whole(path: str, section: str | None = None) -> bool:
    """The single decision of whole or index. Nothing else decides it.

    A file is whole when it is on the named list (`ALWAYS_FILES`, or a `CONTEXT.md`).
    A `## ` section -- `section` is its text, heading first -- is whole when the line
    right after its heading is exactly `ALWAYS_MARKER`; the file it lives in does not
    matter."""
    if section is not None:
        lines = section.split("\n")
        return len(lines) > 1 and _H2_LINE.match(lines[0]) is not None and lines[1] == ALWAYS_MARKER
    return path in ALWAYS_FILES or PurePosixPath(path).name == knowledge.GLOSSARY


def area_of(path: str) -> str:
    """The area a path belongs to, from the path alone."""
    if path.startswith(f"{knowledge.PRODUCT_SUBDIR}/"):
        return "product"
    if path.startswith(f"{knowledge.ADR_SUBDIR}/"):
        return "adr"
    if path == knowledge.STANDARDS:
        return "standards"
    raise ValueError(
        f"{path}: outside the areas {', '.join(AREAS)}; "
        f"a knowledge file lives under {knowledge.PRODUCT_SUBDIR}/, {knowledge.ADR_SUBDIR}/ "
        f"or is {knowledge.STANDARDS}"
    )


def superseded(adr_text: str) -> bool:
    """True only when the frontmatter `status:` starts with `superseded`. Prose, addenda
    and a missing or unreadable frontmatter all say nothing about the status."""
    try:
        status = _frontmatter(adr_text).get("status")
    except ValueError:
        return False
    return isinstance(status, str) and status.strip().casefold().startswith("superseded")


def build_index(
    glossaries: dict[str, str],
    capabilities: dict[str, str],
    adrs: dict[str, str],
    brief: str | None,
    standards: str | None,
    *,
    standards_whole: bool = False,
) -> KnowledgeIndex:
    """The text a role reads and the index files behind it.

    `glossaries` is path -> text, as the adapter reads them; `capabilities` and `adrs`
    are file name -> text. `standards` goes whole (`standards_whole`, chosen by the
    caller: every verifier) or through the index, one file per unmarked section and the
    marked ones inlined. Superseded ADRs get no entry; their path still works."""
    whole: list[str] = []
    if brief:
        whole.append(_whole_file(knowledge.BRIEF, brief))
    whole += [_whole_file(path, text) for path, text in glossaries.items()]

    entries: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {area: [] for area in AREAS}
    files: dict[str, str] = {}
    for subdir, found in ((knowledge.PRODUCT_SUBDIR, capabilities), (knowledge.ADR_SUBDIR, adrs)):
        for name in sorted(found):
            path = f"{subdir}/{name}"
            text = found[name]
            area = area_of(path)
            if always_whole(path):
                whole.append(knowledge.fenced(path, text))
            elif not (area == "adr" and superseded(text)):
                entries[area].append((path, _title(text, name), _roles(path, text)))

    standards_block = ""
    if standards is not None and standards_whole:
        standards_block = (
            knowledge.STANDARDS_HEADING + "\n\n" + knowledge.fenced(knowledge.STANDARDS, standards)
        )
    elif standards is not None:
        for number, section in enumerate(_sections(standards)):
            title = _section_title(section)
            if always_whole(knowledge.STANDARDS, section):
                whole.append(knowledge.fenced(f"{knowledge.STANDARDS} -- {title}", section))
                continue
            path = f"{INDEX_DIR}/standards/{number:02d}-{_slug(title)}.md"
            files[path] = section.strip() + "\n"
            entries["standards"].append((path, title, ()))

    lines = []
    for area in AREAS:
        if entries[area]:
            path = f"{INDEX_DIR}/{area}.md"
            files[path] = _area_file(area, entries[area])
            lines.append(
                f"- `{path}`: {area} -- {AREA_SUMMARY[area]} ({len(entries[area])} entries)"
            )
    areas = "\n".join(["Areas:", *lines]) + "\n\n" + INDEX_SENTENCE if lines else ""
    return KnowledgeIndex("\n\n".join(whole), areas, files, standards_block)


def _whole_file(path: str, text: str) -> str:
    if not always_whole(path):
        raise ValueError(f"{path}: not on the always-whole list; it cannot be given whole")
    return knowledge.fenced(path, text)


def _area_file(area: str, entries: list[tuple[str, str, tuple[str, ...]]]) -> str:
    lines = [f"# Index: {area}", "", f"{AREA_SUMMARY[area]}.", ""]
    lines += [
        f"- `{path}` | {title} | area: {area} | roles: {', '.join(tags) if tags else 'all'}"
        for path, title, tags in entries
    ]
    return "\n".join(lines) + "\n"


def _frontmatter(text: str) -> dict[str, Any]:
    if not (found := _FRONTMATTER.match(text)):
        return {}
    try:
        data = yaml.safe_load(found.group("body"))
    except yaml.YAMLError as error:
        raise ValueError(f"frontmatter is not valid YAML: {error}") from error
    return data if isinstance(data, dict) else {}


def _roles(path: str, text: str) -> tuple[str, ...]:
    """The role slugs a file's frontmatter `roles:` names, validated against the roles
    the system has. A value outside them names the file and the slug."""
    try:
        declared = _frontmatter(text).get("roles", [])
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(declared, list) or not all(isinstance(slug, str) for slug in declared):
        raise ValueError(f"{path}: `roles` must be a list of role slugs, got {declared!r}")
    known = {role.slug for role in roles.ROLES}
    for slug in declared:
        if slug not in known:
            raise ValueError(
                f"{path}: unknown role {slug!r} in `roles`; "
                f"the roles are {', '.join(sorted(known))}"
            )
    return tuple(declared)


def _title(text: str, fallback: str) -> str:
    body = _FRONTMATTER.sub("", text, count=1)
    found = _H1_LINE.search(body)
    return found.group("title") if found else fallback


def _sections(text: str) -> list[str]:
    """The text split at its `## ` lines outside code fences: the preamble first (when it
    holds anything), then each section, heading included."""
    chunks: list[list[str]] = [[]]
    fenced = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _H2_LINE.match(line):
            chunks.append([])
        chunks[-1].append(line)
    return [chunk for body in chunks if (chunk := "\n".join(body).strip())]


def _section_title(section: str) -> str:
    first = section.split("\n", 1)[0]
    found = _H2_LINE.match(first) or re.match(r"^# +(?P<title>.*?)\s*$", first)
    return found.group("title") if found else "Preamble"


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _NUMBERING.sub("", title).casefold()).strip("-")
    return slug or "section"
