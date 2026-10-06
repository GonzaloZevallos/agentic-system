"""The automatic merge of one delivered task's pull request (GH-178, ADR-0045): what
`agentic merge --issue N` does once it holds the task's lock.

Every condition of the merge is `rules.auto_merge_decision`'s, asked before anything is
run and again after the checks; this module gathers the facts the decision reads and
carries out its answer. The checks run through the existing `CheckoutPresubmit` in a
throwaway worktree under `.agentic/worktrees/` holding the pull request's head with the
fetched upstream merged into it: never in the product checkout's working tree, and
nothing here writes in stage0. The merge call runs only after that subprocess exited."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import PullRequest, TaskState
from .checkouts import task_base
from .poll import AUTO_MERGE_REFUSED, AUTO_MERGED, _merge_facts
from .ports import GitError, Ports, Upstream
from .presubmit import CheckoutPresubmit

# The refusals a human can act on, and so the only ones commented, once per (head, reason).
COMMENTED: tuple[str, ...] = (
    "checks_failed",
    "guarded_path",
    "no_merge_method",
    "eval_set_unreadable",
    "attempts_exhausted",
)

# Nothing was committed before the merge worktree existed: every head differs from it, so
# `CheckoutPresubmit` runs the checks even when the upstream was already in the head.
_NOTHING_BEFORE = ""


@dataclass(frozen=True)
class MergeResult:
    """`merged`: the pull request is merged (or ambiguously so: the intent stays);
    `refused`: left to the human, `reason` says why; `stopped`: nothing could be decided,
    `reason` says what failed."""

    kind: Literal["merged", "refused", "stopped"]
    reason: str | None = None
    method: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def merge_task(
    task_id: str,
    number: int,
    cfg: Configuration,
    ports: Ports,
    upstream: Upstream,
    eval_set: frozenset[int] | None,
    log: Callable[[str], None],
    clock: Callable[[], str] = _now,
) -> MergeResult:
    raw = ports.documents.load(task_id)
    reference = raw.get("pull_request") if raw else None
    if raw is None or not reference:
        return MergeResult("stopped", "no_pull_request")
    ports.workspace.prune()
    ports.workspace.remove_checkout(_name(task_id))
    master = upstream.sha
    if master is None:
        return MergeResult("stopped", "no_upstream")
    try:
        pull_request = ports.tracker.pull_request(reference)
    except (ValueError, OSError) as exc:
        return MergeResult("stopped", f"tracker_error: {exc}")
    head = pull_request.head_oid if pull_request is not None else None
    base = task_base(raw, ports)
    touched: tuple[str, ...] | None = None
    if head is not None and ports.workspace.fetch_commit(head):
        try:
            touched = ports.workspace.changed_paths(base, head)
        except GitError as exc:
            return MergeResult("stopped", f"diff_unreadable: {exc}")
    elif head is not None:
        return MergeResult("stopped", f"head_unreachable: {head}")
    facts = _facts(task_id, raw, pull_request, eval_set, number, master, touched)
    decision = rules.auto_merge_decision(facts)
    if decision.kind == "run_checks":
        assert head is not None  # a decision never asks for checks without a head
        _run_checks(task_id, raw, head, master, base, cfg, ports, facts.attempts + 1, log)
        raw = ports.documents.load(task_id) or raw
        facts = _facts(task_id, raw, pull_request, eval_set, number, master, touched)
        decision = rules.auto_merge_decision(facts)
    if decision.kind == "refuse":
        assert decision.reason is not None
        return _refuse(task_id, head, decision.reason, ports)
    if decision.kind != "merge" or head is None:
        return MergeResult("stopped", "no_decision")
    return _merge(task_id, number, str(reference), head, ports, facts, clock, log)


def _name(task_id: str) -> str:
    return f"merge-{task_id}"


def _facts(
    task_id: str,
    raw: TaskState,
    pull_request: PullRequest | None,
    eval_set: frozenset[int] | None,
    number: int,
    master: str,
    touched: tuple[str, ...] | None,
) -> rules.MergeFacts:
    return replace(
        _merge_facts(task_id, raw, pull_request, eval_set, number, master),
        touched_paths=touched,
    )


def _run_checks(
    task_id: str,
    raw: TaskState,
    head: str,
    master: str,
    base: str,
    cfg: Configuration,
    ports: Ports,
    attempt: int,
    log: Callable[[str], None],
) -> None:
    """The checks at the task's base, on the pull request's head merged with `master`,
    and the record of both (`merge_attempts`, `merge_checks`). The worktree is removed
    whatever happens; a merge that conflicts, a setup step that fails or a tree the
    checks could not run in all count as the checks failing."""
    ports.documents.record_closure(task_id, {"merge_attempts": {"head": head, "count": attempt}})
    started = time.monotonic()
    passed = False
    name = _name(task_id)
    try:
        path = ports.workspace.detached_checkout(name, head)
        conflict = ports.workspace.merge(path, master)
        if conflict is not None:
            log(f"agentic: merge: {task_id}: {head} does not merge with {master}")
        elif _setup(base, path, cfg, ports):
            checks = ports.stack.checks(base)
            gate = CheckoutPresubmit(
                ports,
                path,
                _NOTHING_BEFORE,
                checks,
                cfg.check_timeout_s,
                witness=ports.stack.witness(base),
                parallel=ports.stack.parallel_checks(base),
            )
            gate()
            # No declared check proves nothing: it cannot pass.
            passed = (
                bool(checks)
                and gate.results is not None
                and all(result["passed"] for result in gate.results)
            )
    except GitError as exc:
        log(f"agentic: merge: {task_id}: could not build the worktree: {exc}")
    finally:
        ports.workspace.remove_checkout(name)
    record = {
        "head": head,
        "master": master,
        "passed": passed,
        "duration_s": round(time.monotonic() - started, 1),
    }
    ports.documents.record_closure(task_id, {"merge_checks": record})


def _setup(base: str, path: Path, cfg: Configuration, ports: Ports) -> bool:
    for command in ports.stack.setup(base).values():
        if not ports.commands.run(path, command, cfg.check_timeout_s).passed:
            return False
    return True


def _merge(
    task_id: str,
    number: int,
    reference: str,
    head: str,
    ports: Ports,
    facts: rules.MergeFacts,
    clock: Callable[[], str],
    log: Callable[[str], None],
) -> MergeResult:
    master = facts.checks_master
    assert master is not None  # a `merge` decision rests on recorded checks
    # The last look at master: a push since the checks makes them stale, and nothing is
    # written (the next round decides again).
    again = ports.workspace.upstream()
    if again.sha != master:
        log(f"agentic: merge: {task_id}: master moved since the checks ({master} -> {again.sha})")
        return MergeResult("refused", "master_moved")
    # The intent, immediately before the merge call: after a crash the document still
    # shows the system meant to merge this head (`delivery_outcome` reads it).
    ports.documents.record_closure(
        task_id,
        {"auto_merge_head": head, "auto_merge_master": master, "auto_merge_at": clock()},
    )
    try:
        method = ports.tracker.merge(number, head)
    except ValueError as exc:
        return _after_failure(task_id, reference, head, str(exc), ports, log)
    ports.documents.record_closure(
        task_id, {"auto_merged_at": clock(), "auto_merge_method": method}
    )
    ports.tracker.comment(task_id, f"{AUTO_MERGED}{head} via {method}")
    return MergeResult("merged", method=method)


def _after_failure(
    task_id: str,
    reference: str,
    head: str,
    reason: str,
    ports: Ports,
    log: Callable[[str], None],
) -> MergeResult:
    """`Tracker.merge` raised. The pull request is read again: still `OPEN` means the
    merge did not happen, so the intent is withdrawn (a later human merge of this head is
    the human's); `MERGED` keeps it; anything else, or a read that fails, keeps it and
    says so."""
    try:
        current = ports.tracker.pull_request(reference)
    except (ValueError, OSError) as exc:
        log(
            f"agentic: merge: {task_id}: merge failed ({reason}), pull request "
            f"unreadable ({exc}): intent kept"
        )
        return MergeResult("stopped", "ambiguous")
    if current is not None and current.state == "OPEN":
        ports.documents.record_closure(task_id, {"auto_merge_head": ""})
        return _refuse(task_id, head, reason, ports)
    if current is not None and current.state == "MERGED":
        return MergeResult("merged", reason)
    log(f"agentic: merge: {task_id}: merge failed ({reason}), pull request not open: intent kept")
    return MergeResult("stopped", "ambiguous")


def _refuse(task_id: str, head: str | None, reason: str, ports: Ports) -> MergeResult:
    """Comments a refusal the human can act on, once per (head, reason): the last
    `Auto-merge refused:` comment naming the same head and reason is enough."""
    if reason in COMMENTED and head is not None:
        first = f"{AUTO_MERGE_REFUSED}{head} {reason}"
        refused = [
            body
            for body in ports.tracker.issue_comments(task_id)
            if body.startswith(AUTO_MERGE_REFUSED)
        ]
        if not refused or refused[-1].splitlines()[0] != first:
            ports.tracker.comment(task_id, f"{first}\n\nLeft for the human to merge.")
    return MergeResult("refused", reason)
