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

from agentic.core.process.application import poll
from tests.core.process.application.test_conflict_probe import _declaring, _land
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
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: merge_conflict"]
    assert [(c.number, c.kind) for c in lands] == [(5, "land_model")]
    assert ports.tracker.ci_reads == []
    assert not any("waiting: ci" in line for line in lines)

    result = _land(ports, tmp_path)
    assert (result.kind, result.step) == ("dispatched", "sync_master")
