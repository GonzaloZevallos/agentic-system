"""The automatic merge of one delivered task's pull request (GH-178, ADR-0045): what
`agentic merge --issue N` does once it holds the task's lock.

Every condition of the merge is `rules.auto_merge_decision`'s, asked before anything is
run and again after the checks; this module gathers the facts the decision reads and
carries out its answer. The checks run through the existing `CheckoutPresubmit` in a
throwaway worktree under `.agentic/worktrees/` holding the pull request's head with the
fetched upstream merged into it: never in the product checkout's working tree, and
nothing here writes in stage0. The merge call runs only after that subprocess exited."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, assert_never

from agentic.shared.configuration import Configuration

from ..domain import adr_numbers, ci, master_gain, merge_ci, merge_conflict, presubmit, rules
from ..domain import landing as landing_rules
from ..domain.check_schedule import CheckPriority
from ..domain.ci_push import scrub
from ..domain.presubmit import CiDispatch
from ..domain.task import (
    CiCause,
    MergeCiRecord,
    MergeRefusalRecord,
    PullRequest,
    TaskState,
    add_flakes,
    add_merge_ci,
    add_tracker_failures,
)
from . import adr_collision, ci_flakes, ci_push, ci_trust, landing_flakes, landing_plan
from .checkouts import task_base
from .delivered import files_patch
from .poll import (
    AUTO_MERGE_REFUSED,
    AUTO_MERGED,
    LANDING,
    _merge_facts,
    ci_gate,
    read_conflicts,
    with_ci,
    with_red_on_base,
)
from .ports import (
    CommandResult,
    GitError,
    MergeCallFailed,
    MergeConflict,
    MergeFailed,
    Ports,
    RunRegistry,
    TrackerUnreachable,
    Upstream,
)
from .preflight import criteria_regressed, product_name
from .presubmit import CheckoutPresubmit, readiness_of, reuse_of
from .release import release_checkout

# The refusals a human can act on, and so the only ones commented, once per (head, reason).
COMMENTED: tuple[str, ...] = (
    "adr_number_taken",
    "checks_failed",
    "no_merge_method",
    "eval_set_unreadable",
    "attempts_exhausted",
    "merge_failed",
    "ci_failed",
    "ci_missing",
    "ci_unknown_names",
    "ci_unreadable",
    "perf_unverified",
    "criteria_over_cap",
    "criteria_unreadable",
    "ci_no_verdict",
    "ci_stale_exhausted",
    "ci_untrusted_no_dispatch",
)

LEFT_TO_HUMAN = "Left for the human to merge."

# Nothing was committed before the merge worktree existed: every head differs from it, so
# `CheckoutPresubmit` runs the checks even when the upstream was already in the head.
_NOTHING_BEFORE = ""

# The merge commit the gate builds for a fresh GitHub run (GH-463). A dispatched one carries
# `[skip ci]`: only the dispatch ever reads that commit, never a `push` event.
_FRESH_MESSAGE = "Merge {master} into {head} for the merge gate's CI run"
_FRESH_DISPATCH_MESSAGE = _FRESH_MESSAGE + " [skip ci]"
# The id of the workflow run a check link names: `.../actions/runs/<id>/job/<n>`.
_RUN_ID = re.compile(r"/actions/runs/(\d+)(?:/|$)")


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
    sleep: Callable[[float], None] = time.sleep,
    registry: RunRegistry | None = None,
) -> MergeResult:
    raw = ports.documents.load(task_id)
    reference = raw.get("pull_request") if raw else None
    if raw is None or not reference:
        return MergeResult("stopped", "no_pull_request")
    ports.workspace.prune()
    release_checkout(ports, ports.workspace.checkout_path(_name(task_id)))
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
    range_base: str | None = None
    # The workflow the stack names for an untrusted head, read at master because the dispatch
    # runs the default branch's workflow (GH-547); an unreadable stack names none.
    dispatch: CiDispatch | None = None
    if head is not None and ports.workspace.fetch_commit(head):
        # GH-547: what the merge brings in is `merge-base(head, master)...head`, read once per
        # call; a base the task merged master past would count master's own commits as the
        # task's. An unreadable merge base stops the merge: it never falls back to the task base.
        # GH-565: `ci_trust.read_range` is that one read, shared with the full_checks node.
        trust = ci_trust.read_range(ports, head, master)
        if trust.merge_base_error is not None:
            return MergeResult("stopped", f"diff_unreadable: {trust.merge_base_error}")
        range_base, touched, dispatch = trust.range_base, trust.paths, trust.dispatch
    elif head is not None:
        return MergeResult("stopped", f"head_unreachable: {head}")
    else:
        dispatch = ci_trust.dispatch_at(ports, master)
    # Read once, on the one master this verb resolved: only a diff that touches `docs/adr/`
    # costs the read, and the collision cannot change between the decision and the re-decision.
    collision = None
    if head is not None and adr_collision.touches_adrs(touched):
        try:
            collision = adr_collision.adr_collision(ports, master, head)
        except GitError as exc:
            return MergeResult("stopped", f"adr_unreadable: {exc}")
    declared = ports.stack.ci(base)
    names = ci_gate(raw, declared).names
    # GH-463: the decided fresh run, once there is one: it replaces the pull request's own report.
    fresh_runs: list[ci.CiReport] = []
    report: ci.CiReport | ci.CiUnreadable | None = None
    fresh: bool | None = None
    if names:
        report = ports.tracker.ci_report(str(reference))
        if isinstance(report, ci.CiReport) and head is not None:
            fresh = ci.ci_fresh(report.base_sha, master, ports.workspace.contains(head, master))

    criteria: list[tuple[str, ...] | None] = []
    # GH-353: the declared globs and each row master's gain, read at most once per verb call.
    globs: list[tuple[str, ...]] = []
    gains: dict[str, tuple[str, ...] | None] = {}

    def decide(document: TaskState) -> rules.MergeFacts:
        base_facts = _facts(task_id, document, pull_request, eval_set, number, master, touched)
        # GitHub's reading of a conflict on this head and master (GH-326), git-free; the
        # probe is the last look's, immediately before the merge call.
        # GH-492: git's own conflict on this exact pair, once recorded, counts as well.
        github = rules.merge_conflicts_from(
            pull_request,
            head,
            master,
            None,
            recorded=merge_conflict.applies(document.get("merge_conflict"), head, master),
        )
        facts = with_ci(
            replace(
                base_facts,
                adr_collision=collision,
                conflicts=github,
                github_conflicting=rules.github_conflicting(pull_request, head),
            ),
            document,
            declared,
            report,
            fresh,
        )
        facts = replace(
            facts,
            merge_ci=tuple(document.get("merge_ci", [])),
            ci_dispatch_declared=dispatch is not None,
        )
        if fresh_runs:
            facts = with_ci_run(facts, fresh_runs[0], names)
        facts = with_red_on_base(ports, facts, report)
        facts = _with_deferral(ports, facts, document, base, head, report)
        # GH-334: the criteria caps, read once on this one master and only when no refusal
        # ahead of them has already ruled the pull request out.
        if head is not None and (criteria or rules.criteria_probe_wanted(facts)):
            if not criteria:
                criteria.append(_criteria(ports, head, range_base))
            facts = replace(facts, criteria_regressed=criteria[0])
        # GH-353: what master gained since the rows' masters, read once and only when no
        # refusal ahead of the checks has ruled the pull request out and the answer would
        # change the decision.
        if head is not None and rules.declares_ci(facts):
            assert range_base is not None  # a fetched head has its merge base
            facts = _with_ci_gain(
                ports, facts, document, head, master, base, range_base, touched, globs, gains
            )
        elif head is not None and rules.gain_probe_wanted(facts):
            covered = _masters_covered(ports, document, head, master, base, touched, globs, gains)
            facts = replace(facts, masters_covered=covered)
        return facts

    facts = decide(raw)
    decision = rules.auto_merge_decision(facts)
    if decision.kind == "wait":
        return MergeResult("stopped", "ci_wait")
    if decision.kind == "run_checks":
        assert head is not None  # a decision never asks for checks without a head
        run_checks(task_id, raw, head, master, base, cfg, ports, facts.attempts + 1, log)
        raw = ports.documents.load(task_id) or raw
        facts = decide(raw)
        decision = rules.auto_merge_decision(facts)
    cause: CiCause | None = None
    if decision.kind == "ci_run":
        # GH-463: a `ci:` product never runs its suite here; one fresh GitHub run decides.
        assert head is not None  # a decision never asks for a run without a head
        step = _fresh_run(
            task_id, raw, head, master, base, facts, dispatch, names, cfg, ports, clock, log, sleep
        )
        if step.stopped is not None:
            return step.stopped
        cause = step.cause
        if step.report is not None:
            fresh_runs.append(step.report)
            report = step.report
        raw = ports.documents.load(task_id) or raw
        facts = decide(raw)
        decision = rules.auto_merge_decision(facts)
        if decision.kind == "ci_run" and step.report is None:
            # GH-552: a pair this very look ended `no_verdict` was read a moment ago; its one
            # reread belongs to a later invocation, so the refusal stands as it always did.
            decision = rules.MergeDecision("refuse", "ci_no_verdict")
        if decision.kind in ("wait", "ci_run"):
            return MergeResult("stopped", "ci_wait")
    if decision.kind == "refuse":
        assert decision.reason is not None
        if decision.reason == "ci_untrusted_no_dispatch" and head is not None:
            # GH-500: this refusal writes nothing else, so it would repeat every round. The
            # row names the pair it was decided on, and the poller holds the merge back on it.
            refusal: MergeRefusalRecord = {
                "reason": "ci_untrusted_no_dispatch",
                "head": head,
                "master": master,
                "at": clock(),
            }
            ports.documents.record_closure(task_id, {"merge_refusal": refusal})
        if decision.reason == "merge_conflict":
            where = (
                "recorded"
                if merge_conflict.applies(raw.get("merge_conflict"), head, master)
                else "github"
            )
            log(f"agentic: merge: {task_id}: {head} conflicts with master {master} ({where})")
        failure = facts.merge_failure if decision.reason == "merge_failed" else None
        return _refuse(
            task_id,
            head,
            decision.reason,
            ports,
            failure=failure,
            facts=facts,
            report=report,
            cause=cause,
        )
    if decision.kind != "merge" or head is None:
        return MergeResult("stopped", "no_decision")
    if declared:
        raw = _with_ci_flakes(ports, task_id, raw, str(reference), head, base, declared, log)
    # A check that failed on this head, was rerun by the landing and now passes is flaky.
    landing_flakes.file_task_flakes(
        ports,
        task_id,
        raw,
        log,
        cfg=cfg,
        now=datetime.now(UTC),
        flake_lock=(lambda: registry.flake_lock(product_name(ports))) if registry else None,
        head=head,
    )
    relied = _ci_links(report, facts.ci_required) if _merges_on_ci(facts) else None
    if rules.declares_ci(facts) and relied is None:
        # A `ci:` product never merged on a local check (GH-463): a recorded GitHub pass or a
        # fresh run decided it, and only the fresh run has a report of its own to link.
        relied = _ci_links(report, facts.ci_required) if fresh_runs else []
    inert_from = _inert_from(raw, master) if facts.masters_covered is True else None
    return _merge(
        task_id,
        number,
        str(reference),
        head,
        ports,
        facts,
        clock,
        log,
        relied,
        bool(names),
        pull_request,
        inert_from,
    )


def _with_ci_flakes(
    ports: Ports,
    task_id: str,
    raw: TaskState,
    reference: str,
    head: str,
    base: str,
    jobs: tuple[str, ...],
    log: Callable[[str], None],
) -> TaskState:
    """`raw` once the flake block the CI jobs printed on `head` is recorded in the document
    (GH-464). Evidence only: it adds records for the filing that follows and changes no
    decision, so an unreadable block or a write that fails leaves `raw` as it was."""
    records = ci_flakes.block_records(ports, reference, head, base, jobs, "merge", ci_flakes.now())
    if not records:
        return raw
    try:
        ports.documents.record_closure(
            task_id, {"flakes": add_flakes(raw.get("flakes", []), records)}
        )
    except (ValueError, OSError) as exc:
        log(f"agentic: merge: {task_id}: flake block not recorded: {exc}")
        return raw
    return ports.documents.load(task_id) or raw


def with_ci_run(
    facts: rules.MergeFacts, report: ci.CiReport, names: tuple[str, ...]
) -> rules.MergeFacts:
    """`facts` plus the verdict of the decided fresh run (GH-463): `ci_subject` becomes `tested`,
    so the decision compares `report.head_sha` (the commit the run was about) with the latest
    pair through `merge_ci.run_stands` and never with the pull request's head. The verdict is
    `ci.ci_state`'s, over `names`, and `ci_fresh` says whether the run stands for this master."""
    pair = merge_ci.latest(facts.merge_ci, facts.pr_head)
    return replace(
        facts,
        ci_subject="tested",
        ci_head=report.head_sha,
        ci_verdict=ci.ci_state(report, names),
        ci_unreadable=None,
        ci_fresh=merge_ci.run_stands(
            pair, facts.pr_head, facts.current_master, facts.ci_run_inert, ran=report.head_sha
        ),
    )


