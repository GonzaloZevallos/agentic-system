import pytest

from agentic.core.process.domain.task import (
    REDUCERS,
    add_assumptions,
    apply,
    assumption_text,
    delta,
)


def row(text="Show the empty state", role="Developer"):
    return {"role": role, "text": text}


def test_assumption_text_accepts_one_to_240_characters():
    assert assumption_text("a") == "a"
    assert assumption_text("a" * 240) == "a" * 240


@pytest.mark.parametrize("raw", ["", "   ", "a" * 241, "two\nlines", "carriage\rreturn", "x\r\ny"])
def test_assumption_text_refuses_what_the_rule_refuses(raw):
    assert assumption_text(raw) is None


def test_assumption_text_collapses_whitespace_before_measuring():
    assert assumption_text("  a \t  b   c ") == "a b c"
    assert assumption_text("a" + " " * 50 + "b" * 100) == "a " + "b" * 100


def test_assumptions_is_reduced_by_add_assumptions():
    assert REDUCERS["assumptions"] is add_assumptions


def test_the_reducer_appends_rows_with_their_canonical_text():
    out = add_assumptions([row("one")], [row("  two   words ", "Tech Lead")])
    assert out == [row("one"), row("two words", "Tech Lead")]


@pytest.mark.parametrize("bad", ["a\nb", "a" * 241, "   "])
def test_the_reducer_refuses_a_row_the_rule_refuses(bad):
    with pytest.raises(ValueError, match="assumption"):
        add_assumptions([], [row(bad)])


def test_the_reducer_dedupes_by_canonical_text_keeping_the_first_row():
    out = add_assumptions([row("same", "Developer")], [row(" same ", "Tech Lead"), row("other")])
    assert out == [row("same", "Developer"), row("other")]


def test_the_reducer_dedupes_inside_one_side():
    assert add_assumptions([], [row("x"), row("x", "Tech Lead")]) == [row("x")]


def test_apply_and_delta_round_trip_with_assumptions():
    before = {"assumptions": [row("one")]}
    after = apply(before, {"assumptions": [row("two")]})
    assert after["assumptions"] == [row("one"), row("two")]
    assert delta(before, after) == {"assumptions": [row("two")]}
    assert apply(before, delta(before, after)) == after


def test_delta_sends_nothing_when_assumptions_did_not_change():
    state = {"assumptions": [row("one")]}
    assert delta(state, dict(state)) == {}


def test_delta_takes_assumptions_as_they_are_when_the_branch_started_without_them():
    assert delta({}, {"assumptions": [row("one")]}) == {"assumptions": [row("one")]}


def test_spec_cause_is_last_wins_and_delta_sends_a_change():
    assert REDUCERS["spec_cause"](None, "conflict") == "conflict"
    before = {"spec_cause": None}
    assert delta(before, {"spec_cause": "data_loss"}) == {"spec_cause": "data_loss"}
    assert delta(before, {"spec_cause": None}) == {}
