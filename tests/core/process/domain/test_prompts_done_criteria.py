"""The prompts that carry the done-criteria rules (GH-371) render, and say what the
system's own findings do not leave to the model alone."""

import re

import pytest

from agentic.core.process.domain.roles import PROMPTS_DIR, render

PLAN = ("tech_lead.md", "tech_lead_platform.md")
EPHEMERAL = ("tech_lead_ephemeral.md", "tech_lead_ephemeral_platform.md")
REVIEW = ("tech_lead_review.md", "tech_lead_review_light.md")


def rendered(name: str) -> str:
    path = PROMPTS_DIR / name
    raw = path.read_text()
    values = {key: "x" for key in re.findall(r"\{\{(\w+)\}\}", raw)}
    return render(raw, path, **values).text


@pytest.mark.parametrize("name", PLAN)
def test_the_plan_prompt_asks_for_the_checks_and_paths_the_issue_requires(name):
    text = rendered(name)
    for term in ("ci_required", "ci_none_reason", "required_paths", ".github/workflows/"):
        assert term in text
    assert "depend on tracker work" in text


@pytest.mark.parametrize("name", EPHEMERAL)
def test_the_ephemeral_prompt_checks_the_plan_against_the_done_criteria(name):
    text = rendered(name)
    assert all(term in text for term in ("ci_required", "ci_none_reason", "required_paths"))


@pytest.mark.parametrize("name", REVIEW)
def test_the_review_prompt_makes_an_unmet_done_criterion_blocking(name):
    text = " ".join(rendered(name).split())
    assert "done criterion of the issue that the diff does not meet is `blocking`" in text
    assert "never an `observation`" in text
