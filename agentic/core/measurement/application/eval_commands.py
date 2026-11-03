"""The read-only `agentic eval` commands: the evaluation set's declaration, `eval score`
and `eval compare` (ADR-0028), moved out of `cli.py` with no behaviour change.

Reads task documents and nothing else: no lock, no `gh`, no network. The one thing an
adapter owns here is how a `system_commit` resolves to a baseline tag (git), which the
caller hands in as `resolve_baseline`; this module imports no adapter and not `cli`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import yaml

from ....shared.configuration import Configuration
from ....shared.errors import CannotStart
from ...process.application.ports import Ports
from ...process.domain import document as task_document
from ..domain import evaluation, parallel_factor, role_usage, scorecard

# The one string that spells an unknown `system_version` wherever a task is scored: absent,
# empty and null alike, all read back as this single literal (Tech Lead plan, subtask
# `close-command`) -- the *delivering* run's version, never the reader's own.
UNKNOWN_SYSTEM_VERSION = "unknown"


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


def _refuse_open_pull_request(ports: Ports, task_id: str) -> None:
    """A set task whose pull request is still open would have its branch archived out
    from under that pull request by the `--fresh` each child runs with (ADR-0011, sixth
    addendum). Read off the document alone, never `gh`: neither `eval` phase may call
    it (Tech Lead plan invariant). `pull_request` present with no `closed_at` is the
    document's own way of saying "not yet closed" -- `agentic close` is what would set
    `closed_at`, and it is the one place allowed to ask `gh` whether a pull request is
    still open. A task never before run, or one `agentic close` already recorded, never
    refuses here."""
    document = ports.documents.load(task_id)
    reference = (document or {}).get("pull_request")
    if reference and not (document or {}).get("closed_at"):
        raise CannotStart(
            f"{task_id}: has a pull request with no recorded closure (`pull_request` "
            "set, no `closed_at`); --fresh would archive the branch it points at. Run "
            f"`agentic close --issue N` first."
        )


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


def _bucket_documents(
    product_dir: Path, resolve_baseline: Callable[[str | None], str]
) -> tuple[dict[str, list[tuple[str, scorecard.TaskFigures]]], int]:
    """Every task document under `.agentic/tasks/`: this function owns only the glob
    and the parse (Tech Lead review, subtask `scorecard`), handing what it read to
    `scorecard.bucket_by_baseline` for the actual grouping -- a pure function, testable
    at a declared seam (`tests/core/measurement/domain/test_scorecard.py`) without a
    filesystem or git. No port: `cli.py` is the composition root and already reads
    `.agentic/` directly, `_read_eval_set`'s own precedent for `.agentic/eval-set.yaml`.

    `document.parse` rejects a document three ways -- malformed frontmatter YAML
    (`yaml.YAMLError`, from its own unguarded `yaml.safe_load`), a missing frontmatter
    block, or a file this process cannot read (`ValueError`/`OSError`) -- all three
    caught here into the single `None` sentinel `bucket_by_baseline` counts and
    excludes, never raised out of a read-only comparison."""
    tasks_dir = product_dir / task_document.TASKS_SUBDIR
    paths = sorted(tasks_dir.glob("*.md")) if tasks_dir.is_dir() else []
    parsed: list[scorecard.ParsedDocument] = []
    for path in paths:
        try:
            raw = task_document.parse(path.read_text(encoding="utf-8"), path)
        except (ValueError, OSError, yaml.YAMLError):
            parsed.append(None)
            continue
        parsed.append((raw.get("id", path.stem), cast(scorecard.ScorecardDocument, raw)))
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
    ):
        print(
            f"{label:<23}{_fmt_figure(figure)} (median, n={figure.denominator}, "
            f"{figure.excluded} excluded)"
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

    frm_sc = scorecard.build([figures for _, figures in buckets[frm]])
    to_sc = scorecard.build([figures for _, figures in buckets[to]])
    _print_scorecard(product, frm, frm_sc)
    _print_scorecard(product, to, to_sc)
    v = scorecard.verdict(frm_sc, frm, to_sc, to)
    print(f"verdict: {v.outcome} ({v.reason})")
    _print_parallel_factor(product, frm, buckets[frm])
    _print_parallel_factor(product, to, buckets[to])
    _print_role_usage(product, frm, buckets[frm])
    _print_role_usage(product, to, buckets[to])

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
