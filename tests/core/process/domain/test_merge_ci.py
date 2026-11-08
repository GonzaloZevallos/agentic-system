"""GH-463: the pure decision behind the merge gate's fresh GitHub run
(`domain/merge_ci.py`): when a decided run stands for the head on the current master, when a
recorded pass covers it (#353 for a `ci:` product) and how the pairs are counted against their
bounds. No I/O: every case is a record and the answer."""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest

from agentic.core.process.domain import ci, merge_ci
from agentic.core.process.domain.task import (
    FullCheckEntry,
    FullChecksRecord,
    MergeCiRecord,
    add_merge_ci,
)

HEAD = "a" * 40
MASTER = "b" * 40
NEWER = "c" * 40
TESTED = "d" * 40
SETUP = ["uv sync"]
TASK_DOC = ".agentic/tasks/GH-9.md"


def _pair(
    *,
    head: str = HEAD,
    master: str = MASTER,
    tested: str = TESTED,
    mode: Literal["push", "dispatch"] = "push",
    republished: bool = False,
    superseded: list[str] | None = None,
    outcome: Literal["stale", "no_verdict", "green", "red"] | None = None,
    run_id: str | None = None,
    released_at: str | None = None,
) -> MergeCiRecord:
    record = MergeCiRecord(
        head=head,
        master=master,
        tested=tested,
        mode=mode,
        published_at="2026-11-03T10:00:00.000Z",
        republished=republished,
        superseded=superseded or [],
    )
    if outcome is not None:
        record["outcome"] = outcome
    if run_id is not None:
        record["run_id"] = run_id
    if released_at is not None:
        record["released_at"] = released_at
    return record


def _full(
    *,
    head: str = HEAD,
    passed: bool = True,
    results: list[FullCheckEntry] | None = None,
    ran_on: Literal["ci", "local", "ci_dispatch"] = "ci",
) -> FullChecksRecord:
    return FullChecksRecord(
        version=1,
        head=head,
        passed=passed,
        duration_s=1.0,
        declared={"check": "x"},
        results=[{"name": "check", "passed": True}] if results is None else results,
        witness=None,
        ran_on=ran_on,
    )


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


def test_decided_red_is_the_newest_red_pair_on_the_current_master_only():
    red = _pair(outcome="red", run_id="7")
    assert merge_ci.decided_red([red], HEAD, MASTER) == red
    assert merge_ci.decided_red([red], HEAD, NEWER) is None  # master overtook it
    assert merge_ci.decided_red([_pair(outcome="green")], HEAD, MASTER) is None
    assert merge_ci.decided_red([red, _pair(master=NEWER)], HEAD, MASTER) is None  # not newest
    assert merge_ci.decided_red([red], HEAD[:7], MASTER) is None  # a short head names nothing


# --- GH-552: the one reread of a no_verdict pair of the current master ------------------------


def _unread(**over: Any) -> MergeCiRecord:
    return cast(MergeCiRecord, {**_pair(outcome="no_verdict", republished=True), **over})


def test_a_no_verdict_pair_of_the_current_master_is_rereadable():
    pair = _unread()
    assert merge_ci.rereadable([_pair(head="e" * 40), pair], HEAD, MASTER) == pair


@pytest.mark.parametrize(
    ("pr_head", "master"),
    [(None, MASTER), (HEAD, None), (HEAD[:7], MASTER), (HEAD, MASTER[:7]), ("", MASTER)],
)
def test_a_missing_or_short_id_rereads_nothing(pr_head, master):
    assert merge_ci.rereadable([_unread()], pr_head, master) is None


@pytest.mark.parametrize(
    "pair",
    [
        _unread(reread_at="2026-11-03T10:00:00+00:00", reread_state="timed_out"),
        _unread(reread_at="2026-11-03T10:00:00+00:00", reread_state="unreachable"),
        _unread(released_at="2026-11-03T10:00:00+00:00"),
        _pair(outcome="green", republished=True),
        _pair(outcome="red", republished=True),
        _pair(outcome="stale", republished=True),
        _pair(republished=True),
        _unread(superseded=[TESTED]),
        _unread(master=NEWER),
        _unread(master=MASTER[:7]),
        _unread(tested=TESTED[:7]),
        _unread(head="e" * 40),
    ],
    ids=[
        "reread_timed_out",
        "reread_unreachable",
        "released",
        "green",
        "red",
        "stale",
        "undecided",
        "tested_superseded",
        "other_master",
        "short_master",
        "short_tested",
        "other_head",
    ],
)
def test_a_pair_that_does_not_qualify_is_not_rereadable(pair):
    assert merge_ci.rereadable([pair], HEAD, MASTER) is None


def test_only_the_latest_pair_of_the_head_can_be_reread():
    older = _unread()
    newer = _pair(master=NEWER, outcome="green")
    assert merge_ci.rereadable([older, newer], HEAD, MASTER) is None


def test_a_pair_with_only_a_reread_at_is_still_rereadable():
    # GH-564: GH-552's code stamped `reread_at` before reading; only `reread_state` spends it now.
    pair = _unread(reread_at="2026-11-03T10:00:00+00:00")
    assert merge_ci.rereadable([pair], HEAD, MASTER) == pair


def test_reopened_drops_the_reread_marks():
    marked = _unread(reread_at="t", reread_state="timed_out")
    reopened = merge_ci.reopened(marked, "later")
    assert reopened["rerun_at"] == "later"
    assert "reread_at" not in reopened and "reread_state" not in reopened
    assert "reread_at" not in merge_ci.reopened(_unread(), "later")


def test_wait_began_is_the_rerun_when_there_is_one_and_the_publish_otherwise():
    pair = _unread()
    assert merge_ci.wait_began(pair) == pair["published_at"]
    assert merge_ci.wait_began({**pair, "rerun_at": "2026-11-03T11:00:00.000Z"}) == (
        "2026-11-03T11:00:00.000Z"
    )
