"""GH-741: the closed `full_checks_reads` record, its reducer and its `delta` branch (pure)."""

from __future__ import annotations

from typing import Any, get_type_hints

import pytest

from agentic.core.process.domain import document, rules
from agentic.core.process.domain.task import (
    INTEGRATION,
    REDUCERS,
    FullChecksJob,
    FullChecksRead,
    JobLogCause,
    add_full_checks_reads,
    apply,
    delta,
)

HEAD = "a" * 40
AT = "2026-11-14T10:00:00+00:00"


def read(version: int, *tests: str, head: str = HEAD) -> FullChecksRead:
    row: FullChecksRead = {
        "version": version,
        "head": head,
        "jobs": [
            {"job": "check", "link": f"https://x/runs/{version}", "bytes": 10, "cause": "read"}
        ],
    }
    if tests:
        row["tests"] = list(tests)
    return row


def versions(rows: list[FullChecksRead]) -> list[int]:
    return [row["version"] for row in rows]


def test_the_reducer_is_registered_under_its_key():
    assert REDUCERS["full_checks_reads"] is add_full_checks_reads


def test_a_new_version_is_appended():
    assert add_full_checks_reads([read(1)], [read(2)]) == [read(1), read(2)]


def test_the_same_version_is_replaced():
    assert add_full_checks_reads([read(1, "aaaaaaaa")], [read(1, "bbbbbbbb")]) == [
        read(1, "bbbbbbbb")
    ]


def test_only_the_four_highest_versions_are_kept_sorted_ascending():
    rows = add_full_checks_reads([read(5), read(2)], [read(4), read(1), read(3)])
    assert versions(rows) == [2, 3, 4, 5]


def test_the_fold_is_idempotent_and_independent_of_list_order():
    rows = [read(3), read(1), read(2)]
    once = add_full_checks_reads([], rows)
    assert add_full_checks_reads(once, rows) == once
    assert add_full_checks_reads(rows, [read(4)]) == add_full_checks_reads([read(4)], rows)


def test_a_read_without_a_set_never_erases_known_hashes():
    kept = add_full_checks_reads([read(1, "aaaaaaaa")], [read(1)])
    assert kept == [read(1, "aaaaaaaa")]
    # ... and one with a set replaces one without.
    assert add_full_checks_reads([read(1)], [read(1, "aaaaaaaa")]) == [read(1, "aaaaaaaa")]


def test_two_rows_with_tests_keep_the_last():
    assert add_full_checks_reads([read(1, "aaaaaaaa")], [read(1, "bbbbbbbb")])[0]["tests"] == [
        "bbbbbbbb"
    ]


@pytest.mark.parametrize("version", [None, "3", True, 2.0])
def test_a_row_without_an_int_version_is_dropped_without_raising(version: Any):
    bad: Any = {"head": HEAD, "jobs": []}
    if version is not None:
        bad["version"] = version
    assert add_full_checks_reads([bad, "junk", read(1)], [bad, read(2)]) == [read(1), read(2)]
    assert add_full_checks_reads("junk", [bad]) == []


def test_a_row_with_odd_tests_does_not_raise_in_the_reducer():
    odd: Any = {**read(1), "tests": "x"}
    assert add_full_checks_reads([odd], [read(1, "aaaaaaaa")]) == [read(1, "aaaaaaaa")]


def around(before: list[FullChecksRead], after: list[FullChecksRead]) -> None:
    patch = delta({"full_checks_reads": before}, {"full_checks_reads": after})
    assert apply({"full_checks_reads": before}, patch) == {"full_checks_reads": after}


def test_delta_round_trips_the_happy_path():
    around([read(1)], [read(1), read(2, "aaaaaaaa")])


def test_delta_round_trips_when_the_window_slides_and_a_kept_version_is_replaced():
    before = [read(v) for v in (1, 2, 3, 4)]
    after = [read(2, "aaaaaaaa"), read(3), read(4), read(5)]
    around(before, add_full_checks_reads(before, after))


def test_delta_round_trips_when_the_replaced_version_slides_out():
    before = [read(v) for v in (1, 2, 3, 4)]
    around(before, add_full_checks_reads(before, [read(1, "aaaaaaaa"), read(5)]))


def test_delta_emits_only_what_is_new_or_changed():
    before = {"full_checks_reads": [read(1), read(2)]}
    after = {"full_checks_reads": [read(1), read(2, "aaaaaaaa"), read(3)]}
    assert delta(before, after) == {"full_checks_reads": [read(2, "aaaaaaaa"), read(3)]}
    assert delta(before, before) == {}