def _criteria(ports: Ports, head: str, range_base: str | None) -> tuple[str, ...] | None:
    """`poll.read_criteria` on the verb's one merge base (GH-547): the same verdict, without a
    second `merge_base` ask; a `GitError` is None, unreadable, which the decision refuses."""
    if range_base is None:
        return None
    try:
        return tuple(criteria_regressed(ports, range_base, head))
    except GitError:
        return None


def _with_ci_gain(
    ports: Ports,
    facts: rules.MergeFacts,
    document: TaskState,
    head: str,
    master: str,
    base: str,
    range_base: str,
    touched: tuple[str, ...] | None,
    globs: list[tuple[str, ...]],
    gains: dict[str, tuple[str, ...] | None],
) -> rules.MergeFacts:
    """`ci_covered` and `ci_run_inert` (GH-463, #353 for a `ci:` product): what master gained
    since the master of the recorded GitHub pass and of the head's latest fresh pair, read at
    most once per call and only when the answer would change the decision."""
    pair = merge_ci.latest(facts.merge_ci, head)
    wanted = rules.gain_probe_wanted(facts)
    behind = pair is not None and not rules.same_commit(pair["master"], master)
    if not wanted and not behind:
        return facts
    base_of = range_base if wanted else None
    older = [
        m for m in (base_of, pair["master"] if pair is not None else None) if m and m != master
    ]
    if not globs:
        try:
            globs.append(ports.stack.merge_inert(head))
        except ValueError:
            globs.append(())
    if globs[0]:
        for row_master in older:
            if row_master not in gains:
                gains[row_master] = _gain(ports, row_master, master)
    setup = list(ports.stack.setup(base).values())
    inert = (
        master_gain.masters_covered(master, [pair["master"]], gains, globs[0], setup, touched)
        is True
        if pair is not None and behind
        else None
    )
    covered = (
        merge_ci.masters_covered_ci(
            document.get("full_checks"),
            pair,
            pr_head=head,
            current_master=master,
            merge_base=base_of,
            required=facts.ci_required,
            gains=gains,
            globs=globs[0],
            setup_commands=setup,
            task_touched=touched,
        )
        if wanted
        else None
    )
    return replace(facts, ci_covered=covered, ci_run_inert=inert)


