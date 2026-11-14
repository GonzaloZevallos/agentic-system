"""GH-741: the pure rules behind the CI log excerpt of a red full tier: which failing tests a run
recorded (`recorded_tests`), whether a failure repeats, the one sentence that says so and when a
read is due. No I/O."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, JobLog, JobLogCause
from agentic.core.process.domain.task import INTEGRATION, FullChecksRead, apply

FIXTURE = Path(__file__).parents[3] / "fixtures" / "ci_logs" / "e2e_shard_red.log"
HEAD = "a" * 40
OTHER = "b" * 40


def sha8(item: str) -> str:
    return hashlib.sha256(item.encode()).hexdigest()[:8]


def sorted_hashes(ids: tuple[str, ...]) -> list[str]:
    return [sha8(i) for i in sorted(ids)]


def pytest_text(*failed: str) -> str:
    lines = ["=== test session starts ===", "=== short test summary info ==="]
    lines += [f"FAILED {item} - boom" for item in failed]
    return "\n".join([*lines, f"=== {len(failed)} failed ==="])


def job(
    name: str,
    text: str,
    *,
    cause: JobLogCause = "read",
    truncated: int = 0,
    size: int | None = None,
) -> JobLog:
    return JobLog(
        name,
        f"https://x/{name}",
        text,
        len(text.encode()) if size is None else size,
        cause,
        truncated,
    )


def check(name: str) -> CiCheck:
    return CiCheck(
        name, "COMPLETED", "FAILURE", f"https://github.com/o/r/actions/runs/1/job/{name}"
    )


def candidates(*names: str) -> list[CiCheck]:
    return [check(name) for name in names]


DECLARED = ("check",)


def shard(n: int, *failed: str, **more: Any) -> JobLog:
    return job(f"shard {n}", pytest_text(*failed), **more)


# --- test_hashes ------------------------------------------------------------------------------


def test_hashes_are_the_first_eight_hex_of_the_sha256_of_the_sorted_ids():
    assert rules.test_hashes({"b::t", "a::t"}) == [sha8("a::t"), sha8("b::t")]
    assert rules.test_hashes([]) == []


def test_hashes_keep_the_first_sixteen_sorted_ids():
    ids = [f"t{n:02d}" for n in range(20)]
    assert rules.test_hashes(reversed(ids)) == [sha8(i) for i in ids[:16]]


# --- recorded_tests ---------------------------------------------------------------------------


def test_the_aggregate_and_two_shards_give_the_shards_hashes():
    logs = [shard(1, "a::x"), shard(2, "b::y"), job("check", pytest_text("a::x", "b::y"))]
    found = rules.recorded_tests(logs, candidates("shard 1", "shard 2", "check"), DECLARED)
    assert found == [sha8("a::x"), sha8("b::y")]


def test_a_playwright_log_from_the_fixture_gives_hashes():
    text = "\n".join(FIXTURE.read_text(encoding="utf-8").splitlines()[:7])
    log = job("e2e (3/6)", text)
    found = rules.recorded_tests([log], candidates("e2e (3/6)"), DECLARED)
    assert found is not None and len(found) == 2


def test_the_whole_fixture_is_more_than_sixteen_ids_and_gives_none():
    log = job("e2e (3/6)", FIXTURE.read_text(encoding="utf-8"))
    assert ci.failing_set(log) is not None and len(ci.failing_set(log) or ()) == 80
    assert rules.recorded_tests([log], candidates("e2e (3/6)"), DECLARED) is None


def test_seventeen_ids_give_none_and_sixteen_give_hashes():
    ids = [f"t.py::t{n:02d}" for n in range(17)]
    assert rules.recorded_tests([shard(1, *ids)], candidates("shard 1"), DECLARED) is None
    assert rules.recorded_tests([shard(1, *ids[:16])], candidates("shard 1"), DECLARED) == (
        sorted_hashes(tuple(ids[:16]))
    )


def test_a_union_across_shards_over_sixteen_gives_none():
    first = [f"a.py::t{n:02d}" for n in range(9)]
    second = [f"b.py::t{n:02d}" for n in range(8)]
    logs = [shard(1, *first), shard(2, *second)]
    assert rules.recorded_tests(logs, candidates("shard 1", "shard 2"), DECLARED) is None


def test_five_shards_with_four_logs_give_none():
    logs = [shard(n, f"t.py::t{n}") for n in range(1, 5)]
    names = [f"shard {n}" for n in range(1, 6)] + ["check"]
    assert rules.recorded_tests(logs, candidates(*names), DECLARED) is None


def test_the_aggregate_alone_gives_none_because_a_declared_job_counts_only_with_a_set():
    assert rules.recorded_tests([], candidates("check"), DECLARED) is None
    assert rules.recorded_tests([job("check", "")], candidates("check"), DECLARED) is None


def test_a_single_declared_check_job_with_failed_lines_gives_hashes():
    logs = [job("check", pytest_text("t.py::a", "t.py::b"))]
    assert rules.recorded_tests(logs, candidates("check"), DECLARED) == (
        sorted_hashes(("t.py::a", "t.py::b"))
    )


@pytest.mark.parametrize(
    "bad",
    [
        job("shard 2", "", cause="unreadable"),
        job("shard 2", "", cause="timed_out"),
        job("shard 2", "", cause="other_head"),
        job("shard 2", "", cause="empty"),
        job("shard 2", pytest_text("t.py::cut"), truncated=500),
        job("shard 2", "some log with no header at all"),
    ],
)
def test_an_unread_or_truncated_shard_gives_none(bad: JobLog):
    logs = [shard(1, "t.py::a"), bad]
    assert rules.recorded_tests(logs, candidates("shard 1", "shard 2", "check"), DECLARED) is None
    assert rules.recorded_tests([bad], candidates("shard 2"), DECLARED) is None


def test_a_missing_log_of_a_shard_gives_none():
    assert (
        rules.recorded_tests([shard(1, "t.py::a")], candidates("shard 1", "shard 2"), DECLARED)
        is None
    )


SHARDS = [shard(1, "t.py::a"), shard(2, "t.py::b")]
SHARD_NAMES = ["shard 1", "shard 2"]
SHARD_HASHES = sorted_hashes(("t.py::a", "t.py::b"))


def test_a_declared_log_truncated_before_any_header_is_ignored_beside_complete_shards():
    cut = job("check", "setup output\n" * 5, truncated=900)
    found = rules.recorded_tests([*SHARDS, cut], candidates(*SHARD_NAMES, "check"), DECLARED)
    assert found == SHARD_HASHES


def test_a_declared_log_truncated_with_a_visible_header_gives_none():
    cut = job("check", pytest_text("t.py::c"), truncated=900)
    assert rules.recorded_tests([*SHARDS, cut], candidates(*SHARD_NAMES, "check"), DECLARED) is None


def test_a_declared_log_truncated_alone_gives_none():
    cut = job("check", "setup output", truncated=900)
    assert rules.recorded_tests([cut], candidates("check"), DECLARED) is None


def test_a_declared_log_with_a_dropped_id_gives_none():
    dropped = job("check", pytest_text("t.py::" + "x" * 5000))
    assert (
        rules.recorded_tests([*SHARDS, dropped], candidates(*SHARD_NAMES, "check"), DECLARED)
        is None
    )


def test_a_declared_job_without_a_log_or_with_an_empty_one_is_ignored_beside_shards():
    names = [*SHARD_NAMES, "check"]
    assert rules.recorded_tests(SHARDS, candidates(*names), DECLARED) == SHARD_HASHES
    empty = job("check", "", cause="empty")
    assert rules.recorded_tests([*SHARDS, empty], candidates(*names), DECLARED) == SHARD_HASHES


@pytest.mark.parametrize("cause", ["unreadable", "timed_out", "other_head"])
def test_a_declared_log_that_could_not_be_read_gives_none(cause: JobLogCause):
    lost = job("check", "", cause=cause)
    assert (
        rules.recorded_tests([*SHARDS, lost], candidates(*SHARD_NAMES, "check"), DECLARED) is None
    )


# --- repeated_ci_failure ----------------------------------------------------------------------


def row(version: int, tests: Any = None, *, drop: bool = False) -> FullChecksRead:
    out: Any = {"version": version, "head": HEAD, "jobs": []}
    if not drop:
        out["tests"] = tests
    return out


def ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{n:02d}" for n in range(count)]


def test_half_of_the_smaller_set_shared_is_a_repeat():
    assert (
        rules.repeated_ci_failure([row(1, ["a", "b", "c", "d"]), row(2, ["a", "b", "x"])])
        == "repeat"
    )


def test_less_than_half_of_the_smaller_set_shared_is_no_repeat():
    assert (
        rules.repeated_ci_failure([row(1, ["a", "b", "c"]), row(2, ["a", "x", "y", "z"])])
        == "no_repeat"
    )
    assert rules.repeated_ci_failure([row(1, ["a"]), row(2, ["b"])]) == "no_repeat"


def test_a_single_shared_id_against_sixteen_reads_repeat():
    # The accepted threshold: |a∩b|*2 >= min(|a|,|b|) with a set of one.
    assert rules.repeated_ci_failure([row(1, ["a"]), row(2, ids("n", 15) + ["a"])]) == "repeat"


def test_it_compares_the_newest_with_the_latest_earlier_version_whatever_the_list_order():
    reads = [row(3, ["a"]), row(1, ["z"]), row(2, ["b"])]
    assert rules.repeated_ci_failure(reads) == "no_repeat"
    assert rules.repeated_ci_failure([row(3, ["b"]), row(2, ["b"]), row(1, ["z"])]) == "repeat"


@pytest.mark.parametrize(
    "reads",
    [
        [],
        [row(1, ["a"])],
        [row(1, ["a"]), row(1, ["a"])],
        [row(1, ["a"]), row(2, drop=True)],
        [row(1, drop=True), row(2, ["a"])],
        [row(1, ["a"]), row(2, [])],
        [row(1, ["a"]), row(2, "a")],
        [row(1, ["a"]), row(2, ["a", 3])],
        [row(1, ["a"]), row(2, None)],
    ],
)
def test_an_unknown_set_is_never_a_repeat_or_not(reads: list[FullChecksRead]):
    assert rules.repeated_ci_failure(reads) == "unknown"


def test_a_malformed_row_never_raises():
    junk: Any = [{"head": "x"}, {"version": True, "tests": ["a"]}, {"version": "2"}, "text"]
    assert rules.repeated_ci_failure([*junk, row(1, ["a"])]) == "unknown"
    assert rules.repeated_ci_failure([*junk, row(1, ["a"]), row(2, ["a"])]) == "repeat"


# --- repetition_sentence ----------------------------------------------------------------------


def test_the_repetition_sentence_holds_only_a_version_number():
    assert rules.repetition_sentence(3) == (
        "Same failing tests as full_checks v3 (overlap of recorded failing-test ids)."
    )


# --- full_checks_excerpt_due ------------------------------------------------------------------


def due_state(
    *,
    passed: bool = False,
    ran_on: str | None = "ci",
    head: str = HEAD,
    status: str = "returned",
    returned_by: str | None = "full_checks",
) -> Any:
    record: dict[str, Any] = {"version": 1, "head": head, "passed": passed}
    if ran_on is not None:
        record["ran_on"] = ran_on
    unit: dict[str, Any] = {"status": status}
    if returned_by is not None:
        unit["returned_by"] = returned_by
    return {"subtasks": {INTEGRATION: unit, "api": {"status": "approved"}}, "full_checks": record}


def test_a_red_ci_record_of_the_returned_unit_on_its_head_is_due_for_a_read():
    assert rules.full_checks_excerpt_due(due_state(), INTEGRATION, HEAD) == "read"


def test_a_dispatched_record_is_due_for_a_dispatch_read():
    assert (
        rules.full_checks_excerpt_due(due_state(ran_on="ci_dispatch"), INTEGRATION, HEAD)
        == "dispatch"
    )


@pytest.mark.parametrize("ran_on", ["local", None])
def test_a_record_that_did_not_run_on_ci_is_none(ran_on: str | None):
    assert rules.full_checks_excerpt_due(due_state(ran_on=ran_on), INTEGRATION, HEAD) == "none"


def test_it_does_not_depend_on_who_returned_the_unit():
    for returned_by in ("qa_performance", None):
        state = due_state(returned_by=returned_by)
        assert rules.full_checks_excerpt_due(state, INTEGRATION, HEAD) == "read"


def test_the_head_may_be_given_at_another_length():
    assert rules.full_checks_excerpt_due(due_state(), INTEGRATION, HEAD[:12]) == "read"


@pytest.mark.parametrize(
    "state",
    [
        {"subtasks": {INTEGRATION: {"status": "returned"}}},
        due_state(passed=True),
        due_state(status="approved"),
        due_state(head=OTHER),
    ],
)
def test_every_other_case_is_none(state: Any):
    assert rules.full_checks_excerpt_due(state, INTEGRATION, HEAD) == "none"


def test_a_unit_that_is_not_the_reviewed_one_is_none():
    assert rules.full_checks_excerpt_due(due_state(), "api", HEAD) == "none"


def test_a_passed_that_is_not_false_is_none():
    state = due_state()
    state["full_checks"]["passed"] = None
    assert rules.full_checks_excerpt_due(state, INTEGRATION, HEAD) == "none"


RED: Any = {
    "version": 1,
    "head": HEAD,
    "passed": False,
    "duration_s": 1.0,
    "declared": {},
    "results": [{"name": "tests", "passed": False}],
    "witness": None,
    "ran_on": "ci",
}


@pytest.mark.parametrize(
    ("names", "with_integration"),
    [
        (["a"], False),
        (["a"], True),
        (["a", "b"], True),
        (["a", "b", "c"], True),
        (["a", "b"], False),
        (["a", "b", "c"], False),
    ],
)
def test_the_unit_full_checks_return_reopens_is_the_reviewed_unit_or_the_node_says_none(
    names: list[str], with_integration: bool
):
    subtasks = {name: {"status": "approved"} for name in names}
    if with_integration:
        subtasks[INTEGRATION] = {"status": "approved"}
    state: Any = {"subtasks": subtasks, "full_checks": RED}
    patch = rules.full_checks_return(state, RED, 3, at="2026-11-14T10:00:00+00:00")
    (reopened,) = [n for n, s in patch["subtasks"].items() if s["status"] == "returned"]
    reviewed = rules.reviewed_unit_name(state)
    returned = apply(state, patch)
    if reviewed is not None:
        assert reviewed == reopened
        assert rules.full_checks_excerpt_due(returned, reopened, HEAD) == "read"
    else:
        assert len(names) >= 2 and not with_integration
        assert rules.full_checks_excerpt_due(returned, reopened, HEAD) == "none"
