"""GH-612: an `unlandable` verdict stops binding once a human released the escalation it raised
(or the head moved on a task that is not escalated), so the next refusal gets its normal step.
Driven through `landing.land` on the GH-320 shape, with the document the real writers leave:
the escalation `landing_fix._escalate` logs and the `resumed` entry `rules.resumed` writes.
`landing_plan.spent` is read directly for the epoch edge cases."""

from __future__ import annotations

import dataclasses
from typing import Any

from agentic.core.process.application import landing_plan
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import VERDICT_SYNC, TaskState
from tests.core.process.application.test_landing_verdict import (
    _diagnoser,
    _doc,
    _land,
    _world,
)
from tests.core.process.landing_fixtures import HEAD, MASTER
from tests.fakes import FakeLauncher

OLD_MASTER = "e" * 40
OTHER_HEAD = "f" * 40
DIAGNOSED_AT = "2026-10-23T09:00:00+00:00"
ESCALATED_AT = "2026-10-23T09:05:00+00:00"
RESUMED_AT = "2026-10-23T11:00:00+00:00"
REASON = "the only blocker is the missing Breaking-API line"


def _diagnose(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "merge_failed",
        "delivered": True,
        "verdict": "unlandable",
        "upstream": OLD_MASTER,
        "at": DIAGNOSED_AT,
        "resolved_at": DIAGNOSED_AT,
    }
    return {**entry, **over}


def _escalated_log() -> list[dict[str, Any]]:
    """The escalation `landing_fix._escalate` writes: `rules.escalation` through `rules.logged`."""
    patch = rules.logged(dict(rules.escalation("failure", REASON)), at=ESCALATED_AT)
    return list(patch["pause_log"])


def _released_log(raw: TaskState) -> list[dict[str, Any]]:
    """That escalation followed by the `resumed` entry `rules.resumed` stamps."""
    resumed = rules.resumed(raw, None, at=RESUMED_AT)
    return [*_escalated_log(), *resumed["pause_log"]]


def _release(ports, *, syncs: int = 0, pr_head: str = HEAD, still_escalated: bool = False) -> None:
    """The world after the human's `agentic resume` (or, with `still_escalated`, before it)."""
    document = _doc(ports)
    escalated: dict[str, Any] = {
        **document,
        "escalated": REASON,
        "escalation_class": "failure",
        "status": "waiting_on_human",
    }
    sync_entries = [
        {
            "head": HEAD,
            "step": "sync_master",
            "refusal": "merge_conflict",
            "upstream": OLD_MASTER,
            "resolved_at": DIAGNOSED_AT,
        }
        for _ in range(syncs)
    ]
    if still_escalated:
        saved = {**escalated, "pause_log": _escalated_log()}
    else:
        released = rules.resumed(escalated, None, at=RESUMED_AT)
        saved = {
            **document,
            **{k: v for k, v in released.items() if k not in ("pause_log", "sections")},
            "pause_log": _released_log(escalated),
        }
    ports.documents.save({**saved, "landing": [*sync_entries, _diagnose()]})
    if pr_head != HEAD:
        ports.tracker.prs[str(document["pull_request"])] = _moved(ports, pr_head)


def _moved(ports, head: str):
    return dataclasses.replace(ports.tracker.prs[str(_doc(ports)["pull_request"])], head_oid=head)


def test_a_conflict_after_the_human_released_the_unlandable_escalation_is_synced(
    monkeypatch, tmp_path
):
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _release(ports)
    document = _doc(ports)
    spent = landing_plan.spent(document, HEAD, None, MASTER)
    assert document["pause_log"][-1]["kind"] == "resumed"
    assert not document.get("escalated")

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert (result.kind, result.step) == ("dispatched", "sync_master")
    assert spent.unlandable is False and spent.diagnoses == 1
    assert landing_rules.waits_on_human(
        document, HEAD, "merge_conflict", spent=spent, stopped=False, needs_human=False,
        stranded=False,
    ) is False  # fmt: skip


