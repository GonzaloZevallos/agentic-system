"""`land_slice` (GH-297, ADR-0048): the landing step that lands the approved units which
apply cleanly on master as a pull request of their own, before the task is restarted.

The domain chose the units and `landing.land` recorded the entry
before dispatching here; this module carries the step out in an order that is safe to run
twice. Git says what landed (`slice_landed`, never `landed_slices` alone); the slice's merge
facts are `landing.slice_facts`'; `rules.auto_merge_decision` alone decides whether it
merges; the merge goes only through `Tracker.merge`. The slice's pull request is opened with
`closes_issue=False` and is never the document's `pull_request`."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ..domain import ci, presubmit, rules
from ..domain import landing as landing_rules
from ..domain.check_schedule import CheckPriority
from ..domain.task import TaskState
from . import landing_plan
from .checkouts import task_base
from .landing_plan import LandingContext
from .poll import read_criteria
from .ports import GitError, MergeCallFailed, TrackerUnreachable
from .presubmit import CheckoutPresubmit
from .release import release_checkout
from .resume import state_from


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _seconds_since(anchor: str | None, now: str) -> float | None:
    """Seconds from an ISO timestamp to `now`; `None` for no anchor or one that does not
    parse, which the decision reads as an unknown wait."""
    if not anchor:
        return None
    try:
        then, current = datetime.fromisoformat(anchor), datetime.fromisoformat(now)
    except ValueError:
        return None
    then = then if then.tzinfo else then.replace(tzinfo=UTC)
    current = current if current.tzinfo else current.replace(tzinfo=UTC)
    return (current - then).total_seconds()


def open_entry(entries: Sequence[Mapping[str, object]], head: str) -> dict[str, object] | None:
    """The `land_slice` entry for `head` that is neither landed nor refused: the one a crash
    or a CI wait left for the next tick to continue. The last entry decides."""
    for entry in reversed(entries):
        if entry.get("step") == "land_slice" and entry.get("head") == head:
            return None if rules.entry_closed(entry) else dict(entry)
    return None


def _stamp(context: LandingContext, **keys: object) -> dict[str, object]:
    """Writes `keys` on the open `land_slice` entry of this head through `record_closure`,
    from the document as it is now, and answers that entry."""
    loaded = context.ports.documents.load(context.task_id) or {}
    written = [dict(entry) for entry in landing_plan.entries(loaded)]
    entry = open_entry(written, context.head)
    if entry is None:
        return {}
    for item in written:
        if item.get("step") == "land_slice" and item.get("head") == context.head:
            if not rules.entry_closed(item):
                item.update(keys)
                entry = item
    context.ports.documents.record_closure(context.task_id, {"landing": written})
    return entry


def _refuse(context: LandingContext, reference: object, why: str) -> None:
    """Closes the slice as refused: its pull request (when one was opened) is closed and the
    entry gets `closed_at` and no `resolved_at`, which is what `refused_slices` counts."""
    if isinstance(reference, str) and reference:
        try:
            context.ports.tracker.close_pull_request(reference)
        except ValueError as exc:
            context.log(f"agentic: land: {context.task_id}: slice: close failed: {exc}")
    _stamp(context, closed_at=(context.clock or _now)())
    context.log(f"agentic: land: {context.task_id}: slice refused: {why}")


def _complete(context: LandingContext, entry: Mapping[str, object], units: tuple[str, ...]) -> None:
    """Records a slice that landed: appended to `landed_slices` and the entry resolved, in
    one `record_closure` from the document as it is now."""
    loaded = context.ports.documents.load(context.task_id) or {}
    at = (context.clock or _now)()
    cost = loaded.get("cost_usd", 0.0)
    resolved = landing_rules.resolve_open(
        landing_plan.entries(loaded), at, cost if isinstance(cost, int | float) else 0.0
    )
    landed = loaded.get("landed_slices")
    record: dict[str, object] = {"units": list(units), "at": at}
    if isinstance(reference := entry.get("slice_pull_request"), str):
        record["pull_request"] = reference
    context.ports.documents.record_closure(
        context.task_id,
        {
            "landed_slices": [*(landed if isinstance(landed, list) else []), record],
            "landing": resolved,
        },
    )
    context.log(f"agentic: land: {context.task_id}: slice landed: {', '.join(units)}")
    try:
        context.ports.tracker.comment(
            context.task_id, f"Landed {', '.join(units)} ahead of the task's pull request."
        )
    except ValueError as exc:
        context.log(f"agentic: land: {context.task_id}: slice comment failed: {exc}")


def _checks_pass(
    context: LandingContext, raw: TaskState, path: Path, head: str, master: str
) -> bool:
    """The product's checks on the built slice, in its checkout. Nothing is written in the
    document: `merge_checks` is the main pull request's."""
    ports, cfg = context.ports, context.cfg
    base = task_base(raw, ports)
    started = time.monotonic()
    failed_step: presubmit.FailedStep | None = None
    setup_failure: tuple[str, str] | None = None
    declared: dict[str, str] = {}
    gate: CheckoutPresubmit | None = None
    for step, command in ports.stack.setup(base).items():
        result = ports.commands.run(path, command, cfg.check_timeout_s)
        if not result.passed:
            text = presubmit.said(result.output, result.timed_out, cfg.check_timeout_s)
            failed_step, setup_failure = "setup", (step, text)
            break
    else:
        declared = dict(ports.stack.checks(base))
        gate = CheckoutPresubmit(
            ports,
            path,
            "",
            declared,
            cfg.check_timeout_s,
            priority=CheckPriority.LAND,
            wait_max_s=cfg.check_wait_max_s,
            witness=ports.stack.witness(base),
            parallel=ports.stack.parallel_checks(base),
        )
        gate()
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
    return record.get("passed") is True


