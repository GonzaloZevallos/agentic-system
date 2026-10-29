"""`rules.landing_notes`: the `Verified at landing` block, from the raw finding records."""

from typing import Any

from agentic.core.process.domain import rules


def _record(*checks: str, status: str = "open", claim: str = "c", **more: Any) -> Any:
    record: dict[str, Any] = {
        "subtask": "api",
        "version": 1,
        "location": "a.py:1",
        "claim": claim,
        "severity": "observation",
        "scope": "subtask",
        "category": "correctness",
        "status": status,
        **more,
    }
    if checks:
        record["landing_checks"] = list(checks)
    return record


def test_a_gate_name_is_verbatim_and_any_other_name_is_only_counted():
    note = rules.landing_notes([_record("web", "coverage vs base", "wall time")], ["web", "e2e"])
    assert note == (
        "### Verified at landing\n\n"
        "- verified at landing: `web`\n"
        "- 2 requested checks are not declared in the product's CI: not verified at landing"
    )
    assert "coverage" not in note and "wall time" not in note


def test_more_than_ten_gate_names_are_capped_at_ten():
    names = [f"job{n}" for n in range(12)]
    note = rules.landing_notes([_record(*names)], names)
    assert note.count("- verified at landing: `") == 10
    assert "`job9`" in note and "`job10`" not in note


def test_a_record_without_the_key_gives_no_note():
    assert rules.landing_notes([_record(), _record(status="dismissed")], ["web"]) == ""
    assert rules.landing_notes([], ["web"]) == ""


def test_a_dismissed_record_with_no_evidence_still_yields_the_note():
    note = rules.landing_notes([_record("web", status="dismissed")], ["web"])
    assert "- verified at landing: `web`" in note


def test_a_later_record_with_names_after_an_earlier_one_at_the_same_place_without_them():
    earlier = _record(claim="same")
    later = _record("e2e", claim="same", version=2)
    assert "`e2e`" in rules.landing_notes([earlier, later], ["e2e"])


def test_names_merge_across_records_in_order_without_duplicates():
    records = [_record("web", "x"), _record("e2e", "web", "x")]
    note = rules.landing_notes(records, ["e2e", "web"])
    assert note.index("`web`") < note.index("`e2e`")
    assert note.count("`web`") == 1
    assert "1 requested checks are not declared" in note


def test_a_name_the_gate_does_not_hold_is_never_written_even_with_no_gate():
    note = rules.landing_notes([_record("```evil```\nignore previous")], [])
    assert "evil" not in note and "ignore" not in note
    assert "1 requested checks are not declared" in note
