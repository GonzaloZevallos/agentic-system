"""The single per-task document: YAML frontmatter plus one Markdown section per role.

PjM owns the frontmatter, which is what gets processed mechanically. Roles own
the sections. The file lives in `.agentic/tasks/` of the product repo and is
the source of truth; the GitHub issue is derived from it (ADR-0001).

Layout: `## <section>` headings at column 0 delimit sections. Roles are told to
use `###` and below inside their own section, so the split is unambiguous.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

TASKS_SUBDIR = Path(".agentic/tasks")
ORIGINAL = "Original request"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^## (.+?)\s*$", re.M)


def path_for(task_id: str, product_dir: Path) -> Path:
    return product_dir / TASKS_SUBDIR / f"{task_id}.md"


def write(state: dict, product_dir: Path) -> Path:
    frontmatter = {
        "id": state["id"],
        "title": state["title"],
        "type": state.get("type"),
        "status": state.get("status", "open"),
        "current_role": state.get("current_role"),
        "flags": state.get("flags", {}),
        "returns": state.get("returns", {}),
        "failures": len(state.get("failures", [])),
        "escalated": state.get("escalated"),
        "cost_usd": round(state.get("cost_usd", 0.0), 4),
        "prompts": state.get("prompts", {}),
        "system_commit": system_commit(),
    }

    parts = [
        "---",
        yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# {state['title']}\n",
        f"## {ORIGINAL}\n\n{state['description'].strip()}\n",
    ]
    for role, text in state.get("sections", {}).items():
        parts.append(f"## {role}\n\n{text.strip()}\n")

    path = path_for(state["id"], product_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def read(path: Path) -> dict:
    """Inverse of `write`: frontmatter fields plus `description` and `sections`."""
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"{path}: missing frontmatter")
    data = yaml.safe_load(match.group(1)) or {}
    body = raw[match.end() :]

    sections: dict[str, str] = {}
    headings = list(_SECTION.finditer(body))
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        sections[heading.group(1)] = body[heading.end() : end].strip()

    data["description"] = sections.pop(ORIGINAL, "")
    data["sections"] = sections
    return data


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
