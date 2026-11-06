"""The shadow keys of the task document (GH-473): written and read back when there are
records, absent otherwise so every document under another `review_mode` is byte-identical."""

from agentic.core.process.domain import document
from agentic.core.process.domain.task import ShadowRecord, add_shadow_reviews, apply, delta


def base():
    return {"id": "GH-1", "title": "t", "description": "d", "sections": {}}


def record(subtask, status="ran", cause=None, findings=(), cost=1.23456, lines=40):
    return ShadowRecord(
        subtask=subtask,
        head="abc123",
        status=status,
        cause=cause,
        findings=list(findings),
        cost_usd=cost,
        changed_lines=lines,
    )


def test_records_and_the_wait_round_trip():
    finding = {"source": "Tech Lead", "text": "x", "location": "a.py:3"}
    rows = [
        record("s1", findings=[finding]),
        record("s2", "late", "late", cost=0.5, lines=None),
        record("s3", "skipped", "single_unit", cost=0.0),
    ]
    text = document.render({**base(), "shadow_reviews": rows, "shadow_wait_s": 12.5})
    parsed = document.parse(text)
    assert parsed["shadow_wait_s"] == 12.5
    assert parsed["shadow_reviews"][0]["findings"] == [finding]
    assert [r["status"] for r in parsed["shadow_reviews"]] == ["ran", "late", "skipped"]
    assert [r["cause"] for r in parsed["shadow_reviews"]] == [None, "late", "single_unit"]
    assert parsed["shadow_reviews"][0]["cost_usd"] == 1.2346
    assert parsed["shadow_reviews"][1]["changed_lines"] is None


def test_a_zero_wait_is_recorded():
    parsed = document.parse(document.render({**base(), "shadow_wait_s": 0.0}))
    assert parsed["shadow_wait_s"] == 0.0


def test_a_state_without_them_renders_as_it_always_did():
    plain = document.render(base())
    assert "shadow" not in plain
    assert document.render({**base(), "shadow_reviews": []}) == plain


def test_the_reducer_keeps_one_row_per_subtask_the_first_seen():
    first, again = record("s1", cost=1.0), record("s1", "unavailable", "failure", cost=9.0)
    assert add_shadow_reviews([first], [again, record("s2")]) == [first, record("s2")]


def test_a_patch_applies_and_the_delta_carries_only_new_rows():
    before = {**base(), "shadow_reviews": [record("s1")]}
    after = apply(before, {"shadow_reviews": [record("s1"), record("s2")]})
    assert [r["subtask"] for r in after["shadow_reviews"]] == ["s1", "s2"]
    assert [r["subtask"] for r in delta(before, after)["shadow_reviews"]] == ["s2"]
