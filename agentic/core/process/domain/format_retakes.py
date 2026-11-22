"""The pure rules of the format retakes (GH-1019, part of GH-1011): no I/O.

A format failure is a `schema` or `check` refusal that carries its `RefusalRule`. The poller
will retake it `FORMAT_RETAKE_MAX` times per rule, within a backstop of `FORMAT_RETAKE_TASK_MAX`
for the task, and then hold it. This module reads the epoch's terminal failures (the output of
`rules.terminal_failures`, which the caller passes) and composes the only writes of the two
keys it owns, `format_retakes` and `format_hold`. Inert until part 2: nothing decides on them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .task import FormatHold, HoldCause, PauseEntry, RefusalRule, TaskState

FORMAT_REASONS = ("schema", "check")
FORMAT_RETAKE_MAX = 2
FORMAT_RETAKE_TASK_MAX = 6

_KEYS = ("format_retakes", "format_hold")


def is_format_epoch(entries: Sequence[PauseEntry]) -> bool:
    """Whether `entries` are a format failure and nothing else: at least one failure, each of a
    `FORMAT_REASONS` reason that carries a `rule`, and every escalation a plain `failure` one
    with no mechanical cause. A lapse passes through."""
    found = False
    for entry in entries:
        if entry["kind"] == "failure":
            if entry["value"] not in FORMAT_REASONS or "rule" not in entry:
                return False
            found = True
        elif entry["kind"] == "escalation":
            if entry["value"] != "failure" or entry.get("cause") is not None:
                return False
    return found


def _failure_rules(entries: Sequence[PauseEntry]) -> list[RefusalRule]:
    return [e["rule"] for e in entries if e["kind"] == "failure" and "rule" in e]


def format_rules(entries: Sequence[PauseEntry]) -> tuple[RefusalRule, ...]:
    """The distinct rules of a format epoch, in order of first appearance; `()` when `entries`
    are not one."""
    if not is_format_epoch(entries):
        return ()
    return tuple(dict.fromkeys(_failure_rules(entries)))


def limit(counts: Mapping[RefusalRule, int], rules: Sequence[RefusalRule]) -> int:
    """The retakes left: the least over `rules` of `FORMAT_RETAKE_MAX` less the retakes already
    made, also bounded by the task's backstop less all of them, never below 0. No rules, none."""
    if not rules:
        return 0
    per_rule = min(FORMAT_RETAKE_MAX - counts.get(rule, 0) for rule in rules)
    per_task = FORMAT_RETAKE_TASK_MAX - sum(counts.values())
    return max(0, min(per_rule, per_task))


def hold_due(counts: Mapping[RefusalRule, int], rules: Sequence[RefusalRule]) -> bool:
    """Whether no retake is left for a non-empty `rules`."""
    return bool(rules) and limit(counts, rules) == 0


def hold_cause(counts: Mapping[RefusalRule, int], rules: Sequence[RefusalRule]) -> HoldCause | None:
    """Why the hold is due: `rule_spent` when a rule is at its maximum (it wins), `task_spent`
    when only the backstop is, `None` when it is not due."""
    if any(counts.get(rule, 0) >= FORMAT_RETAKE_MAX for rule in rules):
        return "rule_spent"
    return "task_spent" if hold_due(counts, rules) else None


def held_rule(
    counts: Mapping[RefusalRule, int],
    rules: Sequence[RefusalRule],
    entries: Sequence[PauseEntry],
) -> RefusalRule | None:
    """The rule a hold names: the first spent one under `rule_spent`, the rule of the last
    terminal format failure of `entries` under `task_spent`, else `None`."""
    cause = hold_cause(counts, rules)
    if cause == "rule_spent":
        return next(rule for rule in rules if counts.get(rule, 0) >= FORMAT_RETAKE_MAX)
    if cause == "task_spent":
        failed = _failure_rules(entries)
        return failed[-1] if failed else None
    return None


def count_patch(state: TaskState, entries: Sequence[PauseEntry]) -> dict[str, Any]:
    """The patch that counts one retake for each rule of the epoch: the whole `format_retakes`
    map, the old one plus 1 each. `{}` when `entries` are not a format epoch."""
    rules = format_rules(entries)
    if not rules:
        return {}
    counts = dict(state.get("format_retakes", {}))
    for rule in rules:
        counts[rule] = counts.get(rule, 0) + 1
    return {"format_retakes": counts}


def hold_patch(
    state: TaskState,
    rules: Sequence[RefusalRule],
    issue_url: str | None,
    at: str,
    entries: Sequence[PauseEntry],
) -> dict[str, Any]:
    """The patch that holds the task: `{}` when a hold exists already (idempotent), when none is
    due or when no rule is to be named. No composer writes a partial hold: the value always
    carries `rule`, `cause` and `at`, and `issue_url` only when there is one."""
    counts = state.get("format_retakes", {})
    cause = hold_cause(counts, rules)
    rule = held_rule(counts, rules, entries)
    if state.get("format_hold") or cause is None or rule is None:
        return {}
    hold = FormatHold(rule=rule, cause=cause, at=at)
    if issue_url is not None:
        hold["issue_url"] = issue_url
    return {"format_hold": hold}


def noted_patch(state: TaskState, at: str) -> dict[str, Any]:
    """The patch that records the human was told: the whole hold, copied, with `noted_at`.
    `{}` without a hold."""
    hold = state.get("format_hold")
    if not hold:
        return {}
    noted: FormatHold = {**hold, "noted_at": at}
    return {"format_hold": noted}


def release_patch(state: TaskState) -> dict[str, Any]:
    """The patch that clears what a human's resume releases: `None` for each of the two keys
    the state carries, nothing for the others."""
    return {key: None for key in _KEYS if state.get(key) is not None}