def test_the_same_document_before_the_release_still_gives_no_step(monkeypatch, tmp_path):
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _release(ports, still_escalated=True)
    assert landing_plan.spent(_doc(ports), HEAD, None, MASTER).unlandable is True

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert (result.kind, result.step) == ("skipped", "none")


def test_an_escalated_task_with_a_new_head_is_still_the_humans(monkeypatch, tmp_path):
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _release(ports, still_escalated=True, pr_head=OTHER_HEAD)
    assert landing_plan.spent(_doc(ports), OTHER_HEAD, None, MASTER).unlandable is True

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert (result.kind, result.step) == ("skipped", "none")


def test_a_release_does_not_refill_the_sync_budget(monkeypatch, tmp_path):
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _release(ports, syncs=landing_rules.LANDING_SYNCS_MAX)
    spent = landing_plan.spent(_doc(ports), HEAD, None, MASTER)
    assert spent.unlandable is False and spent.syncs == landing_rules.LANDING_SYNCS_MAX

    result, _ = _land(ports, tmp_path, FakeLauncher())

    # The step is `diagnose`, not `none` (which would answer `not_live`); the diagnosis recorded
    # on this very head is the idempotency's, so it is not dispatched a second time.
    assert (result.kind, result.reason, result.step) == (
        "skipped",
        "already_dispatched",
        "diagnose",
    )


def test_a_new_unlandable_diagnosis_after_the_release_binds_again(monkeypatch):
    ports = _world(monkeypatch)
    _release(ports)
    document = _doc(ports)
    again = _diagnose(at="2026-10-23T12:00:00+00:00")
    saved = {**document, "landing": [*document["landing"], again]}

    assert landing_plan.spent(saved, HEAD, None, MASTER).unlandable is True


def test_an_undelivered_diagnosis_with_released_at_frees_no_earlier_verdict():
    freed = _diagnose(delivered=False, released_at=RESUMED_AT)
    raw = {"landing": [_diagnose(), freed], "pause_log": _escalated_log(), "escalated": REASON}

    assert landing_plan.spent(raw, HEAD, None, MASTER).unlandable is True


def test_a_resolved_land_slice_leaves_the_unlandable_reading_as_it_was():
    raw = {
        "landing": [
            _diagnose(),
            {"head": HEAD, "step": "land_slice", "resolved_at": RESUMED_AT, "at": RESUMED_AT},
        ],
    }
    spent = landing_plan.spent(raw, HEAD, None, MASTER)

    assert spent.last_verdict is None and spent.diagnoses > 0
    assert spent.unlandable is True and spent.verdict_in_force is True


def test_the_release_the_landing_writes_frees_the_verdict_it_answered():
    """`released_at` on a later diagnose that was never delivered, then the `resumed` entry, on a
    task that is not escalated."""
    stamped = _diagnose(delivered=False, at=RESUMED_AT, released_at=RESUMED_AT)
    log = [*_escalated_log(), rules.resumed_entry(at=RESUMED_AT)]
    raw = {"landing": [_diagnose(), stamped], "pause_log": log}

    spent = landing_plan.spent(raw, HEAD, None, MASTER)

    assert spent.diagnoses == 1 and spent.unlandable is False


def test_a_sync_verdict_that_reads_as_unlandable_is_freed_by_the_release_too():
    """The second `sync` for the same sha reads as `unlandable`: it is the entry that binds."""
    syncs = [
        _diagnose(verdict="sync", upstream=MASTER, at=f"2026-10-23T09:0{n}:00+00:00") for n in "12"
    ]
    held = {"landing": syncs, "pause_log": _escalated_log(), "escalated": REASON}
    freed = {"landing": syncs, "pause_log": [*_escalated_log(), rules.resumed_entry(at=RESUMED_AT)]}

    assert landing_plan.spent(held, HEAD, None, MASTER).unlandable is True
    assert landing_plan.spent(freed, HEAD, None, MASTER).unlandable is False
