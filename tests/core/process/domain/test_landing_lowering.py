"""`rules.lower_landing_blocks` (GH-441): a finding that asks for landing evidence through
the structured `landing_checks` field is an `observation`, whatever it said it was, and
`Review` validation carries the field. Seams: the rule, pure, and `schemas.validate`."""

import pytest

from agentic.core.process.domain import rules, schemas

OUTPUT = {"markdown": "m", "reason": "r", "settled": []}


def found(severity="blocking", scope="subtask", checks=("web",), at="x.py:1"):
    item = {
        "location": at,
        "claim": "c",
        "evidence": "$ pytest\nF",
        "severity": severity,
        "scope": scope,
        "category": "spec-behaviour",
    }
    if checks is not None:
        item["landing_checks"] = list(checks)
    return item


@pytest.mark.parametrize(
    ("severity", "scope"),
    [
        ("blocking", "subtask"),
        ("blocking", "plan"),
        ("important", "subtask"),
        ("observation", "plan"),
    ],
)
def test_a_finding_with_landing_checks_becomes_an_observation(severity, scope):
    original = found(severity, scope)
    (lowered,), lines = rules.lower_landing_blocks([original])
    assert lowered["severity"] == "observation"
    assert lowered["reported_severity"] == severity
    assert lines == [f"`x.py:1` {severity} lowered to observation: verified at landing"]
    assert original["severity"] == severity and "reported_severity" not in original


def test_a_finding_without_landing_checks_is_untouched_and_the_rule_is_idempotent():
    plain = found(checks=None)
    empty = found(checks=())
    assert rules.lower_landing_blocks([plain, empty]) == ([plain, empty], [])
    once, lines = rules.lower_landing_blocks([found(), plain])
    assert rules.lower_landing_blocks(once)[0] == once
    assert once[1] is plain and len(lines) == 1


def test_the_rule_reads_only_severity_and_landing_checks():
    # The decision never reads the prose: a claim that begs for a human to open the PR is
    # still a block when the field is empty, and a harmless one is lowered when it is not.
    prose = found(checks=None)
    prose["claim"] = "a human must open the PR so CI runs"
    assert rules.lower_landing_blocks([prose]) == ([prose], [])
    (lowered,), _ = rules.lower_landing_blocks([{**found(), "claim": "", "evidence": ""}])
    assert lowered["severity"] == "observation"


def test_the_records_carry_the_key_only_when_it_is_not_empty():
    (kept,), _ = rules.lower_landing_blocks([found(checks=("web", "e2e"))])
    [record] = rules.finding_records("api", 1, [kept])
    assert record["landing_checks"] == ["web", "e2e"]
    assert record["severity"] == "observation" and record["reported_severity"] == "blocking"
    [plain] = rules.finding_records("api", 1, [found(checks=None)])
    assert "landing_checks" not in plain
    [empty] = rules.finding_records("api", 1, [found(checks=())])
    assert "landing_checks" not in empty


def test_a_review_validates_with_and_without_the_field_and_rejects_a_bad_one():
    with_field = {**OUTPUT, "findings": [found()]}
    assert schemas.validate(schemas.Review, with_field, {})["findings"][0]["landing_checks"] == [
        "web"
    ]
    without = {**OUTPUT, "findings": [found(checks=None)]}
    assert schemas.validate(schemas.Review, without, {})["findings"][0]["landing_checks"] == []
    for bad in ("web", [1], None):
        with pytest.raises(ValueError):
            schemas.validate(
                schemas.Review, {**OUTPUT, "findings": [{**found(), "landing_checks": bad}]}, {}
            )
