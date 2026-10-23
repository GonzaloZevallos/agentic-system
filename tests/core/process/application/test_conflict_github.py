"""GH-326: GitHub's own mergeability as the second reading of the one conflict fact -- through
`poll.gather_merges` / `assess_merge` and the `agentic merge` verb, on the in-memory ports.
The regression is PR #324 (GH-320): CONFLICTING on GitHub, no CI check ever reported, and the
poller waited the whole CI timeout as `ci_missing`.

What the material can show of that hour: GitHub's reading of #324 (`mergeable: CONFLICTING`,
`mergeStateStatus: DIRTY`) and the poller's `waiting: ci missing check` lines are in the
issue; the probe's answer and the `upstream.sha` it measured against are in no log line, so
the material cannot tell a probe that returned False on a fresh master from a probe that
measured against a stale upstream sha. It can rule out the `diagnosed` guard (the one
`diagnose` entry in GH-320's document is the result of the hour). `conflict_probe_wanted`
cannot be ruled out from it either. These tests do not pick: the probe says clean here, and
GitHub's reading on matching tips is what decides."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from agentic.core.process.application import merge, poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.ci import CiReport
from tests.core.process.application.test_conflict_probe import _declaring, _land
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _Locks, _ports

# Entries from before the first refused poll: no `diagnose` among them.
EARLIER = [
    {"head": "9" * 40, "step": "renumber", "refusal": "adr_number_taken", "at": "t0"},
    {"head": "9" * 40, "step": "re_review", "refusal": "head_not_reviewed", "at": "t1"},
    {"head": HEAD, "step": "fix_important", "refusal": "open_findings", "at": "t2"},
]


def _conflicting(ports, number=5, **reading):
    url = URL.format(number)
    ports.tracker.prs[url] = dataclasses.replace(
        ports.tracker.prs[url], mergeable="CONFLICTING", base_oid=MASTER, **reading
    )


def test_a_conflicting_pull_request_with_no_ci_is_refused_on_the_first_poll(tmp_path):
    ports = _declaring(_ports(5, checks=False, landing=EARLIER))
    _conflicting(ports)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False  # git says clean: GitHub decides
    ports.tracker.ci_reports = []  # no check run ever reports

    lands: list = []
    lines: list[str] = []
    found = poll.gather_merges(ports, frozenset(), MASTER, _Locks(), lines, lands)

    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: merge_conflict (github)"]
    assert [(c.issue, c.kind) for c in lands] == [(5, "land_model")]
    assert ports.tracker.ci_reads == []
    assert not any("waiting: ci" in line for line in lines)

    result = _land(ports, tmp_path)
    assert (result.kind, result.step) == ("dispatched", "sync_master")


def _assess(ports, number=5):
    return poll.assess_merge(
        ports, f"GH-{number}", ports.documents.load(f"GH-{number}"), number, frozenset(), MASTER
    )


def test_unknown_is_not_a_conflict_and_the_next_poll_that_reads_mergeable_waits_on_the_ci():
    ports = _declaring(_ports(5, checks=False))
    url = URL.format(5)
    unknown = dataclasses.replace(ports.tracker.prs[url], mergeable="UNKNOWN", base_oid=MASTER)
    mergeable = dataclasses.replace(unknown, mergeable="MERGEABLE")
    no_check_yet = CiReport(HEAD, "master", MASTER, datetime.now(UTC).isoformat(), ())
    ports.tracker.ci_reports = [no_check_yet]

    ports.tracker.prs[url] = unknown
    ports.workspace.conflict_answers[(HEAD, MASTER)] = None  # git could not say either
    first = _assess(ports)
    assert first is not None
    assert (first.decision.kind, first.facts.conflicts, first.conflict_unread) == (
        "wait",
        None,
        True,
    )

    ports.tracker.prs[url] = mergeable
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    second = _assess(ports)
    assert second is not None
    assert (second.decision.kind, second.facts.conflicts, second.conflict_source) == (
        "wait",
        False,
        None,
    )
    assert ports.tracker.ci_reads == [url, url]


def test_github_conflicting_over_a_clean_git_keeps_syncing_only_up_to_the_unsynced_bound(tmp_path):
    def lacking():
        return {
            "head": HEAD,
            "step": "sync_master",
            "refusal": "merge_conflict",
            "upstream": MASTER,
            "closed_at": "t",
            "lacks": MASTER,
        }

    steps = []
    for spent in range(landing_rules.LANDING_UNSYNCED_MAX + 1):
        ports = _declaring(_ports(5, checks=False, landing=[lacking() for _ in range(spent)]))
        _conflicting(ports)
        ports.workspace.conflict_answers[(HEAD, MASTER)] = False
        result = _land(ports, tmp_path)
        steps.append(result.step)

    assert (
        steps[: landing_rules.LANDING_UNSYNCED_MAX]
        == ["sync_master"] * landing_rules.LANDING_UNSYNCED_MAX
    )
    assert steps[landing_rules.LANDING_UNSYNCED_MAX] == "diagnose"


def test_the_verb_and_the_poller_refuse_the_same_github_conflict_the_same_way(tmp_path):
    for mergeable, base, expected in (
        ("CONFLICTING", MASTER, ("refuse", "merge_conflict")),
        ("CONFLICTING", "e" * 40, ("merge", None)),  # a different base tip: unread, probe clean
        ("UNKNOWN", MASTER, ("merge", None)),
    ):
        ports = _ports(5)
        url = URL.format(5)
        ports.tracker.prs[url] = dataclasses.replace(
            ports.tracker.prs[url], mergeable=mergeable, base_oid=base
        )
        assessed = _assess(ports)
        assert assessed is not None
        assert (assessed.decision.kind, assessed.decision.reason) == expected

        result = merge.merge_task(
            "GH-5",
            5,
            _cfg(tmp_path),
            ports,
            Upstream("origin/master", MASTER, None),
            frozenset(),
            lambda line: None,
        )
        if expected[0] == "refuse":
            assert result == merge.MergeResult("refused", "merge_conflict")
            assert ports.tracker.merge_calls == []
            assert ports.tracker.comments == []
        else:
            assert result.kind == "merged"
