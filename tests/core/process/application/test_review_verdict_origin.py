"""A returned review carries the reason of the row that returned it (GH-511). On #471 the
only blocking row was the system's `required_paths` one, the reviewer's table was empty and
its reason approved, and the escalation said so. The verdict is derived (ADR-0016): rows that
are `outside`, observations or `important` never return. Seam: `application.review.review`
through the same fakes as `test_review_plan_ci.py`, with the real `change_category`."""

import pytest

from agentic.core.process.application.review import _required_paths_findings, review
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
REQUIRED = "docs/adr/0099-x.md"
SECTION = "Tech Lead: review api"
CLAIM = "the integrated diff touches none of the plan's required paths"
APPROVING = "All checks green and the previous item is done."
# The limit escalates only once the subtask has used its one clean restart (GH-593).
RESTARTED = {"Tech Lead→Developer:api": 1}


class FullIdWorkspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


def state(**plan):
    return {
        "id": "GH-511",
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
                "branch": "agentic/GH-511.api",
                "base": "base1",
            }
        },
        **plan,
    }


async def run(result, *, remaining=2, **plan):
    workspace = FullIdWorkspace()
    workspace.diff_paths = ("agentic/x.py",)
    runner = ScriptedRunner(tech_lead_review=[result])
    ports = memory_ports(runner, stack=MemoryStack(), workspace=workspace)
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, max_changes_requested_per_pair=remaining)
    return await review(state(**plan), cfg, ports)


def outside(severity="blocking", **kw):
    return finding(severity=severity, scope="outside", **kw)


async def test_only_outside_rows_and_observations_approve_at_the_limit_without_counting():
    rows = [outside(), outside("important"), finding(severity="observation")]
    patch = await run(reviewed(reason=APPROVING, findings=rows), remaining=0)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "changes_requested" not in patch and "change_history" not in patch
    assert "escalated" not in patch and patch.get("status") != "waiting_on_human"
    assert "**approved**" in patch["sections"][SECTION]


async def test_an_in_scope_important_row_approves_stays_open_and_does_not_count():
    patch = await run(
        reviewed(reason=APPROVING, findings=[finding(severity="important")]), remaining=0
    )
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "changes_requested" not in patch and "escalated" not in patch
    [row] = patch["findings"]
    assert row["severity"] == "important" and row["status"] == "open"


async def test_a_backed_in_scope_blocking_row_returns_and_counts():
    patch = await run(reviewed("returned", reason="the test is missing"))
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert patch["subtasks"]["api"]["reason"] == "the test is missing"
    assert patch["changes_requested"]
    assert patch["change_history"][0]["reason"] == "the test is missing"


async def test_a_system_row_over_an_approving_reviewer_returns_with_the_rows_own_reason():
    patch = await run(
        reviewed(reason=APPROVING, findings=[]),
        remaining=0,
        required_paths=[REQUIRED],
        restarts=RESTARTED,
    )
    expected = f"{CLAIM} ({REQUIRED})"
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert patch["subtasks"]["api"]["reason"] == expected
    assert patch["escalation_class"] == "returns"
    assert expected in patch["escalated"] and APPROVING not in patch["escalated"]
    section = patch["sections"][SECTION]
    assert f"**returned** (`spec-scope`): {expected}" in section
    assert "Blocking, added by the system:" in section and APPROVING not in section.split("\n\n")[1]


async def test_a_system_row_counted_before_the_limit_writes_its_reason_to_the_history():
    patch = await run(reviewed(reason=APPROVING, findings=[]), required_paths=[REQUIRED])
    assert patch["change_history"][0]["reason"] == f"{CLAIM} ({REQUIRED})"
    assert patch["change_history"][0]["category"] == "spec-scope"


async def test_a_reviewer_block_first_keeps_its_reason_and_the_system_row_is_listed():
    patch = await run(
        reviewed("returned", reason="the test is missing"),
        remaining=0,
        required_paths=[REQUIRED],
        restarts=RESTARTED,
    )
    section = patch["sections"][SECTION]
    assert patch["subtasks"]["api"]["reason"] == "the test is missing"
    assert "**returned** (`spec-behaviour`): the test is missing" in section
    assert f"Blocking, added by the system:\n\n- `{REQUIRED}`: {CLAIM}" in section
    assert "the test is missing" in patch["escalated"]


async def test_the_required_paths_writer_only_appends_to_the_findings_it_is_given():
    # `rules.appended` attributes rows to the system by this prefix contract (GH-511).
    workspace = FullIdWorkspace()
    workspace.diff_paths = ("agentic/x.py",)
    ports = memory_ports(ScriptedRunner(), workspace=workspace)
    before = [finding(severity="observation", claim="note")]
    out = _required_paths_findings(state(required_paths=[REQUIRED]), "HEAD", HEAD, before, ports)
    assert len(out) == 2 and out[: len(before)] == before
    assert out[1]["severity"] == "blocking" and out[1]["location"] == REQUIRED
