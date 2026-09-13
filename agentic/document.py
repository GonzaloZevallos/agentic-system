"""The single per-task document: YAML frontmatter plus one Markdown section per role.

PjM owns the frontmatter, which is what gets processed mechanically. Roles own
the sections. The file lives in `.agentic/tasks/` of the product repo and is
the source of truth; the GitHub issue is derived from it (ADR-0001).

Layout: `## <section>` headings at column 0 delimit sections. Roles are told to
use `###` and below inside their own section, so the split is unambiguous.

The document is the raw record of the task (ADR-0006): a delivery superseded by a
return keeps its section as `## <role> (vN)`, written right before the current
one, and the frontmatter lists every run with its Claude Code session id.
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
_VERSION = re.compile(r"^(.*) \(v(\d+)\)$")
_HEADING = re.compile(r"^(#{2,})(?=\s)", re.M)


def demote(text: str) -> str:
    """Push `##` and deeper headings one level down, so a request pasted from an issue
    cannot open sections of its own."""
    return _HEADING.sub(r"#\1", text)


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
        "escalated": state.get("escalated"),
        "cost_usd": round(state.get("cost_usd", 0.0), 4),
        "prompts": state.get("prompts", {}),
        "system_commit": system_commit(),
        "runs": [dict(r, cost_usd=round(r["cost_usd"], 4)) for r in state.get("runs", [])],
        "failures": [
            dict(f, cost_usd=round(f.get("cost_usd", 0.0), 4)) for f in state.get("failures", [])
        ],
    }

    parts = [
        "---",
        yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# {state['title']}\n",
        f"## {ORIGINAL}\n\n{demote(state['description'].strip())}\n",
    ]
    for name, text in ordered(state.get("sections", {})).items():
        parts.append(f"## {name}\n\n{text.strip()}\n")

    path = path_for(state["id"], product_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def ordered(sections: dict[str, str]) -> dict[str, str]:
    """Sections in delivery order: each role's `(vN)` versions, ascending, then its current."""
    versions: dict[str, list[tuple[int, str]]] = {}
    for name in sections:
        if match := _VERSION.match(name):
            versions.setdefault(match.group(1), []).append((int(match.group(2)), name))
    result: dict[str, str] = {}
    for name in sections:
        if _VERSION.match(name):
            continue
        for _, old in sorted(versions.pop(name, [])):
            result[old] = sections[old]
        result[name] = sections[name]
    for orphans in versions.values():  # a version without a current section: keep it anyway
        for _, old in sorted(orphans):
            result[old] = sections[old]
    return result


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
