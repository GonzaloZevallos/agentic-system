"""`ProductKnowledge` (a `core.process` port) over `docs/product/` and `CONTEXT.md`
of the product repo.

A distillation is several files. Everything is computed and checked before the
first write, and if a write fails what was on disk is put back: PM's approval never
leaves the product half distilled.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...domain import knowledge


class FilesystemKnowledge:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    @property
    def glossary_path(self) -> Path:
        return self.product_dir / knowledge.GLOSSARY

    def approval_check(self, task_type: str) -> Callable[[dict], None]:
        return knowledge.approval_check(task_type)

    def clarification_check(self) -> Callable[[dict], None]:
        return knowledge.clarification_check

    def read(self, root: Path | None = None) -> str:
        root = root or self.product_dir
        capabilities = {
            path.name: path.read_text(encoding="utf-8")
            for path in (root / knowledge.PRODUCT_SUBDIR).glob("*.md")
        }
        return knowledge.assemble(_read(root / knowledge.GLOSSARY), capabilities)

    def standards(self, root: Path | None = None) -> str:
        """The product's `CODING_STANDARDS.md`, or the system's default (ADR-0012)."""
        text = _read((root or self.product_dir) / knowledge.STANDARDS)
        if text is None:
            text = knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")
        return knowledge.assemble_standards(text)

    def distill(self, verdict: dict, root: Path | None = None) -> list[Path]:
        """What an approved verdict leaves: the capability files, then the glossary.
        All or nothing."""
        definitions, terms = knowledge.distillation(verdict)
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
        """Returns the glossary path, or None when there was nothing to write."""
        writes = self._glossary(terms, self.product_dir)
        _write_all(writes)
        return self.glossary_path if writes else None

    def _definitions(self, definitions: dict[str, str], root: Path) -> dict[Path, str]:
        for slug in definitions:
            knowledge.check_slug(slug)
        return {
            root / knowledge.PRODUCT_SUBDIR / f"{slug}.md": content.strip() + "\n"
            for slug, content in definitions.items()
        }

    def _glossary(self, terms: dict[str, dict[str, str]], root: Path) -> dict[Path, str]:
        if not terms:
            return {}
        glossary = root / knowledge.GLOSSARY
        text = knowledge.upsert_terms(_read(glossary), terms, self.product_dir.name)
        return {glossary: text}


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


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
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(before[path], encoding="utf-8")
        raise
