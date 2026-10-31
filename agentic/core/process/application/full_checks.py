"""The `full_checks` node (GH-358): the whole suite, once, on the integrated head.

A product that declares `delivery_checks` gates each delivery on the fast tier only; this node
is where the rest of `checks` runs, with no affected form substituted, before the pull request
opens (`rules.after_integrate` sends the task here once every unit is approved). No role runs
and nothing is paid: it is the system running the product's own commands.

In order: the task checkout (a setup that fails escalates as `deliver`'s does), a clean tree
required, every declared check run once through the same `CheckoutPresubmit` the merge gate
uses, the tree put back as its head holds it whatever the checks did (`restore_clean`, never
`-x`), the record and the `Full checks` section written. A red run reopens the integration
with one row per failed check (`rules.full_checks_return`); a green one writes a passed record
and nothing else, and the fan-in goes on.

A product that declares `ci:` (GH-376) does not run the suite here: the integrated head is
published to its own ref (`rules.ci_ref`) and the node waits for the declared jobs on exactly
that head (`ci.full_tier_verdict`), recording their verdict. Any way the CI cannot be used
runs the local tier above instead, and the record and the section say why."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any

import anyio
from pydantic import TypeAdapter

from agentic.shared.configuration import Configuration

from ..domain import ci, presubmit, rules
from ..domain.check_schedule import CheckPriority
from ..domain.ci import CiCheck, CiReport, CiUnreadable, FullTierVerdict
from ..domain.presubmit import CheckResult, CiCover
from ..domain.roles import DEVELOPER_INTEGRATOR
from ..domain.task import (
    WAITING_ON_HUMAN,
    FallbackCause,
    FullCheckEntry,
    FullChecksRecord,
    TaskState,
)
from .checkouts import Checkout, SetupFailed, task_base, task_checkout_in_thread
from .ports import GitError, Ports
from .presubmit import CheckoutPresubmit, reuse_of
from .run_role import finish

SECTION = "Full checks"
_NOTHING_BEFORE = ""
_ENTRIES = TypeAdapter(list[FullCheckEntry])
_REPRODUCE = (
    "The integrated head failed CI; reproduce it with the product's declared checks in your "
    "checkout."
)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


async def full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    *,
    sleep: Sleep = anyio.sleep,
    clock: Clock = time.monotonic,
) -> TaskState:
    """`sleep` and `clock` are the CI wait's, injectable so a test does not wait."""
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    workspace = ports.workspace
    if not workspace.is_clean(task.path):
        return finish(state, _dirty(task.branch), ports)
    base = task_base(state, ports)
    names = ports.stack.ci(base)
    if not names:
        return await _local_full_checks(state, cfg, ports, task, base, None)
    outcome = await _ci_full_checks(state, cfg, ports, task, base, names, sleep, clock)
    if isinstance(outcome, str):
        return await _local_full_checks(state, cfg, ports, task, base, outcome)
    return finish(state, outcome, ports)


