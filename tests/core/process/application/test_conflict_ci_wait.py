"""GH-395: a pull request GitHub reads as CONFLICTING reports no CI (GitHub runs no
`pull_request` workflow on it), so the poller no longer waits the hour on it -- through
`poll.gather_merges` / `assess_merge`, `landing.land`, `landing_plan.spent` and the `agentic
merge` verb, on the in-memory ports and a fake tracker.

What the material can show of the 2026-10-28 incident (PRs #390 and #394, GH-374 and GH-375):
their task documents hold the `landing` list (`refusal: ci_missing`, `verdict: sync`) but no
GitHub mergeability reading, so the root cause cannot be reproduced from disk. What it does
show is `facts.conflicts` was not True: the case here is the narrowest that explains it, a
CONFLICTING reading over a base one step behind master with a clean probe."""

from __future__ import annotations

import dataclasses

from agentic.core.process.application import landing_plan, merge, poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import rules
from tests.core.process.application.test_conflict_probe import _land
from tests.core.process.application.test_merge_ci import _check, _report, _world
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _Locks

BEHIND = "e" * 40
CI_SYNC = {
    "head": HEAD,
    "step": "sync_master",
    "refusal": "ci_blocked_by_conflict",
    "upstream": MASTER,
    "closed_at": "t",
}


def _read(ports, mergeable: str, base: str = BEHIND) -> None:
    url = URL.format(5)
    ports.tracker.prs[url] = dataclasses.replace(
        ports.tracker.prs[url], mergeable=mergeable, base_oid=base
    )


def _conflicting_world(**document):
    """CONFLICTING on the head over a base one step behind master, a probe that says clean
    and a CI report with no check at all."""
    ports = _world(_report(), **document)
    _read(ports, "CONFLICTING")
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    return ports


def _assess(ports):
    return poll.assess_merge(ports, "GH-5", ports.documents.load("GH-5"), 5, frozenset(), MASTER)


def test_a_conflicting_head_with_no_ci_is_handed_to_the_sync_in_the_same_round(tmp_path):
    ports = _conflicting_world()
    lands: list = []
    lines: list[str] = []

    found = poll.gather_merges(ports, frozenset(), MASTER, _Locks(), lines, lands)

    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: ci_blocked_by_conflict"]
    assert [(c.issue, c.kind) for c in lands] == [(5, "land_model")]
    assert not any("waiting: ci" in line for line in lines)

    result = _land(ports, tmp_path)
    assert (result.kind, result.step) == ("dispatched", "sync_master")


def test_the_verb_decides_the_same_refusal_without_a_comment(tmp_path):
    ports = _conflicting_world()
    assessed = _assess(ports)
    assert assessed is not None
    assert (assessed.decision.kind, assessed.decision.reason) == (
        "refuse",
        "ci_blocked_by_conflict",
    )
    assert assessed.facts.conflicts is False and assessed.facts.github_conflicting

    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda line: None,
    )

    assert result == merge.MergeResult("refused", "ci_blocked_by_conflict")
    assert ports.tracker.merge_calls == []
    assert ports.tracker.comments == []


def test_a_mergeable_head_with_a_missing_check_still_waits(tmp_path):
    ports = _world(_report())
    _read(ports, "MERGEABLE", base=MASTER)
    lines: list[str] = []
    lands: list = []

    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), lines, lands)

    assert lines == ["agentic: poll: auto-merge #5 -> waiting: ci missing web (60s of 3600)"]
    assert lands == []
    result = _land(ports, tmp_path)
    assert (result.kind, result.reason) == ("skipped", "decision_wait")


def test_a_conflicting_read_with_the_check_reported_is_not_decided_here():
    ports = _world(_report())
    _read(ports, "CONFLICTING")
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    ports.tracker.ci_reports = [dataclasses.replace(_report(), checks=(_check("web", None),))]

    assessed = _assess(ports)

    assert assessed is not None
    assert assessed.decision.reason != "ci_blocked_by_conflict"


def test_after_one_sync_for_the_pair_the_step_is_a_diagnosis_not_a_second_sync(tmp_path):
    ports = _conflicting_world(landing=[CI_SYNC])

    result = _land(ports, tmp_path)

    assert result.step == "diagnose"


def test_the_pair_count_is_per_head_and_upstream_and_spends_no_conflict_sync():
    other_head = {**CI_SYNC, "head": "d" * 40}
    other_master = {**CI_SYNC, "upstream": BEHIND}
    verdict = {**CI_SYNC, "refusal": "ci_missing"}
    raw = {"landing": [CI_SYNC, CI_SYNC, other_head, other_master, verdict]}

    spent = landing_plan.spent(raw, HEAD, None, MASTER)

    assert spent.conflict_ci_syncs_on_pair == 2
    assert spent.conflicting_syncs_for_upstream == 0
    assert "ci_blocked_by_conflict" not in landing_rules.CONFLICTING_SYNC_REFUSALS
    assert landing_plan.spent(raw, HEAD, None, None).conflict_ci_syncs_on_pair == 0
    assert landing_plan.spent(raw, "d" * 40, None, MASTER).conflict_ci_syncs_on_pair == 1


def test_the_github_reading_is_filled_by_the_one_rules_function_in_poller_and_verb(tmp_path):
    ports = _conflicting_world()
    url = URL.format(5)
    pull_request = ports.tracker.prs[url]
    raw = ports.documents.load("GH-5")

    polled = poll._merge_facts("GH-5", raw, pull_request, frozenset(), 5, MASTER)
    assert polled.github_conflicting is rules.github_conflicting(pull_request, HEAD) is True

    _read(ports, "MERGEABLE")
    polled = poll._merge_facts("GH-5", raw, ports.tracker.prs[url], frozenset(), 5, MASTER)
    assert polled.github_conflicting is False
