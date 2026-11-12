"""The return limit and the clean restart (GH-593): pure, no I/O.

README §3: a pair that reaches its return limit does not escalate the first time. A Tech Lead→
Developer pair restarts its subtask once from a clean context with a rewritten brief, and the
restarted attempt has its own counter; only its second limit escalates. This module is the one
place that decides which of the three happens (`on_return_limit`), the one condition for a restart
(`eligible`), and the one constructor of its patch (`restart_patch`). `rules` re-exports them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from .roles import Pair, developer_section, pair_for
from .task import (
    INTEGRATION,
    ChangesRequested,
    FindingRecord,
    RestartSettledRow,
    Subtask,
    TaskState,
)

# Restarts one pair may use: the restarted attempt that also reaches its limit escalates.
RESTARTS_MAX = 1

ReturnLimit = Literal["count", "restart", "escalate"]

# The keys of a subtask a restart drops: everything the archived attempt's review and returns
# wrote. What identifies the subtask (id, title, brief, seams, depends_on, screens, branch, base)
# and `approved_once` stay.
_ATTEMPT_KEYS = frozenset(
    (
        "reviewed_head",
        "reviewed_head_carried_from",
        "reason",
        "change_category",
        "returned_by",
        "returned_with",
        "conflicts",
        "conflicted_paths",
    )
)


def restart_section(subtask_id: str) -> str:
    """The section a restart writes: the rewritten brief the restarted Developer is built from."""
    return f"Restart {subtask_id}"


def remaining_changes(state: TaskState, pair: Pair, limit: int) -> int:
    """Returns `pair` may still use before the limit decides."""
    return limit - state.get("changes_requested", {}).get(pair.key, 0)


def attempt(state: TaskState, pair: Pair) -> int:
    """Which attempt a return of `pair` belongs to: `2` once the pair restarted, `1` before.
    What a `change_history` entry carries (`ChangesRequested.attempt`) when it is above 1."""
    return 1 + state.get("restarts", {}).get(pair.key, 0)


def _approved_once(subtask: Subtask) -> bool:
    """Whether the subtask was ever approved. The flag is the fact; a document written before it
    existed is read the old way: `approved` now, or reviewed once and then returned by a verifier
    other than the review."""
    if subtask.get("approved_once") or subtask.get("status") == "approved":
        return True
    return bool(subtask.get("reviewed_head")) and subtask.get("returned_by", "review") != "review"


def eligible(state: TaskState, pair: Pair, subtask_id: str) -> bool:
    """Whether `pair` at its limit restarts `subtask_id` instead of escalating. The only place the
    condition is written: the pair is the subtask's own Tech Lead→Developer one (never the
    integration's), the subtask was never approved, nobody but the review returned it, no pull
    request exists yet, and the pair has restarts left."""
    if subtask_id == INTEGRATION or pair.key != pair_for(subtask_id).key:
        return False
    subtask = state.get("subtasks", {}).get(subtask_id)
    if subtask is None or _approved_once(subtask):
        return False
    if subtask.get("returned_by") not in (None, "review"):
        return False
    if state.get("pull_request"):
        return False
    return state.get("restarts", {}).get(pair.key, 0) < RESTARTS_MAX


def on_return_limit(
    state: TaskState, pair: Pair, limit: int, subtask_id: str | None = None
) -> ReturnLimit:
    """What a return of `pair` does: `count` while it has returns left, `restart` at the limit
    when `eligible`, `escalate` otherwise. Every module that used to compare a pair's returns with
    its limit asks this instead."""
    if remaining_changes(state, pair, limit) > 0:
        return "count"
    if subtask_id is not None and eligible(state, pair, subtask_id):
        return "restart"
    return "escalate"


def _rows(findings: Sequence[FindingRecord]) -> list[str]:
    return [f"- {f['location']}: {f['claim']} ({f['category']})" for f in findings]


def _repeated(findings: Sequence[FindingRecord], history: Sequence[ChangesRequested]) -> list[str]:
    """What came back more than once, categories first then files, each once in order seen."""
    categories = [h["category"] for h in history] + [f["category"] for f in findings]
    files = [f["location"].split(":", 1)[0] for f in findings]
    lines = [f"- category {c}" for c in dict.fromkeys(categories) if categories.count(c) > 1]
    lines += [f"- file {p}" for p in dict.fromkeys(files) if files.count(p) > 1]
    return lines


def brief(
    original: str,
    findings_first: Sequence[FindingRecord],
    findings_second: Sequence[FindingRecord],
    history: Sequence[ChangesRequested],
) -> str:
    """The rewritten brief of a restarted subtask: the original brief, both passes' findings (place,
    claim, category), the reasons of the two returns, and what did not work (the categories and
    files that came back). Deterministic, and it adds no role: same inputs, same text."""
    reasons = [f"- {h['reason']}" for h in history]
    repeated = _repeated([*findings_first, *findings_second], history)
    didnt = (
        "\n".join(repeated)
        if repeated
        else "- nothing repeated: the two passes found different things"
    )
    parts = [
        f"## Original brief\n\n{original}",
        "## First pass: findings\n\n" + ("\n".join(_rows(findings_first)) or "- none recorded"),
        "## Second pass: findings\n\n" + ("\n".join(_rows(findings_second)) or "- none recorded"),
        "## Why it was returned\n\n" + ("\n".join(reasons) or "- none recorded"),
        f"## What did not work\n\n{didnt}",
    ]
    return "\n\n".join(parts)


def _last_run(state: TaskState, section: str) -> int:
    """The `version` of the last run that wrote `section`, `0` when none did."""
    runs = state.get("runs", [])
    return max((r.get("version", 0) for r in runs if r.get("section") == section), default=0)


def restart_patch(
    state: TaskState, subtask_id: str, pair: Pair, brief_text: str, archived_head: str
) -> dict[str, Any]:
    """The one patch of a clean restart: the pair's `restarts` one up and its returns back to
    zero, the subtask as a new pending attempt (no review, no return, no head), its saved session
    forgotten, its open findings discarded as `restarted`, `restart_pending` for the archive and
    the dispatch to finish, and the rewritten brief as the `Restart <subtask>` section. Every
    restart goes through here, so none can leave the counters apart."""
    used = state.get("restarts", {}).get(pair.key, 0) + 1
    kept = state.get("subtasks", {}).get(subtask_id, {})
    fresh: dict[str, Any] = {k: v for k, v in kept.items() if k not in _ATTEMPT_KEYS}
    fresh["status"] = "pending"
    fresh["attempt"] = used + 1
    raised = [f["version"] for f in state.get("findings", []) if f["subtask"] == subtask_id]
    settled: RestartSettledRow = {
        "subtask": subtask_id,
        "version": 1 + max(raised, default=0),
        "outcome": "discarded",
        "reason": "restarted",
    }
    section = developer_section(subtask_id)
    return {
        "restarts": {pair.key: used},
        "changes_requested": {pair.key: 0},
        "subtasks": {subtask_id: fresh},
        "session_floor": {section: _last_run(state, section)},
        "restart_settled": [settled],
        "restart_pending": {"subtask": subtask_id, "archived_head": archived_head},
        "sections": {restart_section(subtask_id): brief_text},
    }


def archive_ref(branch: str, used: int, system_commit: str) -> str:
    """Where a restart parks the archived attempt's branch: `archive/restart<k>-<commit>/<branch>`.
    Not under `archive/run<N>-`, so `rules.next_archive_run` never counts it as a `--fresh` run."""
    return f"archive/restart{used}-{system_commit}/{branch}"


def restarted_fresh(subtask: Mapping[str, Any]) -> bool:
    """Whether the subtask's current attempt was born from a restart and has not delivered yet:
    the Developer it starts is built from the `Restart <subtask>` section alone."""
    return bool(subtask.get("attempt", 1) > 1 and subtask.get("status") == "pending")