async def _local_full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    base: str,
    fallback: FallbackCause | None,
) -> TaskState:
    """The suite run on this machine. `fallback` is why a `ci:` product ended up here: the
    record and the section carry it. `None` for a product without `ci:`, whose record gets
    no new key."""
    workspace = ports.workspace
    declared = ports.stack.checks(base)
    gate = CheckoutPresubmit(
        ports,
        task.path,
        _NOTHING_BEFORE,
        declared,
        cfg.check_timeout_s,
        priority=CheckPriority.INTEGRATION,
        wait_max_s=cfg.check_wait_max_s,
        witness=ports.stack.witness(base),
        parallel=ports.stack.parallel_checks(base),
        reuse=reuse_of(ports, base),
        flake_gate="full_checks",
    )
    head = workspace.full_head(task.path)
    started = time.monotonic()
    try:
        # Called once, with no refusal loop and no affected plan: the suite as declared.
        await anyio.to_thread.run_sync(gate)
    finally:
        # A check that edits a tracked file or writes an untracked one must not refuse the
        # next delivery of a reopened unit, nor the landing's own clean-tree checks.
        # Shielded: it must also run when the signal's cancellation is what unwinds us.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(workspace.restore_clean, task.path)
    covers = ports.stack.ci_covers(base) if fallback is not None else {}
    rows, deferred = _deferrals(gate.results, fallback, covers)
    merged = presubmit.merge_checks_record(
        head=head,
        master=base,
        duration_s=round(time.monotonic() - started, 1),
        declared=declared,
        results=rows,
        witness=gate.witness_result,
        gate=gate.gate,
    )
    record = _record(state, merged, declared, gate, deferred)
    lines = [_deferred_line(name, covers[name], cfg.check_timeout_s) for name in deferred]
    text = presubmit.failed_checks_markdown(
        _without_deferred(merged, deferred), head, neutralise=False
    )
    if record["passed"]:
        patch: TaskState = {"full_checks": record}
        reused = {entry["name"] for entry in record["results"] if entry.get("reused")}
        flaked = {entry["name"] for entry in record["results"] if entry.get("flake")}
        shown = [_shown(name, reused, flaked) for name in declared if name not in deferred]
        text = f"Passed on `{head}`: {', '.join(shown)}."
        if lines:
            text += "\n\n" + "\n".join(lines)
        if breaches := presubmit.budget_breaches(gate.results, ports.stack.check_budgets(base)):
            # A record and a sentence, never a return: a slow suite is not a failed one.
            record["over_budget"] = [
                {**b, "duration_s": _shown_up(b["duration_s"])} for b in breaches
            ]
            text += f" {_over_budget_sentence(record['over_budget'])}"
    else:
        patch = rules.full_checks_return(state, record, cfg.max_changes_requested_per_pair)
        text = text or f"Failed on `{head}`: no check output was recorded."
        if lines:
            text += "\n\n" + "\n".join(lines)
    if fallback is not None:
        record["ran_on"] = "local"
        record["ci_fallback"] = fallback
        text = f"CI not used ({fallback}): ran locally.\n\n{text}"
    patch["sections"] = _section(state, record["version"], text)
    if flakes := gate.flakes():
        patch["flakes"] = flakes
    return finish(state, patch, ports)


async def _ci_full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    base: str,
    names: tuple[str, ...],
    sleep: Sleep,
    clock: Clock,
) -> dict[str, Any] | FallbackCause:
    """The patch of a run decided on GitHub, or the cause for running the local tier. The
    paths are read from the workspace only and the head is published to the task's own CI
    ref; the wait takes no check slot and runs no command (ADR-0070)."""
    workspace = ports.workspace
    head = workspace.full_head(task.path)
    try:
        paths: tuple[str, ...] | None = workspace.changed_paths(base, head)
    except GitError:
        paths = None
    if rules.ci_untrusted(paths) is not False:
        return "untrusted"
    ref = rules.ci_ref(state["id"])
    try:
        await anyio.to_thread.run_sync(workspace.publish_ci_ref, task.path, ref)
    except GitError:
        return "publish_failed"
    gate_names = rules.ci_gate_names(names, state.get("ci_required") or []).names
    verdict, read, waited = await _wait_ci(ports, ref, head, gate_names, cfg, sleep, clock)
    if verdict.state == "unreachable":
        return verdict.cause or "no_run"
    assert isinstance(read, CiReport)  # a verdict other than `unreachable` came from a report
    if verdict.state == "green":
        return _ci_green(state, head, gate_names, read, waited)
    return _ci_red(state, cfg, head, gate_names, verdict, read, waited)


async def _wait_ci(
    ports: Ports,
    ref: str,
    head: str,
    names: tuple[str, ...],
    cfg: Configuration,
    sleep: Sleep,
    clock: Clock,
) -> tuple[FullTierVerdict, CiReport | CiUnreadable, float]:
    """Read the CI of `ref` every `CI_POLL_S` until a verdict that is not `waiting`; the one
    bound is `cfg.check_timeout_s`, counted from the first read."""
    started = clock()
    while True:
        read = await anyio.to_thread.run_sync(ports.tracker.branch_ci, ref)
        waited = clock() - started
        verdict = ci.full_tier_verdict(read, head, names, waited, cfg.check_timeout_s)
        if verdict.state != "waiting":
            return verdict, read, waited
        await sleep(ci.CI_POLL_S)


