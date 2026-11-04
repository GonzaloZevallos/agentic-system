"""The verifiers of the integrated head through the compiled graph (GH-469), on the fake
ports: those a task needs start together in the one `verify` node, `deliver` waits for all of
them, and a return carries the findings of every verifier that returned."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import rules
from tests.core.process.adapters.langgraph.test_graph import run_graph, script_for, technical
from tests.fakes import (
    consolidation,
    copy_draft,
    copy_finding,
    copy_reviewed,
    delivered,
    design_draft,
    design_findings,
    final_validation,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

BOTH = {"touches_ui": True, "touches_copy": True}
UI = {"touches_ui": True, "touches_copy": False}
COPY = {"touches_ui": False, "touches_copy": True}
TEXTS = "### Texts\n\n- `Export` button: Export report."


@pytest.fixture
def config(tmp_path):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def _script(flags, **verifiers):
    queues = {}
    if flags["touches_ui"]:
        queues |= {
            "ux_designer": [design_draft()],
            "ux_lead": [design_findings("approved")],
            "ui_designer": [design_draft()],
            "ui_lead": [design_findings("approved")],
            "pm_consolidator": [consolidation("approved")],
            "ux_lead_final": [design_findings("approved")],
            "ui_lead_final": [design_findings("approved")],
        }
    if flags["touches_copy"]:
        queues["tech_writer"] = [copy_draft(TEXTS)]
    return script_for(pm=[verdict("approved", flags=flags)], **queues, **verifiers, **technical())


async def test_every_verifier_that_applies_runs_once_on_the_same_head_before_deliver(config):
    script = _script(
        BOTH,
        tech_writer_review=[copy_reviewed([copy_finding(scope="outside")])],
        pm_final=[final_validation("approved")],
    )

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert script.slugs.count("tech_writer_review") == 1
    assert script.slugs.count("pm_final") == 1
    assert rules.verifiers_due(final) == ()
    assert not final.get("pending_close_docs")
    last_review = max(i for i, slug in enumerate(script.slugs) if slug == "tech_lead_review")
    assert last_review < script.slugs.index("tech_writer_review")
    assert last_review < script.slugs.index("pm_final")


async def test_only_the_verifiers_that_apply_run(config):
    script = _script(COPY, tech_writer_review=[copy_reviewed([copy_finding(scope="outside")])])

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert "tech_writer_review" in script.slugs
    assert not {"pm_final", "qa_performance"} & set(script.slugs)


async def test_a_return_carries_the_sibling_findings_and_the_set_judges_the_fixed_head_again(
    config,
):
    script = script_for(
        pm=[verdict("approved", flags=BOTH)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=[
            copy_reviewed([copy_finding(scope="outside")]),
            copy_reviewed(settled=[]),
        ],
        ux_designer=[design_draft()],
        ux_lead=[design_findings("approved")],
        ui_designer=[design_draft()],
        ui_lead=[design_findings("approved")],
        pm_consolidator=[consolidation("approved")],
        ux_lead_final=[design_findings("approved")] * 2,
        ui_lead_final=[design_findings("approved")] * 2,
        pm_final=[
            final_validation("returned", "too narrow", blocking=["still 1037px"]),
            final_validation("approved"),
        ],
        **technical(
            developer=[delivered(), delivered("fixed the width")],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        ),
    )

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    # The copy review was not held back by PM's return: it judged the first head too.
    assert script.slugs.count("tech_writer_review") == 2
    assert script.slugs.count("pm_final") == 2
    first = script.slugs.index("tech_writer_review")
    assert first < script.slugs.index("developer", script.slugs.index("pm_final"))
    assert rules.verifiers_due(final) == ()
