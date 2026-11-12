"""A promotion in mid-run through the compiled graph (GH-591, part 2): a light task whose first
subtask is approved and committed asks for the full path, goes through the definition roles and
re-plans, and the commit it already had stays: no branch is reset, discarded or cleaned, the
approved subtask keeps its branch and its reviewed head, and its review is repeated against
the new PRD. The workspace is in memory, so "stays" is what no call to it undid, plus the
head the document keeps."""

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application.ports import IssueState, RiskArea
from tests.core.process.adapters.langgraph.test_graph import (
    Fakes,
    MemoryWorkspace,
    compiled,
    saved,
    script_for,
    technical,
)
from tests.fakes import (
    BlockingRunner,
    MemoryInbox,
    MemoryStack,
    classified,
    delivered,
    planned,
    questions,
    reviewed,
)

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


async def test_a_promotion_keeps_an_approved_subtask_branch_and_the_head_approved_before_it(
    config, tmp_path
):
    """Two subtasks, each on its own branch: `api` is approved (and asks for the full path),
    then the task goes through define and re-plans. Both branches stay as they were: nothing
    reset, discarded or archived, and the head approved before the promotion is the head the
    document still holds after it."""
    fakes = Fakes()
    domain = {"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]}
    queues = script_for(
        pjm=[small_and_safe()],
        **technical(
            tech_lead=[planned(domain), planned(domain)],
            tech_lead_ephemeral=[questions(done=True), questions(done=True)],
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain")],
            tech_lead_review__api=[asking(ASK)],
            tech_lead_review__domain=[reviewed("approved")],
            tech_lead_review=[reviewed("approved")],  # the integration, on the task branch
        ),
    ).queues
    script = BlockingRunner(**queues)
    before: dict[str, str] = {}

    def on_call(key: str) -> None:
        if key == "product_analyst_interviewer__" and not before:
            subtasks = saved(FilesystemTaskDocuments(tmp_path)).get("subtasks", {})
            before.update({sid: s.get("reviewed_head", "") for sid, s in subtasks.items()})

    script.on_call = on_call
    fakes.tracker.states[1] = IssueState("OPEN", "", None, None, ())
    graph = compiled(script, fakes=fakes, inbox=MemoryInbox())
    final = await graph.ainvoke(
        {"id": "GH-1", "title": "Export report", "description": "CSV."}, config=config
    )

    assert final["initial_path"] == "light" and final["path"] == "full"
    assert final["promotion"]["text"] == ASK
    # The definition roles ran only after the subtasks were approved and committed.
    assert all(before.get(sid) for sid in ("api", "domain"))
    subtasks = final["subtasks"]
    for sid in ("api", "domain"):
        assert subtasks[sid]["status"] == "approved"
        assert subtasks[sid]["branch"] == f"agentic/GH-1.{sid}"
        # Kept, not re-approved: the head approved before the promotion is still the one.
        assert subtasks[sid]["reviewed_head"] == before[sid]
    assert script.began.count("developer__api") == 1
    assert script.began.count("tech_lead_review__api") == 1
    assert fakes.workspace.resets == []
    assert fakes.workspace.discarded == []
    assert fakes.workspace.archived_documents == {}


async def test_a_diff_under_a_declared_risk_glob_ends_on_the_full_path(config):
    """The whole net, no model spend on it: the stack declares a risk area, PjM sees a small
    task with no risk, the Developer's diff touches the glob, `risk_net` leaves the request,
    the fan-in promotes and the task goes through the definition roles to a full-path end."""
    fakes = Fakes()
    fakes.workspace.touched = ("src/x.py", "migrations/0001.sql")
    stack = MemoryStack(
        risk_areas=(RiskArea("data-migration", "Converts data", ("migrations/**",)),)
    )
    script = script_for(
        pjm=[small_and_safe()],
        **technical(
            tech_lead=[planned(), planned()],
            tech_lead_ephemeral=[questions(done=True), questions(done=True)],
            developer=[delivered()],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        ),
    )
    fakes.tracker.states[1] = IssueState("OPEN", "", None, None, ())
    graph = compiled(script, fakes=fakes, stack=stack, inbox=MemoryInbox())
    final = await graph.ainvoke(
        {"id": "GH-1", "title": "Export report", "description": "CSV."}, config=config
    )

    slugs = script.slugs
    assert slugs.index("tech_lead") < slugs.index("product_analyst_interviewer")  # light first
    assert final["initial_path"] == "light"
    assert final["path"] == "full"
    assert final["promotion"]["kind"] == "risk_area"
    assert final["promotion"]["from_role"] == "system"
    assert final["promotion"]["text"] == "data-migration"
    assert final["status"] == "ready_for_review"
    assert fakes.workspace.resets == []
