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
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from agentic.core.process.domain.task import TaskState

from .metrics import DeliveryOutcome, changes_requested_total, prompts_fingerprint, run_duration_s

# `.agentic/eval-set.yaml`, relative to the product root, like `stack.yaml`, `flags.yaml`
# and `BRIEF.md`: the set travels with the product whose issues it names. The caller
# reads the file; this module never touches a filesystem.
DECLARATION_PATH = Path(".agentic/eval-set.yaml")
# Every key the declaration may carry. An unknown one is a typo, not an extension --
# `core.knowledge.domain.stack`'s own rule for its own keys, quoted here for the same
# reason: a product that misspells `issues` would otherwise run a set of nothing and
# never know. The declaration carries the issue numbers and the smoke task and nothing
# else -- no per-task expectations, no thresholds; the score is README.md 9's own.
EVAL_SET_KEYS = ("issues", "smoke")
# The request's own 35-45 USD per-task band, taken at its top: an estimate for a set
# budget's floor, never a measurement -- the actual figure is `cost_usd`, read off each
# task's own document once it exists.
PER_TASK_ESTIMATE_USD = 45.0


class TaskDocument(TaskState, total=False):
    """The dict `ports.documents.load` (`document.parse`, ADR-0001) hands back for one
    task: every key `TaskState` declares, plus the three `agentic close` records once a
    pull request closes (`cli.py`'s own `record_closure` call) -- `closed_at`,
    `delivery_outcome` and `system_version` -- which the graph's own reducer schema never
    carries, because they are written once, after the graph stops running, under the run
    lock, by `close` and not by any node."""

    closed_at: str
    delivery_outcome: DeliveryOutcome
    system_version: str


@dataclass(frozen=True)
class EvalSet:
    """*Set de evaluación* (`CONTEXT.md`): the fixed issues re-run identical against
    every version of prompts, and which one of them stands in as the smoke task for a
    prompt or parameter change (README.md 9's addendum). Nothing else: no per-task
    expectations, no thresholds live here."""

    issues: tuple[int, ...]
    smoke: int


def eval_set_refusal(data: object, source: object) -> str | None:
    """Why the declaration cannot drive `eval run`, or `None` when it can. An absent file
    is not this function's business -- absent and malformed are different facts, the way
    `no_brief` and `brief_refusal` keep them apart in `cli.py` -- so the caller reads the
    file and hands in whatever `yaml.safe_load` returned only when there is something to
    load. Rejected: anything that is not a non-empty mapping, any key outside
    `EVAL_SET_KEYS`, an empty or missing `issues` list, a non-integer entry in it, a
    duplicated issue number, and a `smoke` task that is not itself one of `issues`."""
    if not isinstance(data, dict) or not data:
        return f"{source}: expected a non-empty mapping with {EVAL_SET_KEYS}"
    if unknown := [key for key in data if key not in EVAL_SET_KEYS]:
        return f"{source}: unknown key(s) {', '.join(sorted(unknown))}; expected {EVAL_SET_KEYS}"
    issues = data.get("issues")
    if not isinstance(issues, list) or not issues:
        return f"{source}: expected a non-empty `issues` list of GitHub issue numbers"
    for number in issues:
        if not isinstance(number, int) or isinstance(number, bool):
            return f"{source}: expected an integer issue number in `issues`, got {number!r}"
    if len(set(issues)) != len(issues):
        duplicated = sorted({n for n in issues if issues.count(n) > 1})
        return f"{source}: duplicated issue number(s) in `issues`: {duplicated}"
    smoke = data.get("smoke")
    if not isinstance(smoke, int) or isinstance(smoke, bool):
        return f"{source}: expected an integer `smoke` task"
    if smoke not in issues:
        return f"{source}: `smoke` ({smoke}) must be one of `issues` {issues}"
    return None


def parse_eval_set(data: object, source: object) -> EvalSet:
    """The declaration as a value. Raises `ValueError` with `eval_set_refusal`'s own
    message when the declaration cannot be used, so a caller that only wants the parsed
    set never has to call the refusal function itself first."""
    if (reason := eval_set_refusal(data, source)) is not None:
        raise ValueError(reason)
    assert isinstance(data, dict)  # eval_set_refusal already ruled out anything else
    return EvalSet(issues=tuple(data["issues"]), smoke=data["smoke"])


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


@dataclass(frozen=True)
class Score:
    """README.md 9's binary score for one task. `value` is `True` for a pull request
    that merged clean (`delivery_outcome == "merged"`), `False` for one merged with
    human changes or closed unmerged, and `None` -- a fact, not a zero -- for a task
    `agentic close` has not scored yet. `pending_reason` names the exact command that
    would score it, and is `None` whenever `value` is not."""

    value: bool | None
    pending_reason: str | None


def score(document: TaskDocument, issue: int) -> Score:
    """The score rule, read off `document["delivery_outcome"]` and never re-derived:
    `agentic close` is the one place that asks `gh` and decides it (`cli.py`'s own
    docstring for `_close`)."""
    if not document.get("closed_at"):
        return Score(None, f"`agentic close --issue {issue}` has not run yet")
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
    `metrics.prompts_fingerprint` and `metrics.changes_requested_total` rather than
    computing either a second time."""
    seconds, without_timing = role_seconds(document)
    outcome = score(document, issue)
    record: EvaluationRecord = {
        "product": product,
        "issue": issue,
        "task": document["id"],
        "status": document.get("status", "open"),
        "delivery_outcome": document.get("delivery_outcome"),
        "score": outcome.value,
        "score_pending_reason": outcome.pending_reason,
        "cost_usd": document.get("cost_usd", 0.0),
        "role_seconds": seconds,
        "runs_without_timing": without_timing,
        "prompts": document.get("prompts", {}),
        "prompts_fingerprint": prompts_fingerprint(document),
        "system_version": document.get("system_version"),
        "changes_requested": changes_requested_total(document),
        "failures": len(document.get("failures", [])),
        "note": None,
    }
    if escalation_class := document.get("escalation_class"):
        record["escalation_class"] = escalation_class
    if experiment := document.get("experiment"):
        record["experiment"] = experiment
    return record
