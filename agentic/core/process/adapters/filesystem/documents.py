"""`TaskDocuments` over `.agentic/tasks/` of the product repo."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ...domain import document
from ...domain.task import TaskState
from .. import plugins


class FilesystemTaskDocuments:
    def __init__(self, product_dir: Path, system_version: Callable[[], str] | None = None):
        self.product_dir = product_dir
        # The joinable version label's producer (`telemetry.system_version`, wired by
        # `composition.default_ports`): injected rather than computed here, which is
        # what keeps one `git rev-parse` in the system. `None` -- a bare-constructed
        # adapter, the graph regression's own included -- writes today's document plus
        # nothing (`document.render`'s own docstring).
        self.system_version = system_version

    def path_for(self, task_id: str) -> Path:
        return self.product_dir / document.relative_path(task_id)

    def save(self, state: TaskState) -> Path:
        path = self.path_for(state["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        version = self.system_version() if self.system_version is not None else None
        text = document.render(state, system_commit(), plugins.version(), version)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, task_id: str) -> dict | None:
        path = self.path_for(task_id)
        if not path.is_file():
            return None
        return document.parse(path.read_text(encoding="utf-8"), path)

    def record_closure(self, task_id: str, values: dict[str, str]) -> Path:
        """Line-level edit of the closure keys into the document already on disk
        (`document.record_closure`'s own docstring): never through `save`, which would
        re-render `system_commit` from today's sha and `plugins` from today's submodule
        and falsify the provenance of a finished Corrida (ADR-0001)."""
        path = self.path_for(task_id)
        raw = path.read_text(encoding="utf-8")
        path.write_text(document.record_closure(raw, values), encoding="utf-8")
        return path

    def write_into(self, task_id: str, root: Path) -> Path | None:
        source = self.path_for(task_id)
        if not source.is_file():
            return None
        destination = root / document.relative_path(task_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return destination


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
