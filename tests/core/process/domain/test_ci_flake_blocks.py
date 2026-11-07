"""The parser of the `agentic-flakes:` CI log block (GH-464, 1a): records, never a verdict."""

import json

from agentic.core.process.domain.ci import (
    FLAKE_BLOCK_PREFIX,
    FLAKE_ID_MAX_CHARS,
    clean_flake_ids,
    parse_flake_blocks,
)
from agentic.core.process.domain.presubmit import FAILED_IDS_MAX

DECLARED = frozenset({"tests", "lint"})


def line(check="tests", tests=("a",), v=1):
    return f"{FLAKE_BLOCK_PREFIX}{json.dumps({'v': v, 'check': check, 'tests': list(tests)})}"


def parse(jobs, declared=DECLARED):
    return parse_flake_blocks(jobs, declared, "head1", "full_checks")


def test_a_block_becomes_a_record_without_at():
    assert parse({"ci": [line(tests=["a", "b"])]}) == [
        {"check": "tests", "head": "head1", "gate": "full_checks", "tests": ["a", "b"]}
    ]


def test_two_checks_in_one_job_add_up_and_the_last_block_of_a_check_wins():
    jobs = {"ci": [line("tests", ["old"]), line("lint", ["l"]), "noise", line("tests", ["new"])]}
    records = parse(jobs)
    assert {r["check"]: r["tests"] for r in records} == {"tests": ["new"], "lint": ["l"]}


def test_the_same_check_in_two_jobs_is_kept_per_job():
    records = parse({"j1": [line(tests=["a"])], "j2": [line(tests=["b"])]})
    assert sorted(t for r in records for t in r["tests"]) == ["a", "b"]


def test_an_undeclared_check_is_dropped():
    assert parse({"ci": [line("evil", ["a"])]}) == []


def test_ids_are_bounded_and_cleaned():
    bad = ["x" * 201, "a\nb", "a`b", "a<!--b", "a-->b", "a{{b"]
    ok = ["x" * 200, "fine"]
    assert parse({"ci": [line(tests=[*bad, *ok])]})[0]["tests"] == ok


def test_ids_are_capped_at_the_maximum():
    many = [f"t{n}" for n in range(FAILED_IDS_MAX + 20)]
    assert parse({"ci": [line(tests=many)]})[0]["tests"] == many[:FAILED_IDS_MAX]


def test_a_block_with_no_ids_is_a_record_without_tests():
    assert parse({"ci": [line(tests=[])]})[0]["tests"] == []


def test_a_malformed_line_is_dropped_whole_and_leaves_the_earlier_block():
    malformed = [
        f"{FLAKE_BLOCK_PREFIX}{{not json",
        line(v=2),
        f"{FLAKE_BLOCK_PREFIX}" + json.dumps({"v": 1, "check": "tests", "tests": "a"}),
        f"{FLAKE_BLOCK_PREFIX}" + json.dumps({"v": 1, "check": 5, "tests": []}),
        f"{FLAKE_BLOCK_PREFIX}" + json.dumps({"v": 1, "check": "tests", "tests": ["a", 3]}),
        f"{FLAKE_BLOCK_PREFIX}[1]",
    ]
    assert parse({"ci": malformed}) == []
    assert parse({"ci": [line(tests=["keep"]), *malformed]})[0]["tests"] == ["keep"]


def test_a_line_that_merely_quotes_the_prefix_is_not_a_block():
    assert parse({"ci": [f"echo {line()}"]}) == []


def test_clean_flake_ids_keeps_order_drops_duplicates_and_caps():
    assert clean_flake_ids(["b", "a", "b"]) == ["b", "a"]
    assert clean_flake_ids(f"t{n}" for n in range(FAILED_IDS_MAX + 10)) == [
        f"t{n}" for n in range(FAILED_IDS_MAX)
    ]


def test_clean_flake_ids_drops_what_could_break_out_of_a_comment_or_a_marker():
    hostile = ["a`b", "a{{b", "a<!--b", "a-->b", "a\nb", "a\rb", "x" * (FLAKE_ID_MAX_CHARS + 1)]
    assert clean_flake_ids(["ok", *hostile, "x" * FLAKE_ID_MAX_CHARS]) == [
        "ok",
        "x" * FLAKE_ID_MAX_CHARS,
    ]


def test_clean_flake_ids_drops_an_id_with_a_closing_brace_pair():
    assert clean_flake_ids(["ok", "a}}b", "}}"]) == ["ok"]