def _open(
    context: LandingContext, raw: TaskState, units: tuple[str, ...], branch: str, master: str
) -> str | None:
    """Builds the slice on master, runs the checks there, pushes it and opens its pull
    request, and stores the pull request on the entry. `None` when the slice was refused
    (nothing is left open)."""
    ports, task_id = context.ports, context.task_id
    heads = landing_plan.reviewed_heads(raw, units)
    if set(heads) != set(units):
        _refuse(context, None, "a unit has no reviewed head")
        return None
    name = f"slice-{task_id}"
    try:
        path = ports.workspace.detached_checkout(name, master)
        built = ports.workspace.build_slice(path, master, [heads[u] for u in units], branch)
        if built is None:
            _refuse(context, None, "the units do not merge on master")
            return None
        passed = _checks_pass(context, raw, path, built, master)
    except GitError as exc:
        _refuse(context, None, f"git failed: {exc}")
        return None
    finally:
        release_checkout(ports, ports.workspace.checkout_path(name))
        ports.workspace.remove_checkout(name)
    if not passed:
        _refuse(context, None, "the product's checks failed on the slice")
        return None
    loaded = ports.documents.load(task_id) or {}
    main = str(loaded.get("pull_request", "")).rstrip("/").rsplit("/", 1)[-1]
    k = branch.rsplit("-", 1)[-1]
    body = f"Slice {k} of {task_id}, units {', '.join(units)}; the task's pull request is #{main}"
    try:
        ports.workspace.push(branch)
        reference = ports.tracker.open_pull_request(
            task_id, branch, f"{task_id}: slice {k}", body, closes_issue=False
        )
    except (GitError, ValueError) as exc:
        _refuse(context, None, f"the slice could not be opened: {exc}")
        return None
    _stamp(context, slice_pull_request=reference, checks_passed=True, upstream=master)
    return reference


