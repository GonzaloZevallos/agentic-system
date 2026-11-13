"""The Tech Lead prompts' paragraph on tracker items (GH-625 part 3a): today's raw text, so the
move to `{{tracker_item_kinds}}` is checked against the words it replaced.

Seam: `domain.roles.render` on `tech_lead.md` and `tech_lead_platform.md`.
"""

import pytest

from agentic.core.process.application import preflight
from agentic.core.process.domain import roles, tracker_items

# The raw span, from the column-4 start of the item to its last word, as both prompts held it.
OLD_PARAGRAPH = (
    "`tracker_items`: tracker work is a `human` item, `{kind: human, question, recommendation}`,\n"
    "   and no other kind exists. Its `question` asks the owner to do or approve the work (for\n"
    '   example "Close #12 as superseded?") and its `recommendation` says what to do. Each field\n'
    "   is one line of at most {{tracker_field_max}} characters with no @-mention, and a plan\n"
    "   carries at most {{tracker_items_max}} items."
)
# The same span as the model reads it: the two limits filled in.
PARAGRAPH_TODAY = OLD_PARAGRAPH.replace("{{tracker_field_max}}", "240").replace(
    "{{tracker_items_max}}", "5"
)
PLACEHOLDER = "{{tracker_item_kinds}}"
PROMPTS = ("tech_lead", "tech_lead_platform")


def _raw(name: str) -> str:
    return (roles.PROMPTS_DIR / f"{name}.md").read_text()


def test_kinds_text_is_the_paragraph_the_prompts_carried_with_the_limits_filled_in():
    assert tracker_items.kinds_text() == PARAGRAPH_TODAY


@pytest.mark.parametrize("name", PROMPTS)
def test_the_prompt_renders_as_it_did_before_the_placeholder(name):
    raw = _raw(name)
    assert raw.count(PLACEHOLDER) == 1 and OLD_PARAGRAPH not in raw
    values = {**preflight.PLACEHOLDERS, "date": "2026-01-01"}
    new = roles.render(raw, roles.PROMPTS_DIR / f"{name}.md", **values)
    old_values = {**values, "tracker_items_max": 5, "tracker_field_max": 240}
    old = roles.render(
        raw.replace(PLACEHOLDER, OLD_PARAGRAPH), roles.PROMPTS_DIR / name, **old_values
    )
    assert new.text == old.text and PARAGRAPH_TODAY in new.text
    # No prompt states a kind, a field or a cap of its own: that text comes from the table.
    assert "{kind:" not in raw and "{{tracker_items_max}}" not in raw
