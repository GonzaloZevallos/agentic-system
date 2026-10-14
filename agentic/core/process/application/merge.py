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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentic.shared.configuration import Configuration

from ..domain import adr_numbers, ci, presubmit, rules
from ..domain.added_lines import AddedLines
from ..domain.task import PullRequest, TaskState
from .checkouts import task_base
from .poll import AUTO_MERGE_REFUSED, AUTO_MERGED, _merge_facts, renumber_records, with_ci
from .ports import CommandResult, GitError, LeaseLost, MergeCallFailed, Ports, Upstream
from .presubmit import CheckoutPresubmit

# The refusals a human can act on, and so the only ones commented, once per (head, reason).
COMMENTED: tuple[str, ...] = (
    "checks_failed",
    "guarded_path",
    "no_merge_method",
    "eval_set_unreadable",
    "attempts_exhausted",
    "merge_failed",
    "ci_failed",
    "ci_missing",
    "ci_unreadable",
    "adr_number_taken",
)

LEFT_TO_HUMAN = "Left for the human to merge."

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
    try:
        adr = _adr_facts(ports, touched, head, master)
    except GitError as exc:
        return MergeResult("stopped", f"adr_unreadable: {exc}")
    declared = ports.stack.ci(base)
    report: ci.CiReport | ci.CiUnreadable | None = None
    fresh: bool | None = None
    if declared:
        report = ports.tracker.ci_report(str(reference))
        if isinstance(report, ci.CiReport) and head is not None:
            fresh = ci.ci_fresh(report.base_sha, master, ports.workspace.contains(head, master))

    def decide(document: TaskState) -> rules.MergeFacts:
        base_facts = _facts(task_id, document, pull_request, eval_set, number, master, touched)
        base_facts = replace(base_facts, adr_collision=adr.collision, adr_plan=adr.plan)
        return with_ci(base_facts, document, declared, report, fresh)

    facts = decide(raw)
    decision = rules.auto_merge_decision(facts)
    if decision.kind == "wait":
        return MergeResult("stopped", "ci_wait")
    if decision.kind == "renumber":
        assert head is not None and adr.merge_base is not None  # a collision needs both
        return _renumber(task_id, head, adr.merge_base, facts.adr_plan, ports, log)
    if decision.kind == "run_checks":
        assert head is not None  # a decision never asks for checks without a head
        _run_checks(task_id, raw, head, master, base, cfg, ports, facts.attempts + 1, log)
        raw = ports.documents.load(task_id) or raw
        facts = decide(raw)
        decision = rules.auto_merge_decision(facts)
    if decision.kind == "refuse":
        assert decision.reason is not None
        if decision.reason in ("guarded_path", "adr_number_taken") and head is not None:
            _spend_attempts(task_id, raw, head, ports)
        failure = facts.merge_failure if decision.reason == "merge_failed" else None
        return _refuse(task_id, head, decision.reason, ports, failure=failure, facts=facts)
    if decision.kind != "merge" or head is None:
        return MergeResult("stopped", "no_decision")
    relied = _ci_links(report, declared) if _merges_on_ci(facts) else None
    return _merge(
        task_id, number, str(reference), head, ports, facts, clock, log, relied, bool(declared)
    )


@dataclass(frozen=True)
class _AdrFacts:
    """What the ADR directory says of this pull request against the upstream sha the verb
    holds (GH-229): the collision, the renumber it would make, and the merge-base it was
    measured from (None when the branch touches no ADR path and nothing was read)."""

    collision: adr_numbers.Collision = adr_numbers.Collision()
    plan: Mapping[str, str] = field(default_factory=dict)
    merge_base: str | None = None


