"""The relabel net on a resume (GH-591, part 2; glossary: Etiqueta del dueño).

When a task resumes, the owner's labels on its issue are read again and `task_path.relabel`
decides what they change: `path:full` promotes with `from_role: owner`, `path:light` never
demotes, an unreadable label set (`None`) changes nothing. Serialized: the pause node's resume
step is the only caller. The notice `relabel` returns when it applied nothing goes next to the
other path notes, in PjM's section."""

from __future__ import annotations

from ..domain import task_path
from ..domain.task import TaskState
from .classify import owner_labels
from .ports import Ports

SECTION = "PjM"


def relabelled(state: TaskState, ports: Ports) -> TaskState:
    """The patch a resume adds for the owner's labels as they are now; `{}` when nothing changes."""
    patch, note = task_path.relabel(state, owner_labels(state, ports), ports.now())
    result: TaskState = dict(patch)  # type: ignore[assignment]
    if note:
        text = state.get("sections", {}).get(SECTION, "").rstrip()
        result["sections"] = {SECTION: f"{text}\n\n{note}" if text else note}
    return result
