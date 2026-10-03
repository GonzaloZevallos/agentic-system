"""The product directory as the store of what the process writes: task documents,
prompt overlays (ADR-0002) and the run lock (Tech Lead plan, subtask `run-lock`)."""

from .documents import FilesystemTaskDocuments
from .lock import FilesystemRunLock
from .prompts import FilesystemPrompts
from .run_registry import FilesystemRunRegistry

__all__ = [
    "FilesystemPrompts",
    "FilesystemRunLock",
    "FilesystemRunRegistry",
    "FilesystemTaskDocuments",
]
