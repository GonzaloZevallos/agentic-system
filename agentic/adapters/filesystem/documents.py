"""`TaskDocuments` over `.agentic/tasks/` of the product repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ...domain import document
from ...domain.task import TaskState


class FilesystemTaskDocuments:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def path_for(self, task_id: str) -> Path:
        return self.product_dir / document.relative_path(task_id)

    def save(self, state: TaskState) -> Path:
        path = self.path_for(state["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document.render(state, system_commit()), encoding="utf-8")
        return path

    def load(self, task_id: str) -> dict:
        path = self.path_for(task_id)
        return document.parse(path.read_text(encoding="utf-8"), path)


def system_commit() -> str | None:
    """Short commit of this repo, for the prompt version record (ADR-0005). Best effort."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None
