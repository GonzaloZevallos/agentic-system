"""The flake key, the threshold rule and the issue lookup (GH-464, 1a)."""

from datetime import UTC, datetime, timedelta

from agentic.core.process.domain.landing import (
    IssueRef,
    flake_issue_for,
    flake_key,
    flake_marker,
    keys_over_threshold,
)
from agentic.core.process.domain.task import add_flakes

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def rec(check="tests", tests=(), at=None, head="h1", gate="merge"):
    record = {"check": check, "head": head, "gate": gate, "tests": list(tests)}
    if at is not None:
        record["at"] = at.isoformat()
    return record


def ago(days):
    return NOW - timedelta(days=days)


def test_flake_key_is_one_per_test():
    assert flake_key(rec(tests=["a", "b"])) == [("tests", "a"), ("tests", "b")]


def test_flake_key_without_tests_is_the_check_alone():
    assert flake_key(rec(tests=[])) == [("tests", "")]


def test_three_tasks_with_the_same_id_are_over():
    by_task = {t: [rec(tests=["a"], at=ago(1))] for t in ("T1", "T2", "T3")}
    assert keys_over_threshold(by_task, NOW, 3, 14) == {("tests", "a"): ["T1", "T2", "T3"]}


def test_three_tasks_with_three_ids_have_none_over():
    by_task = {f"T{n}": [rec(tests=[f"t{n}"], at=ago(1))] for n in range(3)}
    assert keys_over_threshold(by_task, NOW, 3, 14) == {}


def test_a_check_without_ids_counts_apart_from_its_tests():
    by_task = {
        "T1": [rec(tests=[], at=ago(1))],
        "T2": [rec(tests=["a"], at=ago(1))],
        "T3": [rec(tests=[], at=ago(1))],
    }
    assert keys_over_threshold(by_task, NOW, 3, 14) == {}
    by_task["T4"] = [rec(tests=[], at=ago(2))]
    assert keys_over_threshold(by_task, NOW, 3, 14) == {("tests", ""): ["T1", "T3", "T4"]}


def test_a_record_without_at_does_not_count():
    by_task = {"T1": [rec(tests=["a"])], "T2": [rec(tests=["a"], at=ago(1))]}
    assert keys_over_threshold(by_task, NOW, 2, 14) == {}


def test_an_unparseable_at_does_not_count():
    broken = {**rec(tests=["a"]), "at": "yesterday"}
    by_task = {"T1": [broken], "T2": [rec(tests=["a"], at=ago(1))]}
    assert keys_over_threshold(by_task, NOW, 2, 14) == {}


def test_the_same_task_counts_once_per_key():
    by_task = {
        "T1": [rec(tests=["a"], at=ago(1)), rec(tests=["a"], at=ago(2), head="h2")],
        "T2": [rec(tests=["a"], at=ago(1))],
    }
    assert keys_over_threshold(by_task, NOW, 3, 14) == {}
    assert keys_over_threshold(by_task, NOW, 2, 14) == {("tests", "a"): ["T1", "T2"]}


def test_the_window_edge_is_inclusive_and_beyond_it_is_out():
    inside = {t: [rec(tests=["a"], at=ago(14))] for t in ("T1", "T2")}
    assert keys_over_threshold(inside, NOW, 2, 14) != {}
    outside = {t: [rec(tests=["a"], at=ago(14) - timedelta(seconds=1))] for t in ("T1", "T2")}
    assert keys_over_threshold(outside, NOW, 2, 14) == {}


def test_a_naive_at_is_read_as_utc():
    naive = {**rec(tests=["a"]), "at": "2026-11-03T12:00:00"}
    assert keys_over_threshold({"T1": [naive], "T2": [naive]}, NOW, 2, 14) != {}


def issue(url="u", title="x", body=""):
    return IssueRef(url, title, body)


def test_flake_issue_is_found_by_marker():
    found = issue("u2", body=f"{flake_marker('tests')}\nbody")
    assert flake_issue_for([issue("u1"), found], "tests") is found


def test_flake_issue_is_found_by_exact_title():
    found = issue("u2", title="Flaky check: tests")
    assert flake_issue_for([issue("u1"), found], "tests") is found


def test_flake_issue_is_not_found_by_a_loose_title_or_another_check():
    issues = [
        issue(title="Flaky check: tests-slow"),
        issue(title="flaky check: tests"),
        issue(body=flake_marker("lint")),
    ]
    assert flake_issue_for(issues, "tests") is None


def test_add_flakes_keeps_at_and_stays_commutative():
    early, late = rec(at=ago(3)), rec(at=ago(1))
    merged = add_flakes([late], [early])
    assert merged == add_flakes([early], [late])
    assert merged[0]["at"] == ago(3).isoformat()
    assert add_flakes([rec()], [early])[0]["at"] == ago(3).isoformat()


def test_the_threshold_defaults_are_three_tasks_in_fourteen_days(tmp_path):
    from agentic.shared.configuration import Configuration

    config = Configuration(product_dir=tmp_path)
    assert (config.flake_issue_tasks, config.flake_issue_days) == (3, 14)
