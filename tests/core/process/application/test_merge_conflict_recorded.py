"""GH-492: the conflict git finds when `merge` builds the fresh pair of a `ci:` product is a
fact about that exact head and master. GH-475: GitHub's `mergeable` stayed `UNKNOWN`, the merge
verb refused `merge_conflict` every round with nothing written, `land` read `ci_run` and skipped,
and the poller launched `merge` again. Through the `merge` verb, `poll.assess_merge`,
`landing.land` and `rules.auto_merge_decision`, on the in-memory ports."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentic.core.process.application import landing_plan, merge, poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import rules
from tests.core.process.application.test_conflict_probe import _land
from tests.core.process.application.test_merge_ci_run import _world
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _Locks

AT = "2026-11-04T12:20:00+00:00"
MOVED = "e" * 40


def _unknown(ports, mergeable: str | None = "UNKNOWN") -> None:
    url = URL.format(5)
    ports.tracker.prs[url] = dataclasses.replace(
        ports.tracker.prs[url], mergeable=mergeable, base_oid=MASTER
    )


def _gh475(**document):
    """A `ci:` product, GitHub unable to say, the merge-tree probe unread, and a real merge of
    the head with master that conflicts."""
    ports = _world(**document)
    _unknown(ports)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = None
    ports.workspace.conflicts.add(MASTER)
    return ports


def _merge_round(ports, tmp_path: Path):
    lines: list[str] = []
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        lambda: AT,
    )
    return result, lines


def _document(ports) -> dict:
    return dict(ports.documents.load("GH-5") or {})


def _assess(ports, master: str = MASTER):
    assessed = poll.assess_merge(ports, "GH-5", _document(ports), 5, frozenset(), master)
    assert not isinstance(assessed, poll.Abandoned)
    return assessed


def test_gh475_the_first_round_records_the_conflict_and_land_dispatches_its_step(tmp_path):
    ports = _gh475()
    before = _document(ports)

    result, lines = _merge_round(ports, tmp_path)

    assert result == merge.MergeResult("refused", "merge_conflict")
    assert lines == [f"agentic: merge: GH-5: {HEAD} does not merge with {MASTER}"]
    after = _document(ports)
    assert after.pop("merge_conflict") == {"head": HEAD, "master": MASTER, "at": AT}
    assert after == before  # no merge_ci row, no failure, nothing else
    assert ports.tracker.comments == []

    landed = _land(ports, tmp_path)
    assert (landed.kind, landed.step) == ("dispatched", "sync_master")


def test_gh475_after_the_record_the_poller_stops_launching_merge(tmp_path):
    ports = _gh475()
    _merge_round(ports, tmp_path)

    probes = len(ports.workspace.conflict_probes)
    assessed = _assess(ports)
    assert (assessed.decision.kind, assessed.decision.reason) == ("refuse", "merge_conflict")
    assert assessed.conflict_source == "recorded"
    assert len(ports.workspace.conflict_probes) == probes  # git is not asked again

    lines = []
    lands: list = []
    found = poll.gather_merges(ports, frozenset(), MASTER, _Locks(), lines, lands)
    assert found == []  # the poller no longer launches `merge`
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: merge_conflict (recorded)"]
    assert [(c.issue, c.kind) for c in lands] == [(5, "land_model")]


def test_the_next_merge_round_on_the_recorded_pair_refuses_without_building_it_again(tmp_path):
    ports = _gh475()
    _merge_round(ports, tmp_path)
    merges = len(ports.workspace.merges)

    result, lines = _merge_round(ports, tmp_path)

    assert result == merge.MergeResult("refused", "merge_conflict")
    assert lines == [f"agentic: merge: GH-5: {HEAD} conflicts with master {MASTER} (recorded)"]
    assert len(ports.workspace.merges) == merges


def test_a_recorded_conflict_refuses_on_an_unlandable_pair_too():
    ports = _gh475(
        merge_conflict={"head": HEAD, "master": MASTER, "at": AT},
        landing=[
            {"head": HEAD, "step": "diagnose", "refusal": "merge_conflict", "delivered": True}
        ],
    )
    assert landing_plan.spent(_document(ports), HEAD, None).unlandable

    assessed = _assess(ports)

    assert (assessed.decision.kind, assessed.decision.reason) == ("refuse", "merge_conflict")
    assert assessed.facts.conflicts is True


def test_unknown_over_a_clean_git_merge_still_reaches_ci_run(tmp_path):
    ports = _world()
    _unknown(ports)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False

    assessed = _assess(ports)

    assert assessed.facts.conflicts is False
    assert assessed.decision.kind == "ci_run"
    assert "merge_conflict" not in _document(ports)


def test_unknown_with_no_record_and_an_unread_probe_stays_unread():
    ports = _world()
    _unknown(ports, None)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = None

    assessed = _assess(ports)

    assert assessed.facts.conflicts is None
    assert assessed.decision.kind == "ci_run"


def test_a_record_of_another_pair_is_ignored_and_the_decision_is_todays():
    for head, master in ((HEAD, "9" * 40), ("8" * 40, MASTER)):
        ports = _world(merge_conflict={"head": head, "master": master, "at": AT})
        _unknown(ports)
        ports.workspace.conflict_answers[(HEAD, MASTER)] = None

        assessed = _assess(ports)

        assert assessed.facts.conflicts is None
        assert assessed.decision.kind == "ci_run"


def test_a_record_stops_counting_once_master_moves():
    ports = _world(merge_conflict={"head": HEAD, "master": MASTER, "at": AT})
    _unknown(ports)
    ports.workspace.conflict_answers[(HEAD, MOVED)] = None
    ports.workspace.upstream_answer = Upstream("origin/master", MOVED, None)

    assessed = _assess(ports, master=MOVED)

    assert (assessed.facts.conflicts, assessed.decision.kind) == (None, "ci_run")


def test_a_recorded_conflict_outranks_a_mergeable_reading():
    ports = _world(merge_conflict={"head": HEAD, "master": MASTER, "at": AT})
    _unknown(ports, "MERGEABLE")
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False

    assessed = _assess(ports)

    assert (assessed.decision.kind, assessed.decision.reason) == ("refuse", "merge_conflict")


def test_a_merge_that_fails_without_a_conflict_writes_nothing_and_stops(tmp_path):
    ports = _gh475()
    ports.workspace.conflicts.discard(MASTER)
    ports.workspace.failed_merges.add(MASTER)
    before = _document(ports)

    result, lines = _merge_round(ports, tmp_path)

    assert result == merge.MergeResult("stopped", "ci_publish_failed")
    assert _document(ports) == before
    assert lines == [f"agentic: merge: GH-5: could not publish the fresh CI run: refused: {MASTER}"]


def test_the_base_reading_takes_a_recorded_conflict_over_every_other_reading():
    assert rules.merge_conflicts_from(None, HEAD, MASTER, None, recorded=True) is True
    assert rules.merge_conflicts_from(None, HEAD, MASTER, False, recorded=True) is True
    assert rules.merge_conflicts_from(None, HEAD, MASTER, None, recorded=False) is None
    assert rules.merge_conflicts_from(None, HEAD, MASTER, False, recorded=False) is False
