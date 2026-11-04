"""Which names a check run can carry, read from a product's own workflows (GH-485). Pure:
the workflow texts come in, a verdict on the plan's `ci_required` names comes out; the
plan stage reads the files from the task base and `schemas.plan_check_for` calls `refusals`.

A check run is named after its job: the job's literal `name:` or, with none, its id; a
matrix job reports once per combination, `e2e (1)`, never `e2e`. Matching of CI names stays
exact everywhere else (`ci.ci_state`), so a plan that asks for a matrix job's id is the
defect, and this module says so while the plan is written. It only refuses what the
workflows prove: a reusable workflow (`uses:`), a name built from an expression or text
that does not parse is undecidable, and an undecidable name passes."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import yaml

WORKFLOWS_DIR = ".github/workflows"

Reason = Literal["matrix", "named"]

_WORKFLOW_PATH = re.compile(r"\.github/workflows/[\w.-]+\.ya?ml")


def touched_workflows(texts: Iterable[str]) -> frozenset[str]:
    """The workflow files `texts` (a plan's `required_paths`, its subtasks' titles, briefs
    and seams) name: a plan that names one is taken to change it."""
    return frozenset(found for text in texts for found in _WORKFLOW_PATH.findall(text))


@dataclass(frozen=True)
class WrongId:
    """A job id no check run carries: `matrix` (it reports once per combination) or `named`
    (its literal `name:` is what reports). `needed_by` holds the check-run names of the jobs
    whose `needs:` contain the id, the aggregator candidates."""

    reason: Reason
    literal_name: str | None
    needed_by: tuple[str, ...]
    file: str


@dataclass(frozen=True)
class WorkflowNames:
    """What the workflows prove about check-run names. `carriable`: names a plain job
    reports. `wrong_ids`: job ids that are provably not a check-run name. `undecidable`: some
    job reports under a name the text cannot settle."""

    carriable: frozenset[str]
    wrong_ids: Mapping[str, WrongId]
    undecidable: bool


def check_run_names(texts: Mapping[str, str]) -> WorkflowNames | None:
    """The names of the workflows in `texts` (path -> YAML text), or `None` when there is
    none, one does not parse or holds no `jobs`: nothing can be proved from it."""
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for path, text in texts.items():
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        found = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(found, dict) or not found:
            return None
        for job_id, body in found.items():
            jobs.append((str(job_id), path, body if isinstance(body, dict) else {}))
    if not jobs:
        return None
    carriable: set[str] = set()
    undecidable = False
    verdicts: dict[str, tuple[Reason, str | None, str]] = {}
    for job_id, path, body in jobs:
        label = body.get("name")
        literal = None if label is None else str(label)
        if "uses" in body or (literal is not None and "${{" in literal):
            undecidable = True
        elif isinstance(body.get("strategy"), dict) and "matrix" in body["strategy"]:
            verdicts[job_id] = ("matrix", literal, path)
        elif literal is not None and literal != job_id:
            verdicts[job_id] = ("named", literal, path)
            carriable.add(literal)
        else:
            carriable.add(literal or job_id)
    wrong = {
        job_id: WrongId(reason, literal, _needing(job_id, jobs, carriable), path)
        for job_id, (reason, literal, path) in verdicts.items()
        if job_id not in carriable
    }
    return WorkflowNames(frozenset(carriable), wrong, undecidable)


def _needing(
    job_id: str, jobs: Sequence[tuple[str, str, dict[str, Any]]], carriable: set[str]
) -> tuple[str, ...]:
    """The check-run names of the plain jobs whose `needs:` contain `job_id`."""
    found: list[str] = []
    for other, _, body in jobs:
        needs = body.get("needs")
        listed = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
        if job_id not in listed:
            continue
        name = body.get("name")
        reported = str(name) if name is not None else other
        if reported in carriable and reported not in found:
            found.append(reported)
    return tuple(found)


def refusals(
    required: Iterable[str],
    workflow: WorkflowNames | None,
    stack_ci: Sequence[str],
    touched: frozenset[str],
) -> list[str]:
    """One readable reason per `required` name the workflows prove no check run carries,
    for a plan whose paths declare `touched` workflow files (a plan that changes the
    workflow may add the job it requires). Nothing is refused without a proof: no
    workflow read, an undecidable name, a stack `ci:` name, a touched workflow."""
    if workflow is None:
        return []
    reasons: list[str] = []
    for name in required:
        if name in workflow.carriable or name in stack_ci:
            continue
        wrong = workflow.wrong_ids.get(name)
        if wrong is not None:
            if wrong.file not in touched:
                reasons.append(_wrong_id_reason(name, wrong, stack_ci))
        elif not workflow.undecidable and not touched:
            reasons.append(_unknown_reason(name, workflow, stack_ci))
    return reasons


def _wrong_id_reason(name: str, wrong: WrongId, stack_ci: Sequence[str]) -> str:
    if wrong.reason == "named":
        return (
            f"ci_required `{name}` is the id of a job in {wrong.file} whose check run is "
            f"named `{wrong.literal_name}`: CI names match exactly, so require "
            f"`{wrong.literal_name}`"
        )
    head = (
        f"ci_required `{name}` is a matrix job of {wrong.file}: its check runs are named "
        f"`{name} (<matrix values>)`, never `{name}`, and CI names match exactly"
    )
    candidates = [f"`{c}`" for c in wrong.needed_by]
    stack = [f"`{c}`" for c in stack_ci]
    if not candidates and not stack:
        return (
            f"{head}. No job `needs: {name}` and the stack declares no `ci:` names: add a job "
            f"that `needs: {name}` (an aggregator), declare {wrong.file} in `required_paths` "
            "and require that job's name"
        )
    parts = []
    if candidates:
        parts.append(f"jobs that `needs: {name}`: {', '.join(candidates)}")
    if stack:
        parts.append(f"the stack's `ci:` names: {', '.join(stack)}")
    return (
        f"{head}. Candidates, {'; '.join(parts)}. This check does not know whether any of "
        "them covers the criterion: require one that does, or add an aggregator job and "
        f"declare {wrong.file} in `required_paths`"
    )


def _unknown_reason(name: str, workflow: WorkflowNames, stack_ci: Sequence[str]) -> str:
    known = sorted({*workflow.carriable, *stack_ci})
    return (
        f"ci_required `{name}` is not a name any check run of this product's workflows can "
        f"carry; the names that can: {', '.join(f'`{k}`' for k in known)}. If the plan adds "
        f"the job, declare its workflow under {WORKFLOWS_DIR}/ in `required_paths`"
    )
