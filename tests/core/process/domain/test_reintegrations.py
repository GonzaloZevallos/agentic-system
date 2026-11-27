"""GH-994: the reintegration provenance record (`ReintegrationRecord`), its reducer
`add_reintegrations`, the pure row builder `landing.reintegration_row` and the document
round trip. Nothing in production writes the rows yet."""

from __future__ import annotations

import pytest

from agentic.core.process.domain.document import parse, render
from agentic.core.process.domain.landing import reintegration_row
from agentic.core.process.domain.task import (
    REDUCERS,
    ReintegrationRecord,
    add_reintegrations,
    delta,
)

UP = "b" * 40
SHA = "c" * 40
AT = "2026-11-27T10:00:00Z"
BASE = {"id": "GH-1", "title": "t", "description": "d"}


def _row(mode="mechanical", at: str = AT, upstream: str = UP, **more) -> ReintegrationRecord:
    return reintegration_row(
        at=at, upstream=upstream, mode=mode, cause="clean", attempt_s=1.5, resumed=False, **more
    )


# ---- reintegration_row ------------------------------------------------------------------


def test_a_mechanical_row_cannot_be_resumed():
    with pytest.raises(ValueError, match="mechanical"):
        reintegration_row(
            at=AT, upstream=UP, mode="mechanical", cause="clean", attempt_s=1.0, resumed=True
        )


def test_a_session_row_can_be_resumed():
    row = reintegration_row(
        at=AT, upstream=UP, mode="session", cause="regenerated", attempt_s=2.0, resumed=True
    )
    assert row["resumed"] is True
    assert row["mode"] == "session"


def test_the_optional_keys_are_omitted_when_none_and_kept_when_given():
    bare = _row()
    assert "generate_s" not in bare
    assert "regeneration" not in bare

    full = _row("session", generate_s=0.0, regeneration=SHA)
    assert full["generate_s"] == 0.0  # zero is a measurement, not "absent"
    assert full["regeneration"] == SHA


# ---- add_reintegrations -----------------------------------------------------------------


def test_the_key_is_a_registered_reducer():
    assert REDUCERS["reintegrations"] is add_reintegrations


def test_a_repeated_row_adds_nothing_and_the_first_seen_is_kept_whole():
    first = _row(regeneration=SHA)
    again = _row()  # same key, no regeneration
    joined = add_reintegrations([first], [again])
    assert joined == [first]
    assert add_reintegrations(joined, joined) == joined


def test_mechanical_and_session_rows_for_the_same_upstream_both_stay():
    mechanical, session = _row("mechanical"), _row("session")
    assert add_reintegrations([mechanical], [session]) == [mechanical, session]


def test_arrival_order_is_kept():
    later, earlier = _row(at="2026-11-27T11:00:00Z"), _row(at="2026-11-27T09:00:00Z")
    assert add_reintegrations([later], [earlier]) == [later, earlier]


def test_no_row_is_dropped_however_many_there_are():
    rows = [_row(at=f"t{n:04d}", regeneration=SHA) for n in range(200)]
    assert add_reintegrations([], rows) == rows


# ---- the patch --------------------------------------------------------------------------


def test_the_patch_emits_the_session_row_that_shares_at_and_upstream_with_a_held_mechanical():
    mechanical, session = _row("mechanical"), _row("session")
    before = {**BASE, "reintegrations": [mechanical]}
    after = {**BASE, "reintegrations": [mechanical, session]}

    assert delta(before, after)["reintegrations"] == [session]


def test_the_patch_has_no_key_when_the_held_row_is_repeated():
    mechanical = _row("mechanical")
    before = {**BASE, "reintegrations": [mechanical]}
    after = {**BASE, "reintegrations": [mechanical]}

    assert "reintegrations" not in delta(before, after)


# ---- the document -----------------------------------------------------------------------


def test_the_rows_survive_render_and_parse_as_the_same_list():
    rows = [_row("mechanical"), _row("session", generate_s=3.25, regeneration=SHA)]
    assert parse(render({**BASE, "reintegrations": rows}))["reintegrations"] == rows


def test_a_state_without_rows_renders_as_before():
    assert render({**BASE, "reintegrations": []}) == render(BASE)
    assert "reintegrations" not in render(BASE)