def _adr_facts(
    ports: Ports, touched: tuple[str, ...] | None, head: str | None, master: str
) -> _AdrFacts:
    """Nothing but the diff already read decides whether to look: a branch that touches no
    `docs/adr/NNNN-*.md` path costs no further git call. Raises `GitError`."""
    if head is None or touched is None:
        return _AdrFacts()
    if not any(adr_numbers.adr_number(path) is not None for path in touched):
        return _AdrFacts()
    workspace = ports.workspace
    mb = workspace.merge_base(master, head)
    at_mb = workspace.paths_at(mb, adr_numbers.ADR_DIR)
    at_head = workspace.paths_at(head, adr_numbers.ADR_DIR)
    at_master = workspace.paths_at(master, adr_numbers.ADR_DIR)
    own = adr_numbers.own_adrs(at_mb, at_head)
    found = adr_numbers.collision(own, at_head, at_mb, at_master)
    plan = adr_numbers.renumber_plan(found.colliding, own, at_master) if found.colliding else {}
    return _AdrFacts(found, plan, mb)


def _renumber(
    task_id: str,
    head: str,
    merge_base: str,
    plan: Mapping[str, str],
    ports: Ports,
    log: Callable[[str], None],
) -> MergeResult:
    """Moves the ADRs of `plan` in a throwaway worktree at `head`, commits as the system and
    pushes to the task branch with a lease on `head` (GH-229, ADR-0048). Only a push that
    was accepted is recorded; a lost lease or any other failure writes nothing, comments
    nothing and spends no counter, and the next round decides again from fresh facts. No
    check and no merge in this call: the new head is the next round's."""
    name = _name(task_id)
    try:
        path = ports.workspace.detached_checkout(name, head)
        added = ports.workspace.added_lines(merge_base, head)
        changed, unrewritten = _apply_plan(path, plan, added)
        message = f"Renumber {_numbers(plan)} after master took the numbers (GH-229)"
        if ports.workspace.commit_paths(path, changed, message) is None:
            log(f"agentic: merge: {task_id}: renumber changed nothing")
            return MergeResult("stopped", "renumber_empty")
        pushed = ports.workspace.full_head(path)
        ports.workspace.push_commit(path, pushed, rules.task_branch(task_id), lease=head)
    except LeaseLost:
        log(f"agentic: merge: {task_id}: renumber not pushed, {head} is no longer the branch tip")
        return MergeResult("stopped", "renumber_lease_lost")
    except (GitError, OSError) as exc:
        log(f"agentic: merge: {task_id}: renumber not pushed: {exc}")
        return MergeResult("stopped", "renumber_failed")
    finally:
        ports.workspace.remove_checkout(name)
    record = {"from_head": head, "to_head": pushed, "map": dict(plan), "unrewritten": unrewritten}
    prior = [dict(row) for row in renumber_records(ports.documents.load(task_id) or {})]
    ports.documents.record_closure(task_id, {"adr_renumber": [*prior, record]})
    log(f"agentic: merge: {task_id}: renumbered {_numbers(plan)}, pushed {pushed}")
    return MergeResult("stopped", "renumbered")


def _numbers(plan: Mapping[str, str]) -> str:
    return ", ".join(
        f"{adr_numbers.adr_number(old):04d} -> {adr_numbers.adr_number(new):04d}"
        for old, new in plan.items()
    )


def _apply_plan(
    root: Path, plan: Mapping[str, str], added: AddedLines
) -> tuple[list[str], list[str]]:
    """Renames the planned files under `root` and rewrites, byte for byte elsewhere, only
    the lines in `added` that `adr_numbers.rewrite_line` changes. Returns the paths to commit
    and the `path:line` of the added lines that still name an old number."""
    changed: list[str] = []
    for old, new in plan.items():
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        (root / old).rename(root / new)
        changed += [old, new]
    seen: list[tuple[str, int, str]] = []
    for file, rows in added.lines.items():
        target = plan.get(file, file)
        full = root / target
        if not full.is_file():
            continue
        data = full.read_bytes().split(b"\n")
        edited = False
        for number, text in rows:
            if number > len(data):
                continue
            try:
                current = data[number - 1].decode("utf-8")
            except UnicodeDecodeError:
                seen.append((target, number, text))
                continue
            now = adr_numbers.rewrite_line(
                file,
                current,
                plan,
                added_whole=file in added.whole,
                is_renamed_file=file in plan,
            )
            if now != current:
                data[number - 1] = now.encode("utf-8")
                edited = True
            seen.append((target, number, now))
        if edited:
            full.write_bytes(b"\n".join(data))
            changed.append(target)
    return list(dict.fromkeys(changed)), adr_numbers.unrewritten(seen, plan)


