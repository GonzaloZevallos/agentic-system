"""The technical stage against in-memory ports: the plan, one Developer per subtask
in its own checkout, the review loop per pair, the integration and the delivery
(ADR-0011, ADR-0014)."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.develop import develop
from agentic.core.process.application.escalate import escalate
from agentic.core.process.application.plan import plan
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.review import review
from agentic.core.process.application.validate_definition import validate_definition
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
    planned,
    questions,
    reviewed,
    tech_answers,
    verdict,
)

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
ASK, ANSWER = "tech_lead_ephemeral", "tech_lead_interviewee"
TASK_PATH = MemoryWorkspace.ROOT / "GH-12"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def sub(id_, status="pending", **more):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        **more,
    }


async def test_validate_distils_into_the_task_checkout_and_commits(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("approved")]))
    patch = await validate_definition(TASK, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert ports.knowledge.distilled_in == TASK_PATH
    assert ports.workspace.commits == [(TASK_PATH, "GH-12: product definition")]
    assert ports.runner.calls[0]["cwd"] == TASK_PATH


async def test_plan_interviews_in_the_task_checkout_then_writes_the_subtasks(cfg):
    runner = ScriptedRunner(
        **{ASK: [questions(1, session="e1"), questions(done=True, session="e1")]},
        **{ANSWER: [tech_answers(1, session="t1")]},
        tech_lead=[
            planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["Dashboard"]})
        ],
    )
    ports = memory_ports(runner)
    patch = await plan(TASK, cfg, ports)
    assert patch["status"] == "planned"
    assert runner.slugs == [ASK, ANSWER, ASK, "tech_lead"]
    assert all(c["cwd"] == TASK_PATH for c in runner.calls)
    assert runner.calls[-1]["resume"] == "t1"  # the author is the interviewee's side
    assert runner.calls[0]["prompt"].startswith("## Engineering standards\n\nHex.")
    assert ports.workspace.commits == [(TASK_PATH, "GH-12: solution interview")]
    assert patch["subtasks"] == {
        "api": sub("api", title="API", brief="Build it.", seams=["GET /x"]),
        "domain": sub("domain", title="Domain", brief="b", seams=["Dashboard"]),
    }
    assert "Interview: Tech Lead efímero→Tech Lead" in patch["sections"]


async def test_plan_rejects_a_plan_without_seams_as_a_role_failure(cfg):
    bad = planned({"id": "x", "title": "x", "brief": "b", "seams": []})
    runner = ScriptedRunner(**{ASK: [questions(done=True)]}, tech_lead=[bad, bad])
    patch = await plan(TASK, cfg, memory_ports(runner))
    assert patch["status"] == "waiting_on_human" and "seams" in patch["failures"][0]["error"]


async def test_develop_runs_the_developer_unattended_in_the_subtask_checkout(cfg):
    state = {**TASK, "subtasks": {"api": sub("api"), "domain": sub("domain")}, "subtask_id": "api"}
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(runner)
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    patch = await develop(state, cfg, ports)
    call = runner.calls[0]
    assert call["cwd"] == checkout.path and call["max_budget_usd"] == 10.0
    assert "implement one subtask, `api`" in call["system_prompt"]
    assert call["prompt"].startswith("## Engineering standards")
    assert "## PM\n\n### Consolidated definition" in call["prompt"]
    assert patch["subtasks"] == {
        "api": {**sub("api"), "status": "delivered", "branch": "agentic/GH-12.api", "base": "base1"}
    }
    assert patch["sections"] == {"Developer: api": "### Changes\n\nx.py"}
    assert patch["runs"][0]["section"] == "Developer: api" and patch["status"] == "developing"


async def test_a_dependent_subtask_starts_on_its_dependencies_code(cfg):
    state = {
        **TASK,
        "subtasks": {
            "domain": sub("domain", "approved", branch="agentic/GH-12.domain"),
            "api": sub("api", depends_on=["domain"]),
        },
        "subtask_id": "api",
    }
    ports = memory_ports(ScriptedRunner(developer=[delivered()]))
    api = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(api.path)
    await develop(state, cfg, ports)
    assert ports.workspace.merges == [(api.path, "agentic/GH-12.domain")]


async def test_a_single_subtask_builds_on_the_task_branch(cfg):
    state = {**TASK, "subtasks": {"api": sub("api")}, "subtask_id": "api"}
    ports = memory_ports(ScriptedRunner(developer=[delivered()]))
    ports.workspace.moved.add(TASK_PATH)
    patch = await develop(state, cfg, ports)
    assert patch["subtasks"]["api"]["branch"] == "agentic/GH-12"


async def test_a_developer_that_does_not_commit_fails_and_is_retried(cfg):
    state = {**TASK, "subtasks": {"api": sub("api"), "b": sub("b")}, "subtask_id": "api"}
    runner = ScriptedRunner(developer=[delivered(), delivered()])
    patch = await develop(state, cfg, memory_ports(runner))
    assert patch["status"] == "waiting_on_human"
    assert "no commit on the branch" in patch["failures"][0]["error"]
    assert "no commit on the branch" in runner.calls[1]["prompt"]
    assert patch["subtasks"]["api"]["status"] == "pending"


async def test_a_developer_that_leaves_the_tree_dirty_fails_and_is_retried(cfg):
    # Run 4, `expiring-soon`: the fix sat in the working tree, the branch's HEAD failed
    # the architecture test, and the suite passed against code the branch did not hold.
    state = {**TASK, "subtasks": {"api": sub("api"), "b": sub("b")}, "subtask_id": "api"}
    runner = ScriptedRunner(developer=[delivered(), delivered()])
    ports = memory_ports(runner)
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    ports.workspace.dirty.add(checkout.path)
    patch = await develop(state, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    assert "uncommitted changes" in patch["failures"][0]["error"]
    assert "uncommitted changes" in runner.calls[1]["prompt"]
    assert patch["subtasks"]["api"]["status"] == "pending"


async def test_review_approves_returns_and_adds_the_integration(cfg):
    state = {
        **TASK,
        "subtasks": {
            "api": sub("api", "delivered", branch="agentic/GH-12.api", base="base1"),
            "domain": sub("domain", "delivered", branch="agentic/GH-12.domain", base="base1"),
        },
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: domain": "y"},
    }
    runner = ScriptedRunner(
        tech_lead_review=[reviewed("approved"), reviewed("returned", "no test")]
    )
    ports = memory_ports(runner)
    patch = await review(state, cfg, ports)
    api, domain = runner.calls
    assert api["cwd"] == MemoryWorkspace.ROOT / "GH-12.api"
    assert "from the commit `base1`" in api["system_prompt"]
    assert "subtask `api`" in api["system_prompt"] and "You have 2 left" in api["system_prompt"]
    assert "## Developer: api\n\nx" in api["prompt"] and "## Developer: domain" not in api["prompt"]
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["subtasks"]["domain"] == {
        **state["subtasks"]["domain"],
        "status": "returned",
        "reason": "no test",
        "return_category": "spec-behaviour",
        "reviewed_head": "base",
    }
    assert patch["returns"] == {"Tech Lead→Developer:domain": 1}
    assert patch["return_history"] == [
        {"pair": "Tech Lead→Developer:domain", "category": "spec-behaviour", "reason": "no test"}
    ]
    assert "return_history" in ports.documents.last
    assert INTEGRATION not in patch["subtasks"]  # not until every subtask is approved
    assert set(patch["sections"]) == {"Tech Lead: review api", "Tech Lead: review domain"}
    assert patch["sections"]["Tech Lead: review domain"] == (
        "### Verdict\n\n**returned** (`spec-behaviour`): no test\n\n### Findings\n\nNone."
    )
    assert ports.documents.last["sections"]["Tech Lead: review api"].startswith(
        "### Verdict\n\n**approved**: fine"
    )

    # The Developer of `domain` is sent back; once it is approved, the integrator enters.
    again = apply(state, patch)
    again["subtasks"]["domain"]["status"] = "delivered"
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await review(again, cfg, memory_ports(runner))
    assert "You have 1 left" in runner.calls[0]["system_prompt"]
    assert patch["subtasks"]["domain"]["status"] == "approved"
    assert patch["subtasks"][INTEGRATION]["status"] == "pending"


async def test_a_review_after_a_return_starts_from_the_head_it_reviewed(cfg):
    # The first pass diffs from the subtask's base with the brief as spec. After a
    # return, the next pass diffs from the head that pass reviewed, with its own
    # `For the Developer` list as spec: what it approved is not reviewed twice (ADR-0015).
    path = MemoryWorkspace.ROOT / "GH-12.api"
    state = {
        **TASK,
        "subtasks": {
            "api": sub("api", "delivered", branch="agentic/GH-12.api", base="base1"),
            "b": sub("b"),  # a second subtask, so `api` has a checkout of its own
        },
        "sections": {**TASK["sections"], "Developer: api": "x"},
    }
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "no test"), reviewed()])
    ports = memory_ports(runner)
    ports.workspace.moved.add(path)  # the branch has commits: its head moves on every look
    patch = await review(state, cfg, ports)
    first = runner.calls[0]["system_prompt"]
    assert "from the commit `base1`" in first and "This is a first review" in first
    assert patch["subtasks"]["api"]["reviewed_head"] == "head1"

    again = apply(state, patch)
    again["subtasks"]["api"]["status"] = "delivered"
    patch = await review(again, cfg, ports)
    second = runner.calls[1]["system_prompt"]
    assert "from the commit `head1`" in second and "This is an incremental review" in second
    assert "`base1`" not in second
    assert patch["subtasks"]["api"]["reviewed_head"] == "head2"
    assert patch["subtasks"]["api"]["status"] == "approved"


async def test_review_escalates_when_the_pair_hits_its_limit(cfg):
    state = {
        **TASK,
        "subtasks": {"api": sub("api", "delivered", branch="b", base="x"), "b": sub("b")},
        "returns": {"Tech Lead→Developer:api": 2},
    }
    patch = await review(
        state, cfg, memory_ports(ScriptedRunner(tech_lead_review=[reviewed("returned", "still")]))
    )
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "Tech Lead→Developer:api hit 2 returns: still"
    assert "returns" not in patch and "return_history" not in patch
    # Returned without counting, so the human's answer sends it back to its Developer.
    api = patch["subtasks"]["api"]
    assert api["status"] == "returned" and api["reason"] == "still"
    assert api["return_category"] == "spec-behaviour" and api["reviewed_head"] == "base"


async def test_a_review_whose_only_block_has_no_evidence_approves(cfg):
    from tests.fakes import finding

    state = {**TASK, "subtasks": {"api": sub("api", "delivered", branch="b", base="x")}}
    bare = RawResult(
        {"markdown": "### Findings", "reason": "r", "findings": [finding() | {"evidence": ""}]},
        0.2,
    )
    runner = ScriptedRunner(tech_lead_review=[bare])
    patch = await review(state, cfg, memory_ports(runner))
    assert len(runner.calls) == 1 and patch["failures"] == []
    assert patch["subtasks"]["api"]["status"] == "approved" and "returns" not in patch
    assert (
        "blocking lowered to observation: no evidence" in patch["sections"]["Tech Lead: review api"]
    )


async def test_the_integrator_builds_on_the_task_branch_with_the_branches_listed(cfg):
    state = {
        **TASK,
        "subtasks": {
            "api": sub("api", "approved", branch="agentic/GH-12.api"),
            "domain": sub("domain", "approved", branch="agentic/GH-12.domain"),
            INTEGRATION: sub(INTEGRATION),
        },
        "subtask_id": INTEGRATION,
    }
    runner = ScriptedRunner(developer_integrator=[delivered()])
    ports = memory_ports(runner)
    ports.workspace.moved.add(TASK_PATH)
    patch = await develop(state, cfg, ports)
    call = runner.calls[0]
    assert call["cwd"] == TASK_PATH
    assert (
        "- `agentic/GH-12.api` (api: api)\n- `agentic/GH-12.domain` (domain: domain)"
        in call["system_prompt"]
    )
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"
    assert patch["sections"] == {"Developer: integration": "### Changes\n\nx.py"}


async def test_deliver_pushes_opens_the_pull_request_and_tells_the_issue(cfg):
    state = {**TASK, "sections": {**TASK["sections"], "Tech Lead": "### Solution\n\nHex."}}
    ports = memory_ports()
    patch = await deliver(state, cfg, ports)
    assert patch["status"] == "ready_for_review"
    assert patch["pull_request"] == "https://github.com/o/r/pull/7"
    assert ports.workspace.pushed == ["agentic/GH-12"] and ports.workspace.cleaned == ["GH-12"]
    branch, title, body = ports.tracker.pull_requests[0]
    assert (branch, title) == ("agentic/GH-12", "Dashboard")
    assert body.startswith("## PM\n\n### Consolidated definition")  # `Closes #n` is the adapter's
    assert "## Tech Lead\n\n### Solution" in body
    task_id, text = ports.tracker.comments[0]
    assert task_id == "GH-12" and "pull/7" in text and ".agentic/tasks/GH-12.md" in text
    assert ports.documents.last["status"] == "ready_for_review"


async def test_escalate_labels_and_comments_with_the_questions(cfg):
    state = {**TASK, "escalated": "PM could not decide", "questions_for_human": ["Currency?"]}
    ports = memory_ports()
    assert await escalate(state, cfg, ports) == {}
    assert ports.documents.last == state  # the merged state, as the node received it
    assert ports.tracker.labels == [("GH-12", "needs-human")]
    text = ports.tracker.comments[0][1]
    assert text.startswith("Escalated: PM could not decide") and "1. Currency?" in text
    assert await escalate(TASK, cfg, ports) == {} and len(ports.tracker.labels) == 1


async def test_an_escalation_by_the_task_cap_breaks_the_spending_down_and_proposes_a_cap(cfg):
    state = {
        **TASK,
        "escalated": "Developer stopped by the task cap: 106.27 of 100.00 USD spent",
        "cost_usd": 106.27,
        "runs": [
            {"role": "PM", "section": "PM", "cost_usd": 2.27},
            {"role": "Developer", "section": "Developer: api", "cost_usd": 40.0},
            {"role": "Tech Lead", "section": "Tech Lead: review api", "cost_usd": 64.0},
        ],
    }
    ports = memory_ports()
    await escalate(state, cfg, ports)
    text = ports.tracker.comments[0][1]
    assert "Spent by phase:\n\n- definition: 2.27 USD\n- plan: 0.00 USD" in text
    assert "- development: 40.00 USD\n- review: 64.00 USD" in text
    assert text.endswith("then run `agentic resume --issue 12 --task-budget 122`.")
    # Any other escalation says how to resume, without a cap to raise.
    ports = memory_ports()
    await escalate({**TASK, "escalated": "PM could not decide"}, cfg, ports)
    assert "Spent by phase" not in ports.tracker.comments[0][1]
    assert ports.tracker.comments[0][1].endswith("`agentic resume --issue 12`.")
    ports = memory_ports()
    await escalate({**state, "id": "T-1"}, cfg, ports)  # no issue, no command to resume it
    assert "agentic resume" not in ports.tracker.comments[0][1]
