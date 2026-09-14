"""`Prompts` with the product overlay (ADR-0005): `.agentic/prompts/<slug>.md` in the
product repo wins over the system's default in `agentic/core/process/domain/prompts/`."""

from __future__ import annotations

from pathlib import Path

from ...domain.roles import PROMPTS_DIR, Role

OVERLAY_SUBDIR = Path(".agentic/prompts")


class FilesystemPrompts:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def raw(self, role: Role) -> tuple[str, Path]:
        source = self.product_dir / OVERLAY_SUBDIR / f"{role.slug}.md"
        if not source.exists():
            source = PROMPTS_DIR / f"{role.slug}.md"
        return source.read_text(encoding="utf-8"), source
