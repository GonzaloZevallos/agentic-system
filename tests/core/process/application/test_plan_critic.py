"""The `plan_review` arm in the plan node (GH-472, ADR-0086): a sizer reads the request, a
task under the threshold is planned cold, one critic reviews the draft and the Tech Lead
revises once; a large task, or a draft that turns out large, gets the full interview.
Behaviour seam: `plan()` with a scripted runner and in-memory ports. The default config with
no assignment is asserted too: it must make exactly today's calls and write no `plan_route`."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.application.ports import RawResult
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports, planned, questions

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
CRITIC_ARM = {"experiment": "e1", "arm": "critic", "switches": {"plan_review": "critic"}}
CONTROL_ARM = {"experiment": "e1", "arm": "control", "switches": {"plan_review": "interview"}}


def sized(cost: float = 12.0, files: int = 4, spent: float = 0.02) -> RawResult:
    return RawResult(
        {"markdown": "Small.", "estimated_cost_usd": cost, "estimated_files": files},
        spent,
        1,
        "sz",
    )


def critique(*severities: str, invariants: tuple[str, ...] = (), cost: float = 0.4) -> RawResult:
    findings = [
        {
            "place": "api",
            "claim": f"Problem {i}.",
            "evidence": "agentic/x.py:1" if s != "observation" else "",
            "severity": s,
        }
        for i, s in enumerate(severities)
    ]
    return RawResult(
        {
            "markdown": "### Findings\n\nSee below.",
            "findings": findings,
            "invariants_proposed": list(invariants),
        },
        cost,
        3,
        "cr",
    )


@pytest.fixture
def critic_cfg(tmp_path):
    return Configuration(product_dir=tmp_path, plan_review="critic")


def task(assignment=None, **extra):
    return {**TASK, **({"assignment": assignment} if assignment else {}), **extra}


async def test_the_default_with_no_assignment_makes_todays_calls_and_writes_no_route(tmp_path):
    runner = ScriptedRunner(
        tech_lead_ephemeral=[questions(done=True, session="e1")], tech_lead=[planned()]
    )
    patch = await plan(TASK, Configuration(product_dir=tmp_path), memory_ports(runner))

    assert runner.slugs == ["tech_lead_ephemeral", "tech_lead"]
    assert "plan_route" not in patch
    assert not any(r["slug"] == "plan_sizer" for r in patch["runs"])


async def test_the_interview_arm_sizes_then_interviews_and_records_the_control_route(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized(cost=12.0)],
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned()],
    )
    patch = await plan(task(CONTROL_ARM), Configuration(product_dir=tmp_path), memory_ports(runner))

    assert runner.slugs == ["plan_sizer", "tech_lead_ephemeral", "tech_lead"]
    assert patch["plan_route"] == {
        "pre_estimate_usd": 12.0,
        "files": 4,
        "threshold_usd": 55.0,
        "route": "interview",
        "misrouted": False,
        "critic": "skipped",
    }
    assert patch["status"] == "planned"


async def test_a_small_task_runs_sizer_draft_critic_and_one_resumed_revision(critic_cfg):
    runner = ScriptedRunner(
        plan_sizer=[sized(cost=12.0, spent=0.02)],
        tech_lead=[
            planned(session="tl-draft", cost=0.3, estimated_cost_usd=20.0),
            planned(session="tl-final", cost=0.2, estimated_cost_usd=25.0),
        ],
        tech_lead_critic=[critique("important", cost=0.4)],
    )
    ports = memory_ports(runner)
    patch = await plan(task(CRITIC_ARM), critic_cfg, ports)

    assert runner.slugs == ["plan_sizer", "tech_lead", "tech_lead_critic", "tech_lead"]
    assert [r["slug"] for r in patch["runs"]] == [
        "plan_sizer",
        "tech_lead",
        "tech_lead_critic",
        "tech_lead",
    ]
    assert patch["cost_usd"] == pytest.approx(0.02 + 0.3 + 0.4 + 0.2)
    draft_call, critic_call, revision_call = runner.calls[1:]
    assert draft_call["resume"] is None
    assert critic_call["resume"] is None  # fresh context
    assert revision_call["resume"] == "tl-draft"
    assert patch["plan_route"] == {
        "pre_estimate_usd": 12.0,
        "files": 4,
        "threshold_usd": 55.0,
        "route": "critic",
        "misrouted": False,
        "critic": "ran",
    }
    # One document write for the whole node, holding everything.
    saved = ports.documents.saved
    assert len(saved) == 1


async def test_the_critic_sees_the_draft_and_the_revision_sees_the_findings(critic_cfg):
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[planned(session="tl-draft"), planned(session="tl-final")],
        tech_lead_critic=[critique("blocking", invariants=("Every route is pure.",))],
    )
    await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert "### Solution\n\nHex." in runner.calls[2]["system_prompt"]
    revision = runner.calls[3]
    assert "Problem 0." in revision["prompt"]
    assert "Every route is pure." in revision["system_prompt"]
    assert "adopt or reject" in revision["system_prompt"].lower()


async def test_the_revision_runs_once_even_with_blocking_findings_and_its_plan_is_delivered(
    critic_cfg,
):
    draft = planned(session="tl-draft", estimated_cost_usd=20.0, estimated_files=5)
    revision = planned(
        {"id": "ui", "title": "UI", "brief": "Draw it.", "seams": ["page"]},
        session="tl-final",
        estimated_cost_usd=30.0,
        estimated_files=9,
    )
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[draft, revision],
        tech_lead_critic=[critique("blocking", "blocking", "important")],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert runner.slugs.count("tech_lead") == 2
    assert patch["status"] == "planned"
    assert list(patch["subtasks"]) == ["api", "ui"]
    assert patch["estimate"] == {"cost_usd": 30.0, "files": 9}
    assert patch["sections"]["Tech Lead"].count("#### ui") == 1
    assert "#### ui" not in patch["sections"]["Tech Lead (v1)"]
    assert "Plan critic" in patch["sections"]


async def test_a_large_pre_estimate_goes_to_the_interview(critic_cfg):
    runner = ScriptedRunner(
        plan_sizer=[sized(cost=80.0)],
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned()],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert runner.slugs == ["plan_sizer", "tech_lead_ephemeral", "tech_lead"]
    assert patch["plan_route"]["route"] == "interview"
    assert patch["plan_route"]["critic"] == "skipped"
    assert patch["plan_route"]["misrouted"] is False


async def test_a_sizer_that_fails_goes_to_the_interview_and_keeps_its_cost(critic_cfg):
    bad = RawResult({"markdown": "x"}, 0.03, 1, "sz")
    runner = ScriptedRunner(
        plan_sizer=[bad, bad],
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned()],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert runner.slugs == ["plan_sizer", "plan_sizer", "tech_lead_ephemeral", "tech_lead"]
    assert patch["status"] == "planned"
    assert "escalated" not in patch or not patch["escalated"]
    assert patch["plan_route"]["pre_estimate_usd"] is None
    assert patch["plan_route"]["route"] == "interview"
    assert patch["cost_usd"] >= 0.06


async def test_a_draft_over_the_threshold_is_re_gated_to_the_interview(critic_cfg):
    runner = ScriptedRunner(
        plan_sizer=[sized(cost=12.0)],
        tech_lead=[
            planned(session="tl-draft", estimated_cost_usd=70.0),
            planned(session="tl-final", estimated_cost_usd=70.0),
        ],
        tech_lead_ephemeral=[questions(done=True, session="e1")],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert runner.slugs == ["plan_sizer", "tech_lead", "tech_lead_ephemeral", "tech_lead"]
    assert "tech_lead_critic" not in runner.slugs
    assert patch["plan_route"]["misrouted"] is True
    assert patch["plan_route"]["route"] == "interview"
    assert patch["plan_route"]["critic"] == "skipped"
    assert "Tech Lead (v1)" in patch["sections"]
    assert patch["status"] == "planned"
    assert [r["slug"] for r in patch["runs"]].count("tech_lead") == 2


async def test_a_critic_that_fails_delivers_the_draft_and_records_it(critic_cfg):
    bad = RawResult({"markdown": "x"}, 0.1, 1, "cr")
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[planned(session="tl-draft", estimated_cost_usd=20.0)],
        tech_lead_critic=[bad, bad],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert runner.slugs == ["plan_sizer", "tech_lead", "tech_lead_critic", "tech_lead_critic"]
    assert patch["status"] == "planned"
    assert patch["estimate"]["cost_usd"] == 20.0
    assert patch["plan_route"]["critic"] == "failed"
    assert not patch.get("escalated")
    assert patch["cost_usd"] >= 0.02 + 0.3 + 0.2


async def test_a_revision_that_fails_delivers_the_draft(critic_cfg):
    bad = RawResult({"markdown": "x"}, 0.1, 1, "tl-final")
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[planned(session="tl-draft", estimated_cost_usd=20.0), bad, bad],
        tech_lead_critic=[critique("important")],
    )
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert patch["status"] == "planned"
    assert patch["estimate"]["cost_usd"] == 20.0
    assert patch["plan_route"]["critic"] == "ran"
    assert not patch.get("escalated")


async def test_a_critic_stopped_by_the_task_cap_ends_the_node_with_the_escalation(tmp_path):
    cfg = Configuration(product_dir=tmp_path, plan_review="critic", task_max_budget_usd=0.5)
    runner = ScriptedRunner(
        plan_sizer=[sized(spent=0.02)],
        tech_lead=[planned(session="tl-draft", cost=0.6)],
    )
    patch = await plan(task(CRITIC_ARM), cfg, memory_ports(runner))

    assert "tech_lead_critic" not in runner.slugs
    assert patch["escalation_class"] == "cap"
    assert patch.get("status") != "planned"
    assert patch["plan_route"]["route"] == "critic"


async def test_a_draft_that_cannot_be_made_ends_the_node_with_its_escalation(critic_cfg):
    bad = RawResult({"markdown": "x"}, 0.1, 1, "tl")
    runner = ScriptedRunner(plan_sizer=[sized()], tech_lead=[bad, bad])
    patch = await plan(task(CRITIC_ARM), critic_cfg, memory_ports(runner))

    assert "tech_lead_critic" not in runner.slugs
    assert patch["escalation_class"] == "failure"
    assert [r["slug"] for r in patch["runs"]] == ["plan_sizer"]
    assert patch["plan_route"]["route"] == "critic"
    assert patch["plan_route"]["critic"] == "skipped"
