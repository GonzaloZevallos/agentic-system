"""Anti-corruption translation from the old Design Lead vocabulary to UI Lead
(2026-10-05 rename). Applied at the two places a persisted state enters the process --
a document (`document.from_frontmatter`) and a checkpoint snapshot (`cli._resume`) --
and never on write: `document.render`/`record_closure` write only the new names, and a
document already on disk keeps its old ones untouched.

The mapping lives only here, applied by exact string, never as a substring of prose:
a role's section TEXT, `escalated`, `Failure.error` and `prompts` are records of what
ran, not vocabulary, and are never rewritten.
"""

from __future__ import annotations

import re
from typing import Any

_NAMES: dict[str, str] = {
    "Design Lead": "UI Lead",
    "Design Lead: final comparisons": "UI Lead: final comparisons",
    "design_lead": "ui_lead",
    "design_lead_final": "ui_lead_final",
}

_VERSION = re.compile(r"^(.*)( \(v\d+\))$")


def _translate(name: str) -> str:
    return _NAMES.get(name, name)


def _translate_versioned(name: str) -> str:
    """A section name, with its `" (vN)"` suffix (`rules.sections_for`) kept, if any."""
    if match := _VERSION.match(name):
        return _translate(match.group(1)) + match.group(2)
    return _translate(name)


def _translate_pair(pair: str) -> str:
    """`A→B` or `A→B:sub` (`changes_requested`, `change_history[].pair`): each side of
    the arrow translated on its own, the subtask suffix left alone."""
    base, sep, subtask = pair.partition(":")
    by, arrow, to = base.partition("→")
    if not arrow:
        return pair
    translated = f"{_translate(by)}{arrow}{_translate(to)}"
    return f"{translated}{sep}{subtask}" if sep else translated


def _renamed_keys(
    mapping: dict[str, Any], translate: "Any", field: str, source: object
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        new_key = translate(key)
        if new_key != key and new_key in mapping:
            raise ValueError(f"{source}: {field} has both {key!r} and {new_key!r}")
        result[new_key] = value
    return result


def rename_legacy_roles(state: dict[str, Any], source: object = "document") -> dict[str, Any]:
    """`state` with every old Design Lead name translated to UI Lead, in the fields the
    rename plan names. Idempotent: a state with no old name is handed back equal to
    itself field by field. Raises `ValueError`, naming `source`, the field and both
    keys verbatim, when an old key and its new key are both present in `sections` or
    in `changes_requested` -- never summed, never picked.

    Never touches section TEXT, `escalated`, `failures[].error` or `prompts`: those are
    records of what ran, not vocabulary."""
    result = dict(state)

    if "sections" in state:
        result["sections"] = _renamed_keys(
            state["sections"], _translate_versioned, "sections", source
        )

    if "runs" in state:
        result["runs"] = [
            {
                **run,
                **({"section": _translate_versioned(run["section"])} if "section" in run else {}),
                **({"slug": _translate(run["slug"])} if "slug" in run else {}),
            }
            for run in state["runs"]
        ]

    if "failures" in state:
        result["failures"] = [
            {
                **f,
                **({"section": _translate_versioned(f["section"])} if "section" in f else {}),
                **({"slug": _translate(f["slug"])} if "slug" in f else {}),
            }
            for f in state["failures"]
        ]

    if "changes_requested" in state:
        result["changes_requested"] = _renamed_keys(
            state["changes_requested"], _translate_pair, "changes_requested", source
        )

    if "change_history" in state:
        result["change_history"] = [
            {**h, "pair": _translate_pair(h["pair"])} for h in state["change_history"]
        ]

    if "pause_log" in state:
        result["pause_log"] = [
            {**entry, "section": _translate_versioned(entry["section"])}
            if "section" in entry
            else dict(entry)
            for entry in state["pause_log"]
        ]

    if "current_role" in state and state["current_role"] is not None:
        result["current_role"] = _translate(state["current_role"])

    if "design_items" in state:
        result["design_items"] = [
            {**item, "role_slug": _translate(item["role_slug"])} for item in state["design_items"]
        ]

    return result


def has_legacy_roles(state: dict[str, Any]) -> bool:
    """Whether `state` still carries an old Design Lead name anywhere
    `rename_legacy_roles` looks. A collision `rename_legacy_roles` would raise on
    counts as carrying one too."""
    try:
        return rename_legacy_roles(state) != state
    except ValueError:
        return True
