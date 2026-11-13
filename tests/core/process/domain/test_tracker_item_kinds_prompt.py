"""The Tech Lead prompts' paragraph on tracker items (GH-625 part 3a): today's raw text, so the
move to `{{tracker_item_kinds}}` is checked against the words it replaces.

Seam: `domain.roles.render` on `tech_lead.md` and `tech_lead_platform.md`.
"""

import pytest

from agentic.core.process.domain import roles

# The raw span, from the column-4 start of the item to its last word, as both prompts hold it.
OLD_PARAGRAPH = (
    "`tracker_items`: tracker work is a `human` item, `{kind: human, question, recommendation}`,\n"
    "   and no other kind exists. Its `question` asks the owner to do or approve the work (for\n"
    '   example "Close #12 as superseded?") and its `recommendation` says what to do. Each field\n'
    "   is one line of at most {{tracker_field_max}} characters with no @-mention, and a plan\n"
    "   carries at most {{tracker_items_max}} items."
)
PROMPTS = ("tech_lead", "tech_lead_platform")


def _raw(name: str) -> str:
    return (roles.PROMPTS_DIR / f"{name}.md").read_text()


@pytest.mark.parametrize("name", PROMPTS)
def test_each_tech_lead_prompt_holds_the_tracker_items_paragraph_exactly_once(name):
    assert _raw(name).count(OLD_PARAGRAPH) == 1
