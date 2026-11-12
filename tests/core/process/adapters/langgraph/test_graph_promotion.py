"""A promotion in mid-run through the compiled graph (GH-591, part 2): a light task whose first
subtask is approved and committed asks for the full path, goes through the definition roles and
re-plans, and the commit it already had stays: no branch is reset, discarded or cleaned, the
approved subtask keeps its branch and its reviewed head, and its review is repeated against
the new PRD. The workspace is in memory, so "stays" is what no call to it undid, plus the
head the document keeps."""

import pytest

from agentic.core.process.application.ports import IssueState
from tests.core.process.adapters.langgraph.test_graph import (
    Fakes,
    MemoryWorkspace,
    compiled,
    script_for,
    technical,
)
from tests.fakes import MemoryInbox, classified, delivered, planned, questions, reviewed

pytestmark = pytest.mark.anyio

ASK = "the conversion is bigger than the issue says"


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def small_and_safe():
    result = classified()
    result.structured_output.update(size="small", risk=[])
    return result


def asking(promote: str):
    result = reviewed("approved")
    result.structured_output["promote"] = promote
    return result


async def test_a_light_task_promoted_after_an_approved_commit_keeps_the_commit_and_goes_full(
    config,
):
    fakes = Fakes()
    fakes.workspace.moved.add(MemoryWorkspace.ROOT / "GH-1.api")
    script = script_for(
        pjm=[small_and_safe()],
        **technical(
            tech_lead=[planned(), planned()],
            tech_lead_ephemeral=[questions(done=True), questions(done=True)],
            developer=[delivered()],
            tech_lead_review=[asking(ASK), reviewed("approved")],
        ),
        developer__api=[delivered()],
        tech_lead_review__api=[asking(ASK), reviewed("approved")],
    )
    fakes.tracker.states[1] = IssueState("OPEN", "", None, None, ())
    graph = compiled(script, fakes=fakes, inbox=MemoryInbox())
    final = await graph.ainvoke(
        {"id": "GH-1", "title": "Export report", "description": "CSV."}, config=config
    )

    # Light first: no definition role ran before the first plan.
    slugs = script.slugs
    assert slugs.index("tech_lead") < slugs.index("product_analyst_interviewer")
    assert final["initial_path"] == "light"
    # Then promoted by the role's request, once, applied by the system.
    assert final["path"] == "full"
    assert final["promotion"]["kind"] == "role"
    assert final["promotion"]["text"] == ASK
    assert not any("promote_request" in s for s in final["subtasks"].values())
    assert "plan_promote_request" not in final
    # The approved subtask kept its branch and was reviewed again, against the new PRD.
    api = final["subtasks"]["api"]
    assert api["status"] == "approved"
    assert not api.get("prd_recheck")
    assert slugs.count("tech_lead_review") == 2
    # The commit it had stays: nothing was reset or discarded (the delivery cleans up once, at
    # the end), and the single subtask's work stays on the task branch it was delivered on.
    assert fakes.workspace.resets == []
    assert fakes.workspace.discarded == []
    assert api["reviewed_head"]
