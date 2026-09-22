"""The single per-task document: YAML frontmatter plus one Markdown section per role.

PjM owns the frontmatter, which is what gets processed mechanically. Roles own
the sections. The file lives in `.agentic/tasks/` of the product repo; the GitHub
issue is derived from it (ADR-0001). This module renders and parses the text; the
`TaskDocuments` port puts it on disk.

Layout: `## <section>` headings at column 0 delimit sections. Roles are told to
use `###` and below inside their own section, so the split is unambiguous.

The document is the raw record of the task (ADR-0006): a delivery superseded by a
return keeps its section as `## <role> (vN)`, written right before the current
one, and the frontmatter lists every run with its Claude Code session id.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .task import RoleRun

TASKS_SUBDIR = Path(".agentic/tasks")
ORIGINAL = "Original request"
# What the human decided on each escalation, written by the system on a resume (ADR-0003).
HUMAN = "Human"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^## (.+?)\s*$", re.M)
_VERSION = re.compile(r"^(.*) \(v(\d+)\)$")
_HEADING = re.compile(r"^(#{2,})(?=\s)", re.M)


def demote(text: str) -> str:
    """Push `##` and deeper headings one level down, so a request pasted from an issue
    cannot open sections of its own."""
    return _HEADING.sub(r"#\1", text)


def relative_path(task_id: str) -> Path:
    """Where the document lives, relative to the product root."""
    return TASKS_SUBDIR / f"{task_id}.md"


# Frontmatter keys that do not follow the domain's names: a document already written
# is data, and renaming its keys would make a paused task resume with an empty count
# (`resume.from_document` reads the frontmatter back into the state). The same rule the
# metrics follow (ADR-0008, addendum; ADR-0018, and issue #53). State field -> key.
RECORDED_AS = {"changes_requested": "returns", "change_history": "return_history"}
SUBTASK_RECORDED_AS = {"change_category": "return_category"}


def _recorded(subtask: dict) -> dict:
    return {SUBTASK_RECORDED_AS.get(k, k): v for k, v in subtask.items()}


def from_frontmatter(data: dict) -> dict:
    """The inverse of what `render` writes: the frontmatter under the state's names."""
    inverse = {key: field for field, key in RECORDED_AS.items()}
    state = {inverse.get(k, k): v for k, v in data.items()}
    subtask_inverse = {key: field for field, key in SUBTASK_RECORDED_AS.items()}
    if isinstance(subtasks := state.get("subtasks"), dict):
        state["subtasks"] = {
            sid: {subtask_inverse.get(k, k): v for k, v in sub.items()}
            for sid, sub in subtasks.items()
        }
    return state


def render(
    state: dict,
    system_commit: str | None = None,
    plugins: dict[str, str] | None = None,
    system_version: str | None = None,
) -> str:
    """The document text. `system_commit` is the system's commit, for the prompt
    version record (ADR-0005), and `plugins` the skills plugin version (ADR-0013); the
    port that knows the disk supplies both. `system_version` is the joinable version
    label (`telemetry.system_version`, the one place that computes it): written as its
    own top-level key only when a producer handed one in, never as an empty string or a
    null -- a document written before this task, or by a bare-constructed adapter, gets
    today's document plus nothing."""
    frontmatter = {
        "id": state["id"],
        "title": state["title"],
        "type": state.get("type"),
        "status": state.get("status", "open"),
        "current_role": state.get("current_role"),
        "flags": state.get("flags", {}),
        # Written under their old names on purpose: see `RECORDED_AS`.
        "returns": state.get("changes_requested", {}),
        "return_history": state.get("change_history", []),
        "escalated": state.get("escalated"),
        "escalation_class": state.get("escalation_class"),
        "questions_for_human": state.get("questions_for_human", []),
        "proposed_invariants": state.get("proposed_invariants", []),
        "discard_reason": state.get("discard_reason"),
        "branch": state.get("branch"),
        "pull_request": state.get("pull_request"),
        "subtasks": {sid: _recorded(sub) for sid, sub in state.get("subtasks", {}).items()},
        "invariants": state.get("invariants", []),
        "integration_brief": state.get("integration_brief", ""),
        # Every finding of every review, raw: the `Tech Lead: findings` section below is
        # the folded, open half of this, which is what the next role reads (issue #42).
        "findings": state.get("findings", []),
        "settled_findings": state.get("settled_findings", []),
        "cost_usd": round(state.get("cost_usd", 0.0), 4),
        "prompts": state.get("prompts", {}),
        "plugins": plugins or {},
        "system_commit": system_commit,
        **({"system_version": system_version} if system_version else {}),
        "runs": [_rounded_run(r) for r in state.get("runs", [])],
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
    return "\n".join(parts)


def _rounded_run(run: RoleRun) -> dict:
    """Money in the frontmatter to four decimals. A run recorded before the split by
    model has no `cost_by_model`, and one recorded before this task has no
    `subagent_cost_usd`; both stay without one."""
    rounded = dict(run, cost_usd=round(run["cost_usd"], 4))
    if "cost_by_model" in run:
        rounded["cost_by_model"] = {m: round(c, 4) for m, c in run["cost_by_model"].items()}
    if "subagent_cost_usd" in run:
        rounded["subagent_cost_usd"] = round(run["subagent_cost_usd"], 4)
    if "failed_cost_usd" in run:
        rounded["failed_cost_usd"] = round(run["failed_cost_usd"], 4)
    return rounded


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


def record_closure(raw: str, values: dict[str, str]) -> str:
    """Line-level surgery on the frontmatter block: replace the line of a top-level
    scalar key `values` owns, append when it is absent, everything else -- the body
    included -- byte-for-byte. A `safe_load`/`safe_dump` round-trip of the whole
    document is rejected on purpose: it reformats floats, quoting and block style of
    keys nobody asked it to touch, which is what would happen to a finished Corrida's
    `runs` and `failures` on every close.

    Raises `ValueError` when the frontmatter block cannot be found, or when a key
    `values` must replace is not written as a single scalar line (a block value, which
    this surgery cannot safely overwrite in place)."""
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError("record_closure: missing frontmatter")
    lines = match.group(1).split("\n")
    pending = dict(values)
    for i, line in enumerate(lines):
        for key in list(pending):
            if not line.startswith(f"{key}:"):
                continue
            if i + 1 < len(lines) and _is_continuation(lines[i + 1]):
                raise ValueError(f"record_closure: {key!r} is not a single scalar line")
            lines[i] = _scalar_line(key, pending.pop(key))
            break
    lines.extend(_scalar_line(key, value) for key, value in pending.items())
    block = "\n".join(lines)
    return raw[: match.start(1)] + block + raw[match.end(1) :]


def _is_continuation(line: str) -> bool:
    return line[:1] in (" ", "\t")


def _scalar_line(key: str, value: str) -> str:
    dumped = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    line, _, rest = dumped.partition("\n")
    if rest.strip():
        raise ValueError(f"record_closure: {key!r} value is not a single scalar line")
    return line


def parse(raw: str, source: object = "document") -> dict:
    """Inverse of `render`: frontmatter fields plus `description` and `sections`."""
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"{source}: missing frontmatter")
    data = from_frontmatter(yaml.safe_load(match.group(1)) or {})
    body = raw[match.end() :]

    sections: dict[str, str] = {}
    headings = list(_SECTION.finditer(body))
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        sections[heading.group(1)] = body[heading.end() : end].strip()

    data["description"] = sections.pop(ORIGINAL, "")
    data["sections"] = sections
    return data