def _ci_record(
    state: TaskState,
    head: str,
    passed: bool,
    waited: float,
    results: list[FullCheckEntry],
) -> FullChecksRecord:
    previous = state.get("full_checks")
    return {
        "version": previous["version"] + 1 if previous else 1,
        "head": head,
        "passed": passed,
        "duration_s": round(waited, 1),
        "declared": {"name": "ci"},
        "results": results,
        "witness": None,
        "ran_on": "ci",
    }


def _link(report: CiReport, name: str) -> str:
    return next((check.link for check in report.checks if check.name == name and check.link), "")


def _ci_green(
    state: TaskState, head: str, names: tuple[str, ...], read: CiReport, waited: float
) -> dict[str, Any]:
    record = _ci_record(
        state, head, True, waited, [{"name": name, "passed": True} for name in names]
    )
    links = "; ".join(f"{name}: {link}" for name in names if (link := _link(read, name)))
    text = f"Passed on CI for `{head}`: {', '.join(names)}."
    if links:
        text += f" Runs: {links}."
    return {
        "full_checks": record,
        "sections": _section(state, record["version"], text),
    }


def _ci_red(
    state: TaskState,
    cfg: Configuration,
    head: str,
    names: tuple[str, ...],
    verdict: FullTierVerdict,
    read: CiReport,
    waited: float,
) -> dict[str, Any]:
    """A red job, or a bound that ran out. A job still pending beside a red one is not a
    failure of its own and gets no row; at the bound it does, and the section names it."""
    timed_out = verdict.state == "timed_out"
    red = {check.name: check for check in reversed(verdict.failing)}
    results: list[FullCheckEntry] = []
    for name in names:
        if name in red:
            results.append({"name": name, "passed": False, "link": red[name].link})
        elif name not in verdict.pending:
            results.append({"name": name, "passed": True})
        elif timed_out:
            results.append({"name": name, "passed": False})
    record = _ci_record(state, head, False, waited, results)
    record["failed_step"] = next(iter(red), "timeout")
    lines = [f"Failed on CI for `{head}`."]
    lines += [_red_line(check) for check in verdict.failing]
    if timed_out:
        lines.append(
            f"Not finished within {cfg.check_timeout_s:g}s: still pending "
            f"{', '.join(verdict.pending)}."
        )
    lines.append(_REPRODUCE)
    patch = rules.full_checks_return(state, record, cfg.max_changes_requested_per_pair)
    patch["sections"] = _section(state, record["version"], "\n\n".join(lines))
    return patch


def _red_line(check: CiCheck) -> str:
    return f"- `{check.name}`: {check.conclusion}" + (f" ({check.link})" if check.link else "")


def _record(
    state: TaskState,
    merged: dict[str, object],
    declared: dict[str, str],
    gate: CheckoutPresubmit,
    deferred: list[str],
) -> FullChecksRecord:
    previous = state.get("full_checks")
    record: FullChecksRecord = {
        "version": previous["version"] + 1 if previous else 1,
        "head": str(merged["head"]),
        "passed": merged["passed"] is True,
        "duration_s": float(str(merged["duration_s"])),
        "declared": dict(declared),
        "results": _without_output(_ENTRIES.validate_python(merged["checks"]), deferred),
        "witness": dict(gate.witness_result) if gate.witness_result is not None else None,
    }
    if not record["passed"]:
        record["failed_step"] = str(merged["failed_step"])
    if (taken := gate.gate) is not None:
        record["gate"] = taken
    if deferred:
        record["deferred"] = deferred
    return record


