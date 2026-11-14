import re
from pathlib import Path

import pytest

from agentic.core.process.domain import roles
from agentic.core.process.domain.roles import LINE_PLACEHOLDERS, prompt_hash, render
from tests.core.process.application.test_prompt_placeholder_scan import real_prompts

SOURCE = Path("/memory/x.md")


def old_fill(raw: str, **values: object) -> str:
    """The fill `render` had before line placeholders: the oracle."""
    values = {"date": "D", **values}
    return roles._PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), raw)


def new_fill(raw: str, **values: object) -> str:
    return render(raw, SOURCE, **{"date": "D", **values}).text


def test_the_line_placeholders_are_exactly_the_drawing_rule():
    assert LINE_PLACEHOLDERS == frozenset({"drawing_rule"})


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("a\n{{drawing_rule}}\nb\n", "a\nb\n"),
        ("a\n{{drawing_rule}}", "a\n"),
        ("a\r\n{{drawing_rule}}\r\nb\r\n", "a\r\nb\r\n"),
        ("a\n  {{drawing_rule}} \t\nb", "a\nb"),
        ("a\n\n{{drawing_rule}}\n\nb", "a\n\n\nb"),
        ("{{drawing_rule}}\nb", "b"),
        ("a\n{{drawing_rule}}\n{{drawing_rule}}\nb", "a\nb"),
    ],
)
def test_an_empty_value_alone_on_its_line_takes_the_line_and_its_break(raw, text):
    assert new_fill(raw, drawing_rule="") == text


def test_an_empty_value_inside_a_sentence_leaves_the_line():
    assert new_fill("a\nRule: {{drawing_rule}} end\nb", drawing_rule="") == "a\nRule:  end\nb"


def test_a_value_is_filled_where_it_stands():
    assert new_fill("a\n{{drawing_rule}}\nb", drawing_rule="RULE") == "a\nRULE\nb"


def test_the_empty_line_is_the_raw_text_with_that_line_deleted():
    raw = "one\n{{drawing_rule}}\ntwo {{date}}\n"
    assert new_fill(raw, drawing_rule="") == new_fill("one\ntwo {{date}}\n")


def test_the_hash_is_the_raw_text_hash_with_and_without_a_value():
    raw = "a\n{{drawing_rule}}\nb"
    assert render(raw, SOURCE, drawing_rule="").hash == prompt_hash(raw)
    assert render(raw, SOURCE, drawing_rule="RULE").hash == prompt_hash(raw)


def test_a_line_placeholder_nobody_supplies_is_still_a_missing_value():
    with pytest.raises(ValueError, match="drawing_rule"):
        render("a\n{{drawing_rule}}\n", SOURCE)


ORDINARY = ["flags", "risk_areas", "max_turns", "anything"]


@pytest.mark.parametrize("name", ORDINARY)
@pytest.mark.parametrize("value", ["", "v"])
@pytest.mark.parametrize(
    "raw", ["a\n{{N}}\nb\n", "a\r\n{{N}}\r\nb\r\n", "{{N}}", "x {{N}} y\n{{N}}"]
)
def test_an_ordinary_placeholder_fills_exactly_as_the_old_fill_did(name, value, raw):
    raw = raw.replace("{{N}}", "{{" + name + "}}")
    assert new_fill(raw, **{name: value}) == old_fill(raw, **{name: value})


def test_every_real_prompt_renders_as_the_old_fill_did():
    for slug, raw in real_prompts().items():
        names = set(roles._PLACEHOLDER.findall(raw))
        values = {name: f"<{name}>" for name in names - {"date"}}
        assert new_fill(raw, **values) == old_fill(raw, **values), slug
        empty = {name: "" for name in names - {"date"}}
        assert new_fill(raw, **empty) == old_fill(raw, **empty), slug
        # With the supplier's empty value a real prompt is untouched too.
        assert re.search(r"\{\{drawing_rule\}\}", raw) is None, slug
