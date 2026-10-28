"""The documentation rules of the PM prompts (GH-399): the Spanish rule and the screenshot
rule, word for word, in the three prompts where PM writes knowledge, and `definitions` in
the output of the final validation. Presence of the text only: no language detection."""

import pytest

from agentic.core.process.domain.roles import PROMPTS_DIR

SPANISH_RULE = (
    "All documentation prose you write (capability files, glossary definitions) is in "
    "Spanish (Rioplatense); code identifiers, routes and field names stay in English, and "
    "data the user loaded is never translated. The `## Jobs` heading stays literal. A "
    "capability touched for the first time is returned whole, in Spanish."
)

SCREENSHOT_RULE = (
    "For a product with a UI, a capability that shows a screen links its image, "
    "`docs/assets/screens/<name>.webp`, only when that image exists, only for screens this "
    "task touched, and by the name the product's screen registry gives it."
)

CARRIERS = ("pm.md", "pm_final.md", "pm_consultant.md")


def text(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def missing(names: tuple[str, ...], sentence: str) -> list[str]:
    """The prompts among `names` that do not carry `sentence` verbatim."""
    return [name for name in names if sentence not in text(name)]


@pytest.mark.parametrize("name", CARRIERS)
def test_the_spanish_rule_is_in_every_pm_prompt(name):
    assert missing((name,), SPANISH_RULE) == []


@pytest.mark.parametrize("name", CARRIERS)
def test_the_screenshot_rule_is_in_every_pm_prompt(name):
    assert missing((name,), SCREENSHOT_RULE) == []


def test_a_prompt_with_a_reworded_rule_is_reported():
    assert missing(CARRIERS, SPANISH_RULE.replace("Rioplatense", "neutral")) == list(CARRIERS)
    assert missing(CARRIERS, SCREENSHOT_RULE.replace("only when", "even when")) == list(CARRIERS)


def test_pm_final_returns_definitions_reconciled_with_the_delivered_diff():
    prompt = text("pm_final.md")
    output = prompt[prompt.index("<Output>") :]
    assert "`definitions`" in output
    assert "git diff <base>..<head>" in prompt
    assert "empty on `returned`" in output.lower() or "always empty on `returned`" in output
