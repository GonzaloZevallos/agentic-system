"""The product directory as the store of what the process writes: task documents
and prompt overlays (ADR-0002)."""

from .documents import FilesystemTaskDocuments
from .prompts import FilesystemPrompts

__all__ = ["FilesystemPrompts", "FilesystemTaskDocuments"]
