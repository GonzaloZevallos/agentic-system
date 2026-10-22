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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .legacy_names import rename_legacy_roles
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


def relative_path(task_id: str, *, subdir: Path = TASKS_SUBDIR) -> Path:
    """Where the document lives, relative to the product root. `subdir` defaults to
    `TASKS_SUBDIR`, so every existing call site stays byte-identical; the stack
    review's own corpus (`.agentic/stack-reviews/`, ADR-0034) is the one caller that
    passes another, through `FilesystemTaskDocuments(subdir=...)`."""
    return subdir / f"{task_id}.md"


# Frontmatter keys that do not follow the domain's names: a document already written
# is data, and renaming its keys would make a paused task resume with an empty count
# (`resume.from_document` reads the frontmatter back into the state). The same rule the
# metrics follow (ADR-0008, addendum; ADR-0018, and issue #53). State field -> key.
RECORDED_AS = {"changes_requested": "returns", "change_history": "return_history"}
SUBTASK_RECORDED_AS = {"change_category": "return_category"}
# The keys whose block value `record_closure` may replace: the system's own record of a
# landing (GH-239), which `render` and `record_closure` both write as the one flow line
# `_flow_line` builds (GH-242). A document that still carries it as a block is migrated by
# the next write; every other key keeps the surgery guarantee. `pause_log` (a block) and
# `escalated` (a string yaml wrapped over lines) are the two the migration of a stranded
# diagnose rewrites in place (GH-257): the log whole, the escalation cleared.
# `landed_slices` is the landing's own list of slices it merged ahead of the task's pull
# request (GH-297): written whole through `record_closure` every time one lands.
RECORD_KEYS: frozenset[str] = frozenset({"landing", "pause_log", "escalated", "landed_slices"})
_LANDING_PLACEHOLDER = "<landing-flow-line>"
# The record keys `render` writes as one flow line, swapped in for a placeholder: a list the
# landing keeps about itself, which `record_closure` then replaces whole (GH-242, GH-297).
_FLOW_KEYS = ("landing", "landed_slices")


def _recorded(subtask: dict[str, Any]) -> dict[str, Any]:
    return {SUBTASK_RECORDED_AS.get(k, k): v for k, v in subtask.items()}


def from_frontmatter(data: dict[str, Any], source: object = "document") -> dict[str, Any]:
    """The inverse of what `render` writes: the frontmatter under the state's names,
    with the 2026-10-05 role-rename alias applied last (`legacy_names.
    rename_legacy_roles`), so a document written before that rename seeds the same
    sections, pairs and return counts under the new names. `source` is `parse`'s own,
    for the alias's `ValueError` alone; `render`/`record_closure` never call this with
    anything to translate the other way, by design (ADR-0040's role, renamed)."""
    inverse = {key: field for field, key in RECORDED_AS.items()}
    state = {inverse.get(k, k): v for k, v in data.items()}
    subtask_inverse = {key: field for field, key in SUBTASK_RECORDED_AS.items()}
    if isinstance(subtasks := state.get("subtasks"), dict):
        state["subtasks"] = {
            sid: {subtask_inverse.get(k, k): v for k, v in sub.items()}
            for sid, sub in subtasks.items()
        }
    return rename_legacy_roles(state, source)


