"""The shadow use case (GH-473) at `run_shadows`/`run_one` through the integration's `review`
node: the one attempt, the single-unit and cap skips, the launch order, the checkout removed on
failure and the subtask that already has a record."""

import anyio
import pytest

from agentic.core.process.application import review as review_module
from agentic.core.process.application import shadow_review
from agentic.core.process.application.review import review
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-28",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


def sub(id_, status="approved", **more):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        "branch": f"agentic/GH-28.{id_}",
        "base": "base1",
        **more,
    }


def integration_state(**more) -> dict:
    return {
        **TASK,
        "subtask_id": INTEGRATION,
        "subtasks": {
            "api": sub("api", reviewed_head="h-api"),
            "domain": sub("domain", reviewed_head="h-domain"),
            "ui": sub("ui", reviewed_head="h-ui"),
            INTEGRATION: sub(INTEGRATION, "delivered", branch="agentic/GH-28", base="b"),
        },
        "sections": {
            **TASK["sections"],
            **{f"Developer: {n}": "x" for n in ("api", "domain", "ui", INTEGRATION)},
        },
        **more,
    }


def cfg(**fields) -> Configuration:
    return Configuration(
        product_dir=MemoryWorkspace.ROOT, review_mode="single", shadow_parallel=1, **fields
    )


class Sized(MemoryWorkspace):
    """`changed_lines` by the head asked, so the launch order has something to order by."""

    LINES = {"h-api": 30, "h-domain": 5, "h-ui": 5}

    def changed_lines(self, base: str, head: str) -> int:
        return self.LINES[head]


def shadow_keys(runner: ScriptedRunner) -> list[str]:
    return [n for n in runner.ran("tech_lead_review") if n.startswith("shadow-")]


def everything(runner=None, **queues):
    return runner or ScriptedRunner(tech_lead_review=[reviewed("approved")] * 4, **queues)


async def test_only_the_shadow_runs_with_no_retry(monkeypatch):
    retries: dict[str, int] = {}

    async def fake_run_role(state, role, config, ports, **kwargs):
        retries[kwargs["cwd"].name] = config.role_retries
        return None, {"current_role": role.name}

    monkeypatch.setattr(review_module, "run_role", fake_run_role)
    await review(integration_state(), cfg(role_retries=1), memory_ports(ScriptedRunner()))
    assert retries.pop("GH-28") == 1  # the integration review keeps its retry
    assert set(retries.values()) == {0}
    assert len(retries) == 3


async def test_shadows_start_smallest_diff_first_with_the_id_as_tie_break():
    runner = everything()
    ports = memory_ports(runner, workspace=Sized())
    patch = await review(integration_state(), cfg(), ports)
    assert shadow_keys(runner) == ["shadow-domain", "shadow-ui", "shadow-api"]
    assert [r["subtask"] for r in patch["shadow_reviews"]] == ["api", "domain", "ui"]
    assert {r["status"] for r in patch["shadow_reviews"]} == {"ran"}
    assert {r["changed_lines"] for r in patch["shadow_reviews"]} == {30, 5}
    assert patch["sections"]["Tech Lead: shadow review of api"]
    assert patch["cost_usd"] == pytest.approx(0.2 * 4)


async def test_a_subtask_with_a_record_is_not_relaunched():
    held = {
        "subtask": "api",
        "head": "h-api",
        "status": "ran",
        "cause": None,
        "findings": [],
        "cost_usd": 0.5,
        "changed_lines": 30,
    }
    runner = everything()
    patch = await review(
        integration_state(shadow_reviews=[held]), cfg(), memory_ports(runner, workspace=Sized())
    )
    assert "shadow-api" not in shadow_keys(runner)
    assert sorted(r["subtask"] for r in patch["shadow_reviews"]) == ["domain", "ui"]


async def test_a_second_pass_runs_no_shadow():
    state = integration_state()
    state["subtasks"][INTEGRATION]["reviewed_head"] = "earlier"
    runner = everything()
    patch = await review(state, cfg(), memory_ports(runner, workspace=Sized()))
    assert shadow_keys(runner) == [] and "shadow_reviews" not in patch


