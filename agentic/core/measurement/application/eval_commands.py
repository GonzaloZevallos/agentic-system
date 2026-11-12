"""The read-only `agentic eval` commands: the evaluation set's declaration, `eval score`
and `eval compare` (ADR-0028), moved out of `cli.py` with no behaviour change.

Reads task documents and nothing else: no lock, no `gh`, no network. The one thing an
adapter owns here is how a `system_commit` resolves to a baseline tag (git), which the
caller hands in as `resolve_baseline`; this module imports no adapter and not `cli`.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import yaml

from ....shared.configuration import ENV_PREFIX, Configuration
from ....shared.errors import CannotStart
from ...process.application import poll, scratch_sweep
from ...process.application.ports import Ports, RunRegistry
from ...process.application.registration import REGISTER_WAIT_S, await_registration
from ...process.domain import document as task_document
from ...process.domain import experiment, rules
from ..domain import cycle, evaluation, parallel_factor, planning, role_usage, scorecard
from .ports import EvalChild, EvalRoots, EvalSpawn

# The one string that spells an unknown `system_version` wherever a task is scored: absent,
# empty and null alike, all read back as this single literal (Tech Lead plan, subtask
# `close-command`) -- the *delivering* run's version, never the reader's own.
UNKNOWN_SYSTEM_VERSION = "unknown"
DEFAULT_ARM = "default"
# The fewest tasks an arm needs before `--experiment` states a verdict between arms.
MIN_ARM_TASKS = 10


def _read_eval_set(cfg: Configuration, set_path: str | None) -> tuple[evaluation.EvalSet, str, str]:
    """The declaration as a value, its own text (`evaluation.baseline_id`'s own input)
    and where it came from: `--set` overrides the product's own `.agentic/eval-set.yaml`
    (`evaluation.DECLARATION_PATH`) for a spike, and the caller reports which of the two
    it read (Tech Lead plan, subtask `eval-cli`). No port: `cli.py` is the composition
    root and already reads `.agentic/` directly, the same precedent `flags.py`'s adapter
    has for `.agentic/flags.yaml` -- a second caller would earn this its own port, not a
    first one with no other module to share it with."""
    if set_path is not None:
        path, source = Path(set_path), set_path
    else:
        path = cfg.product_dir / evaluation.DECLARATION_PATH
        source = str(evaluation.DECLARATION_PATH)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotStart(f"{source}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CannotStart(f"{source}: {exc}") from exc
    try:
        declaration = evaluation.parse_eval_set(data, source)
    except ValueError as exc:
        raise CannotStart(str(exc)) from exc
    return declaration, text, source


# How long the driver sleeps between two looks at a hold (a full cap, another product's
# land wait, an account pause, a volume under its floor).
HOLD_POLL_S = 30.0
# Where `eval run` writes what a child reads, inside the child's root.
INPUT_SUBDIR = Path(".agentic/eval-input")


@dataclass(frozen=True)
class _Child:
    """One run to make: `entry` re-run as `arm`."""

    entry: evaluation.EvalEntry
    arm: str


def _arms(ports: Ports, experiment_id: str | None) -> tuple[list[str], object | None]:
    """The arms every entry runs as, and the declaration's raw text-to-be: master's
    `.agentic/experiment.yaml` (never a root's) when `--experiment` names it, else the one
    `default` arm and no declaration."""
    if experiment_id is None:
        return [DEFAULT_ARM], None
    try:
        raw = ports.experiment.read() if ports.experiment is not None else None
        if raw is None:
            raise CannotStart(f"--experiment {experiment_id}: this product declares no experiment")
        declaration = experiment.parse_experiment(raw, ".agentic/experiment.yaml")
    except (ValueError, OSError) as exc:  # `InvalidExperiment` is a `ValueError`
        raise CannotStart(str(exc)) from exc
    if declaration.id != experiment_id:
        raise CannotStart(f"--experiment {experiment_id}: the product declares {declaration.id!r}")
    return [arm.name for arm in declaration.arms], raw


def _refuse_bases(roots: EvalRoots, entries: tuple[evaluation.EvalEntry, ...]) -> None:
    """Before any spend: every entry's `base` is a commit of the product that declares a
    stack, so no child dies on arrival after the first ones were paid for."""
    for entry in entries:
        if not roots.resolves(entry.base):
            raise CannotStart(f"issue {entry.issue}: base {entry.base} does not resolve")
        if not roots.has_stack(entry.base):
            raise CannotStart(
                f"issue {entry.issue}: base {entry.base[:8]} has no .agentic/stack.yaml"
            )


def _next_ordinal(cfg: Configuration, roots: EvalRoots, child: _Child) -> int:
    """1 + the highest ordinal seen for `(issue, base, arm)` among the documents already
    copied to `eval/docs/` and the roots that exist."""
    docs = cfg.product_dir / task_document.EVAL_DOCS_SUBDIR
    seen = [p.stem for p in docs.glob("*.md")] if docs.is_dir() else []
    wanted = (child.entry.issue, child.entry.base[:8], child.arm)
    ordinals = [
        parsed.k
        for name in [*seen, *roots.ids()]
        if (parsed := evaluation.parse_eval_task_id(name)) is not None
        and (parsed.issue, parsed.sha8, parsed.arm) == wanted
    ]
    return max(ordinals, default=0) + 1


def _collect(cfg: Configuration, roots: EvalRoots, eval_id: str) -> float | None:
    """What a run leaves, whether it ended or died: its document copied to
    `<product>/.agentic/eval/docs/<id>.md`, its root removed and its local branches deleted.
    The recorded `cost_usd`, or `None` when it left no readable document."""
    source = roots.path(eval_id) / task_document.TASKS_SUBDIR / f"{eval_id}.md"
    cost: float | None = None
    if source.is_file():
        target = cfg.product_dir / task_document.EVAL_DOCS_SUBDIR / f"{eval_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        try:
            cost = float(
                task_document.parse(target.read_text(encoding="utf-8"), target)["cost_usd"]
            )
        except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError):
            cost = None
    roots.remove(eval_id)
    roots.delete_branches(eval_id)
    return cost


def _sweep_orphans(cfg: Configuration, registry: RunRegistry, roots: EvalRoots) -> None:
    """Roots a driver that died left behind: every one with no live run in the machine's
    registry is collected as a finished one is."""
    alive = {r.task_id for r in registry.live()}
    for eval_id in roots.ids():
        if eval_id not in alive:
            _collect(cfg, roots, eval_id)
            print(f"agentic: eval run: swept the orphan root {eval_id}")


def _write_inputs(
    root: Path, eval_id: str, product: str, child: _Child, declaration: object | None
) -> list[str]:
    """The frozen entry and the experiment inside the child's root; the flags that name them."""
    directory = root / INPUT_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / "entry.yaml"
    frozen = {
        "id": eval_id,
        "product": product,
        "issue": child.entry.issue,
        "base": child.entry.base,
        "title": child.entry.title,
        "body": child.entry.body,
    }
    entry.write_text(yaml.safe_dump(frozen, sort_keys=False), encoding="utf-8")
    flags = ["--issue-input", str(entry)]
    if declaration is not None:
        declared = directory / "experiment.yaml"
        declared.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
        flags += [experiment.ARM_FLAG, child.arm, experiment.EXPERIMENT_FILE_FLAG, str(declared)]
    return flags


def _held_by_slot(
    cfg: Configuration, registry: RunRegistry, resumes: list[rules.ResumeCandidate], now: datetime
) -> str | None:
    """Why a child may not start now, read inside `registry.exclusive()` with the poller's
    own `rules`: no free slot (`counts_against_cap` against the plain cap, never
    `LAND_HEADROOM`), another product's land waiting, or an account pause of this product."""
    live = sum(1 for r in registry.live() if rules.counts_against_cap(r.kind))
    if live >= cfg.max_concurrent_runs:
        return f"no free slot ({live} of {cfg.max_concurrent_runs} in use)"
    own = rules.root_key(str((cfg.product_dir / task_document.TASKS_SUBDIR).resolve()))
    waits, _notes = registry.land_waits(now)
    if any(w.root_key != own for w in waits):
        return "another product's land is waiting for a slot"
    if rules.account_paused(resumes, now):
        return "the account is paused until its limit resets"
    return None


@dataclass(frozen=True)
class Launched:
    """A child started: its id, its root and the process to wait for."""

    eval_id: str
    child: EvalChild


def _launch(
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    roots: EvalRoots,
    spawn: EvalSpawn,
    argv0: str,
    child: _Child,
    declaration: object | None,
    product: str,
    sleep: Callable[[float], None],
    clock: Callable[[], datetime],
) -> Launched:
    """Wait until the child may start, then start it. The count, the holds, the root's
    creation, the spawn and the wait for the child's registration happen inside one
    `registry.exclusive()`: a second driver or the poller cannot count a slot this launch has
    not registered into yet. A hold releases the lock, sleeps and looks again."""
    last: str | None = None
    while True:
        now = clock()
        held, lines = scratch_sweep.read_holds(cfg, ports)
        reason = lines[0] if held else None
        resumes = poll.gather_resumes(ports, now) if reason is None else []
        if reason is None:
            with registry.exclusive():
                reason = _held_by_slot(cfg, registry, resumes, now)
                if reason is None:
                    eval_id = evaluation.eval_task_id(
                        child.entry.issue,
                        child.entry.base,
                        child.arm,
                        _next_ordinal(cfg, roots, child),
                    )
                    root = roots.create(eval_id, child.entry.base)
                    flags = _write_inputs(root, eval_id, product, child, declaration)
                    overlay = {ENV_PREFIX + "PRODUCT_DIR": str(root)}
                    started = spawn([argv0, "run", *flags], overlay)
                    if not await_registration(registry, eval_id, REGISTER_WAIT_S, sleep):
                        print(f"agentic: eval run: {eval_id} did not register in time")
                    return Launched(eval_id, started)
        if reason != last:
            print(f"agentic: eval run: waiting, {reason}")
            last = reason
        sleep(HOLD_POLL_S)


async def _eval_run(
    cfg: Configuration,
    ports: Ports,
    registry: RunRegistry,
    roots: EvalRoots,
    spawn: EvalSpawn,
    argv0: str,
    set_path: str | None,
    experiment_id: str | None,
    budget: float,
    yes: bool,
    dry_run: bool,
    *,
    locked: Callable[[], AbstractContextManager[object]] = nullcontext,
    detach: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """`agentic eval run [--experiment ID]` (GH-470): every entry of the set once per arm
    (`default` when no experiment), each in its own root pinned at the entry's `base`, one
    child at a time. The refusals (declaration, product name, an entry's base) fire before the
    first spend; `--dry-run` reports and returns; `--yes` is required to go on; the total
    `budget` stops the loop; `locked` is the driver's own `"eval"` lock. Evaluation runs are
    never resumed: what a child leaves is collected (`_collect`) and nothing more."""
    declaration, _text, source = _read_eval_set(cfg, set_path)
    arms, raw = _arms(ports, experiment_id)
    children = [_Child(entry, arm) for entry in declaration.entries for arm in arms]
    _refuse_bases(roots, declaration.entries)
    estimate = len(children) * evaluation.PER_TASK_ESTIMATE_USD
    print(
        f"agentic: {source}: {len(children)} run(s), estimated ${estimate:.2f} at "
        f"${evaluation.PER_TASK_ESTIMATE_USD:.2f}/run (smoke: issue {declaration.smoke})"
    )
    for arm in arms:
        print(f"  arm {arm}: {len(declaration.entries)} run(s)")
    if dry_run:
        for child in children:
            k = _next_ordinal(cfg, roots, child)
            planned = evaluation.eval_task_id(child.entry.issue, child.entry.base, child.arm, k)
            where = f"issue {child.entry.issue} at {child.entry.base[:8]} as {child.arm}"
            print(f"  {where}: {planned}")
        return
    if not yes:
        raise CannotStart(
            f"estimated spend ${estimate:.2f} for {len(children)} run(s): pass --yes to proceed"
        )
    if detach is not None:
        detach()
        return
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    with locked():
        _sweep_orphans(cfg, registry, roots)
        spent = 0.0
        for child in children:
            if spent + evaluation.PER_TASK_ESTIMATE_USD > budget:
                print(
                    f"agentic: ${budget:.2f} budget would be exceeded; "
                    f"stopping with {spent:.2f} spent"
                )
                break
            launched = _launch(
                cfg, ports, registry, roots, spawn, argv0, child, raw, product, sleep, clock
            )
            try:
                launched.child.wait()
            finally:
                recorded = _collect(cfg, roots, launched.eval_id)
            # A run that left no document charges the full estimate: a child that dies on
            # arrival cannot loop for free.
            cost = evaluation.PER_TASK_ESTIMATE_USD if recorded is None else recorded
            spent += cost
            print(f"agentic: {launched.eval_id} done, ${cost:.2f} (${spent:.2f} spent so far)")


async def _eval_score(
    cfg: Configuration, ports: Ports, set_path: str | None, out_path: str
) -> None:
    """`agentic eval score`: one JSONL line per evaluation-set task, each entirely
    `evaluation.evaluation_record`'s own read of that task's document -- no `gh`, no
    Langfuse, no metric emission, no write other than `out_path` (Tech Lead plan,
    subtask `eval-cli`). Refuses outright when `out_path` already exists, before the
    declaration is even read: a new file per invocation, merge and history left to
    git, so no run can eat a baseline. Blockable by nothing except the documents it
    reads.

    Each line also carries `declaration_source` (which of the product's own
    `.agentic/eval-set.yaml` or `--set` produced this run) and `baseline_id`
    (`evaluation.baseline_id`: that task's own `system_version` plus a content hash of
    the declaration's text, mixing in the document's own `experiment` snapshot when the
    task ran an arm, issue #28) -- both identity, not a figure `evaluation_record`
    itself would have to read a second source for, so they are added onto its own dict
    rather than duplicated inside `core.measurement.domain.evaluation`, whose field
    list the plan already enumerates in full."""
    destination = Path(out_path)
    if destination.exists():
        raise CannotStart(f"{destination}: already exists; write a new file, merge with git")
    declaration, declaration_text, source = _read_eval_set(cfg, set_path)
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    lines: list[str] = []
    fingerprints: dict[str, list[str]] = {}
    grouped: dict[str, list[evaluation.TaskDocument]] = {}
    pending: list[int] = []
    for issue in declaration.issues:
        task_id = ports.inbox.task_id(issue)
        document = cast(evaluation.TaskDocument | None, ports.documents.load(task_id))
        if document is None:
            print(f"agentic: {task_id} (issue {issue}) has no document yet; skipped")
            continue
        record = evaluation.evaluation_record(product, issue, document)
        version = document.get("system_version") or UNKNOWN_SYSTEM_VERSION
        line: dict[str, object] = {
            **record,
            "declaration_source": source,
            "baseline_id": evaluation.baseline_id(
                version, declaration_text, document.get("experiment", {})
            ),
        }
        lines.append(json.dumps(line, sort_keys=True))
        fingerprints.setdefault(record["prompts_fingerprint"], []).append(task_id)
        grouped.setdefault(record["prompts_fingerprint"], []).append(document)
        if record["score"] is None:
            pending.append(issue)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"agentic: {source}: wrote {len(lines)} line(s) to {destination}")
    if len(fingerprints) > 1:
        print(
            "agentic: prompts fingerprint diverges across "
            f"{len(fingerprints)} groups (a task that stopped early ran fewer prompts):"
        )
        for fingerprint, task_ids in fingerprints.items():
            print(f"  {fingerprint}: {', '.join(sorted(task_ids))}")
        playbook_roles, template_roles = evaluation.fingerprint_divergence(list(grouped.values()))
        if playbook_roles:
            print(f"  playbook differs for: {', '.join(playbook_roles)}")
        if template_roles:
            print(f"  template differs for: {', '.join(template_roles)}")
    for issue in pending:
        print(f"agentic close --issue {issue}")


def _parse_documents(paths: list[Path]) -> list[scorecard.ParsedDocument]:
    """`paths` read and parsed: `(task_id, document)` each, `None` for one `document.parse`
    itself rejected -- malformed frontmatter YAML (`yaml.YAMLError`, from its own
    unguarded `yaml.safe_load`), a missing frontmatter block, or a file this process cannot
    read (`ValueError`/`OSError`) -- all three caught into the single `None` sentinel
    `bucket_by_baseline` counts and excludes, never raised out of a read-only comparison."""
    parsed: list[scorecard.ParsedDocument] = []
    for path in paths:
        try:
            raw = task_document.parse(path.read_text(encoding="utf-8"), path)
        except (ValueError, OSError, yaml.YAMLError):
            parsed.append(None)
            continue
        parsed.append((raw.get("id", path.stem), cast(scorecard.ScorecardDocument, raw)))
    return parsed


def _bucket_documents(
    product_dir: Path, resolve_baseline: Callable[[str | None], str]
) -> tuple[dict[str, list[tuple[str, scorecard.TaskFigures]]], int]:
    """Every task document under `.agentic/tasks/`: this function owns only the glob
    and the parse (Tech Lead review, subtask `scorecard`), handing what it read to
    `scorecard.bucket_by_baseline` for the actual grouping -- a pure function, testable
    at a declared seam (`tests/core/measurement/domain/test_scorecard.py`) without a
    filesystem or git. No port: `cli.py` is the composition root and already reads
    `.agentic/` directly, `_read_eval_set`'s own precedent for `.agentic/eval-set.yaml`.
    Evaluation runs live elsewhere (`_eval_documents`) and never reach this glob."""
    tasks_dir = product_dir / task_document.TASKS_SUBDIR
    paths = sorted(tasks_dir.glob("*.md")) if tasks_dir.is_dir() else []
    return scorecard.bucket_by_baseline(_parse_documents(paths), resolve_baseline)


def _eval_documents(
    product_dir: Path, resolve_baseline: Callable[[str | None], str]
) -> tuple[dict[str, list[tuple[str, scorecard.TaskFigures]]], int]:
    """The evaluation runs' documents, `.agentic/eval/docs/*.md` (GH-470), bucketed like
    `_bucket_documents`'s. This function owns only that glob, and keeps only the documents
    `evaluation.evaluation_documents` accepts: one without the `evaluation` key is not an
    evaluation run's and is no reader's business here."""
    docs_dir = product_dir / task_document.EVAL_DOCS_SUBDIR
    paths = sorted(docs_dir.glob("*.md")) if docs_dir.is_dir() else []
    parsed = evaluation.evaluation_documents(_parse_documents(paths))
    return scorecard.bucket_by_baseline(parsed, resolve_baseline)


def _fmt_figure(figure: scorecard.Figure) -> str:
    return "n/a" if figure.value is None else f"{figure.value:.4f}"


def _print_scorecard(product: str, baseline: str, sc: scorecard.Scorecard) -> None:
    """Every figure names the product it was computed over, the denominator it was
    computed over, and the number of documents excluded from that denominator (Tech
    Lead plan, standard 7): no printed figure sums documents from more than one product
    directory, since `sc` is already this one invocation's own bucket of this one
    product's own documents."""
    print(f"-- {product} / {baseline} --")
    print(
        f"autonomy:          {_fmt_figure(sc.autonomy)} "
        f"({sc.autonomy.denominator} judged, {sc.autonomy.excluded} excluded)"
    )
    print(
        f"own failures/task: {_fmt_figure(sc.own_failures_per_task)} "
        f"({sc.own_failures_per_task.denominator} terminated, "
        f"{sc.own_failures_per_task.excluded} excluded)"
    )
    print(
        "  caveat: `sdk` counts as ours on both sides; a run before ADR-0029 recorded "
        "its 429s as `sdk` and is never reclassified -- a delta here is a quality delta "
        "only if both sides show the post-ADR-0029 vocabulary"
    )
    print(f"  by reason: {sc.failures_by_reason}")
    print(
        f"cost/delivery:     {_fmt_figure(sc.cost_per_delivery)} "
        f"({sc.cost_per_delivery.denominator} judged, {sc.cost_per_delivery.excluded} excluded)"
    )
    print(
        f"rework/task:       {_fmt_figure(sc.rework_per_task)} "
        f"({sc.rework_per_task.denominator} terminated, {sc.rework_per_task.excluded} excluded)"
    )
    print(f"  time to merge known for {sc.time_to_merge_known} of {sc.judged} judged deliveries")
    for label, figure in (
        ("actual/estimate cost:", sc.cost_over_estimate),
        ("actual/estimate files:", sc.files_over_estimate),
        ("hand files/plan:", sc.hand_files_over_plan),
        ("hand lines/plan:", sc.hand_lines_over_plan),
    ):
        print(
            f"{label:<23}{_fmt_figure(figure)} (median, n={figure.denominator}, "
            f"{figure.excluded} excluded)"
        )
    # Informative only, never in the verdict (GH-470): a task missing a bound is left out.
    for label, figure, closed, opened in (
        ("cycle_h:", sc.cycle_h, sc.cycle_closed, sc.cycle_open),
        ("active_h:", sc.active_h, sc.active_closed, sc.active_open),
    ):
        print(
            f"{label:<23}{_fmt_figure(figure)} (median, n={figure.denominator}; "
            f"closed {closed}, open {opened})"
        )
    # Informative only, outside the verdict (GH-467): the cycle's failed, waiting, paused and
    # unattributed hours; a task whose figure is unknown is excluded, never a zero.
    for label, figure in (
        ("failed_h:", sc.failed_h),
        ("waiting_h:", sc.waiting_h),
        ("paused_h:", sc.paused_h),
        ("paused_external_h:", sc.paused_external_h),
        ("paused_human_h:", sc.paused_human_h),
        ("unattributed_h:", sc.unattributed_h),
    ):
        print(
            f"{label:<23}{_fmt_figure(figure)} (median, n={figure.denominator}, "
            f"{figure.excluded} excluded)"
        )
    # Informative only, outside the verdict (GH-593): what escalated and what a human was waited
    # on for, by cause. A bucket with none prints none of it.
    for row in sc.escalations_by_cause:
        print(
            f"escalations [{row.kind}] {row.cause}: {row.escalations} in {row.tasks} of "
            f"{sc.own_failures_per_task.denominator + sc.own_failures_per_task.excluded} tasks"
        )
    for cause, figure in sc.human_wait_by_cause_h:
        print(
            f"human_wait_h [{cause}]: {_fmt_figure(figure)} (median, n={figure.denominator} of "
            f"{figure.denominator + figure.excluded} tasks)"
        )


def _print_role_usage(
    product: str, baseline: str, tasks: list[tuple[str, scorecard.TaskFigures]]
) -> None:
    """The per-role breakdown of one baseline's runs (GH-292), read off the same
    bucket the scorecard above it was built from. Token columns are counts, `cost`
    is the recorded `cost_usd`; a document recorded before a field existed reads 0."""
    usage = role_usage.by_role(run for _, figures in tasks for run in figures.runs)
    print(f"-- per-role usage -- {product} / {baseline} --")
    print(
        f"{'role':<24}{'runs':>6}{'turns':>7}{'input':>10}{'cache_read':>13}"
        f"{'cache_write':>13}{'output':>10}{'cost_usd':>11}"
    )
    for slug, u in usage.items():
        print(
            f"{slug:<24}{u.runs:>6}{u.turns:>7}{u.input_tokens:>10}{u.cache_read_tokens:>13}"
            f"{u.cache_write_tokens:>13}{u.output_tokens:>10}{u.cost_usd:>11.4f}"
        )


def _print_parallel_factor(
    product: str, baseline: str, tasks: list[tuple[str, scorecard.TaskFigures]]
) -> None:
    """How many Developer sessions ran at once over one baseline's tasks (GH-364): the
    recorded attempts' time over the wall time they occupied, summed over the tasks. One
    line, `n/a` when no run of the baseline carries both timestamps. Not the transcript
    figure the issue quotes (ADR-0068); nothing is recorded for it."""
    factor = parallel_factor.aggregate({"runs": figures.runs} for _, figures in tasks)
    print(
        f"parallel factor -- {product} / {baseline}: {'n/a' if factor is None else f'{factor:.4f}'}"
    )


def _print_planning(
    product: str, baseline: str, tasks: list[tuple[str, scorecard.TaskFigures]]
) -> None:
    """What planning cost and returned over one baseline's tasks, split by the route the
    task took (GH-472, ADR-0086): a read over the documents' `runs`, `return_history` and
    `plan_route`, nothing recorded. A task whose document has no `plan_route` is `unsized`."""
    facts = [f.planning_facts for _, f in tasks if f.planning_facts is not None]
    block = planning.lines(planning.aggregate(facts))
    if not block:
        return
    print(f"-- planning -- {product} / {baseline} --")
    for line in block:
        print(line)


def _print_path_report(
    product: str, baseline: str, tasks: list[tuple[str, scorecard.TaskFigures]]
) -> None:
    """Lead time per `initial_path`, the promotions by kind and the excluded tasks by reason
    (GH-599). Informative: printed after the verdict, read by nothing that decides it. The
    figures come from `cycle.path_report`; this only formats them."""
    report = cycle.path_report(figures.path_facts for _, figures in tasks)
    print(f"-- lead time by path -- {product} / {baseline} --")
    for name, row in report.paths.items():
        median = "n/a" if row.median_s is None else f"{row.median_s / 3600:.4f}"
        print(f"lead_time_h [{name}]: {median} (median, n={row.n} of {row.m} tasks)")
    if not report.paths:
        print("lead_time_h: n/a (no closed task with a recorded path)")
    print("promoted: " + ", ".join(f"{kind}={count}" for kind, count in report.promotions.items()))
    print("excluded: " + ", ".join(f"{why}={count}" for why, count in report.exclusions))


ArmGroup = tuple[str, str, list[tuple[str, scorecard.TaskFigures]]]
"""One printed block: its heading name, its arm label and its tasks."""


def _arm_groups(baseline: str, tasks: list[tuple[str, scorecard.TaskFigures]]) -> list[ArmGroup]:
    """A baseline's tasks split by `arm` (`metrics.arm_of`, never an arm's name). A baseline
    whose tasks are all `default` is one group named by the baseline alone, so its output is
    what it always was; otherwise one group per arm, the default arm first."""
    by_arm: dict[str, list[tuple[str, scorecard.TaskFigures]]] = {}
    for entry in tasks:
        by_arm.setdefault(entry[1].arm, []).append(entry)
    if set(by_arm) <= {DEFAULT_ARM}:
        return [(baseline, DEFAULT_ARM, tasks)]
    ordered = sorted(by_arm, key=lambda arm: (arm != DEFAULT_ARM, arm))
    return [(f"{baseline} / arm {arm}", arm, by_arm[arm]) for arm in ordered]


def _print_verdicts(
    frm: str,
    frm_groups: list[ArmGroup],
    to: str,
    to_groups: list[ArmGroup],
    cards: dict[str, scorecard.Scorecard],
) -> None:
    """The verdict between the two baselines. Unsplit on both sides it is the one line it
    always was; otherwise one line per arm both baselines ran, since an arm is only
    compared with itself."""
    if len(frm_groups) == 1 == len(to_groups) and frm_groups[0][1] == to_groups[0][1]:
        v = scorecard.verdict(cards[frm_groups[0][0]], frm, cards[to_groups[0][0]], to)
        print(f"verdict: {v.outcome} ({v.reason})")
        return
    to_by_arm = {arm: name for name, arm, _ in to_groups}
    common = [(name, arm) for name, arm, _ in frm_groups if arm in to_by_arm]
    for name, arm in common:
        v = scorecard.verdict(cards[name], frm, cards[to_by_arm[arm]], to)
        print(f"verdict [{arm}]: {v.outcome} ({v.reason})")
    if not common:
        print("verdict: undecidable (no arm ran under both baselines)")


async def _eval_compare(
    cfg: Configuration,
    ports: Ports,
    frm: str,
    to: str,
    resolve_baseline: Callable[[str | None], str],
) -> None:
    """`agentic eval compare <from> <to>`: ADR-0028's scorecard for two baselines, from
    task documents and local git alone (Tech Lead plan, subtask `scorecard`) -- no
    lock, no `gh`, no network. One product directory per invocation, the one `main()`
    already resolved into `cfg`/`ports`: a second product is another invocation, never
    a second `Configuration`/`Ports` built here."""
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    buckets, unreadable = _bucket_documents(cfg.product_dir, resolve_baseline)
    available = sorted(buckets)
    for name in (frm, to):
        if name not in buckets:
            counts = ", ".join(f"{b} ({len(buckets[b])})" for b in available)
            raise CannotStart(f"{name}: unknown baseline; available: {counts or 'none'}")
    if unreadable:
        print(f"agentic: {unreadable} document(s) could not be read; excluded from every bucket")
    if unknown := buckets.get("unknown"):
        print(
            f"agentic: {len(unknown)} document(s) whose `system_commit` this repository "
            "cannot resolve; re-pin stage0 or pass --system-repo"
        )

    frm_groups = _arm_groups(frm, buckets[frm])
    to_groups = _arm_groups(to, buckets[to])
    cards = {
        name: scorecard.build([figures for _, figures in tasks])
        for name, _, tasks in (*frm_groups, *to_groups)
    }
    for name, _, _ in (*frm_groups, *to_groups):
        _print_scorecard(product, name, cards[name])
    _print_verdicts(frm, frm_groups, to, to_groups, cards)
    for name, _, tasks in (*frm_groups, *to_groups):
        _print_parallel_factor(product, name, tasks)
    for name, _, tasks in (*frm_groups, *to_groups):
        _print_role_usage(product, name, tasks)
    for name, _, tasks in (*frm_groups, *to_groups):
        _print_planning(product, name, tasks)
    for name, _, tasks in (*frm_groups, *to_groups):
        _print_path_report(product, name, tasks)

    # The eval-set declaration is read on a warning path (ADR-0025's `no_brief`/
    # `brief_refusal` asymmetry): absent or malformed says so plainly and the
    # comparison already printed above stands, never a `CannotStart` that would die
    # before a figure is printed (this repo declares no `.agentic/eval-set.yaml`).
    try:
        declaration, _text, _source = _read_eval_set(cfg, None)
    except CannotStart as exc:
        print(f"agentic: eval set: {exc}")
        return
    for name, tasks in ((frm, buckets[frm]), (to, buckets[to])):
        present = {task_id for task_id, _ in tasks}
        missing = [
            issue for issue in declaration.issues if ports.inbox.task_id(issue) not in present
        ]
        if missing:
            print(
                f"agentic: {name}: missing {len(missing)} of {len(declaration.issues)} "
                f"eval-set issue(s): {', '.join(str(i) for i in missing)}"
            )


async def _eval_compare_experiment(
    cfg: Configuration,
    ports: Ports,
    experiment: str,
    resolve_baseline: Callable[[str | None], str],
) -> None:
    """`agentic eval compare --experiment <id>`: the arms of one experiment against each
    other, over the tasks whose `assignment.experiment` is `id` (GH-470). Documents and
    local git alone, like `_eval_compare`. An arm is its `arm_of` label, never its name.

    The verdict needs a fair comparison: tasks spanning more than one baseline are
    `insufficient (mixed baselines)` and listed per baseline, and an arm under
    `MIN_ARM_TASKS` tasks is `insufficient (n/m)` with `n` the smallest arm. Otherwise the
    first arm (the default one if present) is compared with each of the others."""
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    buckets, unreadable = _bucket_documents(cfg.product_dir, resolve_baseline)
    # The evaluation runs of the experiment count beside the real tasks that carry it.
    evaluated, evaluation_unreadable = _eval_documents(cfg.product_dir, resolve_baseline)
    unreadable += evaluation_unreadable
    for baseline, tasks in evaluated.items():
        buckets.setdefault(baseline, []).extend(tasks)
    chosen = [
        (baseline, figures)
        for baseline in sorted(buckets)
        for _, figures in buckets[baseline]
        if figures.experiment_id == experiment
    ]
    if not chosen:
        raise CannotStart(f"{experiment}: no task document carries this experiment")
    if unreadable:
        print(f"agentic: {unreadable} document(s) could not be read; excluded from every bucket")
    arms = sorted({f.arm for _, f in chosen}, key=lambda arm: (arm != DEFAULT_ARM, arm))
    baselines = sorted({baseline for baseline, _ in chosen})
    print(f"experiment {experiment}: {len(chosen)} task(s), {len(arms)} arm(s)")
    if len(baselines) > 1:
        for baseline in baselines:
            counts = ", ".join(
                f"{arm} n={sum(1 for b, f in chosen if b == baseline and f.arm == arm)}"
                for arm in arms
            )
            print(f"  {baseline}: {counts}")
        print("verdict: insufficient (mixed baselines)")
        return
    cards = {arm: scorecard.build([f for _, f in chosen if f.arm == arm]) for arm in arms}
    sizes = {arm: sum(1 for _, f in chosen if f.arm == arm) for arm in arms}
    for arm in arms:
        _print_scorecard(
            product, f"{baselines[0]} / experiment {experiment} / arm {arm}", cards[arm]
        )
    for arm in arms:
        _print_planning(
            product,
            f"{baselines[0]} / experiment {experiment} / arm {arm}",
            [(baseline, f) for baseline, f in chosen if f.arm == arm],
        )
    smallest = min(sizes.values())
    if len(arms) < 2:
        print("verdict: insufficient (only one arm)")
    elif smallest < MIN_ARM_TASKS:
        print(f"verdict: insufficient ({smallest}/{MIN_ARM_TASKS})")
    else:
        first = arms[0]
        for other in arms[1:]:
            v = scorecard.verdict(cards[first], first, cards[other], other)
            print(f"verdict [{first} -> {other}]: {v.outcome} ({v.reason})")
