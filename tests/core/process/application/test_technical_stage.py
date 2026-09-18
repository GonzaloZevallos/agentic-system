"""The technical stage against in-memory ports: the plan, one Developer per subtask
in its own checkout, the review loop per pair, the integration and the delivery
(ADR-0011, ADR-0014)."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.develop import develop
from agentic.core.process.application.escalate import escalate
from agentic.core.process.application.integrate import integrate
from agentic.core.process.application.plan import plan
from agentic.core.process.application.ports import AttemptFailed, CommandResult, RawResult
from agentic.core.process.application.review import review
from agentic.core.process.application.validate_definition import validate_definition
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import FINDINGS
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    finding,
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


async def test_validate_consolidates_into_the_task_checkout_and_commits(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("approved")]))
    patch = await validate_definition(TASK, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert ports.knowledge.consolidated_in == TASK_PATH
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
    assert patch["sections"] == {
        "Developer: api": "### Checks\n\nRun by the system on `head2` when the Developer "
        "delivered.\n\n| Check | Command | Result |\n|---|---|---|\n| `tests` | `pytest` "
        "| passed |\n\n### Changes\n\nx.py"
    }
    assert patch["runs"][0]["section"] == "Developer: api" and patch["status"] == "developing"


async def test_a_returned_developer_resumes_its_session_on_a_delta(cfg):
    # README.md, 3; ADR-0004, sixth addendum: a return goes back to the same Developer,
    # over its own earlier session, with only what the return added as the new message.
    state = {
        **TASK,
        "subtasks": {
            "api": sub(
                "api", "returned", branch="agentic/GH-12.api", base="base1", reason="no test"
            ),
            "domain": sub("domain"),
        },
        "subtask_id": "api",
        "sections": {
            **TASK["sections"],
            "Developer: api": "### Changes\n\nold work",
            "Tech Lead: review api": "### Verdict\n\n**returned** (`spec-behaviour`): no test",
            "Human": "### Decision 1\n\nUSD.",
            FINDINGS: "| blocking | subtask | spec-behaviour | api | x.py:1 | wrong |",
        },
        "runs": [
            {
                "role": "Developer",
                "section": "Developer: api",
                "session_id": "dev-1",
                "cost_usd": 0.1,
                "version": 1,
                "model": "m",
                "cost_by_model": {},
                "turns": 1,
            }
        ],
    }
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(runner)
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    patch = await develop(state, cfg, ports)
    call = runner.calls[0]
    assert call["resume"] == "dev-1"
    prompt = call["prompt"]
    assert prompt.startswith("Branch `agentic/GH-12.api` is at `head1`.")
    assert "## Human\n\n### Decision 1" in prompt
    assert "## Tech Lead: findings\n\n| blocking" in prompt
    assert "**Returned:** no test" in prompt
    assert "## Tech Lead: review api" in prompt
    # Never repeated: the session already has them.
    assert "## Original request" not in prompt
    assert "## PM" not in prompt
    assert "old work" not in prompt
    assert "## Engineering standards" not in prompt
    assert patch["subtasks"]["api"]["status"] == "delivered"


async def test_a_returned_developer_with_a_stale_session_falls_back_cold_with_a_trace(cfg):
    # The session on record cannot be reopened (another machine, a pruned store): the
    # system drops it, restarts cold with the full context, and leaves a trace on the
    # delivered section so an expensive rework run can be explained without logs.
    state = {
        **TASK,
        "subtasks": {
            "api": sub(
                "api", "returned", branch="agentic/GH-12.api", base="base1", reason="no test"
            ),
            "domain": sub("domain"),
        },
        "subtask_id": "api",
        "sections": {
            **TASK["sections"],
            "Developer: api": "### Changes\n\nold work",
            "Tech Lead: review api": "### Verdict\n\n**returned**: no test",
        },
        "runs": [
            {
                "role": "Developer",
                "section": "Developer: api",
                "session_id": "dead-id",
                "cost_usd": 0.1,
                "version": 1,
                "model": "m",
                "cost_by_model": {},
                "turns": 1,
            }
        ],
    }
    stale = AttemptFailed("session not found", 0.02, session_id="")
    runner = ScriptedRunner(developer=[stale, delivered()])
    ports = memory_ports(runner)
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    patch = await develop(state, cfg, ports)
    first, retry = runner.calls
    assert first["resume"] == "dead-id"
    assert retry["resume"] is None
    # The cold prompt is today's full context: the standards, the original request, PM's
    # section, and the Developer's own previous section, all back in.
    assert "## Engineering standards" in retry["prompt"]
    assert "## Original request" in retry["prompt"]
    assert "## PM" in retry["prompt"]
    assert "old work" in retry["prompt"]
    # Plus the same delta the resumed session would have read, appended at the end
    # (`with_context`): the branch head note, the reason and the review.
    assert retry["prompt"].endswith(
        "## Tech Lead: review api\n\n**Returned:** no test\n\n### Verdict\n\n**returned**: no test"
    )
    assert "Branch `agentic/GH-12.api` is at `head1`." in retry["prompt"]
    section = patch["sections"]["Developer: api"]
    assert section.startswith(
        "_The system asked to resume the previous session; the harness could not"
    )
    assert patch["subtasks"]["api"]["status"] == "delivered"


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


def two_delivered() -> dict:
    return {
        **TASK,
        "subtasks": {
            "api": sub("api", "delivered", branch="agentic/GH-12.api", base="base1"),
            "domain": sub("domain", "delivered", branch="agentic/GH-12.domain", base="base1"),
        },
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: domain": "y"},
    }


async def test_a_review_runs_on_its_own_subtask_and_nobody_else(cfg):
    # One run, one subtask: the review is the second half of its Developer's branch in
    # the fan-out, so it never walks the others (ADR-0004, fifth addendum).
    state = {**two_delivered(), "subtask_id": "api"}
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    ports = memory_ports(runner)
    patch = await review(state, cfg, ports)
    (api,) = runner.calls
    assert api["cwd"] == MemoryWorkspace.ROOT / "GH-12.api"
    assert "from the commit `base1`" in api["system_prompt"]
    assert "subtask `api`" in api["system_prompt"] and "You have 2 left" in api["system_prompt"]
    assert "## Developer: api\n\nx" in api["prompt"] and "## Developer: domain" not in api["prompt"]
    assert patch["subtasks"] == {
        "api": {**state["subtasks"]["api"], "status": "approved", "reviewed_head": "base"}
    }
    assert patch["review_kind"] == "first"
    assert set(patch["sections"]) == {"Tech Lead: review api"}
    assert ports.documents.last["sections"]["Tech Lead: review api"].startswith(
        "### Verdict\n\n**approved**: fine"
    )
    # Noticing that every subtask is approved, and adding the integration, is the
    # fan-in's: a branch cannot see its sisters.
    assert INTEGRATION not in patch["subtasks"]


async def test_a_returned_review_counts_for_the_pair_and_keeps_the_reason(cfg):
    state = {**two_delivered(), "subtask_id": "domain"}
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "no test")])
    ports = memory_ports(runner)
    patch = await review(state, cfg, ports)
    assert patch["subtasks"]["domain"] == {
        **state["subtasks"]["domain"],
        "status": "returned",
        "reason": "no test",
        "change_category": "spec-behaviour",
        "reviewed_head": "base",
    }
    assert patch["changes_requested"] == {"Tech Lead→Developer:domain": 1}
    assert patch["change_history"] == [
        {"pair": "Tech Lead→Developer:domain", "category": "spec-behaviour", "reason": "no test"}
    ]
    assert "change_history" in ports.documents.last
    assert patch["sections"]["Tech Lead: review domain"] == (
        "### Verdict\n\n**returned** (`spec-behaviour`): no test\n\n### Findings\n\nNone."
    )

    # The Developer of `domain` is sent back; the next pass spends the pair's last one.
    again = apply(state, patch)
    again["subtasks"]["domain"]["status"] = "delivered"
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await review(again, cfg, memory_ports(runner))
    assert "You have 1 left" in runner.calls[0]["system_prompt"]
    assert patch["subtasks"]["domain"]["status"] == "approved"


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
        "subtask_id": "api",
    }
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "no test"), reviewed()])
    ports = memory_ports(runner)
    ports.workspace.moved.add(path)  # the branch has commits: its head moves on every look
    patch = await review(state, cfg, ports)
    first = runner.calls[0]["system_prompt"]
    assert "from the commit `base1`" in first and "This is a first review" in first
    assert patch["subtasks"]["api"]["reviewed_head"] == "head1"
    assert patch["review_kind"] == "first"

    again = apply(state, patch)
    again["subtasks"]["api"]["status"] = "delivered"
    patch = await review(again, cfg, ports)
    second = runner.calls[1]["system_prompt"]
    assert "from the commit `head1`" in second and "This is an incremental review" in second
    assert "`base1`" not in second
    assert patch["subtasks"]["api"]["reviewed_head"] == "head2"
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["review_kind"] == "incremental"


async def test_review_kind_is_integration_for_the_fan_ins_own_subtask(cfg):
    state = {
        **TASK,
        "subtasks": {INTEGRATION: sub(INTEGRATION, "delivered", branch="agentic/GH-12", base="b")},
        "subtask_id": INTEGRATION,
    }
    runner = ScriptedRunner(tech_lead_review=[reviewed()])
    patch = await review(state, cfg, memory_ports(runner))
    assert patch["review_kind"] == "integration"


async def test_review_escalates_when_the_pair_hits_its_limit(cfg):
    state = {
        **TASK,
        "subtasks": {"api": sub("api", "delivered", branch="b", base="x"), "b": sub("b")},
        "changes_requested": {"Tech Lead→Developer:api": 2},
        "subtask_id": "api",
    }
    patch = await review(
        state, cfg, memory_ports(ScriptedRunner(tech_lead_review=[reviewed("returned", "still")]))
    )
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "Tech Lead→Developer:api hit 2 returns: still"
    assert patch["escalation_class"] == "returns"
    assert "changes_requested" not in patch and "change_history" not in patch
    # Returned without counting, so the human's answer sends it back to its Developer.
    api = patch["subtasks"]["api"]
    assert api["status"] == "returned" and api["reason"] == "still"
    assert api["change_category"] == "spec-behaviour" and api["reviewed_head"] == "base"


async def test_a_review_whose_only_block_has_no_evidence_approves(cfg):
    from tests.fakes import finding

    state = {
        **TASK,
        "subtasks": {"api": sub("api", "delivered", branch="b", base="x")},
        "subtask_id": "api",
    }
    bare = RawResult(
        {"markdown": "### Findings", "reason": "r", "findings": [finding() | {"evidence": ""}]},
        0.2,
    )
    runner = ScriptedRunner(tech_lead_review=[bare])
    patch = await review(state, cfg, memory_ports(runner))
    assert len(runner.calls) == 1 and patch["failures"] == []
    assert patch["subtasks"]["api"]["status"] == "approved" and "changes_requested" not in patch
    assert (
        "blocking lowered to observation: no evidence" in patch["sections"]["Tech Lead: review api"]
    )


async def test_the_integrator_builds_on_the_task_branch_with_the_branches_listed(cfg):
    state = {
        **TASK,
        "subtasks": {
            "api": sub("api", "approved", branch="agentic/GH-12.api"),
            "domain": sub("domain", "approved", branch="agentic/GH-12.domain"),
            INTEGRATION: sub(INTEGRATION, conflicts=["agentic/GH-12.domain"]),
        },
        "subtask_id": INTEGRATION,
    }
    runner = ScriptedRunner(developer_integrator=[delivered()])
    ports = memory_ports(runner)
    ports.workspace.moved.add(TASK_PATH)
    patch = await develop(state, cfg, ports)
    call = runner.calls[0]
    assert call["cwd"] == TASK_PATH
    # What the fan-in already merged and what it left: the integrator is here for the
    # second list, and a merge it repeats over the first is a no-op it pays for.
    assert (
        "- `agentic/GH-12.api` (api: api) — already merged\n"
        "- `agentic/GH-12.domain` (domain: domain) — **conflicts, not merged**"
        in call["system_prompt"]
    )
    # The integration never merges its dependencies itself: the fan-in did that.
    assert ports.workspace.merges == []
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"
    (section,) = patch["sections"]
    assert section == "Developer: integration"
    assert patch["sections"][section].endswith("| passed |\n\n### Changes\n\nx.py")


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


def developing(ports, subtasks=("api", "domain")):
    state = {**TASK, "subtasks": {sid: sub(sid) for sid in subtasks}, "subtask_id": "api"}
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    ports.workspace.branches["agentic/GH-12"] = ("head", "base0")
    return state, checkout


async def test_the_presubmit_runs_the_checks_declared_at_the_tasks_base_and_records_them(cfg):
    checks = {"tests": "uv run pytest", "lint": "ruff check ."}
    commands = MemoryCommands(**{"ruff check .": [CommandResult(1, "E501 line too long")]})
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(runner, stack=MemoryStack(checks), commands=commands)
    state, checkout = developing(ports)
    patch = await develop(state, cfg, ports)
    # The version at the task's base holds, never the branch's (issue #22, 7). Asked
    # twice: once for the setup of the task checkout born here, once for the checks.
    assert ports.stack.revisions == ["base0", "base0"]
    assert "- `lint`: `ruff check .`" in runner.calls[0]["system_prompt"]
    # Red, the model is told inside its turn and delivers again; green, it counts.
    (reason,) = runner.refused
    assert "(1 of 2)" in reason and "`lint` failed: `ruff check .`" in reason
    assert "E501 line too long" in reason
    assert commands.ran == [(checkout.path, c) for c in [*checks.values(), *checks.values()]]
    section = patch["sections"]["Developer: api"]
    assert section.startswith("### Checks\n\nRun by the system on `head")
    assert "| `lint` | `ruff check .` | passed |" in section
    assert section.endswith("### Changes\n\nx.py")
    assert patch["subtasks"]["api"]["status"] == "delivered"


async def test_the_presubmit_refuses_twice_then_lets_the_reviewer_see_the_red(cfg):
    red = [CommandResult(1, "1 failed")] * 3
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(runner, commands=MemoryCommands(pytest=red))
    state, _ = developing(ports)
    patch = await develop(state, cfg, ports)
    assert len(runner.refused) == 2 and "(2 of 2)" in runner.refused[1]
    assert "| `tests` | `pytest` | **failed** |" in patch["sections"]["Developer: api"]
    assert patch["subtasks"]["api"]["status"] == "delivered"
    # Three delivery attempts (two refused, one the backstop let through despite the
    # red) leave three invocations, the measurement-only key the adapter reads for
    # `agentic.presubmit.checks`: one set of check samples per invocation.
    assert len(patch["presubmit_checks"]) == 3
    assert all(check["passed"] is False for inv in patch["presubmit_checks"] for check in inv)


async def test_the_presubmit_refuses_an_uncommitted_delivery_without_running_the_checks(cfg):
    runner = ScriptedRunner(developer=[delivered(), delivered()])
    ports = memory_ports(runner)
    state, checkout = developing(ports)
    ports.workspace.dirty.add(checkout.path)
    patch = await develop(state, cfg, ports)
    assert len(runner.refused) == 2 and all("uncommitted" in r for r in runner.refused)
    # Past its refusals a delivery that is not one is left to the backstop, unchecked.
    assert ports.commands.ran == []
    assert "uncommitted changes" in patch["failures"][0]["error"]


async def test_the_reviewer_may_run_the_declared_checks_and_reads_them(cfg):
    state = {
        **TASK,
        "subtasks": {"api": sub("api", "delivered", branch="agentic/GH-12.api", base="b")},
        "subtask_id": "api",
    }
    checks = {"tests": "uv run --no-project pytest"}
    runner = ScriptedRunner(tech_lead_review=[reviewed()])
    ports = memory_ports(runner, stack=MemoryStack(checks))
    await review(state, cfg, ports)
    call = runner.calls[0]
    assert "Bash(uv run --no-project pytest:*)" in call["role"].allowed
    assert "- `tests`: `uv run --no-project pytest`" in call["system_prompt"]


# The fan-in (ADR-0004, fifth addendum): the system's turn between two waves.


def approved(sid: str) -> dict:
    return sub(sid, "approved", branch=f"agentic/GH-12.{sid}", base="base1")


async def test_the_fan_in_merges_each_approved_subtask_into_the_task_branch(cfg):
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}
    ports = memory_ports(ScriptedRunner())
    patch = await integrate(state, cfg, ports)

    assert ports.workspace.merges == [
        (TASK_PATH, "agentic/GH-12.api"),
        (TASK_PATH, "agentic/GH-12.domain"),
    ]
    assert patch["branch"] == "agentic/GH-12"
    # Everything approved and more than one: the integration enters here, not in a
    # review, because no single branch can see that its sisters are done. Every merge
    # applied and the plan declared no integration work, so it enters `delivered`: the
    # branch goes straight to its review and no integrator is paid for a merge the
    # system already did.
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"
    assert patch["subtasks"][INTEGRATION]["conflicts"] == []
    assert ports.documents.last["subtasks"][INTEGRATION]["status"] == "delivered"


async def test_a_merge_the_fan_in_cannot_do_is_left_for_the_integrator(cfg):
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}
    ports = memory_ports(ScriptedRunner())
    ports.workspace.conflicts.add("agentic/GH-12.domain")
    patch = await integrate(state, cfg, ports)

    # The task branch keeps what did apply; the rest is named for the role whose job
    # resolving it is. Not an escalation: a conflict is a fact about the code.
    integration = patch["subtasks"][INTEGRATION]
    assert integration["conflicts"] == ["agentic/GH-12.domain"]
    # A conflict is what an integrator is for, so now one runs and the brief names it.
    assert integration["status"] == "pending"
    assert "`agentic/GH-12.domain`" in integration["brief"]
    assert "escalated" not in patch


async def test_the_integrator_runs_when_the_plan_declared_integration_work(cfg):
    """No conflict and the merges are the whole job: nothing for a Developer to do, and
    the integration goes to its review delivered. Unless the plan said otherwise."""
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}
    ports = memory_ports(ScriptedRunner())

    plain = await integrate(state, cfg, ports)
    assert plain["subtasks"][INTEGRATION]["status"] == "delivered"

    declared = await integrate({**state, "integration_brief": "Wire the two ends."}, cfg, ports)
    assert declared["subtasks"][INTEGRATION]["status"] == "pending"
    assert "Wire the two ends." in declared["subtasks"][INTEGRATION]["brief"]


async def test_the_fan_in_creates_the_wave_checkouts_one_at_a_time(cfg):
    # Four `git worktree add` starting within 250 ms of each other raced for the same
    # lock in run 4, and a lost lock was a crash with no escalation.
    state = {**TASK, "subtasks": {"api": sub("api"), "domain": sub("domain")}}
    ports = memory_ports(ScriptedRunner())
    await integrate(state, cfg, ports)
    # The task's branch is asked for again before each subtask's, and answered with the
    # worktree that exists: what must stay serial is making them, not asking.
    assert ports.workspace.checkouts_made == [
        "agentic/GH-12",
        "agentic/GH-12",
        "agentic/GH-12.api",
        "agentic/GH-12",
        "agentic/GH-12.domain",
    ]


async def test_the_fan_in_settles_the_status_of_a_task_a_branch_escalated(cfg):
    # `status` is last-write-wins: the branch that escalated wrote `waiting_on_human`
    # and a sister that delivered fine wrote `developing` in the same superstep.
    state = {**TASK, "status": "developing", "escalated": "Developer failed 2 times: x"}
    ports = memory_ports(ScriptedRunner())
    patch = await integrate(state, cfg, ports)
    assert patch == {"status": "waiting_on_human", "sections": {FINDINGS: "None open."}}
    # Nothing touches the workspace or the document while the task waits: `escalate`
    # saves it next, from the state with every branch's work already merged in.
    assert ports.workspace.merges == [] and ports.documents.saved == []


# The channel between reviews (issue #42).


async def test_the_plan_keeps_the_invariants_the_ephemeral_proposed_and_it_adopted(cfg):
    """The ephemeral proposes from what the grill uncovered; the Tech Lead decides. What
    it adopted is the state's, and the reviewer checks each delivery against it."""
    runner = ScriptedRunner(
        **{
            ASK: [
                questions(1, session="e1", invariants=["fold duplicates", "fold duplicates"]),
                questions(done=True, session="e1", invariants=["resolve relative imports"]),
            ]
        },
        **{ANSWER: [tech_answers(1, session="t1")]},
        tech_lead=[planned(invariants=["Every subscription query folds duplicates."])],
    )
    patch = await plan(TASK, cfg, memory_ports(runner))

    # Proposed once each, in order, whatever a round repeats; the closing round counts.
    assert "- fold duplicates\n- resolve relative imports" in runner.calls[-1]["system_prompt"]
    assert patch["invariants"] == ["Every subscription query folds duplicates."]
    assert patch["integration_brief"] == ""


