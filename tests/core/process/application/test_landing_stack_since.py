"""GH-885, subtask dispatch: an escalation of an unfetchable stacked head is keyed by the clock it
ran out on (`stack_since`), so a released wait that runs out again is asked again on the same head.
The real reducer (`stacking.unfetchable_patch`), expiry (`stacking.unfetchable_expired`),
`landing_plan.step_for`, `landing.land` and `landing.already_dispatched` over in-memory ports; only
`assess_merge` is wrapped, to say the heads cannot be fetched and to carry the clock the poller
computes (`heads_fetched`, `unfetchable_expired`, `unfetchable_since`)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application import landing, landing_plan
from agentic.core.process.application.ports import IssueState, Upstream
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import stacking
from agentic.core.process.domain.task import StackParent
from tests.core.process.application.test_landing_fix import _cfg, _doc
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _HeldLocks, _ports

T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
WAIT = stacking.STACK_UNFETCHABLE_WAIT
PARENT = "c" * 40


class _Round:
    """One poller round over one document: the reducer writes the record, `land` decides."""

    def __init__(self, monkeypatch, tmp_path) -> None:
        record = StackParent(
            issue=9, task_id="GH-9", head=PARENT, started_at="2026-11-16T10:00:00+00:00"
        )
        self.ports = _ports(5, stack_parent=record)
        self.ports.tracker.states[9] = IssueState("OPEN", "", (), None)
        self.tmp_path = tmp_path
        self.now = T0
        monkeypatch.setattr(landing, "assess_merge", self._assess)

    def _assess(self, ports, task_id, raw, *args, **kwargs):
        result = _real_assess(ports, task_id, raw, *args, **kwargs)
        assert result.facts.stack is not None
        unfetchable = dataclasses.replace(result.facts.stack, heads_fetched=False)
        patch = stacking.unfetchable_patch(raw, unfetchable, HEAD, self.now)
        record = stacking.parse_unfetchable({**raw, **patch})
        expired = stacking.unfetchable_expired(record, self.now)
        since = (
            None if record is None or isinstance(record, stacking.Unreadable) else record["since"]
        )
        stack = dataclasses.replace(
            unfetchable, unfetchable_expired=expired, unfetchable_since=since
        )
        return dataclasses.replace(result, facts=dataclasses.replace(result.facts, stack=stack))

    def poll(self) -> None:
        """What the poller does with the reducer before it lands: write the patch, if any."""
        raw = _doc(self.ports)
        facts = dataclasses.replace(_unread_stack(), heads_fetched=False)
        patch = stacking.unfetchable_patch(raw, facts, HEAD, self.now)
        if patch:
            self.ports.documents.record_closure("GH-5", patch)

    def land(self):
        return landing.land(
            "GH-5",
            _cfg(self.tmp_path),
            self.ports,
            _HeldLocks(),
            Upstream("origin/master", MASTER, None),
            frozenset(),
            lambda _line: None,
            clock=lambda: self.now.isoformat(),
            turn="head",
        )

    def entries(self) -> list[dict]:
        return [dict(e) for e in landing_plan.live_entries(_doc(self.ports))]

    def release(self) -> None:
        """A person answers: the escalation is lifted, the entry and the wait are untouched."""
        document = _doc(self.ports)
        for key in ("escalated", "escalation_class"):
            document.pop(key, None)
        document["status"] = "ready_for_review"
        self.ports.documents.save(document)


_real_assess = landing.assess_merge


def _unread_stack() -> stacking.StackFacts:
    return stacking.StackFacts(
        record=stacking.unread_facts().record,
        parent_state="open",
        parent_in_upstream=None,
        child_has_parent=None,
    )


def test_a_released_wait_that_runs_out_again_is_asked_again_on_the_same_head(monkeypatch, tmp_path):
    world = _Round(monkeypatch, tmp_path)

    # T0: the clock starts, the task waits.
    world.poll()
    assert _doc(world.ports)["stack_unfetchable"] == {"head": HEAD, "since": T0.isoformat()}
    assert world.land().kind != "dispatched" and world.entries() == []

    # Just before the bound: still a wait, nothing written.
    world.now = T0 + WAIT - timedelta(seconds=1)
    world.poll()
    assert world.land().kind != "dispatched" and world.entries() == []

    # At the bound: the stacked escalation, keyed by the clock it ran out on.
    world.now = T0 + WAIT
    world.poll()
    result = world.land()
    assert result.kind == "dispatched" and result.step == "escalate"
    [entry] = world.entries()
    assert entry["step"] == "escalate" and entry["refusal"] == "stack_parent_unmerged"
    assert entry["head"] == HEAD and entry["stack_since"] == T0.isoformat()
    document = _doc(world.ports)
    assert document["escalation_class"] == "questions"
    assert document["escalated"] == landing_rules.STACK_ESCALATION["stack_parent_unmerged"]

    # The escalation owns the task: the reducer clears the record, the entry is taken for its clock.
    world.now += timedelta(hours=1)
    world.poll()
    assert _doc(world.ports)["stack_unfetchable"] is None
    assert landing_rules.already_dispatched(
        world.entries(), HEAD, "escalate", MASTER, stack_since=T0.isoformat()
    )

    # Released with the head still unfetchable: a new clock at T1, a wait, no second escalation.
    world.release()
    t1 = world.now = T0 + WAIT + timedelta(hours=2)
    world.poll()
    assert _doc(world.ports)["stack_unfetchable"] == {"head": HEAD, "since": t1.isoformat()}
    assert world.land().kind != "dispatched"
    assert len(world.entries()) == 1

    # A second 12 h: asked again, on the same head, for the new clock.
    world.now = t1 + WAIT
    world.poll()
    result = world.land()
    assert result.kind == "dispatched" and result.step == "escalate"
    first, second = world.entries()
    assert first["stack_since"] == T0.isoformat() and second["stack_since"] == t1.isoformat()
    assert second["head"] == first["head"] == HEAD
    assert landing_rules.already_dispatched(
        world.entries(), HEAD, "escalate", MASTER, stack_since=t1.isoformat()
    )


def test_a_wait_released_without_a_round_seeing_the_escalation_restarts_the_clock(
    monkeypatch, tmp_path
):
    """R5: the record is still the one the escalation was asked for, so it restarts at T1."""
    world = _Round(monkeypatch, tmp_path)
    world.poll()
    world.now = T0 + WAIT
    world.poll()
    assert world.land().step == "escalate"
    world.release()
    t1 = world.now = T0 + WAIT + timedelta(minutes=5)
    world.poll()
    assert _doc(world.ports)["stack_unfetchable"] == {"head": HEAD, "since": t1.isoformat()}
    assert world.land().kind != "dispatched" and len(world.entries()) == 1


def test_a_caller_that_passes_no_stack_since_reads_the_entry_as_taken(monkeypatch, tmp_path):
    """`merge.py` and `resume.py` call `already_dispatched` without the keyword: never a second
    escalation from them, whatever clock the entry carries."""
    world = _Round(monkeypatch, tmp_path)
    world.poll()
    world.now = T0 + WAIT
    world.poll()
    world.land()
    [entry] = world.entries()
    assert entry["stack_since"] == T0.isoformat()
    assert landing_rules.already_dispatched([entry], HEAD, "escalate", MASTER)
    assert landing_rules.already_dispatched(
        [entry], HEAD, "escalate", MASTER, stack_since=T0.isoformat()
    )
    other = (T0 + timedelta(days=1)).isoformat()
    assert not landing_rules.already_dispatched(
        [entry], HEAD, "escalate", MASTER, stack_since=other
    )


@pytest.mark.parametrize("expired", [None, False])
def test_step_for_without_an_expired_wait_never_carries_a_clock(expired):
    """The plan seam: `step_for` over a facts with no expiry is a `wait` with no `stack_since`."""
    facts = dataclasses.replace(
        _unread_stack(), heads_fetched=False, unfetchable_expired=expired, unfetchable_since=None
    )
    merge_facts = dataclasses.replace(_gh_facts(), stack=facts)
    step = landing_plan.step_for({}, "stack_parent_unmerged", merge_facts, turn="head")
    assert step.kind == "wait" and step.stack_since is None


def _gh_facts():
    from tests.core.process.application import gh374_shape as gh374

    return gh374.facts()


def test_pending_offers_an_expired_wait_again_after_an_entry_of_an_earlier_clock():
    """`landing_plan.pending` (the poller's candidate filter) passes the step's clock: an entry of
    clock T0 takes the escalation of T0 and not the one of T1, on the same head."""
    t1 = (T0 + WAIT + timedelta(hours=2)).isoformat()
    entry = {
        "head": HEAD,
        "step": "escalate",
        "refusal": "stack_parent_unmerged",
        "stack_since": T0.isoformat(),
    }
    raw = {"landing": [entry]}
    live = frozenset({"escalate"})

    def offered(since: str):
        facts = dataclasses.replace(
            _unread_stack(), heads_fetched=False, unfetchable_expired=True, unfetchable_since=since
        )
        merge_facts = dataclasses.replace(_gh_facts(), stack=facts, pr_head=HEAD)
        return landing_plan.pending(raw, "stack_parent_unmerged", merge_facts, live, turn="head")

    assert offered(T0.isoformat()) is None
    step = offered(t1)
    assert step is not None and step.kind == "escalate" and step.stack_since == t1
