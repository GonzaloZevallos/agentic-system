"""The Tech Writer through the graph (README.md, 4), on the fake ports: a feature that
touches copy has its texts written after the definition and reviewed after the integration;
any other task goes through the nodes it always did."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.copy import (
    NO_WRITING_CLAIM,
    NO_WRITING_LOCATION,
    NO_WRITING_NOTE,
    copy_location_parts,
)
from agentic.core.process.domain.task import INTEGRATION
from tests.core.process.adapters.langgraph.test_graph import (
    DESIGN_SLUGS,
    Fakes,
    run_graph,
    script_for,
    technical,
)
from tests.fakes import (
    consolidation,
    copy_draft,
    copy_finding,
    copy_reviewed,
    delivered,
    design_draft,
    design_findings,
    final_validation,
    planned,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

COPY = {"touches_ui": False, "touches_copy": True}
TEXTS = "### Texts\n\n- `Export` button: Export report."


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def _copy_script(review, **queues):
    return script_for(
        pm=[verdict("approved", flags=COPY)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=review,
        **technical(**queues),
    )


async def test_a_touches_copy_feature_writes_before_the_plan_and_reviews_after_the_approval(
    config, tmp_path
):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    fakes = Fakes()
    fakes.workspace.diff_paths = ("src/x.py",)
    script = _copy_script([copy_reviewed([copy_finding()])])
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == "ready_for_review"
    slugs = script.slugs
    assert slugs.index("pm") < slugs.index("tech_writer") < slugs.index("tech_lead_ephemeral")
    assert slugs.index("tech_lead_review") < slugs.index("tech_writer_review")
    assert slugs.count("tech_writer") == 1 and slugs.count("tech_writer_review") == 1
    # The section is what the role wrote, untouched: the criteria exist, so no note.
    assert final["sections"]["Tech Writer"] == TEXTS
    # The row is the review's, owned by the subtask whose diff touches the file.
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["location"] == "copy:1.1:src/x.py:12"
    assert (row["severity"], row["subtask"], row["version"]) == ("important", "api", 1)
    # The review never records a return, a pair or a change request.
    assert not final.get("changes_requested") and not final.get("change_history")
    # Both roles read the criteria whole; the review is told the base and gets the Tech
    # Writer's section as written.
    for slug in ("tech_writer", "tech_writer_review"):
        assert "Errors say what to do next." in script.prompts(slug)[0]
    assert f"## Tech Writer\n\n{TEXTS}" in script.prompts("tech_writer_review")[0]
    assert "- src/x.py" in script.prompts("tech_writer_review")[0]
    assert "Errors say" not in script.prompts("developer")[0]
    assert "Errors say" not in script.prompts("tech_lead_review")[0]


async def test_a_product_without_writing_criteria_says_so_in_both_nodes(config):
    script = _copy_script([copy_reviewed(settled=[], proposed_standards="# Writing\n\nVoice.")])
    final = await run_graph(script, config)
    assert final["status"] == "ready_for_review"
    assert final["sections"]["Tech Writer"] == f"_{NO_WRITING_NOTE}_\n\n{TEXTS}"
    assert NO_WRITING_CLAIM in script.prompts("tech_writer_review")[0]
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["location"] == NO_WRITING_LOCATION
    assert (row["severity"], row["scope"]) == ("important", "outside")
    assert row["proposed"] == "# Writing\n\nVoice."
    assert final.get("escalated") in (None, False)


async def test_the_review_follows_the_integration_when_there_are_several_subtasks(config, tmp_path):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    fakes = Fakes()
    fakes.workspace.diff_paths = ("src/x.py",)
    script = script_for(
        pm=[verdict("approved", flags=COPY)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=[copy_reviewed([copy_finding()])],
        **technical(
            tech_lead=[planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})],
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain")],
            tech_lead_review__api=[reviewed("approved")],
            tech_lead_review__domain=[reviewed("approved")],
            tech_lead_review=[reviewed("approved")],
        ),
    )
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == "ready_for_review"
    assert INTEGRATION in final["subtasks"]
    last_review = max(i for i, s in enumerate(script.slugs) if s == "tech_lead_review")
    assert script.slugs.index("tech_writer_review") > last_review
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["subtask"] == "api, domain"


async def test_a_feature_with_touches_ui_and_no_touches_copy_goes_through_today_nodes(
    config, tmp_path
):
    """Byte-equality outside `copy_applies`: the same nodes, and the same input for every
    role whether or not the product has writing criteria, which only the two text roles read."""

    def scripted():
        return script_for(
            pm=[verdict("approved", flags={"touches_ui": True, "touches_copy": False})],
            ux_designer=[design_draft()],
            ux_lead=[design_findings("approved")],
            ui_designer=[design_draft()],
            ui_lead=[design_findings("approved")],
            pm_consolidator=[consolidation("approved")],
            ux_lead_final=[design_findings("approved")],
            ui_lead_final=[design_findings("approved")],
            pm_final=[final_validation("approved")],
            **technical(),
        )

    other = tmp_path / "with-criteria"
    other.mkdir()
    (other / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    plain, with_criteria = scripted(), scripted()
    await run_graph(plain, config)
    await run_graph(
        with_criteria, {"configurable": {**config["configurable"], "product_dir": str(other)}}
    )
    assert plain.slugs == with_criteria.slugs
    i = plain.slugs.index("ux_designer")
    assert plain.slugs[i : i + len(DESIGN_SLUGS)] == DESIGN_SLUGS
    assert not {"tech_writer", "tech_writer_review"} & set(plain.slugs)
    assert [c["prompt"] for c in plain.calls] == [c["prompt"] for c in with_criteria.calls]


def test_the_texts_are_the_last_step_of_the_definition_ladder():
    state = {
        "type": "feature",
        "flags": COPY,
        "runs": [{"role": "Product Analyst", "section": "Product Analyst"}],
    }
    assert rules.design_step(state) == "write_copy"
    assert (
        rules.after_write_copy({**state, "runs": [*state["runs"], {"role": "Tech Writer"}]})
        == "plan"
    )
    assert rules.after_write_copy({**state, "escalated": True}) is None
