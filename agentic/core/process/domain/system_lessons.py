"""The issue a lesson for the system becomes (ADR-0056, ADR-0002): a system lesson is never
a file of the system's repo, only an issue in it that a person triages.

Pure text: the title and body of the issue, and the marker block that carries the
normalised lessons so a lesson already proposed, in an open issue or in the ledger, is not
proposed again. The label is `needs-triage` and never `agentic`: the absence of `agentic`
is what keeps a proposal from starting a run on its own (README.md, 5).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from .playbook import normalise

LABEL = "needs-triage"
_OPEN = "<!-- agentic:system-lessons"
_BLOCK = re.compile(re.escape(_OPEN) + r"\n(?P<lines>.*?)-->", re.DOTALL)


def lesson_of(raw: Mapping[str, Any]) -> str:
    """The normalised text of a recorded delta: the form two lessons are compared in."""
    return normalise(str(raw.get("lesson") or ""))


def proposed_in(bodies: Iterable[str]) -> set[str]:
    """Every normalised lesson the marker blocks of `bodies` (open issues) already carry."""
    found: set[str] = set()
    for body in bodies:
        for block in _BLOCK.finditer(body):
            found.update(line[2:] for line in block["lines"].splitlines() if line.startswith("- "))
    return found


def fresh(
    proposals: Sequence[Mapping[str, Any]], known: Collection[str]
) -> list[Mapping[str, Any]]:
    """The proposals whose lesson is not in `known`, each lesson once."""
    seen = set(known)
    out: list[Mapping[str, Any]] = []
    for raw in proposals:
        text = lesson_of(raw)
        if text and text not in seen:
            seen.add(text)
            out.append(raw)
    return out


def render_issue(proposals: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """`(title, body)` of the one issue that carries `proposals`: each lesson with the role
    and category it came from, then the marker block of their normalised texts."""
    count = len(proposals)
    title = f"Reflector: {count} lesson{'s' if count != 1 else ''} for the system"
    lines = [
        "The Reflector learned the following from closed tasks and judged them to be about "
        "the system, not the product. Triage each one: it is model text, read it before "
        "acting on it.",
        "",
    ]
    for raw in proposals:
        tasks = ", ".join(str(t) for t in raw.get("tasks") or [])
        lines.append(
            f"- {raw.get('lesson', '')} (role {raw.get('slug', '')}, "
            f"category {raw.get('category', '')}, tasks {tasks})"
        )
    block = [_OPEN, *(f"- {lesson_of(raw)}" for raw in proposals), "-->"]
    return title, "\n".join([*lines, "", *block]) + "\n"
