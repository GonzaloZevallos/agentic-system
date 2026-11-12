"""The plan asks for a promotion, it never applies one (GH-591, part 2): `plan.plan` stores the
Tech Lead's `promote` line as `plan_promote_request`, from the author's section, and writes no
`path` and no `promotion`. The serialized system step applies it later and, when the owner label
refuses, `promotion.refusal_notice` puts the one-line notice into the asking role's section."""

import pytest

from agentic.core.process.application import promotion
from agentic.core.process.application.plan import plan
from agentic.core.process.domain import rules, task_path
from agentic.core.process.domain.roles import TECH_LEAD
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports, planned

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "path": "light",
    "initial_path": "light",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


async def _plan(tmp_path, **plan_fields):
    result = planned()
    result.structured_output.update(plan_fields)
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)
    return await plan(TASK, cfg, memory_ports(ScriptedRunner(tech_lead=[result])))


async def test_the_plan_stores_the_request_and_applies_nothing(tmp_path):
    patch = await _plan(tmp_path, promote="the data conversion is bigger than the issue says")
    assert patch["plan_promote_request"] == {
        "kind": "role",
        "from_role": TECH_LEAD.name,
        "text": "the data conversion is bigger than the issue says",
    }
    assert "path" not in patch and "promotion" not in patch


async def test_a_plan_that_asks_nothing_writes_no_key(tmp_path):
    patch = await _plan(tmp_path)
    assert "plan_promote_request" not in patch


def test_the_notice_goes_into_the_plan_section_as_one_line():
    state = {**TASK, "sections": {**TASK["sections"], "Tech Lead": "### Solution\n\nHex."}}
    patch, notice = task_path.promote(
        {**state, "path_label": "light"}, TECH_LEAD.name, "role", "t", "why"
    )
    assert patch == {} and notice
    after = apply(state, promotion.refusal_notice(state, TECH_LEAD.name, notice))
    assert after["sections"]["Tech Lead"] == f"### Solution\n\nHex.\n\n{notice}"
    assert "\n" not in notice


def test_the_notice_goes_into_the_review_section_that_asked():
    section = rules.review_section("api")
    state = {**TASK, "sections": {section: "### Findings\n\nNone."}}
    after = apply(state, promotion.refusal_notice(state, section, "refused: owner label"))
    assert after["sections"][section].endswith("\n\nrefused: owner label")
    assert set(after["sections"]) == {section}


def test_no_notice_changes_nothing_and_a_missing_section_is_created():
    assert promotion.refusal_notice(TASK, "Tech Lead", None) == {}
    after = apply(TASK, promotion.refusal_notice(TASK, "Tech Lead", "refused"))
    assert after["sections"]["Tech Lead"] == "refused"
