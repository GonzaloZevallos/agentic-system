"""Delivery from an approved Spec, end to end (GH-594, part 4b-4): the whole task graph over fake
ports, with the real `DomainSpecValidator` -- a fake validator would not show the route is on.

Only the spec reader is a fake: it answers the approved text at every commit but the task
branch's head, where a test may put another text. The other ports are the in-memory ones."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import composition
from agentic.core.knowledge.adapters.spec_validator import DomainSpecValidator
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.application.ports import SpecUnreadable, Upstream
from agentic.core.process.domain.spec_delivery import (
    body_sha256,
    render_approved_front_matter,
    render_draft_front_matter,
)
from tests.fakes import (
    MemoryWorkspace,
    ScriptedRunner,
    answers,
    classified,
    defined,
    delivered,
    diagnosed,
    memory_ports,
    planned,
    questions,
    reviewed,
    ruled,
    verdict,
)

pytestmark = pytest.mark.anyio

OWNER = "owner"
PATH = "docs/specs/export.md"
UPSTREAM = "a" * 40
HEAD = "b" * 40
BODY = (
    "# Export\n\n## Goal\n\nUsers want CSV.\n\n## Acceptance criteria\n\n- A CSV downloads.\n\n"
    "## Out of scope\n\n- Other formats.\n\n## Decisions\n\n- Use CSV\n  - Reason: simple.\n\n"
    "## Screens\n\nnone\n\n## Risks\n\nSome risk.\n\n## Slices\n\n### csv\n"
    "#### Acceptance criteria\n- A CSV downloads.\n#### Risks\n- none\n\n"
)
SPEC_TASK = {"id": "GH-1", "title": "Export", "description": f"Users want CSV.\n\nSpec: {PATH}\n"}
PLAIN_TASK = {"id": "GH-1", "title": "Export", "description": "Users want CSV."}
DRAFTING = (
    "product_analyst",
    "product_analyst_interviewer",
    "pm",
    "pm_interviewee",
    "ux_designer",
    "ux_lead",
    "ui_designer",
    "ui_lead",
    "tech_writer",
)


def approved_text(body: str = BODY) -> str:
    sha = body_sha256(render_draft_front_matter() + body)
    assert sha is not None
    return render_approved_front_matter(OWNER, "2026-11-12T10:00:00Z", sha) + body


class Reader:
    """The approved spec at every commit but `HEAD`, where it is `at_head` (default: the same)."""

    def __init__(self, at_head: str | None = None) -> None:
        self.at_head = at_head

    def read(self, commit: str, path: str) -> str:
        if path != PATH:
            raise SpecUnreadable("missing")
        if commit == HEAD and self.at_head is not None:
            return self.at_head
        return approved_text()


class Workspace(MemoryWorkspace):
    """The upstream is a full commit id, and the task branch's head is another one."""

    def __init__(self) -> None:
        super().__init__()
        self.upstream_answer = Upstream("origin/main", UPSTREAM, None)

    def base_of(self, branch):
        return UPSTREAM

    def full_head(self, path):
        return HEAD if path in self.moved else UPSTREAM


def technical(**queues):
    defaults = dict(
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    return {**defaults, **queues}


def definition(**queues):
    defaults = dict(
        product_analyst_interviewer=[questions(1), questions(done=True)],
        pm_interviewee=[answers(1)],
        product_analyst=[defined()],
        pm=[verdict("approved")],
    )
    return {**defaults, **queues}


async def run(task, script, reader=None, owner=OWNER, tmp_path=None):
    ports = memory_ports(
        script, workspace=Workspace(), spec_reader=reader, spec_validator=DomainSpecValidator()
    )
    ports.workspace.moved.update({MemoryWorkspace.ROOT / "GH-1", MemoryWorkspace.ROOT / "GH-1.api"})
    app = build_graph(lambda cfg: ports, arm_for=composition._arm_for).compile(
        checkpointer=InMemorySaver()
    )
    configurable = {"product_dir": str(tmp_path), "thread_id": "GH-1", "owner_login": owner}
    return await app.ainvoke(task, config={"configurable": configurable})


async def test_a_feature_with_an_approved_spec_goes_straight_to_the_tech_lead(tmp_path):
    script = ScriptedRunner(pjm=[classified()], **technical())

    final = await run(SPEC_TASK, script, Reader(), tmp_path=tmp_path)

    assert final["spec"]["path"] == PATH and final["spec"]["commit"] == UPSTREAM
    assert final["type"] == "feature"
    assert "tech_lead" in script.slugs
    for role in DRAFTING:
        assert role not in script.slugs, role


async def test_the_same_feature_without_a_spec_line_still_drafts_a_definition(tmp_path):
    script = ScriptedRunner(pjm=[classified()], **definition(), **technical())

    await run(PLAIN_TASK, script, Reader(), tmp_path=tmp_path)

    assert "product_analyst" in script.slugs and "pm" in script.slugs
    assert script.slugs.index("product_analyst") < script.slugs.index("tech_lead")


async def test_a_spec_changed_at_the_head_blocks_the_final_unit_review(tmp_path):
    script = ScriptedRunner(pjm=[classified()], **technical())
    reader = Reader(at_head=approved_text().replace("Users want CSV.", "Users want PDF."))

    final = await run(SPEC_TASK, script, reader, tmp_path=tmp_path)

    rows = [f for f in final["findings"] if f["category"] == "spec-scope"]
    assert [(f["severity"], f["status"]) for f in rows] == [("blocking", "open")]
    assert final["spec_refusal"]["cause"] == "spec_changed"
    assert "tech_lead_review" not in script.slugs  # a head with a changed spec reaches no reviewer


BUG_ROLES = [
    "pjm",
    "product_analyst_bug",
    "pm_bug",
    "tech_lead_ephemeral",
    "tech_lead",
    "developer",
    "tech_lead_review",
]
PLATFORM_ROLES = [
    "pjm",
    "tech_lead_ephemeral_platform",
    "tech_lead_platform",
    "developer",
    "tech_lead_review",
]


def _bug_script():
    return ScriptedRunner(
        pjm=[classified("bug")], product_analyst_bug=[diagnosed()], pm_bug=[ruled()], **technical()
    )


def _platform_script():
    return ScriptedRunner(
        pjm=[classified("platform_enhancement")],
        tech_lead_ephemeral_platform=[questions(done=True)],
        tech_lead_platform=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )


@pytest.mark.parametrize(
    ("make", "roles"), [(_bug_script, BUG_ROLES), (_platform_script, PLATFORM_ROLES)]
)
async def test_a_bug_and_a_platform_enhancement_without_a_spec_line_run_the_same_roles(
    tmp_path, make, roles
):
    script = make()

    await run(PLAIN_TASK, script, Reader(), tmp_path=tmp_path)

    assert script.slugs == roles


async def test_a_platform_enhancement_with_a_valid_spec_runs_only_pjm_and_escalates(tmp_path):
    script = ScriptedRunner(pjm=[classified("platform_enhancement")])

    final = await run(SPEC_TASK, script, Reader(), tmp_path=tmp_path)

    assert script.slugs == ["pjm"]
    assert final["spec_refusal"]["cause"] == "spec_type"
    assert "type" not in final and "path" not in final


async def test_a_spec_line_without_an_owner_login_escalates_before_any_role(tmp_path):
    script = ScriptedRunner()

    final = await run(SPEC_TASK, script, Reader(), owner="", tmp_path=tmp_path)

    assert script.slugs == []
    assert final["escalation_class"] == "classification"
    assert final["spec_refusal"] == {"cause": "spec_unapproved", "detail": ["owner_login"]}
