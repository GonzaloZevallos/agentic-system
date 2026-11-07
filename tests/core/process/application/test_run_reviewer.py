"""`review()` and `run_reviewer` under the `single` arm (GH-473): `full` passes `run_role`
exactly what it always passed, `single` approves a branch with no role, leaves the Developer's
section out of the integration review, and returns the integration twice before it escalates
with `returns` on the third blocking verdict. The pair counter is the existing one."""

import pytest

from agentic.core.process.application import review as review_module
from agentic.core.process.application.review import review
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import pair_for
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, apply
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


def sub(id_, status="delivered", **more):
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


def two_delivered(subtask_id="api") -> dict:
    return {
        **TASK,
        "subtask_id": subtask_id,
        "subtasks": {"api": sub("api"), "domain": sub("domain")},
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: domain": "y"},
    }


def integration_state(**more) -> dict:
    return {
        **TASK,
        "subtask_id": INTEGRATION,
        "subtasks": {INTEGRATION: sub(INTEGRATION, branch="agentic/GH-28", base="b")},
        "sections": {**TASK["sections"], "Developer: integration": "z"},
        **more,
    }


def cfg(**fields) -> Configuration:
    return Configuration(product_dir=MemoryWorkspace.ROOT, **fields)


def spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_run_role(state, role, config, ports, **kwargs):
        calls.append({"role": role, **kwargs})
        return None, {"current_role": role.name}

    monkeypatch.setattr(review_module, "run_role", fake_run_role)
    return calls


async def test_review_under_full_passes_run_role_exactly_what_it_always_did(monkeypatch):
    calls = spy(monkeypatch)
    await review(two_delivered(), cfg(), memory_ports(ScriptedRunner()))
    [call] = calls
    assert call.pop("role").slug == "tech_lead_review"
    assert call.pop("cwd") is not None
    assert call == {
        "section": "Tech Lead: review api",
        "extra_sections": ("Developer: api", "Tech Lead: review api"),
        "subtask": "api",
        "base": "base1",
        "review": "a first review",
        "remaining_changes": 2,
        "invariants": rules.invariants_markdown(two_delivered()),
        "required_paths": "",
        "checks": call["checks"],
        "docs_review": call["docs_review"],
        "landing_gate": rules.NO_LANDING_GATE,
    }


async def test_the_integration_review_under_single_leaves_the_developer_section_out(monkeypatch):
    calls = spy(monkeypatch)
    await review(integration_state(), cfg(review_mode="single"), memory_ports(ScriptedRunner()))
    [call] = calls
    assert call["extra_sections"] == ("Tech Lead: review integration",)
    assert call["role"].slug == "tech_lead_review"


async def test_a_branch_under_single_is_approved_by_the_system_with_no_role(monkeypatch):
    calls = spy(monkeypatch)
    runner = ScriptedRunner()
    patch = await review(two_delivered(), cfg(review_mode="single"), memory_ports(runner))
    assert calls == [] and runner.slugs == []
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["subtasks"]["api"]["reviewed_head"]
    assert patch["sections"] == {
        "Tech Lead: review api": "approved by the system: review_mode single"
    }
    assert patch["experiment"]["review_mode"] == "single"
    assert "findings" not in patch and "ci_speculation" not in patch and "runs" not in patch


async def test_a_lone_subtask_under_single_is_still_reviewed(monkeypatch):
    calls = spy(monkeypatch)
    lone = {**two_delivered(), "subtasks": {"api": sub("api")}}
    await review(lone, cfg(review_mode="single"), memory_ports(ScriptedRunner()))
    assert len(calls) == 1


async def test_blocking_verdicts_under_single_use_the_integrations_pair_and_the_third_escalates():
    config = cfg(review_mode="single")
    pair = pair_for(INTEGRATION)
    state = integration_state()
    for round_ in (1, 2):
        runner = ScriptedRunner(tech_lead_review=[reviewed("returned")])
        patch = await review(state, config, memory_ports(runner))
        assert patch["subtasks"][INTEGRATION]["status"] == "returned"
        assert "escalated" not in patch
        state = apply(state, patch)
        assert state["changes_requested"] == {pair.key: round_}
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned")])
    patch = await review(state, config, memory_ports(runner))
    assert patch["escalation_class"] == "returns"
    assert patch["status"] == WAITING_ON_HUMAN
    assert patch["escalated"].startswith(f"{pair.key} hit 2 returns: ")