def _merges_on_ci(facts: rules.MergeFacts) -> bool:
    """The `merge` rested on the CI alone: declared, green on this head and fresh. The
    same three facts `rules.auto_merge_decision` merges on before it looks at any local
    check."""
    return (
        bool(facts.ci_required)
        and facts.ci_verdict is not None
        and facts.ci_verdict.state == "green"
        and facts.ci_fresh is True
    )


def _ci_links(
    report: ci.CiReport | ci.CiUnreadable | None, declared: tuple[str, ...]
) -> list[dict[str, str]]:
    """The declared checks the merge relied on, each with the run link it was read at."""
    if not isinstance(report, ci.CiReport):
        return []
    return [
        {"name": check.name, "link": check.link}
        for check in report.checks
        if check.name in declared
    ]


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
    name = _name(task_id)
    failed_step: presubmit.FailedStep | None = None
    setup_failure: tuple[str, str] | None = None
    declared: dict[str, str] = {}
    gate: CheckoutPresubmit | None = None
    try:
        path = ports.workspace.detached_checkout(name, head)
        conflict = ports.workspace.merge(path, master)
        if conflict is not None:
            log(f"agentic: merge: {task_id}: {head} does not merge with {master}")
            failed_step = "conflict"
        elif (failed := _setup(base, path, cfg, ports)) is not None:
            step, result = failed
            setup_failure = (
                step,
                presubmit.said(result.output, result.timed_out, cfg.check_timeout_s),
            )
            failed_step = "setup"
        else:
            checks = declared = ports.stack.checks(base)
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
    except GitError as exc:
        log(f"agentic: merge: {task_id}: could not build the worktree: {exc}")
        failed_step = "worktree"
    finally:
        ports.workspace.remove_checkout(name)
    # No declared check proves nothing: it cannot pass; a witness that says the checks ran
    # against another checkout makes them prove nothing either (`merge_checks_record`).
    record = presubmit.merge_checks_record(
        head=head,
        master=master,
        duration_s=round(time.monotonic() - started, 1),
        declared=declared,
        results=gate.results if gate is not None else None,
        witness=gate.witness_result if gate is not None else None,
        failed_step=failed_step,
        setup_failure=setup_failure,
    )
    ports.documents.record_closure(task_id, {"merge_checks": record})
    shown = presubmit.failed_checks_markdown(record, head, neutralise=False)
    for number, line in enumerate(shown.splitlines()):
        log(f"agentic: merge: {task_id}: {line}" if number == 0 else f"    {line}")


def _spend_attempts(task_id: str, raw: TaskState, head: str, ports: Ports) -> None:
    """The poller cannot see a diff, so it would spawn this child for a guarded-path head
    every round. Spending the head's attempts makes `auto_merge_decision` itself leave the
    pull request alone (`attempts_exhausted`, a log line) until a push gives a new head."""
    recorded = raw.get("merge_attempts")
    count = (
        recorded.get("count", 0)
        if isinstance(recorded, dict) and recorded.get("head") == head
        else 0
    )
    if count < rules.MERGE_ATTEMPTS_MAX:
        ports.documents.record_closure(
            task_id, {"merge_attempts": {"head": head, "count": rules.MERGE_ATTEMPTS_MAX}}
        )


def _setup(
    base: str, path: Path, cfg: Configuration, ports: Ports
) -> tuple[str, CommandResult] | None:
    """None when every setup step passed, else the first one that did not, with what it
    said: the steps after it do not run."""
    for step, command in ports.stack.setup(base).items():
        result = ports.commands.run(path, command, cfg.check_timeout_s)
        if not result.passed:
            return step, result
    return None


