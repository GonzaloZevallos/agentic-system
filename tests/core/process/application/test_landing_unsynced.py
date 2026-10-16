"""GH-267, subtask `verify`: a `sync_master` is resolved only when the head holds the upstream
it was dispatched for. `land` records that upstream, a clean sync is judged by the same
verdict `deliver` uses, and a sync that brings nothing in is counted by upstream sha until the
`diagnose`. Over the memory ports, with `deliver` re-delivering the way the resume would."""

from __future__ import annotations

from agentic.core.process.application import landing, landing_plan
from agentic.core.process.application.ports import Upstream, UpstreamMerge
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import rules
from tests.core.process.application.test_landing_sync import (
    FOREIGN,
    HEAD,
    LIVE,
    MASTER,
    _behind,
    _diffs,
    _document,
    _redeliver,
    _reviewed_again,
    _unit,
)
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import _HeldLocks, _ports

CONFLICT = UpstreamMerge(clean=False, conflicted=("shared.py",), head=HEAD)


def _land(ports, tmp_path, master=MASTER):
    return landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", master, None),
        frozenset(),
        lambda line: None,
        clock=lambda: "2026-10-14T00:00:00+00:00",
        live=LIVE,
        resume=lambda number: 777,
    )


def test_land_records_the_upstream_on_a_sync_entry(tmp_path):
    ports, _ = _behind(tmp_path)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))

    _land(ports, tmp_path)

    [entry] = _document(ports)["landing"]
    assert (entry["step"], entry["upstream"]) == ("sync_master", MASTER)


def test_land_records_no_upstream_on_a_step_that_is_not_a_sync(tmp_path):
    ports = _ports(5)
    ports.tracker.head_oid = FOREIGN

    result = _land(ports, tmp_path)

    assert result.step == "re_review"
    [entry] = _document(ports)["landing"]
    assert entry["step"] == "re_review" and "upstream" not in entry


def test_a_clean_sync_resolves_because_the_head_holds_its_upstream(tmp_path):
    ports, _ = _behind(tmp_path)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))

    _land(ports, tmp_path)

    [entry] = _document(ports)["landing"]
    assert entry["resolved_at"] and "lacks" not in entry


def test_a_clean_sync_whose_merged_head_lacks_the_upstream_is_closed_not_resolved(tmp_path):
    ports, _ = _behind(tmp_path)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))
    ports.workspace.merge_brings_nothing = True

    _land(ports, tmp_path)

    [entry] = _document(ports)["landing"]
    assert "resolved_at" not in entry
    assert entry["lacks"] == MASTER and entry["closed_at"]
    assert _unit(ports)["reviewed_head"] == HEAD  # no verdict carried to an unsynced head


CONFLICTING_CHECKS = {"head": HEAD, "master": MASTER, "passed": False, "failed_step": "conflict"}


def _noop_resume(ports, tmp_path):
    """What the incident did: the resume pushes nothing master-shaped, the review approves
    the same head and `deliver` re-delivers it; the checks, run again on the same head and
    master, fail on the same conflict."""
    _reviewed_again(ports, HEAD)
    _redeliver(ports, tmp_path)
    ports.documents.save({**_document(ports), "merge_checks": CONFLICTING_CHECKS})


def test_a_resume_that_pushes_no_master_is_counted_and_the_third_tick_is_the_diagnose(tmp_path):
    ports = _ports(5, checks=False, merge_checks=CONFLICTING_CHECKS)
    ports.workspace.upstream_merges[MASTER] = CONFLICT

    assert _land(ports, tmp_path).kind == "dispatched"
    first = dict(_document(ports)["landing"][0])
    _noop_resume(ports, tmp_path)
    [closed] = _document(ports)["landing"]
    assert closed["lacks"] == MASTER and "resolved_at" not in closed
    assert landing_rules.entry_closed(closed)

    second = _land(ports, tmp_path)  # not `already_dispatched`: the first was unsynced
    assert (second.kind, second.step) == ("dispatched", "sync_master")
    entries = _document(ports)["landing"]
    assert len(entries) == 2
    assert entries[0]["cost_base"] == first["cost_base"]
    assert entries[0]["cost_usd"] == closed["cost_usd"]
    assert "lacks" in entries[0] and "lacks" not in entries[1]
    _noop_resume(ports, tmp_path)
    assert [e.get("lacks") for e in _document(ports)["landing"]] == [MASTER, MASTER]

    third = _land(ports, tmp_path)
    assert (third.kind, third.step) == ("skipped", "diagnose")
    assert len(_document(ports)["landing"]) == 2  # nothing recorded for it


def _facts(master):
    return rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=master,
        conflicts=True,
    )


def _lacking(upstream, head=HEAD):
    return {
        "head": head,
        "step": "sync_master",
        "refusal": "attempts_exhausted",
        "upstream": upstream,
        "closed_at": "t",
        "lacks": upstream,
    }


def test_the_unsynced_count_is_by_upstream_sha_not_by_head():
    raw = {
        "landing": [_lacking("1" * 40, "a" * 40), _lacking("1" * 40, "b" * 40), _lacking("2" * 40)]
    }
    assert landing_plan.spent(raw, HEAD, None, "1" * 40).unsynced_for_upstream == 2
    assert landing_plan.spent(raw, HEAD, None, "2" * 40).unsynced_for_upstream == 1
    assert landing_plan.spent(raw, HEAD, None, "3" * 40).unsynced_for_upstream == 0
    assert landing_plan.spent(raw, HEAD, None).unsynced_for_upstream == 0


def test_two_unsynced_passes_for_the_current_upstream_make_the_step_a_diagnose():
    master = "1" * 40
    state = {"id": "GH-9", "title": "", "description": ""}
    one = {**state, "landing": [_lacking(master)]}
    two = {**state, "landing": [_lacking(master), _lacking(master, "c" * 40)]}
    assert landing_plan.step_for(one, "attempts_exhausted", _facts(master)).kind == "sync_master"
    assert landing_plan.step_for(two, "attempts_exhausted", _facts(master)).kind == "diagnose"


def test_a_busy_master_reaches_the_diagnose_through_passes_not_the_unsynced_count():
    entries = [{**_lacking(f"{n}" * 40, f"{n}" * 40), "refusal": "merge_conflict"} for n in "123"]
    state = {"id": "GH-9", "title": "", "description": "", "landing": entries}
    master = "4" * 40

    assert landing_plan.spent(state, HEAD, None, master).unsynced_for_upstream == 0
    assert landing_plan.spent(state, HEAD, None, master).passes == 3
    assert landing_plan.step_for(state, "merge_conflict", _facts(master)).kind == "diagnose"
