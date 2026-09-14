"""The product repo as the store of its knowledge: `docs/product/`, `CONTEXT.md`
and `.agentic/flags.yaml` (ADR-0002, ADR-0006)."""

from .flags import FilesystemFlags
from .knowledge import FilesystemKnowledge

__all__ = ["FilesystemFlags", "FilesystemKnowledge"]
