"""The test-pyramid rule (GH-365): one sentence, word for word, in the four prompts whose
role writes or judges tests. It is one constant here so that editing the wording in one
prompt and not the others fails this file, and it names no tool."""

import pytest

from agentic.core.process.domain.roles import PROMPTS_DIR

SEAMS_SENTENCE = (
    "Test at the seams the plan agrees for the subtask; a new test file outside them, or "
    "at a slower seam than the plan names for that behaviour, needs one line in the "
    "delivery's markdown saying why the agreed seam cannot show it."
)

CARRIERS = (
    "developer.md",
    "developer_integrator.md",
    "tech_lead_review.md",
    "tech_lead_review_light.md",
)

TOOLS = ("browser", "e2e", "vitest", "playwright", "pytest")


def flat(name: str) -> str:
    """The prompt's text as written: the sentence sits on one line, so no wrap is folded."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def missing_sentence(names: tuple[str, ...], sentence: str) -> list[str]:
    """The prompts among `names` that do not carry `sentence` verbatim."""
    return [name for name in names if sentence not in flat(name)]


@pytest.mark.parametrize("name", CARRIERS)
def test_the_seams_sentence_is_in_every_carrier_verbatim(name):
    assert missing_sentence((name,), SEAMS_SENTENCE) == []


def test_the_sentence_names_no_tool():
    assert [tool for tool in TOOLS if tool in SEAMS_SENTENCE.lower()] == []


def test_a_prompt_with_a_reworded_sentence_is_reported():
    reworded = SEAMS_SENTENCE.replace("agrees", "agreed")
    assert missing_sentence(CARRIERS, reworded) == list(CARRIERS)


def test_the_plan_prompts_ask_each_subtask_to_name_its_test_seams():
    for name in ("tech_lead.md", "tech_lead_platform.md"):
        assert "names the test seams" in " ".join(flat(name).split()), name
