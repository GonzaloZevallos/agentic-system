"""The single per-task document: YAML frontmatter plus Markdown sections.

PjM owns the frontmatter, which is what gets processed mechanically. Roles own
the sections, which are read by humans and by the next role.
"""
from __future__ import annotations

from pathlib import Path

import yaml

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"


def write(state: dict) -> Path:
    frontmatter = {
        "id": state["id"],
        "title": state["title"],
        "type": state.get("type"),
        "status": state.get("status", "open"),
        "current_role": state.get("current_role"),
        "flags": state.get("flags", {}),
        "escalated": state.get("escalated"),
        "cost_usd": round(state.get("cost_usd", 0.0), 4),
    }

    parts = [
        "---",
        yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# {state['title']}\n",
        f"## Original request\n\n{state['description']}\n",
    ]
    for role, text in state.get("sections", {}).items():
        parts.append(f"## {role}\n\n{text}\n")

    TASKS_DIR.mkdir(exist_ok=True)
    path = TASKS_DIR / f"{state['id']}.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
