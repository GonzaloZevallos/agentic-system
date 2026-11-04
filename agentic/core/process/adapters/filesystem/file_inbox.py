"""`Inbox` over one frozen entry file (GH-470): the task of an evaluation run.

The file holds the run's `id`, the `issue` it re-runs, the `base` it runs from and the
`title` and `body` frozen from the issue as it was. Nothing is asked of a tracker: the
task, its id and its issue come from the file, and the listings the stack review and the
landing read are empty. The driver writes the file and `cli` validates it against the
set's own entry rule (`evaluation.entry_refusal`) before this adapter is built; this
class only refuses a file it cannot read as that shape.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ...domain.task import TaskInput

# Where the driver puts a root's frozen entry: `<root>/.agentic/eval-input/<root name>.yaml`,
# the root name being the run's task id. `composition.default_ports` reads it by this
# convention, because the graph builds its ports per node and never sees `--issue-input`.
FROZEN_DIR = Path(".agentic") / "eval-input"


def frozen_input_path(root: Path) -> Path:
    return root / FROZEN_DIR / f"{root.resolve().name}.yaml"


class FileInbox:
    def __init__(self, path: Path):
        self.path = path
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"{path}: cannot be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping with id, issue, base, title and body")
        for key in ("id", "base", "title", "body"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{path}: expected a non-empty text `{key}`, got {value!r}")
        issue = data.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool):
            raise ValueError(f"{path}: expected an integer `issue`, got {issue!r}")
        self._id: str = data["id"]
        self._issue: int = issue
        self._base: str = data["base"]
        self._title: str = data["title"]
        self._body: str = data["body"]

    @property
    def issue(self) -> int:
        return self._issue

    @property
    def id(self) -> str:
        return self._id

    @property
    def entry(self) -> dict[str, object]:
        """The frozen entry as the set declares one (`issue`, `base`, `title`, `body`), for
        the caller to validate against the set's own rule."""
        return {
            "issue": self._issue,
            "base": self._base,
            "title": self._title,
            "body": self._body,
        }

    def task(self, number: int) -> TaskInput:
        if number != self._issue:
            raise ValueError(f"{self.path}: holds issue {self._issue}, not issue {number}")
        return {
            "id": self._id,
            "title": self._title,
            "description": self._body,
            "evaluation": {"issue": self._issue, "base": self._base, "ci": "local"},
        }

    def task_id(self, number: int) -> str:
        return self._id

    def issue_of(self, task_id: str) -> int | None:
        return self._issue if task_id == self._id else None

    def open_issues(self) -> list[tuple[str, str]]:
        return []

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        return []