def render(
    state: dict[str, Any],
    system_commit: str | None = None,
    plugins: dict[str, str] | None = None,
    system_version: str | None = None,
    *,
    system_baseline: str | None = None,
) -> str:
    """The document text. `system_commit` is the system's commit, for the prompt
    version record (ADR-0005), and `plugins` the skills plugin version (ADR-0013); the
    port that knows the disk supplies both. `system_version` is the joinable version
    label (`telemetry.system_version`, the one place that computes it): written as its
    own top-level key only when a producer handed one in, never as an empty string or a
    null -- a document written before this task, or by a bare-constructed adapter, gets
    today's document plus nothing. `system_baseline` (`telemetry.system_baseline`,
    ADR-0030) follows the exact same rule, one key beside the other, so `eval compare`
    (the next issue) never has to resolve a tag backwards from a commit. Keyword-only:
    four `str | None` version-record arguments in a row are indistinguishable
    positionally to a reader and to mypy alike, and a swap here would write a baseline
    where the version belongs with nothing to catch it."""
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
        # The remote sha a delivery replaced (GH-204), written only when set so a document
        # without a replace is byte-identical to one written before it.
        **(
            {"replaced_remote_tip": replaced}
            if (replaced := state.get("replaced_remote_tip"))
            else {}
        ),
        # Where the CI wait is measured from (GH-216): written only when a delivery set
        # them, so a document without them is byte-identical to one written before.
        **({"pushed_head": pushed} if (pushed := state.get("pushed_head")) else {}),
        **({"pushed_at": pushed_at} if (pushed_at := state.get("pushed_at")) else {}),
        # What the release reaped when the task closed (GH-282, ADR-0052), written only when
        # something was, so a document without one is byte-identical to one written before.
        **({"reaped_at_cleanup": r} if (r := state.get("reaped_at_cleanup")) else {}),
        # The landing's record (GH-239), written only when set so a document without one is
        # byte-identical to one written before.
        # A placeholder here, swapped for the flow line below (`_flow_line`).
        **({"landing": _LANDING_PLACEHOLDER} if state.get("landing") else {}),
        # The slices the landing merged ahead of the pull request (GH-297): the same shape.
        **({"landed_slices": _LANDING_PLACEHOLDER} if state.get("landed_slices") else {}),
        **({"adr_renumbered": r} if (r := state.get("adr_renumbered")) else {}),
        **({"auto_merge_guarded": g} if (g := state.get("auto_merge_guarded")) else {}),
        **({"followups_filed": True} if state.get("followups_filed") else {}),
        **({"landing_restarts": n} if (n := state.get("landing_restarts")) else {}),
        **({"landing_cost_carried": c} if (c := state.get("landing_cost_carried")) else {}),
        "subtasks": {sid: _recorded(sub) for sid, sub in state.get("subtasks", {}).items()},
        "invariants": state.get("invariants", []),
        "integration_brief": state.get("integration_brief", ""),
        # Every finding of every review, raw: the `Tech Lead: findings` section below is
        # the folded, open half of this, which is what the next role reads (issue #42).
        "findings": state.get("findings", []),
        "settled_findings": state.get("settled_findings", []),
        # What approvals settled (`rules.settled_on_approval`), only when non-empty: a
        # document without one is byte-identical to one written before GH-201. Under its
        # own name, never through `RECORDED_AS`.
        **({"approval_settled": approved} if (approved := state.get("approval_settled")) else {}),
        "cost_usd": round(state.get("cost_usd", 0.0), 4),
        # The plan's estimate and what follows from it (ADR-0054), written only when the
        # state carries them, so a document from before them is byte-identical.
        **({"estimate": e} if (e := state.get("estimate")) else {}),
        **({"estimate_revisions": e} if (e := state.get("estimate_revisions")) else {}),
        **({"proposed_estimate": e} if (e := state.get("proposed_estimate")) else {}),
        **({"actual_files": n} if (n := state.get("actual_files")) is not None else {}),
        # The partition of a plan that did not fit (ADR-0054), written only when set.
        **({"parts": e} if (e := state.get("parts")) else {}),
        **({"split_issues": e} if (e := state.get("split_issues")) else {}),
        **({"fit_waived": True} if state.get("fit_waived") else {}),
        "prompts": state.get("prompts", {}),
        # The playbook section hash each role first ran with, and any later one
        # (ADR-0043), written under their own names and only when non-empty, so a task
        # with no playbook is byte-identical to a document written before them.
        **({"playbooks": playbooks} if (playbooks := state.get("playbooks")) else {}),
        **({"playbooks_changed": changed} if (changed := state.get("playbooks_changed")) else {}),
        # The review-mode/ephemeral-arm switches this run ran under
        # (`shared.configuration.experiment_snapshot`), omitted entirely when empty --
        # the switches are at their default -- so a default-path document stays
        # byte-identical to one written before this task (issue #28).
        **({"experiment": experiment} if (experiment := state.get("experiment")) else {}),
        # Whether the request entered with a `## Decided` heading and so runs one-round
        # interviews (`rules.entering`): written only when true, so a document without it
        # is byte-identical to one written before the cap existed, and reads as not decided.
        **({"decided": True} if state.get("decided") else {}),
        "plugins": plugins or {},
        "system_commit": system_commit,
        **({"system_version": system_version} if system_version else {}),
        **({"system_baseline": system_baseline} if system_baseline else {}),
        "runs": [_rounded_run(r) for r in state.get("runs", [])],
        "failures": [
            dict(f, cost_usd=round(f.get("cost_usd", 0.0), 4)) for f in state.get("failures", [])
        ],
        # The poller's own append-only register (issue #137): written under its own
        # name, like `invariants` or `findings`, never renamed through `RECORDED_AS`.
        "pause_log": state.get("pause_log", []),
        # The design branch's own register (ADR-0040): written under its own name too,
        # never renamed through `RECORDED_AS`. Omitted would still read back as `[]`
        # through `.get`, but every other append-only register is always written, so
        # this one is too, for the same reason `findings` always is.
        "design_items": state.get("design_items", []),
    }

    dumped = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    for key in _FLOW_KEYS:
        if value := state.get(key):
            placeholder = yaml.safe_dump({key: _LANDING_PLACEHOLDER}).strip()
            dumped = dumped.replace(placeholder, _flow_line(key, value), 1)
    parts = [
        "---",
        dumped,
        "---",
        "",
        f"# {state['title']}\n",
        f"## {ORIGINAL}\n\n{demote(state['description'].strip())}\n",
    ]
    for name, text in ordered(state.get("sections", {})).items():
        parts.append(f"## {name}\n\n{text.strip()}\n")
    return "\n".join(parts)


