"""An estimate below a floor the system already knew is raised, not refused (GH-529): the
four apostrophe tasks of 2026-11-07 at the `plan.plan` seam, with a fake Tech Lead output and
a fake history of closed tasks. Each one used to fail the plan check twice and escalate as
`failure`; now one Tech Lead attempt plans the task."""

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
LINE = "The system raised the estimate from"


def closed(task_id, cost, subtasks=1):
    return {
        "id": task_id,
        "title": task_id,
        "description": "d",
        "closed_at": "2026-10-01",
        "cost_usd": cost,
        "subtasks": {f"s{i}": {"id": f"s{i}"} for i in range(subtasks)},
    }


def cold(tmp_path):
    return Configuration(product_dir=tmp_path, skip_ephemeral=True)


def lines(patch):
    return [text for text in patch["sections"].values() if LINE in text]


# (task, spent, declared, closed costs, raised figure, cause)
INCIDENTS = [
    ("486", 10.01, 6.0, [], 16.01, "below what the task already spent 10.01"),
    ("583", 9.91, 3.0, [], 12.91, "below what the task already spent 9.91"),
    ("584", 3.61, 3.0, [], 6.61, "below what the task already spent 3.61"),
    ("596", 0.0, 7.5, [17.1] * 5, 17.1, "below half the median 17.10 of closed tasks"),
]


@pytest.mark.parametrize(
    ("number", "spent", "declared", "history", "raised", "cause"),
    INCIDENTS,
    ids=[row[0] for row in INCIDENTS],
)
async def test_an_incident_is_planned_in_one_attempt_with_the_raised_estimate(
    number, spent, declared, history, raised, cause, tmp_path
):
    runner = ScriptedRunner(tech_lead=[planned(estimated_cost_usd=declared)])
    ports = memory_ports(runner)
    ports.documents.saved.extend(closed(f"OLD-{i}", cost) for i, cost in enumerate(history))
    patch = await plan({**TASK, "cost_usd": spent}, cold(tmp_path), ports)

    assert runner.slugs.count("tech_lead") == 1
    assert "escalation_class" not in patch
    assert not patch.get("failures")
    assert patch["status"] == "planned"
    assert patch["estimate"]["cost_usd"] == pytest.approx(raised)
    assert patch["estimate"]["declared_usd"] == declared
    (section,) = lines(patch)
    assert f"{LINE} {declared:.2f} to {raised:.2f} USD: {cause}" in section


async def test_the_interview_arm_raises_the_same_way(tmp_path):
    runner = ScriptedRunner(
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned(estimated_cost_usd=3.0)],
    )
    patch = await plan(
        {**TASK, "cost_usd": 9.91}, Configuration(product_dir=tmp_path), memory_ports(runner)
    )

    assert runner.slugs.count("tech_lead") == 1
    assert "escalation_class" not in patch
    assert patch["estimate"]["declared_usd"] == 3.0
    assert patch["estimate"]["cost_usd"] >= 9.91 + 3.0
    assert len(lines(patch)) == 1


async def test_a_raised_estimate_covers_what_the_node_itself_spent(tmp_path):
    # Spent 3.00, declared 2.00 -> raised 5.00; the attempt costs 8.00, so the estimate in
    # force is lifted to the 11.00 the task has spent after the node.
    runner = ScriptedRunner(tech_lead=[planned(estimated_cost_usd=2.0, cost=8.0)])
    state = {**TASK, "cost_usd": 3.0, "landing_cost_carried": 1.0}
    patch = await plan(state, cold(tmp_path), memory_ports(runner))

    after = state["cost_usd"] + patch["cost_usd"] + state["landing_cost_carried"]
    assert patch["estimate"]["cost_usd"] >= after
    assert patch["estimate"]["cost_usd"] == pytest.approx(12.0)
    assert patch["estimate"]["declared_usd"] == 2.0


async def test_a_plan_that_clears_both_floors_leaves_the_patch_as_it_was(tmp_path):
    ports = memory_ports(ScriptedRunner(tech_lead=[planned(estimated_cost_usd=20.0, cost=8.0)]))
    ports.documents.saved.extend(closed(f"OLD-{i}", 30.0) for i in range(5))
    patch = await plan({**TASK, "cost_usd": 5.0}, cold(tmp_path), ports)

    assert patch["estimate"] == {"cost_usd": 20.0, "files": 5}
    assert lines(patch) == []


async def test_a_raise_across_the_line_is_refused_as_not_fitting_and_retried(tmp_path):
    # Spent 140 + declared 20 = 160 does not fit; the retry reads the raised figure and cause.
    runner = ScriptedRunner(
        tech_lead=[planned(estimated_cost_usd=20.0), planned(estimated_cost_usd=170.0)]
    )
    patch = await plan({**TASK, "cost_usd": 140.0}, cold(tmp_path), memory_ports(runner))

    error = patch["failures"][0]["error"]
    assert "raised by the system from 20.00 to 160.00 USD" in error
    assert "below what the task already spent 140.00" in error
    assert "does not fit one task" in error


def sized(cost: float) -> RawResult:
    return RawResult(
        {"markdown": "Small.", "estimated_cost_usd": cost, "estimated_files": 4}, 0.02, 1, "sz"
    )


def critique() -> RawResult:
    return RawResult(
        {"markdown": "### Findings\n\nNone.", "findings": [], "invariants_proposed": []},
        0.4,
        3,
        "cr",
    )


async def test_the_critic_arms_regate_reads_the_raised_figure(tmp_path):
    # The draft says 20 (under the 55 threshold: critic), but the task spent 50, so the
    # estimate in force is 70 and the draft was misrouted to the interview.
    runner = ScriptedRunner(
        plan_sizer=[sized(12.0)],
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned(estimated_cost_usd=20.0), planned(estimated_cost_usd=100.0)],
    )
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    state = {
        **TASK,
        "cost_usd": 50.0,
        "assignment": {
            "experiment": "e1",
            "arm": "critic",
            "switches": {"plan_review": "critic"},
        },
    }
    patch = await plan(state, cfg, memory_ports(runner))

    assert "tech_lead_critic" not in runner.slugs
    assert patch["plan_route"]["misrouted"] is True
    assert "escalation_class" not in patch


async def test_the_critic_arm_stores_the_raised_estimate_and_its_line(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized(12.0)],
        tech_lead_critic=[critique()],
        tech_lead=[planned(estimated_cost_usd=2.0), planned(estimated_cost_usd=2.0)],
    )
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    state = {
        **TASK,
        "cost_usd": 10.0,
        "assignment": {
            "experiment": "e1",
            "arm": "critic",
            "switches": {"plan_review": "critic"},
        },
    }
    patch = await plan(state, cfg, memory_ports(runner))

    assert "escalation_class" not in patch
    assert patch["estimate"]["declared_usd"] == 2.0
    assert patch["estimate"]["cost_usd"] >= 12.0
    assert len(lines(patch)) == 1
