"""The pure rules of the format-refusal issue (GH-1011): marker, find, due, value_len,
earlier_rules, evidence, and the `format_issue` record's reducer and single read."""

from dataclasses import dataclass
from typing import Any

import pytest

from agentic.core.process.domain import format_issue as fi
from agentic.core.process.domain.task import OutputRefusal, add_format_issues, format_issue_for


@dataclass(frozen=True)
class Ref:
    url: str
    body: str


def failure(reason: str, rule: str | None = None, **extra: Any) -> Any:
    row = {"role": "r", "attempt": 1, "error": "boom", "cost_usd": 0.0, "session_id": "s"}
    row["reason"] = reason
    if rule is not None:
        row["rule"] = rule
    return {**row, **extra}


def test_marker_and_matches_round_trip():
    assert fi.marker("schema_invalid") == "<!-- agentic-refusal: schema_invalid -->"
    body = f"text\n\n{fi.marker('schema_invalid')}"
    assert fi.matches(body, "schema_invalid")


def test_a_marker_quoted_mid_body_does_not_match():
    body = f"quoting {fi.marker('unmapped')}\nand then more text"
    assert not fi.matches(body, "unmapped")
    assert not fi.matches("", "unmapped")


@pytest.mark.parametrize("tail", ["  ", "\r\n", "\n\n", " \r\n"])
def test_trailing_whitespace_still_matches(tail):
    assert fi.matches(f"x\n{fi.marker('unmapped')}{tail}", "unmapped")


def test_one_rule_never_matches_another_that_it_prefixes():
    assert not fi.matches(fi.marker("output_missing"), "unmapped")
    assert not fi.matches("<!-- agentic-refusal: schema -->", "schema_invalid")
    assert not fi.matches("<!-- agentic-refusal: schema_invalid_x -->", "schema_invalid")


def test_marker_raises_on_an_unknown_rule():
    with pytest.raises(ValueError, match="unknown refusal rule"):
        fi.marker("schema")


def test_find_picks_the_lowest_number():
    body = fi.marker("unmapped")
    refs = [
        Ref("https://github.com/o/r/issues/30", body),
        Ref("https://github.com/o/r/issues/7/", body),
        Ref("https://github.com/o/r/issues/12", fi.marker("output_missing")),
        Ref("https://github.com/o/r/issues/9", body),
    ]
    found = fi.find(refs, "unmapped")
    assert found is refs[1]


def test_find_skips_a_url_without_a_numeric_tail_and_returns_none_when_nothing_matches():
    body = fi.marker("unmapped")
    refs = [Ref("https://github.com/o/r/issues/abc", body), Ref("https://x/5", "no marker")]
    assert fi.find(refs, "unmapped") is None
    assert fi.find([], "unmapped") is None


@pytest.mark.parametrize("reason", ["disk_full", "max_turns", "stall", "render", "limit"])
def test_due_is_false_for_other_reasons_even_with_a_rule(reason):
    assert not fi.due([failure(reason, "unmapped")])


def test_due_needs_a_rule_and_reads_the_last_failure():
    assert not fi.due([])
    assert not fi.due([failure("schema")])
    assert fi.due([failure("schema", "schema_invalid")])
    assert fi.due([failure("stall"), failure("check", "part_over_budget")])
    assert not fi.due([failure("schema", "unmapped"), failure("stall")])


@pytest.mark.parametrize(
    ("output", "field", "expected"),
    [
        ({"a": "héllo"}, "a", 5),
        ({"a": ""}, "a", 0),
        ({"a": [1, 2, 3]}, "a", 3),
        ({"a": {"x": 1, "y": 2}}, "a", 2),
        ({"a": 12345}, "a", None),
        ({"a": True}, "a", None),
        ({"a": 1.5}, "a", None),
        ({"a": None}, "a", None),
        ({"a": "x"}, "b", None),
        ({"a": "x"}, "", None),
        ("not a dict", "a", None),
        (None, "a", None),
    ],
)
def test_value_len_table(output, field, expected):
    assert fi.value_len(output, field) == expected


def test_earlier_rules_of_a_mixed_epoch():
    epoch = [
        failure("stall"),
        failure("schema", "schema_invalid"),
        failure("schema", "schema_invalid"),
        failure("schema", "unmapped"),
    ]
    assert fi.earlier_rules(epoch) == ["schema_invalid", "schema_invalid"]
    assert fi.earlier_rules([]) == []


def test_evidence_uses_closed_fields_only():
    # One sentinel in the refused value, one in each earlier failure's `error` and one in a
    # refusal's `detail` (carried as the row's `error`, as `run_role` records it), sent through
    # the real path `earlier_rules(epoch)` -> `evidence`.
    value_sentinel, error_sentinel, detail_sentinel = "SENT-VALUE", "SENT-ERROR", "SENT-DETAIL"
    refusal = OutputRefusal("schema_invalid", "markdown", f"bad field: {detail_sentinel}")
    epoch = [
        failure("schema", "schema_invalid", error=f"first: {error_sentinel}"),
        failure("check", "unmapped", error=str(refusal)),
        failure("schema", "schema_invalid", error=f"last: {error_sentinel}"),
    ]
    output = {"markdown": value_sentinel * 2}
    title, body = fi.evidence(
        refusal.rule,
        refusal.field,
        "Developer",
        "acme",
        41,
        fi.value_len(output, refusal.field),
        len(epoch),
        fi.earlier_rules(epoch),
    )
    for sentinel in (value_sentinel, error_sentinel, detail_sentinel, "bad field"):
        assert sentinel not in title + body
    assert "value length: 20" in body
    assert "earlier rules in this epoch: schema_invalid, unmapped" in body
    assert "#41" in body and "acme" in body and "Developer" in body


def test_evidence_shows_an_unknown_length_and_ends_with_a_matching_marker():
    _, body = fi.evidence("unmapped", "", "Tech Lead", "acme", 5, None, 2, ["schema_invalid"])
    assert "value length: unknown" in body
    assert "schema_invalid" in body
    assert body.splitlines()[-1] == fi.marker("unmapped")
    assert fi.matches(body, "unmapped")
    assert body.count("Closing this issue releases the held tasks once the fix runs") == 1


def record(rule, url="https://x/1"):
    return {"rule": rule, "url": url, "at": "2026-11-20T00:00:00+00:00"}


def test_add_format_issues_keeps_the_first_per_rule_in_order():
    first = [record("unmapped", "https://x/1"), record("output_missing", "https://x/2")]
    merged = add_format_issues(first, [record("unmapped", "https://x/9"), record("schema_invalid")])
    assert [r["rule"] for r in merged] == ["unmapped", "output_missing", "schema_invalid"]
    assert merged[0]["url"] == "https://x/1"


def test_add_format_issues_merges_two_simultaneous_writers():
    a, b = [record("unmapped", "https://x/1")], [record("output_missing", "https://x/2")]
    merged = add_format_issues(add_format_issues([], a), b)
    assert {r["rule"]: r["url"] for r in merged} == {
        "unmapped": "https://x/1",
        "output_missing": "https://x/2",
    }
    assert add_format_issues(merged, merged) == merged


def test_format_issue_for_returns_the_record_or_none():
    entries = [record("unmapped", "https://x/1"), record("output_missing", "https://x/2")]
    found = format_issue_for(entries, "output_missing")
    assert found is not None and found["url"] == "https://x/2"
    assert format_issue_for(entries, "schema_invalid") is None
    assert format_issue_for([], "unmapped") is None