@dataclass(frozen=True)
class _Fresh:
    """What one look at the gate's fresh run did: `stopped` the result to return (a run was
    triggered or is still running), `report` the decided run's, `cause` why no verdict came."""

    stopped: MergeResult | None = None
    report: ci.CiReport | None = None
    cause: CiCause | None = None


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _write_pairs(
    ports: Ports, task_id: str, document_pairs: list[MergeCiRecord], *rows: MergeCiRecord
) -> None:
    """The pairs, with `rows` replacing the ones of the same `(head, master)`: written whole."""
    ports.documents.record_closure(task_id, {"merge_ci": add_merge_ci(document_pairs, list(rows))})


def _fresh_run(
    task_id: str,
    raw: TaskState,
    head: str,
    master: str,
    base: str,
    facts: rules.MergeFacts,
    dispatch: CiDispatch | None,
    names: tuple[str, ...],
    cfg: Configuration,
    ports: Ports,
    clock: Callable[[], str],
    log: Callable[[str], None],
    sleep: Callable[[float], None],
) -> _Fresh:
    """The gate's one fresh GitHub run of `head` merged with `master` (GH-463). A pair that
    already stands for the head is read, never rebuilt or triggered again; otherwise the pair
    is recorded and then published. Nothing here runs a command in a checkout."""
    pairs: list[MergeCiRecord] = list(raw.get("merge_ci", []))
    pair = merge_ci.latest(pairs, head)
    if (unread := merge_ci.rereadable(pairs, head, master)) is not None:
        return _reread_pair(task_id, pairs, unread, names, dispatch, cfg, ports, clock)
    if pair is not None and merge_ci.run_stands(
        pair, head, master, facts.ci_run_inert, ran=pair["tested"]
    ):
        return _read_pair(
            task_id, pairs, pair, head, master, names, dispatch, cfg, ports, clock, log, sleep
        )
    return _build_pair(
        task_id,
        pairs,
        pair,
        None,
        head,
        master,
        facts.ci_untrusted is not False,
        dispatch,
        ports,
        clock,
        log,
        sleep,
    )


def _build_pair(
    task_id: str,
    pairs: list[MergeCiRecord],
    previous: MergeCiRecord | None,
    again: MergeCiRecord | None,
    head: str,
    master: str,
    untrusted: bool,
    dispatch: CiDispatch | None,
    ports: Ports,
    clock: Callable[[], str],
    log: Callable[[str], None],
    sleep: Callable[[float], None],
) -> _Fresh:
    """Builds `head` merged with `master` with git only, records the pair, publishes it. `again`
    is the pair being republished (the second dispatch): its new `tested` is written to the
    same pair and the old one moves to `superseded`. A newer master than the pair's marks it
    `stale` when it was decided or undecided, never when it already ended otherwise."""
    name = _name(task_id)
    mode: Literal["push", "dispatch"] = "dispatch" if untrusted else "push"
    if again is not None:
        mode = again["mode"]
    message = (_FRESH_DISPATCH_MESSAGE if mode == "dispatch" else _FRESH_MESSAGE).format(
        master=master, head=head
    )
    try:
        path = ports.workspace.detached_checkout(name, head)
        merged = ports.workspace.merge(path, master, message)
        if isinstance(merged, MergeConflict):
            # GH-492: git's own finding is a fact about this exact head and master: written as
            # the one `merge_conflict` row, so the next round (and the landing) refuses it.
            log(f"agentic: merge: {task_id}: {head} does not merge with {master}")
            ports.documents.record_closure(
                task_id, {"merge_conflict": merge_conflict.record(head, master, clock())}
            )
            return _Fresh(stopped=MergeResult("refused", "merge_conflict"))
        if isinstance(merged, MergeFailed):
            # A refusal with no unmerged path says nothing about the code: nothing is recorded.
            # Git's own text reaches the log only through `scrub` (GH-487), never a `str.format`.
            said = scrub(merged.reason)
            log(f"agentic: merge: {task_id}: could not publish the fresh CI run: " + said)
            return _Fresh(stopped=MergeResult("stopped", "ci_publish_failed"))
        tested = ports.workspace.full_head(path)
        if again is None:
            record: MergeCiRecord = {
                "head": head,
                "master": master,
                "tested": tested,
                "mode": mode,
                "published_at": clock(),
                "republished": False,
                "superseded": [],
            }
            rows = [record]
            if (
                previous is not None
                and not rules.same_commit(previous["master"], master)
                and previous.get("outcome") in (None, "green")
            ):
                rows.insert(0, {**previous, "outcome": "stale"})
            _write_pairs(ports, task_id, pairs, *rows)
        else:
            superseded = [*again["superseded"]]
            if not rules.same_commit(again["tested"], tested):
                superseded.append(again["tested"])
            record = {
                **again,
                "tested": tested,
                "superseded": superseded,
                "republished": True,
                "published_at": clock(),
            }
            _write_pairs(ports, task_id, pairs, record)
        if mode == "push":
            ref = rules.ci_ref(task_id)
            if again is None:
                ci_push.publish_with_retry(
                    lambda: ports.workspace.publish_ci_ref(path, ref), sleep=sleep
                )
            else:
                ci_push.republish(ports.workspace, path, ref, sleep=sleep)
        else:
            if dispatch is None:
                return _Fresh(stopped=MergeResult("stopped", "ci_dispatch_undeclared"))
            ci_push.publish_with_retry(
                lambda: ports.workspace.publish_dispatch_ref(path, task_id), sleep=sleep
            )
            refused = ports.tracker.dispatch_ci(dispatch.workflow, "", {dispatch.sha_input: tested})
            if refused is not None:
                # A refusal ends the pair at once: no second dispatch (as `full_checks`).
                ended: MergeCiRecord = {
                    **record,
                    "republished": True,
                    "outcome": "no_verdict",
                    "cause": refused.cause,
                }
                _write_pairs(ports, task_id, pairs, ended)
                return _Fresh(cause=refused.cause)
    except GitError as exc:
        # Git's words reach the log only through `scrub`; a `PushFailed` has done it already.
        said = (
            f"after {exc.attempts} attempt(s) ({exc.kind}): {exc.said}"
            if isinstance(exc, ci_push.PushFailed)
            else scrub(str(exc))
        )
        log(f"agentic: merge: {task_id}: could not publish the fresh CI run: {said}")
        return _Fresh(stopped=MergeResult("stopped", "ci_publish_failed"))
    finally:
        release_checkout(ports, ports.workspace.checkout_path(name))
        ports.workspace.remove_checkout(name)
    return _Fresh(stopped=MergeResult("stopped", "ci_wait"))