def _merge(
    task_id: str,
    number: int,
    reference: str,
    head: str,
    ports: Ports,
    facts: rules.MergeFacts,
    clock: Callable[[], str],
    log: Callable[[str], None],
    relied: list[dict[str, str]] | None = None,
    ci_declared: bool = False,
) -> MergeResult:
    """`relied` is the CI links a merge on the CI rested on (None: it rested on the local
    checks); `ci_declared` says the product declares its CI, which is the only case that
    records how the merge was decided (`auto_merge_via`)."""
    # A `merge` rests on recorded checks, or on a fresh CI: either way on this master.
    master = facts.current_master
    assert master is not None
    # The last look at master: a push since the checks makes them stale, and nothing is
    # written (the next round decides again).
    again = ports.workspace.upstream()
    if again.sha != master:
        log(f"agentic: merge: {task_id}: master moved since the checks ({master} -> {again.sha})")
        return MergeResult("refused", "master_moved")
    # The intent, immediately before the merge call: after a crash the document still
    # shows the system meant to merge this head (`delivery_outcome` reads it). The method
    # is not part of it: it is written only after the merge returned (ADR-0045, addendum
    # 2026-10-06).
    intent: TaskState = {
        "auto_merge_head": head,
        "auto_merge_master": master,
        "auto_merge_at": clock(),
    }
    if relied is not None:
        intent["auto_merge_via"] = "ci"
        intent["auto_merge_ci"] = relied
    elif ci_declared:
        intent["auto_merge_via"] = "local"
    ports.documents.record_closure(task_id, intent)
    try:
        method = ports.tracker.merge(reference, head)
    except MergeCallFailed as exc:
        return _after_failure(task_id, reference, head, facts, exc.failure, str(exc), ports, log)
    except ValueError as exc:
        return _after_failure(
            task_id, reference, head, facts, "gh_unreadable", str(exc), ports, log
        )
    ports.documents.record_closure(
        task_id, {"auto_merged_at": clock(), "auto_merge_method": method}
    )
    text = f"{AUTO_MERGED}{head} via {method}"
    if relied is not None:
        text += "\n\nCI relied on:\n" + "\n".join(
            f"- {row['name']}: {row['link']}" for row in relied
        )
    ports.tracker.comment(task_id, text)
    return MergeResult("merged", method=method)


def _after_failure(
    task_id: str,
    reference: str,
    head: str,
    facts: rules.MergeFacts,
    failure: rules.MergeFailure,
    error: str,
    ports: Ports,
    log: Callable[[str], None],
) -> MergeResult:
    """`Tracker.merge` raised (GH-196). The failed call is counted first, before anything
    else is read: a terminal class spends the whole budget, any other one call of it. The
    pull request is then read again: still `OPEN` means the merge did not happen, so the
    intent is withdrawn (a later human merge of this head is the human's) and the decision
    itself says whether the budget is spent -- the comment is the last failure's, once;
    `MERGED` keeps the intent; anything else, or a read that fails, keeps it and says so.
    `error` is the raw text of the CLI and goes to `log` only."""
    count = (
        rules.MERGE_CALLS_MAX
        if failure in rules.TERMINAL_MERGE_FAILURES
        else facts.merge_failures + 1
    )
    ports.documents.record_closure(
        task_id, {"merge_failures": {"head": head, "count": count, "failure": failure}}
    )
    try:
        current = ports.tracker.pull_request(reference)
    except (ValueError, OSError) as exc:
        log(
            f"agentic: merge: {task_id}: merge failed ({failure}: {error}), pull request "
            f"unreadable ({exc}): intent kept"
        )
        return MergeResult("stopped", "ambiguous")
    if current is not None and current.state == "OPEN":
        ports.documents.record_closure(task_id, {"auto_merge_head": ""})
        decision = rules.auto_merge_decision(
            replace(facts, merge_failures=count, merge_failure=failure)
        )
        if decision.kind == "refuse" and decision.reason == "merge_failed":
            log(f"agentic: merge: {task_id}: merge failed ({failure}: {error}), budget spent")
            return _refuse(task_id, head, "merge_failed", ports, failure=failure)
        log(
            f"agentic: merge: {task_id}: merge failed ({failure}: {error}), "
            f"{count} of {rules.MERGE_CALLS_MAX}"
        )
        return MergeResult("refused", "merge_failed")
    if current is not None and current.state == "MERGED":
        return MergeResult("merged", failure)
    log(
        f"agentic: merge: {task_id}: merge failed ({failure}: {error}), "
        "pull request not open: intent kept"
    )
    return MergeResult("stopped", "ambiguous")


