"""`ProductKnowledge` (a `core.process` port) over `docs/product/`, `docs/adr/` and
`CONTEXT.md` of the product repo. The ADRs are read, never written here: Tech Lead
writes them with its own tools.

A consolidation is several files. Everything is computed and checked before the
first write, and if a write fails what was on disk is put back: PM's approval never
leaves the product half consolidated.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from ...domain import knowledge, knowledge_index


class FilesystemKnowledge:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    @property
    def glossary_path(self) -> Path:
        return self.product_dir / knowledge.GLOSSARY

    def contexts(self, root: Path | None = None) -> dict[str, str]:
        """The product's bounded contexts, by name, empty when it holds one glossary."""
        text = _read((root or self.product_dir) / knowledge.CONTEXT_MAP)
        return knowledge.contexts(text) if text is not None else {}

    def _glossaries(self, root: Path) -> dict[str, str]:
        """Path -> text of every glossary a role reads. A mapped product sends the map
        first: its relationships say which context may know which, and they live
        nowhere else (issue #45)."""
        if not (contexts := self.contexts(root)):
            text = _read(root / knowledge.GLOSSARY)
            return {knowledge.GLOSSARY: text} if text is not None else {}
        found = {knowledge.CONTEXT_MAP: _read(root / knowledge.CONTEXT_MAP)}
        found |= {path: _read(root / path) for path in contexts.values()}
        return {path: text for path, text in found.items() if text is not None}

    def approval_check(self, task_type: str) -> Callable[[dict[str, Any]], None]:
        # The contexts go into the check, not into the write: `run_role` turns a
        # `ValueError` here into a failed attempt and retries with the reason as
        # input, while a raise at write time would kill the run after PM was paid.
        return knowledge.approval_check(task_type, self.contexts())

    def clarification_check(self) -> Callable[[dict[str, Any]], None]:
        return knowledge.clarification_check(self.contexts())

    def _index(
        self, root: Path | None, *, standards: bool, product: bool = True
    ) -> knowledge_index.KnowledgeIndex:
        """The index as `build_index` makes it from what is on disk under `root`. Both
        `read` and `index_files` come from here, so the text and the files it points at
        can never describe different trees. `product=False` leaves capabilities and ADRs
        out, so their area files are not built."""
        root = root or self.product_dir
        return knowledge_index.build_index(
            self._glossaries(root),
            _read_dir(root / knowledge.PRODUCT_SUBDIR) if product else {},
            _read_dir(root / knowledge.ADR_SUBDIR) if product else {},
            self.brief(root),
            self._standards_text(root) if standards else None,
        )

    def read(self, root: Path | None = None) -> str:
        """What always goes whole plus the area index. The standards are not part of it:
        `standards` is where a role gets them."""
        return self._index(root, standards=False).text

    def index_files(self, root: Path | None, families: Collection[str]) -> dict[str, str]:
        """The files `read` and `standards(whole=False)` point at, relative to `root`, for
        the criteria families a role has: the capability and ADR files for `product`, the
        standards files for `engineering`. `design` is served whole and has no file."""
        return self._index(
            root, standards="engineering" in families, product="product" in families
        ).files

    def design(self, root: Path | None = None) -> str:
        """The product's `DESIGN_STANDARDS.md`, whole, or "" when it has none: the system
        ships no default (ADR-0055)."""
        text = _read((root or self.product_dir) / knowledge.DESIGN_STANDARDS)
        if text is None:
            return ""
        return (
            knowledge.DESIGN_HEADING + "\n\n" + knowledge.fenced(knowledge.DESIGN_STANDARDS, text)
        )

    def criteria_files(self) -> tuple[str, ...]:
        """The criteria documents the line cap measures (ADR-0055)."""
        return (knowledge.STANDARDS, knowledge.DESIGN_STANDARDS)

    def over_cap(self, texts: dict[str, str]) -> list[str]:
        """The criteria documents past the line cap, one message each (ADR-0055)."""
        return knowledge.criteria_over_cap(texts)

    def brief(self, root: Path | None = None) -> str | None:
        """The product's `BRIEF.md`, or None when it has not written one yet
        (ADR-0025). No system default: unlike `standards`, absent is a fact `cli.py`
        prints, never a fallback text a run reads instead."""
        return _read((root or self.product_dir) / knowledge.BRIEF)

    def _standards_text(self, root: Path) -> str:
        """The product's `CODING_STANDARDS.md`, or the system's default (ADR-0012)."""
        text = _read(root / knowledge.STANDARDS)
        return text if text is not None else knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")

    def standards(self, root: Path | None = None, whole: bool = True) -> str:
        """The engineering standards, whole when the caller is a verifier and through the
        index otherwise: the sections marked as overriding everything inline, and a line
        pointing at the file that lists the rest."""
        found = knowledge_index.build_index(
            {}, {}, {}, None, self._standards_text(root or self.product_dir), standards_whole=whole
        )
        if whole:
            return found.standards
        parts = [part for part in (found.always, found.areas) if part]
        return knowledge.STANDARDS_HEADING + "\n\n" + "\n\n".join(parts) if parts else ""

    def consolidate(self, verdict: dict[str, Any], root: Path | None = None) -> list[Path]:
        """What an approved verdict leaves: the capability files, then the glossary.
        All or nothing."""
        definitions, terms = knowledge.consolidation(verdict)
        root = root or self.product_dir
        writes = self._definitions(definitions, root) | self._glossary(terms, root)
        _write_all(writes)
        return list(writes)

    def write_definitions(self, definitions: dict[str, str]) -> list[Path]:
        """Replace each capability file whole. Creates `docs/product/` on first use."""
        writes = self._definitions(definitions, self.product_dir)
        _write_all(writes)
        return list(writes)

    def upsert_terms(self, terms: dict[str, dict[str, str]]) -> Path | None:
        """Returns the glossary path written, or None when there was nothing to write.
        A mapped product may write several; the last one is returned, and the caller
        that cares reads `contexts` itself."""
        writes = self._glossary(terms, self.product_dir)
        _write_all(writes)
        return list(writes)[-1] if writes else None

    def _definitions(self, definitions: dict[str, str], root: Path) -> dict[Path, str]:
        for slug in definitions:
            knowledge.check_slug(slug)
        return {
            root / knowledge.PRODUCT_SUBDIR / f"{slug}.md": content.strip() + "\n"
            for slug, content in definitions.items()
        }

    def _glossary(self, terms: dict[str, dict[str, str]], root: Path) -> dict[Path, str]:
        """One write per bounded context the terms belong to. A term belongs to a
        context, not to a file: a task that changes behaviour across modules leaves
        terms in each of their glossaries, never all in one (issue #45)."""
        if not terms:
            return {}
        name = self.product_dir.name  # the product's, never the task checkout's
        if not (contexts := self.contexts(root)):
            glossary = root / knowledge.GLOSSARY
            return {glossary: knowledge.upsert_terms(_read(glossary), terms, name)}
        # The check already refused a term without a context or with an unknown one
        # (`approval_check`); this is the same rule at the only other way in.
        knowledge.check_contexts([{**e, "term": t} for t, e in terms.items()], contexts)
        by_path: dict[Path, dict[str, dict[str, str]]] = {}
        for term, entry in terms.items():
            path = root / knowledge.context_path(entry["context"].strip(), contexts)
            by_path.setdefault(path, {})[term] = entry
        return {
            path: knowledge.upsert_terms(_read(path), found, name)
            for path, found in by_path.items()
        }


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _read_dir(directory: Path) -> dict[str, str]:
    """The `.md` files directly under `directory`, by name. Empty if it does not exist."""
    return {path.name: path.read_text(encoding="utf-8") for path in directory.glob("*.md")}


def _write_all(writes: dict[Path, str]) -> None:
    """Write every file, or restore every file: a failure halfway puts back what each
    target held before (or removes it, if it did not exist)."""
    before = {path: _read(path) for path in writes}
    written: list[Path] = []
    try:
        for path, text in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)
    except OSError:
        for path in written:
            content = before[path]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content, encoding="utf-8")
        raise