def _decide(
    context: LandingContext,
    raw: TaskState,
    entry: Mapping[str, object],
    units: tuple[str, ...],
    reference: str,
    master: str,
) -> None:
    """Asks `rules.auto_merge_decision` over `landing.slice_facts` and carries the answer
    out: `merge` merges and records, `wait` leaves the entry for the next tick, anything
    else closes the pull request and the entry as refused."""
    ports, cfg = context.ports, context.cfg
    try:
        pull_request = ports.tracker.pull_request(reference)
    except (ValueError, OSError) as exc:
        context.log(f"agentic: land: {context.task_id}: slice: pull request unreadable: {exc}")
        return
    if pull_request is None or pull_request.state != "OPEN":
        _refuse(context, None, "the slice's pull request is not open")
        return
    head = pull_request.head_oid
    if entry.get("upstream") != master or entry.get("checks_passed") is not True:
        _refuse(context, reference, "master moved since the slice was checked")
        return
    try:
        carries, _ = landing_plan.unit_reads(raw, units, cfg, ports, master)
        heads = landing_plan.reviewed_heads(raw, units)
        contains = {
            n: ports.workspace.is_ancestor(cfg.product_dir, h, head) for n, h in heads.items()
        }
        touched = ports.workspace.changed_paths(master, head)
        declared = ports.stack.ci(task_base(raw, ports))
    except (GitError, ValueError) as exc:
        _refuse(context, reference, f"unreadable: {exc}")
        return
    ci_head: str | None = None
    ci_verdict: ci.CiVerdict | None = None
    ci_unreadable: ci.CiUnreadable | None = None
    ci_waited_s: float | None = None
    if declared:
        report = ports.tracker.ci_report(reference)
        opened_at = None if isinstance(report, ci.CiUnreadable) else report.opened_at
        anchor = ci.ci_wait_anchor(head, str(entry.get("at", "")) or None, head, opened_at)
        ci_waited_s = _seconds_since(anchor, (context.clock or _now)())
        if isinstance(report, ci.CiUnreadable):
            ci_unreadable = report
        else:
            ci_head, ci_verdict = report.head_sha, ci.ci_state(report, declared)
    facts = landing_rules.slice_facts(
        landing_rules.Slice(units),
        raw,
        task_id=context.task_id,
        slice_head=head,
        built_clean=True,
        contains_reviewed=contains,
        carries=carries,
        pr_state=pull_request.state,
        pr_head=head,
        in_eval_set=context.facts.in_eval_set,
        touched_paths=touched,
        current_master=master,
        checks_head=head,
        checks_master=master,
        checks_passed=True,
        ci_required=declared,
        ci_head=ci_head,
        ci_verdict=ci_verdict,
        ci_unreadable=ci_unreadable,
        ci_waited_s=ci_waited_s,
    )
    if rules.criteria_probe_wanted(facts):
        # GH-334: a slice is a merge like any other; it must not push a criteria document
        # past its cap either (the incident, apostrophe PR #357, was a slice merge).
        facts = replace(facts, criteria_regressed=read_criteria(ports, head, master))
    decision = rules.auto_merge_decision(facts)
    if decision.kind == "wait":
        context.log(f"agentic: land: {context.task_id}: slice waits for its CI")
        return
    if decision.kind != "merge":
        _refuse(context, reference, f"{decision.kind} {decision.reason}")
        return
    if ports.workspace.upstream().sha != master:
        _refuse(context, reference, "master moved before the merge")
        return
    try:
        ports.tracker.merge(reference, head)
    except TrackerUnreachable as exc:
        context.log(f"agentic: land: {context.task_id}: slice: GitHub unreachable: {exc}")
        return
    except (MergeCallFailed, ValueError) as exc:
        _refuse(context, reference, f"the merge failed: {exc}")
        return
    _complete(context, {**entry, "slice_pull_request": reference}, units)


def land_slice(context: LandingContext) -> None:
    """Carries out the `land_slice` entry already recorded for the head. Idempotent: units
    git says landed only complete the record; an entry with a pull request goes straight to
    the decision; one without builds, checks, pushes and opens first."""
    ports = context.ports
    loaded = ports.documents.load(context.task_id) or {}
    entry = open_entry(landing_plan.entries(loaded), context.head)
    master = context.upstream.sha
    if entry is None or master is None:
        context.log(f"agentic: land: {context.task_id}: land_slice: nothing open")
        return
    raw = state_from(loaded)
    recorded = entry.get("slice_units")
    units = tuple(str(u) for u in recorded) if isinstance(recorded, list) else context.step.units
    heads = landing_plan.reviewed_heads(raw, units)
    try:
        ancestry = {
            name: ports.workspace.is_ancestor(context.cfg.product_dir, head, master)
            for name, head in heads.items()
        }
    except GitError as exc:
        context.log(f"agentic: land: {context.task_id}: land_slice: ancestry unreadable: {exc}")
        return
    if landing_rules.slice_landed(units, ancestry):
        _complete(context, entry, units)
        return
    reference = entry.get("slice_pull_request")
    if not isinstance(reference, str) or not reference:
        k = sum(1 for e in landing_plan.entries(loaded) if e.get("step") == "land_slice")
        branch = f"{rules.task_branch(context.task_id)}.slice-{k}"
        reference = _open(context, raw, units, branch, master)
        if reference is None:
            return
        entry = (
            open_entry(
                landing_plan.entries(ports.documents.load(context.task_id) or {}), context.head
            )
            or entry
        )
    _decide(context, raw, entry, units, reference, master)