def _rounded_run(run: RoleRun) -> dict[str, Any]:
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


def record_closure(raw: str, values: Mapping[str, object]) -> str:
    """Line-level surgery on the frontmatter block: replace the line of a top-level
    scalar key `values` owns, append when it is absent, everything else -- the body
    included -- byte-for-byte. A `safe_load`/`safe_dump` round-trip of the whole
    document is rejected on purpose: it reformats floats, quoting and block style of
    keys nobody asked it to touch, which is what would happen to a finished Corrida's
    `runs` and `failures` on every close.

    Raises `ValueError` when the frontmatter block cannot be found, or when a key
    `values` must replace is not written as a single scalar line (a block value, which
    this surgery cannot safely overwrite in place). The one exception is a key in
    `RECORD_KEYS`: its key line and every continuation line become the one flow line."""
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError("record_closure: missing frontmatter")
    lines = match.group(1).split("\n")
    pending = dict(values)
    i = 0
    while i < len(lines):
        for key in list(pending):
            if not lines[i].startswith(f"{key}:"):
                continue
            end = i + 1
            while end < len(lines) and _is_continuation(lines[end]):
                end += 1
            if end > i + 1 and key not in RECORD_KEYS:
                raise ValueError(f"record_closure: {key!r} is not a single scalar line")
            lines[i:end] = [_scalar_line(key, pending.pop(key))]
            break
        i += 1
    lines.extend(_scalar_line(key, value) for key, value in pending.items())
    block = "\n".join(lines)
    return raw[: match.start(1)] + block + raw[match.end(1) :]


def _is_continuation(line: str) -> bool:
    """Whether `line` continues the block value of the key on the line before it: an
    indented mapping or sequence entry, or a `- ` item at the same column as its own
    key -- the indentless block-sequence shape `yaml.safe_dump` gives `runs` and
    `failures` (no top-level frontmatter key starts with `-`, so this reads unambiguously
    as a continuation, never as the next key)."""
    return line[:1] in (" ", "\t") or line.startswith("-")


def _flow_line(key: str, value: Mapping[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    """A record the system keeps about itself (`merge_checks`, GH-178; `landing`, GH-239,
    a list of them) as one flow-style line, so it replaces in place like any scalar and
    parses back as a mapping or a list. The only place that chooses the shape, for
    `render` and `record_closure` alike (GH-242)."""
    plain = dict(value) if isinstance(value, Mapping) else list(value)
    flow = yaml.safe_dump(plain, default_flow_style=True, width=10**6).strip()
    return f"{key}: {flow}"


def _scalar_line(key: str, value: object) -> str:
    if isinstance(value, Mapping | list | tuple):
        return _flow_line(key, value)
    dumped: str = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    line, _, rest = dumped.partition("\n")
    if rest.strip():
        raise ValueError(f"record_closure: {key!r} value is not a single scalar line")
    return line


def parse(raw: str, source: object = "document") -> dict[str, Any]:
    """Inverse of `render`: frontmatter fields plus `description` and `sections`."""
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"{source}: missing frontmatter")
    body = raw[match.end() :]

    sections: dict[str, str] = {}
    headings = list(_SECTION.finditer(body))
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        sections[heading.group(1)] = body[heading.end() : end].strip()

    # `sections` rides along into `from_frontmatter` so its own alias application
    # (`legacy_names.rename_legacy_roles`) translates the section keys too, in the
    # same pass as `current_role`/`changes_requested`/etc: one call site, not two.
    raw_frontmatter = yaml.safe_load(match.group(1)) or {}
    raw_frontmatter["sections"] = sections
    data = from_frontmatter(raw_frontmatter, source)
    data["description"] = data["sections"].pop(ORIGINAL, "")
    return data