def test_delta_never_raises_on_a_malformed_row():
    junk: Any = {"head": "x"}
    assert delta({"full_checks_reads": [junk]}, {"full_checks_reads": [junk, read(1)]}) == {
        "full_checks_reads": [read(1)]
    }


def test_the_record_survives_a_fold_where_full_checks_and_another_verifier_return_together():
    state: Any = {"subtasks": {INTEGRATION: {"status": "approved"}}, "full_checks_reads": []}
    record = {
        "version": 1,
        "head": HEAD,
        "passed": False,
        "duration_s": 1.0,
        "declared": {"tests": "pytest"},
        "results": [{"name": "tests", "passed": False}],
        "witness": None,
        "ran_on": "ci",
    }
    mine = {
        **rules.full_checks_return(state, record, 3, at=AT),
        "full_checks_reads": [read(1, "aaaaaaaa")],
    }
    other = {
        "subtasks": {
            INTEGRATION: {"status": "returned", "reason": "gap", "returned_by": "qa_performance"}
        }
    }
    combined = rules.combined_return(state, {"full_checks": mine, "qa_performance": other})
    after = apply(state, combined)
    assert after["full_checks_reads"] == [read(1, "aaaaaaaa")]
    assert after["full_checks"]["version"] == 1
    assert after["subtasks"][INTEGRATION]["returned_with"] == ["qa_performance"]


def test_the_record_round_trips_through_the_frontmatter():
    row: FullChecksRead = {
        **read(2, "0a1b2c3d", "ffffffff"),
        "jobs": [
            {"job": "e2e (1/2)", "link": "https://x/runs/2/job/1", "bytes": 120, "cause": "read"},
            {"job": "check", "link": "https://x/runs/2/job/2", "bytes": 0, "cause": "timed_out"},
        ],
    }
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    with_rows = {**state, "full_checks_reads": [read(1), row]}
    parsed = document.parse(document.render(with_rows))
    assert parsed["full_checks_reads"] == [read(1), row]
    assert "full_checks_reads" not in document.parse(document.render(state))
    assert document.render({**state, "full_checks_reads": []}) == document.render(state)


def test_a_job_reuses_the_one_cause_list():
    # The module defers annotations, so the name resolves through `get_type_hints`.
    hints = get_type_hints(FullChecksJob)
    assert hints["cause"] is JobLogCause
    assert set(hints) == {"job", "bytes", "cause", "link"}


# --- GH-790: previous_read, the one chooser of the earlier read ---------------------------------


def _version_of(row: Any) -> Any:
    return row.get("version") if isinstance(row, dict) else None


PREVIOUS_TABLE: list[tuple[str, list[Any], int | None]] = [
    ("empty", [], None),
    ("one version", [read(1, "aaaaaaaa")], None),
    ("same version twice", [read(2, "aaaaaaaa"), read(2, "bbbbbbbb")], None),
    ("two versions", [read(1, "aaaaaaaa"), read(2, "aaaaaaaa")], 1),
    ("unsorted input", [read(3), read(1), read(2)], 2),
    ("newest twice", [read(1), read(3), read(3, "aaaaaaaa")], 1),
    ("not a dict", ["x", None, 3, read(1), read(2)], 1),
    ("version not an int", [{"version": "1"}, {"version": True}, {"version": 2.0}, read(3)], None),
    ("malformed beside valid", [{"head": HEAD}, read(1), read(2, "aaaaaaaa")], 1),
    ("v2 lacks tests, v1 overlaps v3", [read(1, "aaaaaaaa"), read(2), read(3, "aaaaaaaa")], 2),
]


@pytest.mark.parametrize(
    ("label", "rows", "expected"), PREVIOUS_TABLE, ids=[p[0] for p in PREVIOUS_TABLE]
)
def test_previous_read_is_the_highest_version_below_the_newest(
    label: str, rows: list[Any], expected: int | None
):
    found = rules.previous_read(rows)
    assert _version_of(found) == expected
    if rules.repeated_ci_failure(rows) == "repeat":
        assert found is not None


def test_three_reads_where_the_middle_lacks_tests_are_unknown_with_no_repeat():
    rows = [read(1, "aaaaaaaa"), read(2), read(3, "aaaaaaaa")]
    assert rules.repeated_ci_failure(rows) == "unknown"
    previous = rules.previous_read(rows)
    assert previous is not None and previous["version"] == 2


def test_overlapping_reads_repeat_and_previous_read_names_the_earlier_one():
    rows = [read(1, "aaaaaaaa", "bbbbbbbb"), read(2, "bbbbbbbb")]
    assert rules.repeated_ci_failure(rows) == "repeat"
    previous = rules.previous_read(rows)
    assert previous is not None and previous["version"] == 1