def _ci_evidence(reason: str, facts: rules.MergeFacts) -> str:
    """What a CI refusal says, from structured facts only: one line per failing check
    (name, conclusion, link) with no tail, the declared names never reported, or `gh` and
    the cause class of an unreadable report."""
    verdict = facts.ci_verdict
    if reason == "ci_failed" and verdict is not None:
        return "\n".join(
            f"- {check.name} {check.conclusion or check.status} {check.link}"
            for check in verdict.failing
        )
    if reason == "ci_missing" and verdict is not None:
        if verdict.missing:
            return f"CI did not report: {', '.join(verdict.missing)}"
        return f"CI checks still pending: {', '.join(facts.ci_required)}"
    cause = facts.ci_unreadable.cause if facts.ci_unreadable is not None else "gh_unreadable"
    return f"`gh` could not read the CI report ({cause})"


def _summary_line(comment: str) -> str:
    """The line after the first of an `Auto-merge refused:` comment that names what
    failed, or "" when there is none (the comment goes straight to its closing sentence)."""
    for line in comment.splitlines()[1:]:
        if line.strip():
            return "" if line == LEFT_TO_HUMAN else line
    return ""


def _refuse(
    task_id: str,
    head: str | None,
    reason: str,
    ports: Ports,
    failure: rules.MergeFailure | None = None,
    facts: rules.MergeFacts | None = None,
) -> MergeResult:
    """Comments a refusal the human can act on, once per (head, reason): the last
    `Auto-merge refused:` comment naming the same head and reason is enough. A
    `merge_failed` one names the closed class of the last failure (or `unknown`) and a
    fixed advice sentence, never the CLI's own text."""
    if reason in COMMENTED and head is not None:
        first = f"{AUTO_MERGE_REFUSED}{head} {reason}"
        summary = ""
        text = f"{first}\n\n{LEFT_TO_HUMAN}"
        if reason == "checks_failed":
            recorded = (ports.documents.load(task_id) or {}).get("merge_checks")
            record = recorded if isinstance(recorded, dict) else {}
            summary = presubmit.failed_checks_summary(record, head)
            evidence = presubmit.failed_checks_markdown(record, head)
            text = "\n\n".join(part for part in (first, evidence, LEFT_TO_HUMAN) if part)
        if facts is not None and reason in ("ci_failed", "ci_missing", "ci_unreadable"):
            text = f"{first}\n\n{_ci_evidence(reason, facts)}\n\n{LEFT_TO_HUMAN}"
        if reason == "merge_failed":
            advice = (
                rules.MERGE_FAILURE_ADVICE[failure]
                if failure is not None
                else rules.MERGE_FAILURE_UNKNOWN_ADVICE
            )
            first = f"{AUTO_MERGE_REFUSED}{head} gh pr merge failed: {failure or 'unknown'}"
            text = f"{first}\n\n{advice}\n\nLeft for the human to merge."
        refused = [
            body
            for body in ports.tracker.issue_comments(task_id)
            if body.startswith(AUTO_MERGE_REFUSED)
        ]
        if (
            not refused
            or refused[-1].splitlines()[0] != first
            or (reason == "checks_failed" and _summary_line(refused[-1]) != summary)
        ):
            ports.tracker.comment(task_id, text)
    return MergeResult("refused", reason)