async def test_a_lone_subtask_records_skipped_single_unit_and_launches_nothing():
    state = {
        **integration_state(),
        "subtasks": {"api": sub("api", "delivered")},
        "subtask_id": "api",
    }
    runner = everything()
    patch = await review(state, cfg(), memory_ports(runner, workspace=Sized()))
    [record] = patch["shadow_reviews"]
    assert (record["status"], record["cause"]) == ("skipped", "single_unit")
    assert shadow_keys(runner) == []


@pytest.mark.parametrize("fields", [{"max_concurrent_roles": 1}, {"task_max_budget_usd": 10.0}])
async def test_no_slots_or_a_task_cap_the_shadows_could_reach_skips_them_all(fields):
    runner = everything()
    patch = await review(integration_state(), cfg(**fields), memory_ports(runner))
    assert {(r["status"], r["cause"]) for r in patch["shadow_reviews"]} == {("skipped", "cap")}
    assert shadow_keys(runner) == [] and "cost_usd" in patch


async def test_a_shadow_that_raises_is_unavailable_and_its_checkout_is_removed():
    workspace = Sized()
    runner = ScriptedRunner(
        tech_lead_review=[reviewed("approved")],
        **{"tech_lead_review__shadow-api": [RuntimeError("x")]},
    )
    runner.queues["tech_lead_review__shadow-domain"] = [reviewed("returned")]
    runner.queues["tech_lead_review__shadow-ui"] = [reviewed("approved")]
    patch = await review(integration_state(), cfg(), memory_ports(runner, workspace=workspace))
    by = {r["subtask"]: r for r in patch["shadow_reviews"]}
    assert (by["api"]["status"], by["api"]["cause"]) == ("unavailable", "failure")
    assert by["domain"]["status"] == "ran" and by["domain"]["findings"]
    assert sorted(workspace.removed_checkouts) == [
        f"GH-28.shadow-{n}" for n in ("api", "domain", "ui")
    ]
    assert patch["subtasks"][INTEGRATION]["status"] == "approved"
    assert "escalated" not in patch and patch["failures"] == []


async def test_a_checkout_git_cannot_make_is_unavailable_no_checkout():
    workspace = Sized()
    workspace.checkout_error = "no such sha"
    runner = everything()
    patch = await review(integration_state(), cfg(), memory_ports(runner, workspace=workspace))
    assert {(r["status"], r["cause"]) for r in patch["shadow_reviews"]} == {
        ("unavailable", "no_checkout")
    }
    assert shadow_keys(runner) == []


async def test_the_cap_a_shadow_meets_is_unavailable_cap_with_nothing_escalated(monkeypatch):
    # The shadow's role stops on the cap: recorded, never an escalation of the task.
    async def fake_run_role(state, role, config, ports, **kwargs):
        if kwargs["cwd"].name == "GH-28":
            return None, {"current_role": role.name}
        return None, {"escalated": "cap", "escalation_class": "cap"}

    monkeypatch.setattr(review_module, "run_role", fake_run_role)
    outcome = await shadow_review.run_shadows(
        integration_state(), cfg(), memory_ports(ScriptedRunner(), workspace=Sized()), _noop
    )
    assert {(r["status"], r["cause"]) for r in outcome[1].records} == {("unavailable", "cap")}
    assert "escalated" not in outcome[1].patch()


async def _noop():
    return None


async def test_a_shadow_past_the_grace_is_late_with_the_cost_seen_and_no_findings(monkeypatch):
    state = integration_state()
    ports = memory_ports(everything(), workspace=Sized())

    async def main():
        return "single review"

    stuck = anyio.Event()

    async def never(*args, **kwargs):
        await stuck.wait()

    monkeypatch.setattr(review_module, "run_reviewer", never)
    result, outcome = await shadow_review.run_shadows(state, cfg(shadow_grace_s=0), ports, main)
    assert result == "single review"
    assert {(r["status"], r["cause"]) for r in outcome.records} == {("late", "late")}
    assert all(r["findings"] == [] for r in outcome.records)
    # One slot: the smallest diff was running, the other two never started and made no checkout.
    assert ports.workspace.removed_checkouts == ["GH-28.shadow-domain"]
