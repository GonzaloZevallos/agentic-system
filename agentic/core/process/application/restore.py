"""Restauración (ADR-0011 addendum, ADR-0048 addendum; `CONTEXT.md`): what `agentic restore`
does to bring an archived, approved task back so it can land, without a fresh run.

`restore_task` is the whole use case, the inverse of `archive.archive_previous_run` by copy:
the archive's refs are never renamed or deleted, the task branch is created again at the head
its review approved (`rules.approved_head`) and each subtask branch at its `reviewed_head`
(`rules.restore_heads`), the document is written with a `restorations` record that cuts the
`landing` list (`rules.live_landing`), a closed pull request is reopened and the
`PM: final validation` rows are settled through `plan_settlement`, the only place that decides
which rows close. Nothing is pushed that the pull request did not already carry.

What is still owed is `rules.restore_steps`' to say, from the state observed here and nowhere
else, so a restore cut off at any step finishes from what it finds on a retry. This module
only observes, runs the port verbs in order and maps a refusal of the platform to the closed
set. It runs under the caller's `RunLock`; it touches no label and no issue state."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from ..domain import document, plan_settlement, rules
from ..domain import landing as landing_rules
from ..domain.task import PullRequest, Restoration, TaskState, apply
from . import archive
from .merge import settlement_facts
from .ports import GitError, Ports
from .resume import state_from

Refusal = rules.RestoreRefusal | Literal["task_live", "push_refused"]
"""The closed set of reasons a restore refuses: the rule's own (`rules.RestoreRefusal`) and the
two only the use case can see, a held lock (`task_live`, said by the caller that takes it)
and a remote that said no to the push (`push_refused`)."""

Settlement = Literal["settled", "none_owed", "no_pm_approval", "not_settleable"]
"""How the plan rows ended: written (`settled`), nothing to write (`none_owed`), no live
approval of `PM: final validation` to settle by (`no_pm_approval`) or facts that do not allow
it (`not_settleable`). The last two are reported, never a refusal: the restore itself stands."""

_PULL_REQUEST = re.compile(r"/pull/(\d+)$")
_PR_STATES: Mapping[str, rules.RestorePrState] = {
    "OPEN": "open",
    "CLOSED": "closed",
    "MERGED": "merged",
}


@dataclass(frozen=True)
class RestoreResult:
    """What one call to `restore_task` did: the `refusal` or the `steps` it ran, in order, and
    how the plan rows ended. `detail` is the platform's or git's own text for a refusal that
    has one (`push_refused`, `pr_base_gone`, `pr_merged`)."""

    refusal: Refusal | None = None
    steps: tuple[rules.RestoreStep, ...] = ()
    settlement: Settlement | None = None
    settled_rows: int = 0
    detail: str | None = None


@dataclass(frozen=True)
class _Seen:
    """What `_observe` read: the rule's `RestoreObserved`, plus the live document, the pull
    request and its number, and where upstream stood."""

    observed: rules.RestoreObserved
    live: dict[str, Any] | None
    pull_request: PullRequest | None
    number: int | None
    master: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def restore_task(
    task_id: str,
    archive_prefix: str,
    ports: Ports,
    product_dir: Path,
    clock: Callable[[], str] = _now,
) -> RestoreResult:
    """Restore `task_id` from the refs under `archive_prefix` (`archive/run<N>-<sha>`).
    `product_dir` is the checkout git is asked in (ancestry and the remote tip), the same one
    the landing asks. Raises `GitError` for a git failure that is not a refusal."""
    seen = _observe(task_id, archive_prefix, ports, product_dir)
    plan = rules.restore_steps(seen.observed)
    if plan.refusal is not None:
        return RestoreResult(refusal=plan.refusal)
    archived = cast(TaskState, seen.observed.state)
    taken: list[rules.RestoreStep] = []
    for step in plan.steps:
        if step == "archive_live":
            archive.archive_previous_run(task_id, ports)
        elif step == "create_refs":
            for name, sha in rules.restore_heads(archived, task_id).items():
                ports.workspace.create_branch(name, sha)
        elif step == "write_document":
            ports.documents.save(_restored(archived, seen, archive_prefix, clock()))
        elif step == "push":
            try:
                ports.workspace.push(rules.task_branch(task_id))
            except GitError as exc:
                return RestoreResult(refusal="push_refused", steps=tuple(taken), detail=str(exc))
        elif step == "reopen":
            if (refused := _reopen(seen, ports)) is not None:
                return RestoreResult(refusal=refused[0], steps=tuple(taken), detail=refused[1])
        if step != "settle":  # it runs last, over the document the steps above wrote
            taken.append(step)
    return _settled(task_id, seen, ports, tuple(taken))


def _observe(task_id: str, prefix: str, ports: Ports, product_dir: Path) -> _Seen:
    workspace = ports.workspace
    branch = rules.task_branch(task_id)
    refs = workspace.branches(f"{prefix}/")
    task_ref = rules.archived_branch(prefix, branch, refs)
    text = (
        workspace.show(task_ref, document.relative_path(task_id).as_posix())
        if task_ref is not None
        else None
    )
    state = _parsed(text, task_id)
    live = _live_document(task_id, ports)
    held = [row for row in (live or {}).get("restorations", []) if isinstance(row, dict)]
    live_heads = {
        name: head
        for name in rules.branches_of_task(workspace.branches(branch), task_id)
        if (head := workspace.branch_head(name)) is not None
    }
    heads = rules.restore_heads(state, task_id) if state is not None else {}
    reachable = {
        name: _reachable(ports, product_dir, prefix, refs, name, sha) for name, sha in heads.items()
    }
    reference = state.get("pull_request") if state is not None else None
    pull_request = _pull_request(ports, reference)
    current = state_from(live) if held and live is not None else state
    observed = rules.RestoreObserved(
        task_id=task_id,
        archive=prefix,
        archived=refs,
        state=state,
        held=cast(list[Restoration], held),
        live_heads=live_heads,
        reachable=reachable,
        remote_head=workspace.remote_tip(product_dir, branch),
        pr_state=_PR_STATES.get(pull_request.state) if pull_request is not None else None,
        pr_head=pull_request.head_oid if pull_request is not None else None,
        # Only the platform knows, and only when asked to reopen: `_reopen` reads its refusal.
        pr_base_exists=True,
        settled=current is None or not plan_settlement.pending(current),
    )
    return _Seen(observed, live, pull_request, _number(reference), workspace.upstream().sha)


def _parsed(text: str | None, task_id: str) -> TaskState | None:
    if text is None:
        return None
    try:
        return state_from(document.parse(text, f"{task_id} (archived)"))
    except ValueError:
        return None


def _live_document(task_id: str, ports: Ports) -> dict[str, Any] | None:
    try:
        return ports.documents.load(task_id)
    except (ValueError, OSError):
        return None


def _reachable(
    ports: Ports, product_dir: Path, prefix: str, refs: list[str], branch: str, sha: str
) -> bool:
    ref = rules.archived_branch(prefix, branch, refs)
    if ref is None:
        return False
    try:
        return ports.workspace.is_ancestor(product_dir, sha, ref)
    except GitError:
        return False


def _number(reference: object) -> int | None:
    match = _PULL_REQUEST.search(reference) if isinstance(reference, str) else None
    return int(match.group(1)) if match else None


def _pull_request(ports: Ports, reference: object) -> PullRequest | None:
    if not isinstance(reference, str):
        return None
    try:
        return ports.tracker.pull_request(reference)
    except ValueError:
        return None


def _is_copy(live: dict[str, Any], archived: TaskState) -> bool:
    """Whether the live document is the archived one put back in place by hand: the same pull
    request at the same approved head. Anything else on disk is another run, displaced."""
    return live.get("pull_request") == archived.get("pull_request") and rules.same_commit(
        rules.approved_head(state_from(live)), rules.approved_head(archived)
    )


def _amount(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _restored(archived: TaskState, seen: _Seen, prefix: str, at: str) -> TaskState:
    """The document a restore writes: the archived one -- or the live copy of it, which may
    hold landing entries the archive does not, and an entry is never dropped -- plus the
    `restorations` record cutting after the entries there are, and the restart depth and cost
    of a displaced run folded in."""
    live = seen.live
    state = archived
    restarts = landing_rules.restart_depth(archived)
    carried = _amount(archived.get("landing_cost_carried"))
    if live is not None:
        restarts = max(restarts, landing_rules.restart_depth(live))
        if _is_copy(live, archived):
            state = state_from(live)
            carried = max(carried, _amount(live.get("landing_cost_carried")))
        else:
            carried += _amount(live.get("landing_cost_carried")) + _amount(live.get("cost_usd"))
    row: Restoration = {
        "archive": prefix,
        "head": rules.approved_head(archived) or "",
        "upstream": seen.master or "",
        "at": at,
        "landing_cut": len(state.get("landing", [])),
    }
    restored = apply(state, state_from({"restorations": [row]}))
    patch: dict[str, object] = {}
    if restarts:
        patch["landing_restarts"] = restarts
    if carried:
        patch["landing_cost_carried"] = carried
    return cast(TaskState, {**restored, **patch})


def _reopen(seen: _Seen, ports: Ports) -> tuple[Refusal, str] | None:
    """Reopen the pull request; a refusal of the platform is read, never retried: a merged
    pull request is `pr_merged`, anything else it refuses (a deleted base) `pr_base_gone`."""
    reference = cast(TaskState, seen.observed.state).get("pull_request", "")
    if seen.number is None:
        return "not_delivered", f"no pull request number in {reference!r}"
    try:
        ports.tracker.reopen_pull_request(seen.number)
    except ValueError as exc:
        now = _pull_request(ports, reference)
        if now is not None and now.state == "MERGED":
            return "pr_merged", str(exc)
        return "pr_base_gone", str(exc)
    return None


def _settled(
    task_id: str, seen: _Seen, ports: Ports, taken: tuple[rules.RestoreStep, ...]
) -> RestoreResult:
    """The last step: write what `plan_settlement.pending` says the live approval covers, when
    `plan_settlement.settleable` allows it on the facts of the restored task."""
    raw = _live_document(task_id, ports)
    if raw is None:
        return RestoreResult(steps=taken, settlement="none_owed")
    current = state_from(raw)
    if rules.pm_final_approved_version(current) == 0:
        return RestoreResult(steps=taken, settlement="no_pm_approval")
    rows = plan_settlement.pending(current)
    if not rows:
        return RestoreResult(steps=taken, settlement="none_owed")
    facts = settlement_facts(task_id, current, seen.pull_request, seen.number or 0, seen.master)
    if not plan_settlement.settleable(facts, rules.restored(current)):
        return RestoreResult(steps=taken, settlement="not_settleable")
    ports.documents.save(apply(current, state_from({"plan_settled": rows})))
    return RestoreResult(steps=(*taken, "settle"), settlement="settled", settled_rows=len(rows))
