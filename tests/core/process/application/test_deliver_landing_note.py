"""`deliver` writes the `Verified at landing` block into the pull request body (GH-441)."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.domain import rules
from agentic.core.process.domain.pull_request import PR_BODY_MAX, github_length
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, memory_ports

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


def _finding(*checks, **more):
    return {
        "subtask": "api",
        "version": 1,
        "location": "a.py:1",
        "claim": "needs the PR's CI",
        "severity": "observation",
        "scope": "subtask",
        "category": "correctness",
        "status": "open",
        "landing_checks": list(checks),
        **more,
    }


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _ports(ci=("web", "e2e")):
    return memory_ports(stack=MemoryStack(ci=ci))


async def test_the_body_carries_the_block_with_gate_names_verbatim(cfg):
    ports = _ports()
    state = {**TASK, "findings": [_finding("web", "coverage vs base")]}

    await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert body.startswith("### Verified at landing")
    assert "- verified at landing: `web`" in body
    assert "1 requested checks are not declared in the product's CI" in body
    assert "coverage vs base" not in body
    assert "## PM" in body


async def test_the_plan_required_names_join_the_gate(cfg):
    ports = _ports(ci=("web",))
    state = {**TASK, "ci_required": ["e2e"], "findings": [_finding("e2e")]}

    await deliver(state, cfg, ports)

    assert "- verified at landing: `e2e`" in ports.tracker.pull_requests[0][2]


async def test_the_block_comes_after_the_other_prefaces(cfg):
    ports = _ports()
    ports.workspace.diff_paths = ("CODING_STANDARDS.md",)

    await deliver({**TASK, "findings": [_finding("web")]}, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert body.index("## Guarded changes") < body.index("### Verified at landing")


async def test_a_body_over_the_limit_keeps_the_block_whole_and_trims_the_sections(cfg):
    ports = _ports()
    big = {"PM": "p" * 70_000}
    state = {**TASK, "sections": big, "findings": [_finding("web", "e2e", "other")]}

    await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    note = rules.landing_notes(state["findings"], ["web", "e2e"])
    assert body.startswith(note)
    assert github_length(body) <= PR_BODY_MAX
    assert "## Task document" in body


async def test_without_a_record_asking_the_body_is_what_it_was(cfg):
    plain = _ports()
    await deliver(TASK, cfg, plain)
    asked_nothing = _ports()
    state = {**TASK, "findings": [_finding(), _finding(status="dismissed")]}
    for record in state["findings"]:
        record.pop("landing_checks")
    await deliver(state, cfg, asked_nothing)

    assert asked_nothing.tracker.pull_requests[0][2] == plain.tracker.pull_requests[0][2]
    assert (
        plain.tracker.pull_requests[0][2] == "## PM\n\n### Consolidated definition\n\nSix numbers."
    )


async def test_a_refusing_auto_merge_leaves_the_block_in_the_body(cfg):
    facts = rules.MergeFacts(
        task_id="GH-12",
        status="ready_for_review",
        reviewed_unit_approved=False,
        reviewed_head=None,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=None,
        in_eval_set=None,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=None,
    )
    assert rules.auto_merge_decision(facts).kind == "refuse"
    ports = _ports()

    await deliver({**TASK, "findings": [_finding("web")]}, cfg, ports)

    assert "- verified at landing: `web`" in ports.tracker.pull_requests[0][2]
