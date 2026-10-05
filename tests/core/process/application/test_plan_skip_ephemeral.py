"""The ephemeral-arm skip path (issue #28): `AGENTIC_SKIP_EPHEMERAL` set makes `plan()`
run no solution interview at all -- no interviewer, no interviewee, no consultation, no
transcript -- and runs the Tech Lead cold instead. Unset, `plan()` behaves exactly as
`test_technical_stage.py` already proves; this file only adds the new branch."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.run_role import context_for
from agentic.core.process.domain.plan_view import NOTE_WRITTEN
from agentic.core.process.domain.roles import SOLUTION_INTERVIEW, TECH_LEAD
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, memory_ports, planned, questions

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
TASK_PATH = MemoryWorkspace.ROOT / "GH-12"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path, skip_ephemeral=True)


def test_context_for_tolerates_a_role_reading_a_section_the_state_lacks():
    # `TECH_LEAD.reads` names `SOLUTION_INTERVIEW`; the skip path never writes it, so
    # the context builder must skip it rather than raise -- the acceptance clause's own
    # words, "asserted by a test, never assumed", not left untested for this exact case.
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {"PM": "def"}}
    text = context_for(state, TECH_LEAD)
    assert SOLUTION_INTERVIEW not in text
    assert "## PM\n\ndef" in text


async def test_skip_ephemeral_runs_the_tech_lead_cold_with_no_interview(cfg):
    # No `tech_lead_ephemeral`/`tech_lead_interviewee` queue at all: a call to either
    # would raise `AssertionError` from `ScriptedRunner`, so this also rejects a
    # regression that quietly reintroduces the interview under the skip switch.
    runner = ScriptedRunner(tech_lead=[planned()])
    ports = memory_ports(runner)
    patch = await plan(TASK, cfg, ports)

    assert runner.slugs == ["tech_lead"]
    call = runner.calls[0]
    assert call["cwd"] == TASK_PATH
    assert call["resume"] is None
    assert patch["status"] == "planned"
    assert SOLUTION_INTERVIEW not in patch.get("sections", {})
    # `commit_all` still runs, on the clean checkout: no branch is needed for it.
    assert ports.workspace.commits == [(TASK_PATH, "GH-12: solution interview")]
    # No consultation, escalation or question path: nothing of that shape appears.
    assert "questions_for_human" not in patch and "escalated" not in patch


async def test_skip_ephemeral_passes_a_note_through_proposed_invariants(cfg):
    runner = ScriptedRunner(tech_lead=[planned()])
    patch = await plan(TASK, cfg, memory_ports(runner))
    prompt = runner.calls[0]["system_prompt"]
    assert "No ephemeral interview ran" in prompt
    assert patch["invariants"] == []


async def test_skip_ephemeral_stamps_the_experiment_snapshot(cfg):
    runner = ScriptedRunner(tech_lead=[planned()])
    patch = await plan(TASK, cfg, memory_ports(runner))
    assert patch["experiment"]["skip_ephemeral"] == "True"
    assert list(patch["experiment"]) == ["review_mode", "skip_ephemeral", "reviewer_model"]


async def test_a_plan_with_no_subtasks_heading_is_repaired_not_retried(cfg):
    # issue #152 (GH-55): a Tech Lead plan missing `### Subtasks` no longer fails
    # `schemas.plan_check` -- the plan stage repairs the markdown from the structured
    # `subtasks` instead (`plan_view.with_subtasks_block`), so the first attempt
    # delivers. Built here, not via `fakes.planned`, which always emits the heading.
    no_heading = RawResult(
        {
            "markdown": "### Solution\n\nHex, no `### Subtasks` heading this time.",
            "subtasks": [{"id": "api", "title": "API", "brief": "Build it.", "seams": ["GET /x"]}],
            "invariants": [],
            "integration": "",
        },
        0.2,
        1,
        "tl-bad",
    )
    runner = ScriptedRunner(tech_lead=[no_heading])
    patch = await plan(TASK, cfg, memory_ports(runner))

    assert runner.slugs == ["tech_lead"]  # one attempt, no retry
    assert patch["status"] == "planned"
    assert patch["failures"] == []
    section = patch["sections"]["Tech Lead"]
    assert section.startswith("### Solution\n\nHex, no `### Subtasks` heading this time.")
    assert f"### Subtasks\n\n{NOTE_WRITTEN}\n\n#### API" in section


async def test_skip_ephemeral_unset_still_runs_the_interview(tmp_path):
    # The guard the other way: the default config takes the branch this task did not
    # touch, so the interviewer still runs, unlike the tests above.
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(
        tech_lead_ephemeral=[questions(done=True, session="e1")],
        tech_lead=[planned()],
    )
    patch = await plan(TASK, cfg, memory_ports(runner))
    assert runner.slugs == ["tech_lead_ephemeral", "tech_lead"]
    assert not patch.get("experiment")
