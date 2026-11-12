"""A pair at its return limit restarts its subtask once instead of escalating (GH-593, README §3).
Seam: `application.review.review` (the node that reviews one subtask) over the same fakes as
`test_review_verdict_origin.py`, with the real `rules.on_return_limit` and the restart patch."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain import restart, rules
from agentic.core.process.domain.roles import pair_for
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
PAIR = pair_for("api")
SECTION = "Tech Lead: review api"
CFG = Configuration(product_dir=MemoryWorkspace.ROOT, max_changes_requested_per_pair=2)


class FullIdWorkspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


def row(version, claim, location="agentic/x.py:1"):
    return {
        "subtask": "api",
        "version": version,
        "location": location,
        "claim": claim,
        "evidence": "e",
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
    }


def at_limit(**more):
    """`api` returned twice already (both counted), reviewed once more and about to be returned
    for the third time: the pair is at its limit of 2."""
    return {
        "id": "GH-593",
        "title": "T",
        "description": "d",
        "type": "feature",
        "sections": {
            "PM": "### Consolidated definition\n\nx",
            SECTION: "first review",
        },
        "runs": [{"role": "Tech Lead", "section": SECTION, "version": 2, "cost_usd": 0.1}],
        "subtask_id": "api",
        "subtasks": {
            "api": {
                "id": "api",
                "title": "api",
                "brief": "build the api",
                "seams": ["s"],
                "depends_on": [],
                "status": "delivered",
                "branch": "agentic/GH-593.api",
                "base": "base1",
                "reviewed_head": "b" * 40,
                "returned_by": "review",
                "reason": "second reason",
                "change_category": "spec-behaviour",
            }
        },
        "changes_requested": {PAIR.key: 2},
        "change_history": [
            rules.change_entry(PAIR, "spec-behaviour", "first reason"),
            rules.change_entry(PAIR, "spec-behaviour", "second reason"),
        ],
        "findings": [row(1, "first claim"), row(2, "second claim", "agentic/y.py:2")],
        **more,
    }


async def run(state, result):
    workspace = FullIdWorkspace()
    workspace.diff_paths = ("agentic/x.py",)
    runner = ScriptedRunner(tech_lead_review=[result])
    return await review(state, CFG, memory_ports(runner, stack=MemoryStack(), workspace=workspace))


async def test_the_return_at_the_limit_restarts_the_subtask_and_does_not_escalate():
    state = at_limit()
    patch = await run(state, reviewed("returned", "third reason"))

    assert "escalated" not in patch and patch.get("status") != "waiting_on_human"
    assert patch["restarts"] == {PAIR.key: 1}
    assert patch["changes_requested"] == {PAIR.key: 0}
    assert patch["restart_pending"] == {"subtask": "api", "archived_head": HEAD}
    fresh = patch["subtasks"]["api"]
    assert fresh["status"] == "pending" and fresh["attempt"] == 2
    assert "reviewed_head" not in fresh and "returned_by" not in fresh
    assert fresh["branch"] == "agentic/GH-593.api" and fresh["brief"] == "build the api"


async def test_the_restart_goes_through_the_one_constructor():
    state = at_limit()
    patch = await run(state, reviewed("returned", "third reason"))
    written = patch["sections"]["Restart api"]
    seen = apply(state, {"findings": patch["findings"]})
    expected = restart.restart_patch(seen, "api", PAIR, written, HEAD)
    # Same constructor, same keys: nothing is written beside it but the review's own records.
    for key in ("restarts", "changes_requested", "restart_pending", "session_floor"):
        assert patch[key] == expected[key]


async def test_the_rewritten_brief_holds_the_original_brief_both_passes_and_what_did_not_work():
    patch = await run(at_limit(), reviewed("returned", "third reason"))
    text = patch["sections"]["Restart api"]
    assert "build the api" in text
    assert "first claim" in text and "second claim" in text
    assert "first reason" in text and "second reason" in text
    assert "## What did not work" in text


async def test_every_open_row_of_the_archived_attempt_is_discarded_including_this_reviews():
    state = at_limit()
    patch = await run(state, reviewed("returned", "third reason"))
    after = apply(state, patch)
    rows = rules.findings_rows(after)
    api = [r for r in rows if r["subtask"] == "api"]
    assert api and all(r["status"] != "open" for r in api)


async def test_the_second_attempt_hitting_the_limit_escalates_with_todays_text():
    state = at_limit(restarts={PAIR.key: 1})
    patch = await run(state, reviewed("returned", "third reason"))
    assert patch["escalation_class"] == "returns"
    assert patch["escalated"] == rules.limit_reached(PAIR, 2, "third reason")
    assert patch["status"] == "waiting_on_human"
    assert "restart_pending" not in patch and "restarts" not in patch


async def test_a_subtask_ever_approved_escalates_instead_of_restarting():
    state = at_limit()
    state["subtasks"]["api"]["approved_once"] = True
    patch = await run(state, reviewed("returned", "third reason"))
    assert patch["escalation_class"] == "returns"
    assert "restart_pending" not in patch


async def test_a_return_below_the_limit_still_counts():
    state = at_limit(changes_requested={PAIR.key: 1})
    patch = await run(state, reviewed("returned", "again"))
    assert patch["changes_requested"] == {PAIR.key: 2}
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert "restart_pending" not in patch and "escalated" not in patch


async def test_a_return_of_the_restarted_attempt_is_marked_attempt_two_in_the_history():
    state = at_limit(restarts={PAIR.key: 1}, changes_requested={PAIR.key: 0})
    patch = await run(state, reviewed("returned", "again"))
    assert patch["change_history"][0]["attempt"] == 2


async def test_a_finding_without_a_blocking_row_never_restarts():
    state = at_limit()
    patch = await run(state, reviewed("approved", findings=[finding(severity="observation")]))
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "restart_pending" not in patch
