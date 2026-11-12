"""GH-611, the owner's rule: `landing_plan.release` frees an unanswered numeric `escalate_adr`
question without a human once the stack at the upstream declares `adr_naming: dated`, once per
(head, upstream) pair, and never when the stack says numbered or cannot be read as dated."""

from __future__ import annotations

from agentic.core.process.application import landing_plan
from agentic.core.process.application.ports import IssueState, Upstream
from agentic.core.process.domain import landing
from agentic.shared.configuration import Configuration
from tests.core.process.domain.test_landing_dated import HEAD, UPSTREAM, _escalated_document
from tests.fakes import MemoryStack, MemoryTracker, memory_ports

CFG = Configuration(product_dir="/p")
AT = "2026-11-12T11:00:00+00:00"


class _UnreadableStack(MemoryStack):
    def adr_naming(self, revision):
        self.adr_naming_reads.append(revision)
        raise ValueError("adr_naming: 'dates'")


def _world(stack):
    ports = memory_ports(tracker=MemoryTracker(), stack=stack)
    ports.tracker.states[9] = IssueState("OPEN", "", (), None, ())
    ports.workspace.upstream_answer = Upstream("origin/master", UPSTREAM, None)
    return ports


def _release(ports, raw=None, upstream=UPSTREAM):
    return landing_plan.release(
        ports, CFG, "GH-9", raw or _escalated_document(), 9, head=HEAD, upstream=upstream
    )


def test_a_dated_stack_at_the_upstream_releases_the_numeric_question_with_no_decision():
    ports = _world(MemoryStack(adr_naming="dated"))

    found = _release(ports)

    assert (found.released, found.why, found.decision) == (True, "released_by_naming", None)
    assert ports.stack.adr_naming_reads == [UPSTREAM]  # read at the upstream, nowhere else


def test_the_release_patch_stamps_the_upstream_and_asks_nobody():
    raw = _escalated_document()
    found = _release(_world(MemoryStack(adr_naming="dated")), raw)

    patch = landing_plan.release_patch(raw, found, UPSTREAM, AT)

    [entry] = patch["landing"]
    assert entry["naming_released"] == UPSTREAM and entry["released_at"] == AT
    assert patch["escalated"] is None and "sections" not in patch


def test_the_same_upstream_is_released_once():
    raw = _escalated_document()
    ports = _world(MemoryStack(adr_naming="dated"))
    patch = landing_plan.release_patch(raw, _release(ports, raw), UPSTREAM, AT)
    again = {**raw, "landing": patch["landing"], "escalated": "ADR question"}

    assert not _release(ports, again).released


def test_a_numbered_stack_releases_nothing():
    found = _release(_world(MemoryStack(adr_naming="numbered")))
    assert not found.released and found.why != "released_by_naming"


def test_a_stack_that_cannot_be_read_as_dated_releases_nothing():
    found = _release(_world(_UnreadableStack()))
    assert not found.released and found.why != "released_by_naming"


def test_a_question_without_numeric_moves_is_not_released_by_naming():
    ports = _world(MemoryStack(adr_naming="dated"))
    found = _release(ports, _escalated_document(moves=False))
    assert found.why != "released_by_naming"
    assert landing.numeric_adr_question(_escalated_document(moves=False)) is None