def _deferrals(
    results: list[CheckResult] | None,
    fallback: FallbackCause | None,
    covers: Mapping[str, CiCover],
) -> tuple[list[CheckResult] | None, list[str]]:
    """The rows with `deferred: true` on each check whose last row `presubmit.deferred_verdict`
    defers (GH-453), and those checks' names in the order they ran. The one place the
    verdict is asked; the rows stay the gate's own, never stored."""
    if results is None or fallback is None or not covers:
        return results, []
    chosen = {
        id(row): row["name"]
        for row in presubmit.final_results(results)
        if presubmit.deferred_verdict(row, "full_checks", fallback, covers.get(row["name"]))
    }
    marked: list[CheckResult] = [
        {**row, "deferred": True} if id(row) in chosen else row for row in results
    ]
    return marked, list(chosen.values())


def _deferred_line(name: str, cover: CiCover, timeout_s: float) -> str:
    return f"{name}: timed out after {timeout_s:g}s, deferred to CI job `{cover.job}`"


def _without_deferred(merged: dict[str, object], deferred: list[str]) -> dict[str, object]:
    """`merged` without the entries of the deferred checks: they are no failure, so the
    failure text never names them."""
    checks = merged["checks"]
    assert isinstance(checks, list)  # `merge_checks_record` always writes the list
    return {**merged, "checks": [c for c in checks if c["name"] not in deferred]}


def _shown_up(seconds: float) -> float:
    """A breach's duration to a tenth, rounded up: the verdict compared the raw figure, so
    10.04s against a 10s budget must not be written as `10.0s / 10.0s`, which reads as no
    breach."""
    return math.ceil(round(seconds * 10, 6)) / 10


def _over_budget_sentence(breaches: list[presubmit.BudgetBreach]) -> str:
    """`Over budget: <name> <N>s / <M>s, ...`: declared names and numbers only."""
    return (
        "Over budget: "
        + ", ".join(f"{b['name']} {b['duration_s']}s / {b['budget_s']}s" for b in breaches)
        + "."
    )


def _shown(name: str, reused: set[str], flaked: set[str]) -> str:
    """A check in the passing section: a reused one says so, and a flake says `flake` (red
    on its first run, green on the one rerun), never plain passed (ADR-0066)."""
    marks = [mark for mark, held in (("reused", name in reused), ("flake", name in flaked)) if held]
    return f"{name} ({', '.join(marks)})" if marks else name


def _without_output(
    entries: list[FullCheckEntry], deferred: Collection[str] = ()
) -> list[FullCheckEntry]:
    """The name and verdict of each check: the record holds no command output (the `tail` the
    merge record keeps is free text); the `Full checks` section is where it is read. A reused
    row keeps its `reused: true` (ADR-0065) and a flaked one its `flake: true` (ADR-0066); a
    check with a sample keeps its `duration_s`, the number `check_durations` summed."""
    kept: list[FullCheckEntry] = []
    for entry in entries:
        row: FullCheckEntry = {"name": entry["name"], "passed": entry["passed"]}
        if entry.get("reused"):
            row["reused"] = True
        if entry.get("flake"):
            row["flake"] = True
        if entry["name"] in deferred:
            row["deferred"] = True
        if "duration_s" in entry:
            row["duration_s"] = entry["duration_s"]
        kept.append(row)
    return kept


def _section(state: TaskState, version: int, text: str) -> dict[str, str]:
    """The new section, and its previous run kept as history under the superseded name."""
    sections = {SECTION: text}
    previous = state.get("sections", {}).get(SECTION)
    if previous is not None:
        sections[rules.superseded(SECTION, version - 1)] = previous
    return sections


def _dirty(branch: str) -> dict[str, Any]:
    """The task checkout holds uncommitted work the suite would be run over: no command
    runs, and nothing is cleaned, which could throw away what a human left there."""
    return rules.logged(
        {
            "current_role": DEVELOPER_INTEGRATOR.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "setup",
                f"the task checkout of {branch!r} has uncommitted changes, so the full suite "
                "would not be a run of the integrated head. Commit or discard them and resume.",
            ),
        }
    )
