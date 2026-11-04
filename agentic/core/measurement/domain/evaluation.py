"""The evaluation set and its record (the Medición glossary, `core/measurement/CONTEXT.md`:
*Set de evaluación*, *Muestra* and *Resultado de entrega*). Pure domain -- no filesystem,
no port, no subprocess, no I/O of any kind: the caller reads `.agentic/eval-set.yaml`
(`DECLARATION_PATH`, relative to the product root) and one task's document, and hands in
text or an already-loaded dict; this module turns either into values.

**Two pure verdict functions**, `knowledge.brief_refusal`'s own shape (`str | None`, the
message naming the offending value), so each rule is enumerable case by case in a unit
test instead of implied by whichever caller happens to invoke it: `eval_set_refusal` over
the parsed declaration, `score` over one task's document.

**Baseline identity is `system_version` plus a content hash of the declaration text, never
`metrics.prompts_fingerprint`.** That fingerprint hashes the prompts a task actually ran
(`metrics.prompts_fingerprint`'s own docstring), so a task that escalated at `classify`
carries one hash and its sibling that reached a pull request carries a dozen -- same
commit, same files, different fingerprints. Joining two baselines on it would report an
escalation as a prompt regression. `evaluation_record` therefore carries the fingerprint
too, but only as a descriptive fact next to `prompts`, `status` and `escalation_class`;
`baseline_id` never reads it.

**No wall-clock duration field, on purpose.** A span from a task's first run to its last
would silently include the days it sat paused on a human, meaning one thing for an
unpaused Corrida and another for a paused one. `role_seconds` sums `metrics.run_duration_s`
over the runs that carry both timestamps instead, with `runs_without_timing` counting the
rest, so a document written before ADR-0008's 2026-09-18 addendum reads as undercounted
rather than fast.

**The score is read, never re-derived.** `agentic close` already asks `gh`, computes
`delivery_outcome` and writes it under the run lock; `score` only reads it. A document
with no `closed_at` yet scores `None`, with a reason naming the exact command that would
score it -- a zero and "the human has not looked yet" are different facts, and conflating
them corrupts the baseline.

**An evaluation run is a task whose document carries `evaluation`** (GH-470, part 2):
`is_evaluation` is the one predicate for that, and every reader of
`<product>/.agentic/eval/docs/` filters through `evaluation_documents`. Whether a process
is wired as one is decided by its *root* alone (`is_evaluation_root`), checked against its
task id by `wiring_refusal` -- never by the document's key, which a reader sees only after
the run has already spent. The root test resolves symlinks (`os.path.realpath`), the one
read of the filesystem this module makes: it opens no file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from agentic.core.process.domain.task import TaskState

from .metrics import DeliveryOutcome, changes_requested_total, prompts_fingerprint, run_duration_s
from .scorecard import LANDED_UNJUDGED, ParsedDocument, task_figures

# `.agentic/eval-set.yaml`, relative to the product root, like `stack.yaml`, `flags.yaml`
# and `BRIEF.md`: the set travels with the product whose issues it names. The caller
# reads the file; this module never touches a filesystem.
DECLARATION_PATH = Path(".agentic/eval-set.yaml")
# Every key the declaration may carry. An unknown one is a typo, not an extension --
# `core.knowledge.domain.stack`'s own rule for its own keys, quoted here for the same
# reason: a product that misspells `entries` would otherwise run a set of nothing and
# never know. The declaration carries the entries and the smoke task and nothing else --
# no per-task expectations, no thresholds; the score is README.md 9's own. The old
# `issues:` form is not a key any more: no product ever held the file (GH-470).
EVAL_SET_KEYS = ("entries", "smoke")
# What one entry freezes: the issue it re-runs, the commit it runs from and the text the
# roles receive -- the issue's body as it was, never read again from the tracker.
ENTRY_KEYS = ("issue", "base", "title", "body")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
# The request's own 35-45 USD per-task band, taken at its top: an estimate for a set
# budget's floor, never a measurement -- the actual figure is `cost_usd`, read off each
# task's own document once it exists.
PER_TASK_ESTIMATE_USD = 45.0


class TaskDocument(TaskState, total=False):
    """The dict `ports.documents.load` (`document.parse`, ADR-0001) hands back for one
    task: every key `TaskState` declares, plus what `agentic close` records once a pull
    request closes (`cli.py`'s own `record_closure` call) -- which the graph's own
    reducer schema never carries, because it is written once, after the graph stops
    running, under the run lock, by `close` and not by any node. `human_commits`,
    `human_lines_changed` and `time_to_merge_seconds` are `str`, not `int`: every
    closure key goes through `TaskDocuments.record_closure`, which writes a single
    scalar line (`document._scalar_line`, a `yaml.safe_dump` of a string) -- typing them
    `str` matches what occurs on disk rather than a numeric type that never does.
    `system_commit` and `system_baseline` are `document.render`'s own top-level keys
    (ADR-0005, ADR-0030), not part of `TaskState`'s own reducer schema either, since
    the graph never carries a fact about the checkout that is running it."""

    closed_at: str
    delivery_outcome: DeliveryOutcome
    system_version: str
    human_commits: str
    human_lines_changed: str
    # `record_closure`'s fourth key (Tech Lead plan, subtask `close-all`): seconds from
    # the pull request's `created_at` to its `merged_at`, written only when it merged
    # (`metrics.time_to_merge_seconds` returns `None` otherwise, and the key is omitted
    # entirely rather than written empty). `str`, not `int`: `record_closure` writes
    # every closure key through `_scalar_line`, which yaml-dumps whatever it is given,
    # and `document.parse` hands every frontmatter scalar back as a string -- this
    # types what occurs at runtime, never a type that never does. Absent on every
    # document written before this commit, and never backfilled onto one already closed.
    time_to_merge_seconds: str
    system_commit: str
    system_baseline: str


@dataclass(frozen=True)
class EvalEntry:
    """One frozen input of the set: `issue` re-run from `base` (a full sha) with `title`
    and `body` as its request."""

    issue: int
    base: str
    title: str
    body: str


@dataclass(frozen=True)
class EvalSet:
    """*Set de evaluación* (`CONTEXT.md`): the frozen entries re-run identical against
    every version of prompts, and which issue of them stands in as the smoke task for a
    prompt or parameter change (README.md 9's addendum). Nothing else: no per-task
    expectations, no thresholds live here."""

    entries: tuple[EvalEntry, ...]
    smoke: int

    @property
    def issues(self) -> tuple[int, ...]:
        """The distinct issue numbers of `entries`, sorted: the one source of the set's
        issues for the merge guard, `eval score` and `eval compare` (GH-470)."""
        return tuple(sorted({entry.issue for entry in self.entries}))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def entry_refusal(entry: object, source: object) -> str | None:
    """Why `entry` is not a frozen input, or `None`. The one definition of an entry, read
    by the declaration (`eval_set_refusal`) and by the file a run is handed
    (`FileInbox`): `issue` a positive integer, `base` 40 lowercase hex characters, `title`
    and `body` non-empty text, no other key."""
    if not isinstance(entry, dict):
        return f"{source}: expected an entry mapping with {ENTRY_KEYS}, got {entry!r}"
    if unknown := [key for key in entry if key not in ENTRY_KEYS]:
        return f"{source}: unknown entry key(s) {', '.join(sorted(unknown))}; expected {ENTRY_KEYS}"
    issue = entry.get("issue")
    if not _is_int(issue) or issue < 1:
        return f"{source}: expected a positive integer `issue` in an entry, got {issue!r}"
    base = entry.get("base")
    if not isinstance(base, str) or not _FULL_SHA.fullmatch(base):
        return (
            f"{source}: issue {issue}: expected `base` as a full 40-character lowercase "
            f"hex sha, got {base!r}"
        )
    for key in ("title", "body"):
        text = entry.get(key)
        if not isinstance(text, str) or not text.strip():
            return f"{source}: issue {issue}: expected a non-empty `{key}`, got {text!r}"
    return None


def eval_set_refusal(data: object, source: object) -> str | None:
    """Why the declaration cannot drive `eval run`, or `None` when it can. An absent file
    is not this function's business -- absent and malformed are different facts, the way
    `no_brief` and `brief_refusal` keep them apart in `cli.py` -- so the caller reads the
    file and hands in whatever `yaml.safe_load` returned only when there is something to
    load. Rejected: anything that is not a non-empty mapping, any key outside
    `EVAL_SET_KEYS` (the old `issues:` form included), an empty or missing `entries`
    list, an entry `entry_refusal` rejects, a duplicated `(issue, base)` pair, and a
    `smoke` task that is not the issue of one of the entries."""
    if not isinstance(data, dict) or not data:
        return f"{source}: expected a non-empty mapping with {EVAL_SET_KEYS}"
    if unknown := [key for key in data if key not in EVAL_SET_KEYS]:
        return f"{source}: unknown key(s) {', '.join(sorted(unknown))}; expected {EVAL_SET_KEYS}"
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return f"{source}: expected a non-empty `entries` list of frozen issues"
    for entry in entries:
        if (reason := entry_refusal(entry, source)) is not None:
            return reason
    pairs = [(entry["issue"], entry["base"]) for entry in entries]
    if duplicated := sorted({pair for pair in pairs if pairs.count(pair) > 1}):
        shown = ", ".join(f"issue {issue} at {base[:8]}" for issue, base in duplicated)
        return f"{source}: duplicated entry in `entries`: {shown}"
    smoke = data.get("smoke")
    if not _is_int(smoke):
        return f"{source}: expected an integer `smoke` task"
    issues = sorted({issue for issue, _ in pairs})
    if smoke not in issues:
        return f"{source}: `smoke` ({smoke}) must be the issue of one of `entries` {issues}"
    return None


def parse_eval_set(data: object, source: object) -> EvalSet:
    """The declaration as a value. Raises `ValueError` with `eval_set_refusal`'s own
    message when the declaration cannot be used, so a caller that only wants the parsed
    set never has to call the refusal function itself first."""
    if (reason := eval_set_refusal(data, source)) is not None:
        raise ValueError(reason)
    assert isinstance(data, dict)  # eval_set_refusal already ruled out anything else
    entries = tuple(
        EvalEntry(issue=e["issue"], base=e["base"], title=e["title"], body=e["body"])
        for e in data["entries"]
    )
    return EvalSet(entries=entries, smoke=data["smoke"])


# --- what an evaluation run is ---

EVAL_PREFIX = "EVAL-"
# `<product>/.agentic/eval/EVAL-<id>/`: where a run's root lives and, beside it, `docs/`.
_ROOT_PARENT = (".agentic", "eval")
_EVAL_TASK_ID = re.compile(r"EVAL-(\d+)-([0-9a-f]{8})-([a-z0-9-]{1,24})-(\d+)")


def is_evaluation(document: Mapping[str, object]) -> bool:
    """Whether `document` is an evaluation run's: it carries the `evaluation` key."""
    return "evaluation" in document


def is_evaluation_root(product_dir: Path) -> bool:
    """Whether `product_dir` is an evaluation run's root, by the shape of its resolved
    path: `<x>/.agentic/eval/EVAL-...`. The only thing that wires a process as an
    evaluation (the document's key never does). Symlinks are resolved first, so a link
    into a root is a root and a link named like one is not."""
    parts = Path(os.path.realpath(product_dir)).parts
    return len(parts) >= 4 and parts[-3:-1] == _ROOT_PARENT and parts[-1].startswith(EVAL_PREFIX)


def wiring_refusal(product_dir: Path, task_id: str) -> str | None:
    """Why this root and this task id may not run together, or `None`: an `EVAL-` id in a
    root that is not an evaluation's would write to the real tracker under a name the
    tracker never saw; a real id in an evaluation root would run unrecorded. Asked before
    anything spends."""
    root = is_evaluation_root(product_dir)
    if task_id.startswith(EVAL_PREFIX) and not root:
        return f"{task_id}: an evaluation task id outside an evaluation root ({product_dir})"
    if root and not task_id.startswith(EVAL_PREFIX):
        return f"{task_id}: not an evaluation task id, inside an evaluation root ({product_dir})"
    return None


@dataclass(frozen=True)
class EvalTaskId:
    """The four facts an evaluation run's id carries: `issue`, `base` shortened to its
    first 8 characters, `arm` and the ordinal `k` (1-based) of the run."""

    issue: int
    sha8: str
    arm: str
    k: int


def eval_task_id(issue: int, base: str, arm: str, k: int) -> str:
    """`EVAL-<issue>-<sha8>-<arm>-<k>`: unique per `(issue, base, arm, ordinal)`."""
    return f"{EVAL_PREFIX}{issue}-{base[:8]}-{arm}-{k}"


def parse_eval_task_id(task_id: str) -> EvalTaskId | None:
    """The inverse of `eval_task_id` (with `base` shortened), or `None` for any string it
    never makes. An arm may hold hyphens; the ordinal is the last segment, so it is
    unambiguous."""
    match = _EVAL_TASK_ID.fullmatch(task_id)
    if match is None or int(match.group(4)) < 1:
        return None
    return EvalTaskId(int(match.group(1)), match.group(2), match.group(3), int(match.group(4)))


def evaluation_documents(parsed: Sequence[ParsedDocument]) -> list[ParsedDocument]:
    """Of the documents read from `eval/docs/`, the evaluation ones. The one filter every
    reader of that directory goes through. A document its parser rejected (`None`) stays,
    so the reader counts it as unreadable instead of silently dropping it."""
    return [item for item in parsed if item is None or is_evaluation(item[1])]


def baseline_id(system_version: str, declaration_text: str, experiment: Mapping[str, str]) -> str:
    """Identity of one baseline run of the set (module docstring): `system_version` plus
    a content hash of the declaration's own text. Deliberately not
    `metrics.prompts_fingerprint` -- see the module docstring for why that would corrupt
    the comparison.

    `experiment` is the task document's own `experiment` key (`TaskState.experiment`,
    `shared.configuration.experiment_snapshot`): empty on the default path, so this
    returns exactly what it did before the two arms existed, byte for byte. Mixed into
    the digest only when it carries a value, so the two arms of one declaration land on
    different baselines instead of colliding on one (issue #28)."""
    digest_input = declaration_text
    if experiment:
        digest_input += "\n" + json.dumps(dict(experiment), sort_keys=True)
    digest = hashlib.sha256(digest_input.encode()).hexdigest()[:8]
    return f"{system_version}+{digest}"


# Why an auto-merged delivery has no score (ADR-0045): the pull request merged without a
# human, so there is no verdict to read -- never `False`, which would say the human judged it.
AUTO_MERGED_REASON = (
    "auto-merged deliveries carry no human verdict (ADR-0045): the merge was the system's own"
)


# Why a task its slices carried to master has no score (GH-334): the system closed it, and
# nobody judged a delivery -- never `False`.
SLICES_REASON = "closed because every unit landed through slices (GH-334): no delivery was judged"


@dataclass(frozen=True)
class Score:
    """README.md 9's binary score for one task. `value` is `True` for a pull request
    that merged clean (`delivery_outcome == "merged"`), `False` for one merged with
    human changes or closed unmerged, and `None` -- a fact, not a zero -- for a task
    `agentic close` has not scored yet or one that `auto_merged` without a human verdict
    (ADR-0045). `pending_reason` says why there is no score, and is `None` whenever
    `value` is not."""

    value: bool | None
    pending_reason: str | None


def score(document: TaskDocument, issue: int) -> Score:
    """The score rule, read off `document["delivery_outcome"]` and never re-derived:
    `agentic close` is the one place that asks `gh` and decides it (`cli.py`'s own
    docstring for `_close`)."""
    if not document.get("closed_at"):
        return Score(None, f"`agentic close --issue {issue}` has not run yet")
    outcome = document.get("delivery_outcome")
    if outcome is None and document.get("closed_by_slices"):
        return Score(None, SLICES_REASON)
    if outcome in LANDED_UNJUDGED:
        return Score(None, AUTO_MERGED_REASON)
    return Score(document.get("delivery_outcome") == "merged", None)


def role_seconds(document: TaskDocument) -> tuple[float, int]:
    """`(total, runs_without_timing)`: the sum of `metrics.run_duration_s` over the runs
    that carry both timestamps, and how many of the document's runs do not. See the
    module docstring for why there is no wall-clock span across invocations instead."""
    durations = [run_duration_s(run) for run in document.get("runs", [])]
    timed = [d for d in durations if d is not None]
    return sum(timed), len(durations) - len(timed)


class EvaluationRecord(TypedDict):
    """One line of the JSONL README.md 12's pendiente 14 describes: every figure read
    from one task's `TaskDocument`, never recomputed from a second source. `note` is
    always `None`: the human fills it by hand with "qué le faltó" once the task is
    scored (the request's own point 2)."""

    product: str
    issue: int
    task: str
    status: str
    escalation_class: NotRequired[str]
    # The review-mode/ephemeral-arm switches this task ran under (issue #28), off the
    # document's own `experiment` key: absent when the run was on the default path,
    # handled the way `escalation_class` above already is.
    experiment: NotRequired[dict[str, str]]
    delivery_outcome: str | None
    score: bool | None
    score_pending_reason: str | None
    cost_usd: float
    # `metrics.rework_cost_usd`, read once by `scorecard.task_figures` and reused here
    # rather than recomputed a second time (Tech Lead plan, subtask `scorecard`): a new
    # field, since `evaluation_record` carried no rework figure before this task.
    rework_usd: float
    role_seconds: float
    runs_without_timing: int
    prompts: dict[str, str]
    prompts_fingerprint: str
    system_version: str | None
    changes_requested: int
    failures: int
    note: None


def evaluation_record(product: str, issue: int, document: TaskDocument) -> EvaluationRecord:
    """The evaluation record for one task, built entirely from `document` -- this task's
    own frontmatter, as `ports.documents.load` hands it back -- plus the product name the
    caller read from that same document or from `ports.stack.product()`. Reuses
    `metrics.prompts_fingerprint` and `metrics.changes_requested_total`, and now
    `scorecard.task_figures` for `cost_usd`, `rework_usd`, `status` and `failures`
    (its per-reason breakdown summed), so each of those has one definition read from
    two callers instead of two (module docstring, "Import direction")."""
    figures = task_figures(document)
    seconds, without_timing = role_seconds(document)
    outcome = score(document, issue)
    record: EvaluationRecord = {
        "product": product,
        "issue": issue,
        "task": document["id"],
        "status": figures.status,
        "delivery_outcome": document.get("delivery_outcome"),
        "score": outcome.value,
        "score_pending_reason": outcome.pending_reason,
        "cost_usd": figures.cost_usd,
        "rework_usd": figures.rework_usd,
        "role_seconds": seconds,
        "runs_without_timing": without_timing,
        "prompts": document.get("prompts", {}),
        "prompts_fingerprint": prompts_fingerprint(document),
        "system_version": document.get("system_version"),
        "changes_requested": changes_requested_total(document),
        "failures": sum(figures.failures_by_reason.values()),
        "note": None,
    }
    if escalation_class := document.get("escalation_class"):
        record["escalation_class"] = escalation_class
    if experiment := document.get("experiment"):
        record["experiment"] = experiment
    return record


def fingerprint_divergence(
    groups: Sequence[Sequence[TaskDocument]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(playbook_roles, template_roles)`: why `eval compare`'s fingerprint groups differ
    (ADR-0043), each a sorted tuple of role slugs. `groups` are the documents of each
    group; a group shares one prompt fingerprint, so its first document stands for it.

    A role whose `playbooks` value differs between groups (absent counts as its own
    value) is a playbook difference. A role whose `prompts` hash differs while its
    `playbooks` value is equal is a template difference. Only a role every group ran is
    compared: a role one group never reached (a task that stopped early) is no
    difference of prompt, and the message already says so."""
    firsts = [group[0] for group in groups if group]
    if not firsts:
        return (), ()
    ran = set.intersection(*(set(document.get("prompts", {})) for document in firsts))
    playbook_roles: list[str] = []
    template_roles: list[str] = []
    for role in sorted(ran):
        playbooks = {document.get("playbooks", {}).get(role, "") for document in firsts}
        prompts = {document.get("prompts", {}).get(role, "") for document in firsts}
        if len(playbooks) > 1:
            playbook_roles.append(role)
        elif len(prompts) > 1:
            template_roles.append(role)
    return tuple(playbook_roles), tuple(template_roles)
