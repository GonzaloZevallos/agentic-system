"""The product directory as the store of everything the system writes (ADR-0002)."""

from .documents import FilesystemTaskDocuments
from .flags import FilesystemFlags
from .knowledge import FilesystemKnowledge
from .prompts import FilesystemPrompts

__all__ = [
    "FilesystemFlags",
    "FilesystemKnowledge",
    "FilesystemPrompts",
    "FilesystemTaskDocuments",
]
