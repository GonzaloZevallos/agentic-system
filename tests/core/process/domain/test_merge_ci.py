"""GH-463: the pure decision behind the merge gate's fresh GitHub run
(`domain/merge_ci.py`): when a decided run stands for the head on the current master, when a
recorded pass covers it (#353 for a `ci:` product) and how the pairs are counted against their
bounds. No I/O: every case is a record and the answer."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci, merge_ci
from agentic.core.process.domain.task import FullChecksRecord, MergeCiRecord, add_merge_ci

HEAD = "a" * 40
MASTER = "b" * 40
NEWER = "c" * 40
TESTED = "d" * 40
SETUP = ["uv sync"]
TASK_DOC = ".agentic/tasks/GH-9.md"


def _pair(**over) -> MergeCiRecord:
    base: MergeCiRecord = {
        "head": HEAD,
        "master": MASTER,
        "tested": TESTED,
        "mode": "push",
        "published_at": "2026-11-03T10:00:00.000Z",
        "republished": False,
        "superseded": [],
    }
    return {**base, **over}  # type: ignore[typeddict-item]


def _full(**over) -> FullChecksRecord:
    base: FullChecksRecord = {
        "version": 1,
        "head": HEAD,
        "passed": True,
        "duration_s": 1.0,
        "declared": {"check": "x"},
        "results": [{"name": "check", "passed": True}],
        "witness": None,
        "ran_on": "ci",
    }
    return {**base, **over}  # type: ignore[typeddict-item]


# --- run_stands -------------------------------------------------------------------------


def test_a_run_on_the_current_master_stands():
    assert merge_ci.run_stands(_pair(), HEAD, MASTER, None)


def test_a_run_behind_master_stands_only_by_an_inert_gain():
    assert merge_ci.run_stands(_pair(), HEAD, NEWER, True)
    assert not merge_ci.run_stands(_pair(), HEAD, NEWER, False)
    assert not merge_ci.run_stands(_pair(), HEAD, NEWER, None)


def test_a_run_of_another_head_never_stands():
    assert not merge_ci.run_stands(_pair(head=NEWER), HEAD, MASTER, None)


def test_a_short_sha_never_stands():
    assert not merge_ci.run_stands(_pair(), HEAD[:12], MASTER, None)
    assert not merge_ci.run_stands(_pair(master=MASTER[:12]), HEAD, MASTER, None)
    assert not merge_ci.run_stands(_pair(tested=TESTED[:12]), HEAD, MASTER, None)


def test_a_run_ended_stale_or_without_verdict_never_stands():
    assert not merge_ci.run_stands(_pair(outcome="stale"), HEAD, MASTER, None)
    assert not merge_ci.run_stands(_pair(outcome="no_verdict"), HEAD, MASTER, None)
    assert merge_ci.run_stands(_pair(outcome="green"), HEAD, MASTER, None)


def test_a_superseded_tested_sha_never_stands():
    old = "e" * 40
    record = _pair(superseded=[old])
    assert merge_ci.run_stands(record, HEAD, MASTER, None, ran=TESTED)
    assert not merge_ci.run_stands(record, HEAD, MASTER, None, ran=old)
    assert not merge_ci.run_stands(record, HEAD, MASTER, None, ran=NEWER)


def test_no_record_stands_for_nothing():
    assert not merge_ci.run_stands(None, HEAD, MASTER, None)


# --- masters_covered_ci -----------------------------------------------------------------


def _covered(
    full=None, fresh=None, *, master=MASTER, base=MASTER, gains=None, globs=(), touched=()
):
    return merge_ci.masters_covered_ci(
        full,
        fresh,
        pr_head=HEAD,
        current_master=master,
        merge_base=base,
        required=("check",),
        gains=gains or {},
        globs=list(globs),
        setup_commands=SETUP,
        task_touched=list(touched),
    )


def test_a_ci_full_checks_pass_on_the_merge_base_covers_when_it_is_current():
    assert _covered(_full())
    assert _covered(_full(ran_on="ci_dispatch"))


@pytest.mark.parametrize("ran_on", ["local", None])
def test_a_full_checks_pass_that_did_not_run_on_ci_never_covers(ran_on):
    record = _full()
    if ran_on is None:
        del record["ran_on"]
    else:
        record["ran_on"] = ran_on
    assert not _covered(record)


def test_a_failed_or_other_head_full_checks_record_never_covers():
    assert not _covered(_full(passed=False))
    assert not _covered(_full(head=NEWER))
    assert not _covered(_full(head=HEAD[:12]))


def test_rows_must_cover_every_required_name_including_the_plan_only_ones():
    rows = [{"name": "check", "passed": True}]
    plan = merge_ci.masters_covered_ci(
        _full(results=rows),
        None,
        pr_head=HEAD,
        current_master=MASTER,
        merge_base=MASTER,
        required=("check", "browser"),
        gains={},
        globs=[],
        setup_commands=SETUP,
        task_touched=[],
    )
    assert not plan
    deferred = [{"name": "check", "passed": False, "deferred": True}]
    assert not _covered(_full(results=deferred))


def test_a_pass_behind_master_covers_only_when_the_gain_is_inert():
    globs = [".agentic/tasks/*.md"]
    gains = {MASTER: [TASK_DOC]}
    assert _covered(_full(), master=NEWER, gains=gains, globs=globs, touched=["a.py"])
    assert not _covered(_full(), master=NEWER, gains={MASTER: ["a.py"]}, globs=globs, touched=[])
    assert not _covered(_full(), master=NEWER, gains={MASTER: None}, globs=globs, touched=[])
    assert not _covered(_full(), master=NEWER, gains=gains, globs=[], touched=["a.py"])


def test_a_green_fresh_run_covers_on_its_own_master():
    assert _covered(None, _pair(outcome="green"), base=NEWER)
    assert not _covered(None, _pair(outcome="red"), base=NEWER)
    assert not _covered(None, _pair(), base=NEWER)
    assert not _covered(None, _pair(outcome="green", head=NEWER), base=NEWER)


def test_a_green_fresh_run_behind_master_covers_by_an_inert_gain_only():
    globs = [".agentic/tasks/*.md"]
    green = _pair(outcome="green")
    assert _covered(
        None, green, master=NEWER, gains={MASTER: [TASK_DOC]}, globs=globs, touched=["a.py"]
    )
    assert not _covered(
        None, green, master=NEWER, gains={MASTER: ["a.py"]}, globs=globs, touched=[]
    )


def test_nothing_recorded_covers_nothing():
    assert not _covered(None, None)


# --- pair_counts ------------------------------------------------------------------------


def _n(i: int) -> str:
    return f"{i:040x}"


def test_pair_counts_split_stale_from_no_verdict_pairs_of_the_head():
    records = [
        _pair(master=_n(1), outcome="stale"),
        _pair(master=_n(2), outcome="stale"),
        _pair(master=_n(3), outcome="no_verdict", republished=True),
        _pair(master=_n(4), outcome="stale", head=NEWER),
    ]
    assert merge_ci.pair_counts(records, HEAD) == merge_ci.PairCounts(stale=2, no_verdict=1)
    assert merge_ci.pair_counts([], HEAD) == merge_ci.PairCounts(stale=0, no_verdict=0)


def test_a_no_verdict_pair_counts_only_once_its_republish_was_spent():
    records = [_pair(outcome="no_verdict", republished=False)]
    assert merge_ci.pair_counts(records, HEAD).no_verdict == 0


def test_no_verdict_pairs_count_only_since_the_last_release():
    records = [
        _pair(master=_n(1), outcome="no_verdict", republished=True, released_at="t1"),
        _pair(master=_n(2), outcome="no_verdict", republished=True),
    ]
    assert merge_ci.pair_counts(records, HEAD).no_verdict == 1
    released = [*records[:1], {**records[1], "released_at": "t2"}]
    assert merge_ci.pair_counts(released, HEAD).no_verdict == 0


def test_the_bounds_are_named_in_ci():
    assert ci.CI_STALE_PAIRS_MAX == 3
    assert ci.CI_NO_VERDICT_PAIRS_MAX == 1


def test_latest_is_the_last_pair_of_the_head():
    records = [_pair(master=_n(1)), _pair(master=_n(2), head=NEWER), _pair(master=_n(3))]
    assert merge_ci.latest(records, HEAD) == records[2]
    assert merge_ci.latest(records, "f" * 40) is None


# --- the reducer ------------------------------------------------------------------------


def test_the_reducer_replaces_a_known_pair_in_place_and_appends_a_new_one():
    first = _pair()
    updated = _pair(outcome="green", run_id="7")
    other = _pair(master=NEWER)
    assert add_merge_ci([first], [updated, other]) == [updated, other]
    assert add_merge_ci([], [first]) == [first]