def _read_pair(
    task_id: str,
    pairs: list[MergeCiRecord],
    pair: MergeCiRecord,
    head: str,
    master: str,
    names: tuple[str, ...],
    dispatch: CiDispatch | None,
    cfg: Configuration,
    ports: Ports,
    clock: Callable[[], str],
    log: Callable[[str], None],
    sleep: Callable[[float], None],
) -> _Fresh:
    """Reads the verdict of the pair's run once (`ci.full_tier_verdict`, `ci.dispatch_verdict`).
    Waiting is not blocking: an undecided run is `stopped ci_wait`, bounded by the grace period
    while no run exists and by the check timeout once one does. The grace is spent once by a
    republish; after that, no run or a run still undecided is `no_verdict`."""
    tested = pair["tested"]
    published = _instant(pair["published_at"])
    # The wait counts from the last time GitHub was asked for a run: a rerun restarts it.
    waited = (_instant(clock()) - _instant(merge_ci.wait_began(pair))).total_seconds()
    report: ci.CiReport | None = None
    run_id: str | None = None
    if pair["mode"] == "push":
        read = ports.tracker.branch_ci(rules.ci_ref(task_id))
        verdict = ci.full_tier_verdict(
            read, tested, names, waited, cfg.check_timeout_s, cfg.ci_start_grace_s
        )
        if isinstance(read, ci.CiReport) and verdict.state in ("green", "red"):
            report = read
            link = next((c.link for c in (*verdict.failing, *read.checks) if c.link), None)
            run_id = _run_id_of(link)
    else:
        if dispatch is None:
            return _Fresh(stopped=MergeResult("stopped", "ci_dispatch_undeclared"))
        since = (published - timedelta(seconds=ci.CI_DISPATCH_SKEW_S)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        runs = ports.tracker.dispatched_ci(dispatch.workflow, since)
        if isinstance(runs, ci.CiUnreadable):
            verdict = ci.full_tier_verdict(
                runs, tested, names, waited, cfg.check_timeout_s, cfg.ci_start_grace_s
            )
        else:
            verdict = ci.dispatch_verdict(
                runs, tested, names, waited, cfg.ci_start_grace_s, cfg.check_timeout_s
            )
            chosen = ci.chosen_run(runs, tested)
            if chosen is not None and verdict.state in ("green", "red"):
                report = ci.CiReport(tested, None, None, None, chosen.jobs)
                run_id = str(chosen.run_id)
    match verdict.state:
        case "waiting":
            return _Fresh(stopped=MergeResult("stopped", "ci_wait"))
        case "cancelled":
            return _cancelled_pair(task_id, pairs, pair, verdict, waited, cfg, ports, clock)
        case "green" | "red" | "timed_out" | "unreachable":
            pass
        case _ as unreachable:
            assert_never(unreachable)
    if report is not None:
        row: MergeCiRecord = {**pair, "outcome": "green" if verdict.state == "green" else "red"}
        row["ci_timing"] = ci.timing(
            pair["published_at"],
            merge_ci.wait_began(pair),
            "green" if verdict.state == "green" else "red",
            report.checks,
            names,
        )
        if run_id is not None:
            row["run_id"] = run_id
        if verdict.state == "red":
            row["failing"] = [check.name for check in verdict.failing]
        _write_pairs(ports, task_id, pairs, row)
        return _Fresh(report=report)
    if verdict.cause == "no_run" and not pair["republished"]:
        return _build_pair(
            task_id,
            pairs,
            pair,
            pair,
            head,
            master,
            pair["mode"] == "dispatch",
            dispatch,
            ports,
            clock,
            log,
            sleep,
        )
    undecided: MergeCiRecord = {**pair, "republished": True, "outcome": "no_verdict"}
    undecided["ci_timing"] = ci.timing(
        pair["published_at"], merge_ci.wait_began(pair), "no_verdict", (), ()
    )
    if verdict.cause is not None:
        undecided["cause"] = verdict.cause
    _write_pairs(ports, task_id, pairs, undecided)
    return _Fresh(cause=verdict.cause)


def _reread_pair(
    task_id: str,
    pairs: list[MergeCiRecord],
    pair: MergeCiRecord,
    names: tuple[str, ...],
    dispatch: CiDispatch | None,
    cfg: Configuration,
    ports: Ports,
    clock: Callable[[], str],
) -> _Fresh:
    """Reads once more the run of a `no_verdict` pair of the current master (GH-552, GH-564): the
    run may have finished after the grace the pair spent. The wait counts from
    `merge_ci.wait_began`. Nothing is written before the read. `green` or `red` rewrites the pair
    with `reread_at` and the outcome; `timed_out` or `unreachable` (the bound is spent) writes
    `reread_at` and `reread_state`, which spends the reread; `waiting` or `cancelled` (the run
    lives on) writes nothing and the gate stops `ci_wait`. Nothing is republished, rerun, pushed
    or dispatched from a reread."""
    tested = pair["tested"]
    waited = (_instant(clock()) - _instant(merge_ci.wait_began(pair))).total_seconds()
    report: ci.CiReport | None = None
    run_id: str | None = None
    if pair["mode"] == "push":
        read = ports.tracker.branch_ci(rules.ci_ref(task_id))
        verdict = ci.full_tier_verdict(
            read, tested, names, waited, cfg.check_timeout_s, cfg.ci_start_grace_s
        )
        if isinstance(read, ci.CiReport) and verdict.state in ("green", "red"):
            report = read
            link = next((c.link for c in (*verdict.failing, *read.checks) if c.link), None)
            run_id = _run_id_of(link)
    else:
        if dispatch is None:
            return _Fresh(stopped=MergeResult("stopped", "ci_dispatch_undeclared"))
        since = (
            _instant(pair["published_at"]) - timedelta(seconds=ci.CI_DISPATCH_SKEW_S)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        runs = ports.tracker.dispatched_ci(dispatch.workflow, since)
        if isinstance(runs, ci.CiUnreadable):
            verdict = ci.full_tier_verdict(
                runs, tested, names, waited, cfg.check_timeout_s, cfg.ci_start_grace_s
            )
        else:
            verdict = ci.dispatch_verdict(
                runs, tested, names, waited, cfg.ci_start_grace_s, cfg.check_timeout_s
            )
            chosen = ci.chosen_run(runs, tested)
            if chosen is not None and verdict.state in ("green", "red"):
                report = ci.CiReport(tested, None, None, None, chosen.jobs)
                run_id = str(chosen.run_id)
    if verdict.state in ("timed_out", "unreachable"):
        spent: MergeCiRecord = {**pair, "reread_at": clock(), "reread_state": verdict.state}
        _write_pairs(ports, task_id, pairs, spent)
        return _Fresh(cause=pair.get("cause"))
    if report is None or verdict.state not in ("green", "red"):
        return _Fresh(stopped=MergeResult("stopped", "ci_wait"))
    outcome: Literal["green", "red"] = "green" if verdict.state == "green" else "red"
    row: MergeCiRecord = {**pair, "reread_at": clock(), "outcome": outcome}
    row.pop("cause", None)
    row["ci_timing"] = ci.timing(
        pair["published_at"], merge_ci.wait_began(pair), outcome, report.checks, names
    )
    if run_id is not None:
        row["run_id"] = run_id
    if outcome == "red":
        row["failing"] = [check.name for check in verdict.failing]
    _write_pairs(ports, task_id, pairs, row)
    return _Fresh(report=report)


def _cancelled_pair(
    task_id: str,
    pairs: list[MergeCiRecord],
    pair: MergeCiRecord,
    verdict: ci.FullTierVerdict,
    waited: float,
    cfg: Configuration,
    ports: Ports,
    clock: Callable[[], str],
) -> _Fresh:
    """A fresh run left only by a superseded or cancelled workflow run (GH-539): never a red pair.
    A pair with no `rerun_at` reruns the run once and keeps waiting; one that was rerun waits out
    `ci_start_grace_s` from `rerun_at` (`waited` counts from it) for the run to restart, and after
    that its cancellation is `no_verdict` with the cause `not_started`, the way a run that never
    started ends. A rerun the platform refuses, or a run id that cannot be read, is the same end."""
    if "rerun_at" not in pair:
        run_id = next(
            (found for c in verdict.cancelled if (found := ci.run_id_of_link(c.link))), None
        )
        if run_id is not None and ports.tracker.rerun_ci_run(run_id) is None:
            _write_pairs(
                ports, task_id, pairs, merge_ci.reopened({**pair, "run_id": run_id}, clock())
            )
            return _Fresh(stopped=MergeResult("stopped", "ci_wait"))
    elif waited < cfg.ci_start_grace_s:
        return _Fresh(stopped=MergeResult("stopped", "ci_wait"))
    undecided: MergeCiRecord = {
        **pair,
        "republished": True,
        "outcome": "no_verdict",
        "cause": "not_started",
    }
    undecided["ci_timing"] = ci.timing(
        pair["published_at"], merge_ci.wait_began(pair), "no_verdict", (), ()
    )
    _write_pairs(ports, task_id, pairs, undecided)
    return _Fresh(cause="not_started")


def _run_id_of(link: str | None) -> str | None:
    """The workflow run a check link names, or None when it names none."""
    found = _RUN_ID.search(link or "")
    return found.group(1) if found else None


def _recorded_masters(record: object) -> tuple[str, ...] | None:
    """The masters the rows of a `merge_checks` record ran on (`presubmit.row_masters`), or
    None when any row names none it can read: that is a record in doubt (invariant 2), never
    one `row_masters` quietly shortens. A record with no row at all gives `()`."""
    if not isinstance(record, Mapping):
        return ()
    rows = record.get("checks")
    if not isinstance(rows, list):
        return ()
    seen: dict[str, None] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        one = presubmit.row_masters({"master": record.get("master"), "checks": [row]})
        if not one:
            return None
        seen.setdefault(one[0])
    return tuple(seen)


def _inert_from(document: TaskState, master: str) -> list[str]:
    """The row masters, other than the current one, a merge on a covered pass rested on."""
    return [m for m in _recorded_masters(document.get("merge_checks")) or () if m != master]


def _masters_covered(
    ports: Ports,
    document: TaskState,
    head: str,
    master: str,
    base: str,
    touched: tuple[str, ...] | None,
    globs: list[tuple[str, ...]],
    gains: dict[str, tuple[str, ...] | None],
) -> bool | None:
    """`MergeFacts.masters_covered` (GH-353) for the recorded rows against `master`: None
    when every row ran on it (or there is no row), so the decision compares the record's
    master as it always did; else `master_gain.masters_covered`'s verdict. The head's
    declared globs are read once, and a gain only when some are declared; whatever cannot be
    read -- a broken declaration, a master that is not an ancestor, a failed diff -- is a
    gain of None, which is not covered."""
    masters = _recorded_masters(document.get("merge_checks"))
    if masters is None:
        return False
    behind = [m for m in masters if m != master]
    if not behind:
        return None
    if not globs:
        try:
            globs.append(ports.stack.merge_inert(head))
        except ValueError:
            globs.append(())
    if globs[0]:
        for row_master in behind:
            if row_master not in gains:
                gains[row_master] = _gain(ports, row_master, master)
    return master_gain.masters_covered(
        master, masters, gains, globs[0], list(ports.stack.setup(base).values()), touched
    )


def _gain(ports: Ports, row_master: str, master: str) -> tuple[str, ...] | None:
    """What `master` gained since `row_master`: None unless `master` has it as an ancestor
    and git could list the difference."""
    if ports.workspace.contains(master, row_master) != "yes":
        return None
    try:
        return ports.workspace.gain_paths(row_master, master)
    except GitError:
        return None


def _with_deferral(
    ports: Ports,
    facts: rules.MergeFacts,
    document: TaskState,
    base: str,
    head: str | None,
    report: ci.CiReport | ci.CiUnreadable | None,
) -> rules.MergeFacts:
    """`facts` plus what the CI says about the jobs covering the checks the latest `full_checks`
    record deferred (GH-453). Read only from a record whose head is the pull request head
    (`same_commit`): any other imposes nothing. Each job is judged only by `ci.covering_state`;
    a deferred check the stack no longer maps to a job is uncovered. A stack whose `ci_covers`
    cannot be read covers nothing."""
    record = document.get("full_checks")
    deferred = record.get("deferred") if record else None
    if not record or not deferred or head is None or not rules.same_commit(record["head"], head):
        return facts
    try:
        covers = ports.stack.ci_covers(base)
    except ValueError:
        covers = {}
    jobs = [covers[name].job if name in covers else None for name in deferred]
    states = [
        ci.covering_state(report, job, head) if job is not None else "uncovered" for job in jobs
    ]
    return replace(
        facts,
        deferral=rules.deferral_state(states),
        deferral_jobs=tuple(dict.fromkeys(job for job in jobs if job is not None)),
    )


def _merges_on_ci(facts: rules.MergeFacts) -> bool:
    """The `merge` rested on the CI alone: declared by the stack (a name only the plan
    required is never enough, GH-371), green on this head and fresh. The same four facts
    `rules.auto_merge_decision` merges on before it looks at any local check."""
    return (
        bool(set(facts.ci_required) - set(facts.ci_plan_only))
        and facts.ci_verdict is not None
        and facts.ci_verdict.state == "green"
        and facts.ci_fresh is True
        and rules.ci_trusted(facts.ci_untrusted, facts.ci_dispatched)
        and facts.deferral != "uncovered"
    )


def _ci_links(
    report: ci.CiReport | ci.CiUnreadable | None, declared: tuple[str, ...]
) -> list[dict[str, str]]:
    """The declared checks the merge relied on, each with the run link it was read at."""
    if not isinstance(report, ci.CiReport):
        return []
    claimed = {id(run) for runs in ci.runs_of(report.checks, declared).values() for run in runs}
    return [
        {"name": check.name, "link": check.link} for check in report.checks if id(check) in claimed
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
    pr_head = pull_request.head_oid if pull_request is not None else None
    return replace(
        _merge_facts(task_id, raw, pull_request, eval_set, number, master),
        touched_paths=touched,
        ci_untrusted=rules.ci_untrusted(touched),
        # GH-462: whether the recorded dispatched run of the base's workflow stands for this head.
        ci_dispatched=rules.ci_dispatched(raw.get("full_checks"), pr_head),
    )


def settlement_facts(
    task_id: str,
    raw: TaskState,
    pull_request: PullRequest | None,
    number: int,
    master: str | None,
) -> rules.MergeFacts:
    """The facts `merge_task` starts from before it reads any diff, a worktree or the CI --
    the builder `agentic restore` asks `plan_settlement.settleable` over, so a settlement
    and a merge never read a document two ways. No eval set is read (`in_eval_set` is `None`)."""
    return _merge_facts(task_id, raw, pull_request, None, number, master)


def run_checks(
    task_id: str,
    raw: TaskState,
    head: str,
    master: str,
    base: str,
    cfg: Configuration,
    ports: Ports,
    attempt: int | None,
    log: Callable[[str], None],
    only: Collection[str] | None = None,
) -> None:
    """The checks at the task's base, on the pull request's head merged with `master`,
    and the record of both (`merge_attempts`, `merge_checks`). The worktree is removed
    whatever happens; a merge that conflicts, a setup step that fails or a tree the
    checks could not run in all count as the checks failing."""
    if attempt is not None:
        ports.documents.record_closure(
            task_id, {"merge_attempts": {"head": head, "count": attempt}}
        )
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
            checks = declared = {
                name: command
                for name, command in ports.stack.checks(base).items()
                if only is None or name in only
            }
            gate = CheckoutPresubmit(
                ports,
                path,
                _NOTHING_BEFORE,
                checks,
                cfg.check_timeout_s,
                priority=CheckPriority.LAND,
                wait_max_s=cfg.check_wait_max_s,
                witness=ports.stack.witness(base),
                parallel=ports.stack.parallel_checks(base),
                reuse=reuse_of(ports, base),
                rerun_forms=ports.stack.rerun_checks(base),
                flake_gate="merge",
                readiness=readiness_of(ports, base, cfg.check_cores),
            )
            gate()
    except GitError as exc:
        log(f"agentic: merge: {task_id}: could not build the worktree: {exc}")
        failed_step = "worktree"
    finally:
        release_checkout(ports, ports.workspace.checkout_path(name))
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
        gate=gate.gate if gate is not None else None,
        row_master=master,
    )
    if rules.restored(raw):
        # Dated only on a restored task, so the landing can tell this run's record from the
        # archived one's (`landing_plan._gate_reran`); any other document is written as before.
        record["at"] = _now()
    previous = raw.get("merge_checks")
    if only is not None and isinstance(previous, Mapping):
        # Only the failed checks ran: the rest stay as the earlier run recorded them.
        record = presubmit.rerun_record(previous, record)
    closure: dict[str, object] = {"merge_checks": record}
    if gate is not None and (flakes := gate.flakes()):
        closure["flakes"] = add_flakes(
            raw.get("flakes", []), ci_flakes.dated(flakes, ci_flakes.now())
        )
    ports.documents.record_closure(task_id, closure)
    shown = presubmit.failed_checks_markdown(record, head, neutralise=False)
    for number, line in enumerate(shown.splitlines()):
        log(f"agentic: merge: {task_id}: {line}" if number == 0 else f"    {line}")


def _setup(
    base: str, path: Path, cfg: Configuration, ports: Ports
) -> tuple[str, CommandResult] | None:
    """None when every setup step passed, else the first one that did not, with what it
    said: the steps after it do not run."""
    for step, command in ports.stack.setup(base).items():
        result = ports.commands.run(path, command, cfg.check_timeout_s)
        if not result.passed:
            return step, result
    ports.workspace.record_setup_tree(path)
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
    pull_request: PullRequest | None = None,
    inert_from: list[str] | None = None,
) -> MergeResult:
    """`relied` is the CI links a merge on the CI rested on (None: it rested on the local
    checks); `ci_declared` says the product declares its CI, which is the only case that
    records how the merge was decided (`auto_merge_via`). `inert_from` is the older row
    masters the recorded pass rested on through an inert gain (GH-353), if any."""
    # A `merge` rests on recorded checks, or on a fresh CI: either way on this master.
    master = facts.current_master
    assert master is not None
    # The last look at master: a push since the checks makes them stale, and nothing is
    # written (the next round decides again).
    again = ports.workspace.upstream()
    if again.sha != master:
        log(f"agentic: merge: {task_id}: master moved since the checks ({master} -> {again.sha})")
        return MergeResult("refused", "master_moved")
    # GH-255: the last look at the merge itself. A head that conflicts with this master is
    # refused without an intent, a failure count or a comment (`merge_conflict` is not in
    # `COMMENTED`) and `Tracker.merge` is never called. GH-500: the one `merge_conflict` row is
    # written, as the GH-492 path does, so the next round refuses it from the document and hands
    # over to the landing instead of starting this verb again. Unread changes nothing.
    conflicts, source = read_conflicts(ports, pull_request, head, master, recorded=False)
    if conflicts is True:
        log(f"agentic: merge: {task_id}: {head} conflicts with master {master} ({source})")
        ports.documents.record_closure(
            task_id, {"merge_conflict": merge_conflict.record(head, master, clock())}
        )
        return MergeResult("refused", "merge_conflict")
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
    # The system's own record and configuration the change touches (GH-239): no longer a
    # reason to refuse, so the merge says so instead, in the document and the comment.
    guarded = [path for path in facts.touched_paths or () if rules.guarded_path(path, task_id)]
    if guarded:
        intent["auto_merge_guarded"] = guarded
    if inert_from:
        intent["auto_merge_inert_from"] = inert_from
    ports.documents.record_closure(task_id, intent)
    try:
        method = ports.tracker.merge(reference, head)
    except TrackerUnreachable as exc:
        # Only the `repo view` read can reach here (GH-290): nothing was sent to merge, so
        # the intent is withdrawn and nothing is counted or commented; the next round decides
        # again. A lost `pr merge` response is `MergeCallFailed("gh_unreadable")` below.
        ports.documents.record_closure(task_id, {"auto_merge_head": ""})
        log(
            f"agentic: merge: {task_id}: gh could not reach GitHub ({exc}): "
            "nothing merged, next round retries"
        )
        return MergeResult("stopped", "unreachable")
    except MergeCallFailed as exc:
        return _after_failure(task_id, reference, head, facts, exc.failure, str(exc), ports, log)
    except ValueError as exc:
        return _after_failure(
            task_id, reference, head, facts, "gh_unreadable", str(exc), ports, log
        )
    ports.documents.record_closure(
        task_id,
        {
            "auto_merged_at": clock(),
            "auto_merge_method": method,
            **files_patch(reference, ports, log),
        },
    )
    text = f"{AUTO_MERGED}{head} via {method}"
    if guarded:
        text += "\n\nGuarded paths changed:\n" + "\n".join(f"- {path}" for path in guarded)
    if relied is not None:
        text += "\n\nCI relied on:\n" + "\n".join(
            f"- {row['name']}: {row['link']}" for row in relied
        )
    if accepted := _accepted_without_a_fix(ports.documents.load(task_id) or {}):
        text += "\n\nAccepted without a fix:\n" + accepted
    _note(task_id, text, ports)
    return MergeResult("merged", method=method)


def _note(task_id: str, text: str, ports: Ports) -> None:
    """Posts an informative comment of the merge (GH-389). A GitHub error that outlasts the
    retries does not turn a merge or a refusal into a crash: the failure goes to the
    document as a `tracker_failures` row. The merge child has no state owner, so the row
    is applied to the stored list with the state's own reducer and written with
    `record_closure`, the only line-level write the child ever makes."""
    if (failure := ports.tracker.note(task_id, text)) is None:
        return
    stored = (ports.documents.load(task_id) or {}).get("tracker_failures")
    rows = add_tracker_failures(stored if isinstance(stored, list) else [], [failure.record()])
    ports.documents.record_closure(task_id, {"tracker_failures": rows})


def _accepted_without_a_fix(document: TaskState) -> str:
    """The task's own `important` rows (`rules.important_partition`'s `owned` and `plan`)
    still open at the merge, one line each, a row owned by several units once; empty when
    there are none (the comment then has no such section)."""
    part = rules.important_partition(document)
    rows: dict[tuple[str, str], str] = {}
    for row in [*(r for owned in part.owned.values() for r in owned), *part.plan]:
        rows.setdefault((row["location"], row["claim"]), f"- `{row['location']}`: {row['claim']}")
    return "\n".join(rows.values())


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
            return _refuse(task_id, head, "merge_failed", ports, failure=failure, facts=facts)
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


def _ci_evidence(
    reason: str, facts: rules.MergeFacts, report: ci.CiReport | ci.CiUnreadable | None = None
) -> str:
    """What a CI refusal says, from structured facts only: one line per failing check
    (name, conclusion, link) with no tail, the declared names never reported, or `gh` and
    the cause class of an unreadable report."""
    if facts.deferral in ("red", "pending") and isinstance(report, ci.CiReport):
        return _deferral_evidence(facts, report)
    verdict = facts.ci_verdict
    if reason == "ci_unknown_names":
        text = ci.unreported_text(
            facts.ci_unreported, facts.ci_reported, facts.ci_head or "", facts.ci_plan_only
        )
        return f"Planning defect, not a CI failure: {text}"
    if reason == "ci_failed" and verdict is not None:
        return "\n".join(
            f"- {check.name} {check.conclusion or check.status} {check.link}"
            for check in verdict.failing
        )
    if reason == "ci_missing" and verdict is not None:
        if verdict.missing:
            text = f"CI did not report: {', '.join(verdict.missing)}"
            if facts.ci_plan_only and isinstance(report, ci.CiReport):
                # A plan-required name (GH-371): say what the head's report did carry, so the
                # human sees a misspelt or renamed check at once.
                text += f"; the report carried: {', '.join(ci.reported_names(report)) or 'none'}"
            return text
        return f"CI checks still pending: {', '.join(facts.ci_required)}"
    cause = facts.ci_unreadable.cause if facts.ci_unreadable is not None else "gh_unreadable"
    return f"`gh` could not read the CI report ({cause})"


def _deferral_evidence(facts: rules.MergeFacts, report: ci.CiReport) -> str:
    """What a refusal on a deferral says (GH-453): the covering jobs of the deferred checks
    that are not green, name, conclusion and link, from the report alone."""
    claimed = {
        id(run) for runs in ci.runs_of(report.checks, facts.deferral_jobs).values() for run in runs
    }
    runs = [check for check in report.checks if id(check) in claimed]
    lines = [
        f"- {check.name} {check.conclusion or check.status} {check.link}"
        for check in runs
        if check.conclusion != "SUCCESS"
    ]
    return "\n".join([f"The CI job covering a deferred check ({facts.deferral}):", *lines])


def _fresh_run_evidence(reason: str, facts: rules.MergeFacts | None, cause: CiCause | None) -> str:
    """What a refusal of the gate's fresh run says (GH-463), from closed facts only: the
    `CiCause` of the missing verdict, the stale pairs spent, or a fixed sentence."""
    if reason == "ci_untrusted_no_dispatch":
        return (
            "The head changes the CI's own definition and the stack declares no `ci_dispatch`: "
            "no workflow of the base can judge it. Declare `ci_dispatch` in `.agentic/stack.yaml`."
        )
    if reason == "ci_stale_exhausted":
        spent = merge_ci.pair_counts(facts.merge_ci, facts.pr_head).stale if facts else 0
        return (
            f"Master overtook {spent} of {ci.CI_STALE_PAIRS_MAX} fresh CI runs of this head; "
            "the landing takes master in."
        )
    if cause is None:
        return "GitHub gave no verdict for the head merged with master within the check timeout."
    return f"GitHub gave no verdict for the head merged with master ({cause})."


def _adr_evidence(facts: rules.MergeFacts) -> str:
    """What an `adr_number_taken` refusal says, from the structured collision only: one
    `old -> new` line per move, or the ambiguity's reason and detail."""
    collision = facts.adr_collision
    if isinstance(collision, adr_numbers.Renaming):
        return "\n".join(f"- {old} -> {new}" for old, new in collision.moves)
    if isinstance(collision, adr_numbers.Ambiguous):
        return f"Ambiguous: {collision.reason}: {collision.detail}"
    return ""


def _perf_evidence(facts: rules.MergeFacts) -> str:
    """What a `perf_unverified` refusal says, one fixed line from the structured facts: the
    run's head when it differs from the pull request's, else its verdict (`none`: no run)."""
    if facts.perf_verdict == "approved":
        return f"measured {facts.perf_head or 'none'}, head {facts.pr_head}"
    return f"verdict {facts.perf_verdict or 'none'}"


def _criteria_evidence(facts: rules.MergeFacts) -> str:
    """What a `criteria_over_cap` refusal says: the messages `knowledge` wrote, one per
    line (file, count, cap), and nothing this module counted. An unreadable read has none."""
    return "\n".join(f"- {message}" for message in facts.criteria_regressed or ())


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
    reason: rules.MergeRefusal,
    ports: Ports,
    failure: rules.MergeFailure | None = None,
    facts: rules.MergeFacts | None = None,
    report: ci.CiReport | ci.CiUnreadable | None = None,
    cause: CiCause | None = None,
) -> MergeResult:
    """Comments a refusal the human can act on, once per (head, reason): the last
    `Auto-merge refused:` comment naming the same head and reason is enough. A
    `merge_failed` one names the closed class of the last failure (or `unknown`) and a
    fixed advice sentence, never the CLI's own text."""
    if reason in COMMENTED and head is not None:
        # Only a refusal the landing has nothing to do about is left to the human
        # (GH-239); any other names the step it takes (`Landing:` below).
        step = (
            landing_plan.step_for(ports.documents.load(task_id) or {}, reason, facts)
            if facts is not None
            else None
        )
        closing = LEFT_TO_HUMAN if step is None or step.kind == "none" else ""
        first = f"{AUTO_MERGE_REFUSED}{head} {reason}"
        summary = ""
        text = "\n\n".join(part for part in (first, closing) if part)
        if reason == "checks_failed":
            recorded = (ports.documents.load(task_id) or {}).get("merge_checks")
            record = recorded if isinstance(recorded, dict) else {}
            summary = presubmit.failed_checks_summary(record, head)
            evidence = presubmit.failed_checks_markdown(record, head)
            text = "\n\n".join(part for part in (first, evidence, closing) if part)
        if facts is not None and reason == "adr_number_taken":
            text = "\n\n".join(part for part in (first, _adr_evidence(facts), closing) if part)
        if facts is not None and reason in (
            "ci_failed",
            "ci_missing",
            "ci_unknown_names",
            "ci_unreadable",
        ):
            text = "\n\n".join(
                part for part in (first, _ci_evidence(reason, facts, report), closing) if part
            )
        if facts is not None and reason == "perf_unverified":
            text = "\n\n".join(part for part in (first, _perf_evidence(facts), closing) if part)
        if facts is not None and reason == "criteria_over_cap":
            text = "\n\n".join(part for part in (first, _criteria_evidence(facts), closing) if part)
        if reason in ("ci_no_verdict", "ci_stale_exhausted", "ci_untrusted_no_dispatch"):
            text = "\n\n".join(
                part for part in (first, _fresh_run_evidence(reason, facts, cause), closing) if part
            )
        if reason == "merge_failed":
            advice = (
                rules.MERGE_FAILURE_ADVICE[failure]
                if failure is not None
                else rules.MERGE_FAILURE_UNKNOWN_ADVICE
            )
            first = f"{AUTO_MERGE_REFUSED}{head} gh pr merge failed: {failure or 'unknown'}"
            text = "\n\n".join(part for part in (first, advice, closing) if part)
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
            _note(task_id, text, ports)
        # Announced only when the system will take it: an inert step says nothing.
        if step is not None and landing_plan.takes_action(step.kind, landing_plan.LIVE_STEPS):
            line = f"{LANDING}{head} {step.kind}"
            # Once per (head, step): not when the document's `landing` list already holds
            # the key, and not when the comment is already on the issue (the step may not
            # have been recorded yet).
            taken = landing_rules.already_dispatched(
                landing_plan.live_entries(ports.documents.load(task_id) or {}),
                head,
                step.kind,
                facts.current_master if facts is not None else None,
                names=step.names,
            )
            if not taken and not any(
                body.splitlines()[:1] == [line] for body in ports.tracker.issue_comments(task_id)
            ):
                _note(task_id, line, ports)
    return MergeResult("refused", reason)
