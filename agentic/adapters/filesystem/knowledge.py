"""`ProductKnowledge` over `docs/product/` and `CONTEXT.md` of the product repo."""

from __future__ import annotations

from pathlib import Path

from ...domain import knowledge


class FilesystemKnowledge:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    @property
    def glossary_path(self) -> Path:
        return self.product_dir / knowledge.GLOSSARY

    def read(self) -> str:
        glossary = (
            self.glossary_path.read_text(encoding="utf-8") if self.glossary_path.is_file() else None
        )
        capabilities = {
            path.name: path.read_text(encoding="utf-8")
            for path in (self.product_dir / knowledge.PRODUCT_SUBDIR).glob("*.md")
        }
        return knowledge.assemble(glossary, capabilities)

    def write_definitions(self, definitions: dict[str, str]) -> list[Path]:
        """Replace each capability file whole. Creates `docs/product/` on first use."""
        for slug in definitions:
            knowledge.check_slug(slug)
        written = []
        for slug, content in definitions.items():
            path = self.product_dir / knowledge.PRODUCT_SUBDIR / f"{slug}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.strip() + "\n", encoding="utf-8")
            written.append(path)
        return written

    def upsert_terms(self, terms: dict[str, dict[str, str]]) -> Path | None:
        """Returns the glossary path, or None when there was nothing to write."""
        if not terms:
            return None
        current = (
            self.glossary_path.read_text(encoding="utf-8") if self.glossary_path.is_file() else None
        )
        self.glossary_path.write_text(
            knowledge.upsert_terms(current, terms, self.product_dir.name), encoding="utf-8"
        )
        return self.glossary_path