async def test_the_invariants_reach_the_developer_and_the_reviewer(cfg):
    state = {**two_delivered(), "invariants": ["Every subscription query folds duplicates."]}
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    await review({**state, "subtask_id": "api"}, cfg, memory_ports(runner))
    assert "- Every subscription query folds duplicates." in runner.calls[0]["system_prompt"]

    building = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(building)
    ports.workspace.moved.add(MemoryWorkspace.ROOT / "GH-12.api")
    await develop({**state, "subtask_id": "api"}, cfg, ports)
    assert "- Every subscription query folds duplicates." in building.calls[0]["system_prompt"]


async def test_every_review_appends_its_findings_whatever_the_verdict(cfg):
    """An approval with an `important` finding is the run-4 case: "worth a remark, not a
    charge" is exactly what came back as an integration return."""
    remark = finding(severity="important", claim="counts twice", scope="plan")
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved", findings=[remark])])
    patch = await review({**two_delivered(), "subtask_id": "api"}, cfg, memory_ports(runner))

    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["findings"] == [
        {
            **{k: v for k, v in remark.items() if k != "evidence"},
            "subtask": "api",
            "version": 1,
            "status": "open",
        }
    ]


async def test_the_fan_in_writes_the_table_the_next_wave_reads(cfg):
    """A branch cannot fold the table: its sisters are reviewing at the same time. The
    fan-in is where the branches are merged, so it is where the table is written."""
    state = {
        **TASK,
        "subtasks": {"api": approved("api"), "domain": approved("domain")},
        "findings": [
            *rules.finding_records("api", 1, [finding(severity="important", claim="counts twice")]),
            *rules.finding_records(
                "domain", 1, [{**finding(claim="gone"), "location": "gone.py:2"}]
            ),
            *rules.finding_records("domain", 2, [finding(claim="still here")]),
        ],
        "settled_findings": ["gone.py:2"],
    }
    patch = await integrate(state, cfg, memory_ports(ScriptedRunner()))

    table = patch["sections"][FINDINGS]
    assert "counts twice" in table and "still here" in table
    assert "gone" not in table  # a review settled it by name: fixed, not open


async def test_the_table_is_written_even_when_a_branch_escalated(cfg):
    """The escalated fan-in touches nothing, but folding the table is pure computation:
    the human reading the document sees what this wave found, not the wave before."""
    state = {
        **TASK,
        "escalated": "Developer failed twice",
        "subtasks": {"api": approved("api")},
        "findings": rules.finding_records("api", 1, [finding(claim="counts twice")]),
    }
    ports = memory_ports(ScriptedRunner())
    patch = await integrate(state, cfg, ports)

    assert patch["status"] == "waiting_on_human"
    assert "counts twice" in patch["sections"][FINDINGS]
    assert ports.workspace.merges == []  # nothing was touched
