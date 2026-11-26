"""The review of a delivery whose gate saw a check time out with no failing test (GH-1075): one
observation row of category `process-check-timeout` is added on the first pass over a head, none
on a second, and the row is never a return. Seam: `application.review.review` over port fakes."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    finding,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

HEAD = "a" * 40
OTHER = "b" * 40
SECTION = "Developer: api"
APPROVING = "All checks green."


class FullIdWorkspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


def fact(check="tests", head=HEAD):
    return {"head": head, "check": check, "seconds": 1800.0, "workers": 6}


def run_of(*facts):
    return {"role": "Developer", "section": SECTION, "timeouts": list(facts)}


def state(*runs, **more):
    return {
        "id": "GH-1075",
        "title": "T",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtask_id": "api",
        "subtasks": {
            "api": {
                "id": "api",
                "title": "api",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "delivered",
                "branch": "agentic/GH-1075.api",
                "base": "base1",
            }
        },
        "runs": list(runs),
        **more,
    }


async def review_of(task, result=None):
    workspace = FullIdWorkspace()
    workspace.diff_paths = ("agentic/x.py",)
    runner = ScriptedRunner(tech_lead_review=[result or reviewed(reason=APPROVING)])
    ports = memory_ports(runner, stack=MemoryStack(), workspace=workspace)
    return await review(task, Configuration(product_dir=MemoryWorkspace.ROOT), ports)


def timeout_rows_of(patch):
    return [r for r in patch.get("findings", []) if r["category"] == "process-check-timeout"]


async def test_the_first_pass_adds_one_observation_row_and_a_second_adds_none():
    task = state(run_of(fact()))
    first = await review_of(task)
    [row] = timeout_rows_of(first)
    assert row["severity"] == "observation" and row["location"] == "checks:tests"
    assert row["subtask"] == "api" and row["status"] == "open"
    assert first["subtasks"]["api"]["status"] == "approved"  # never a return
    again = apply(task, first)
    again["subtasks"] = {"api": {**again["subtasks"]["api"], "status": "delivered"}}
    assert timeout_rows_of(await review_of(again)) == []


async def test_a_second_pass_on_a_new_head_with_the_row_still_open_adds_none():
    task = state(run_of(fact()))
    first = await review_of(task)
    again = apply(task, first)
    again["subtasks"] = {
        "api": {k: v for k, v in again["subtasks"]["api"].items() if k != "reviewed_head"}
    }
    again["subtasks"]["api"]["status"] = "delivered"
    assert timeout_rows_of(await review_of(again)) == []


async def test_only_the_run_with_a_fact_for_this_head_adds_a_row():
    task = state(run_of(fact()), run_of(fact(head=OTHER)))
    assert len(timeout_rows_of(await review_of(task))) == 1


async def test_a_fact_of_another_head_adds_nothing():
    assert timeout_rows_of(await review_of(state(run_of(fact(head=OTHER))))) == []


async def test_a_run_of_another_section_or_with_no_timeouts_adds_nothing():
    other = {"role": "Developer", "section": "Developer: web", "timeouts": [fact()]}
    old = {"role": "Developer", "section": SECTION}
    assert timeout_rows_of(await review_of(state(other, old))) == []


async def test_a_blocking_row_still_returns_beside_the_timeout_row():
    result = reviewed(reason="no", findings=[finding(severity="blocking")])
    patch = await review_of(state(run_of(fact())), result)
    assert patch["subtasks"]["api"]["status"] != "approved"
    assert len(timeout_rows_of(patch)) == 1
