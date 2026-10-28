"""The triggers of `.github/workflows/ci.yml` that the full tier on GitHub relies on
(ADR-0064, addendum GH-376).

With `ci:` declared, `full_checks` pushes the integrated head to `agentic-ci/<id>` and
waits for the jobs of `ci:` there. The workflow must therefore run on pushes to that
ref, and must not run on the `agentic/<id>` task branches too: the pull request already
runs once per event and a second trigger would double the runs. The job named in
`.agentic/stack.yaml` `ci:` must exist in the workflow, or the wait never ends.
"""

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from agentic.core.knowledge.domain import stack

REPO = Path(__file__).resolve().parent.parent
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
STACK_YAML = REPO / ".agentic" / "stack.yaml"

CI_REF_SAMPLE = "agentic-ci/GH-376"
TASK_BRANCH_SAMPLE = "agentic/GH-376"


def push_branches(workflow: dict[str, Any]) -> list[str]:
    """The `on.push.branches` patterns. PyYAML reads the bare key `on` as `True`."""
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict):
        return []
    push = triggers.get("push")
    if not isinstance(push, dict):
        return []
    branches = push.get("branches", [])
    return [str(b) for b in branches] if isinstance(branches, list) else []


def violations(workflow: dict[str, Any], declared_jobs: list[str]) -> list[str]:
    """What is wrong with the workflow for the full tier on GitHub; empty when sound."""
    found: list[str] = []
    patterns = push_branches(workflow)
    if not any(fnmatchcase(CI_REF_SAMPLE, p) for p in patterns):
        found.append(f"push.branches {patterns} does not cover {CI_REF_SAMPLE}")
    if any(fnmatchcase(TASK_BRANCH_SAMPLE, p) for p in patterns):
        found.append(f"push.branches {patterns} matches task branches {TASK_BRANCH_SAMPLE}")
    jobs = workflow.get("jobs")
    present = set(jobs) if isinstance(jobs, dict) else set()
    for name in declared_jobs:
        if name not in present:
            found.append(f"job {name!r} declared in stack.yaml `ci:` is missing")
    return found


def load_workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(CI_YML.read_text())
    assert isinstance(loaded, dict)
    return loaded


def declared_ci_jobs() -> list[str]:
    declared = stack.ci(yaml.safe_load(STACK_YAML.read_text()), STACK_YAML)
    assert declared, "stack.yaml declares no `ci:`"
    return list(declared)


def test_workflow_runs_on_the_ci_ref_and_not_on_task_branches() -> None:
    assert violations(load_workflow(), declared_ci_jobs()) == []


def test_removing_the_ci_ref_trigger_is_rejected() -> None:
    workflow = load_workflow()
    workflow["on"] = {"pull_request": None, "push": {"branches": ["master"]}}
    workflow.pop(True, None)
    found = violations(workflow, declared_ci_jobs())
    assert any("does not cover" in f for f in found)


def test_a_trigger_matching_task_branches_is_rejected() -> None:
    workflow = load_workflow()
    workflow["on"] = {"push": {"branches": ["master", "agentic-ci/**", "agentic/**"]}}
    workflow.pop(True, None)
    found = violations(workflow, declared_ci_jobs())
    assert any("matches task branches" in f for f in found)


def test_a_missing_declared_job_is_rejected() -> None:
    workflow = load_workflow()
    workflow["jobs"] = {"other": {}}
    found = violations(workflow, declared_ci_jobs())
    assert any("is missing" in f for f in found)
