"""GH-859: the stacking truth table and the `stack_parent` parser, pure. Each row is a record or a
`StackFacts` built by hand and the one answer it must give."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest

from agentic.core.process.domain import stacking
from agentic.core.process.domain.stacking import (
    Absent,
    StackFacts,
    Unreadable,
    parse_stack_parent,
    parse_unfetchable,
    refusal,
    unfetchable_expired,
    unfetchable_patch,
)
from agentic.core.process.domain.task import StackParent

HEAD = "a" * 40
STARTED = "2026-11-16T10:00:00Z"
States = ("open", "closed", "unknown")


def _record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"issue": 7, "task_id": "GH-7", "head": HEAD, "started_at": STARTED}
    return {**base, **over}


def _facts(
    state: Literal["open", "closed", "unknown"] = "open",
    in_upstream: bool | None = None,
    has: bool | None = None,
) -> StackFacts:
    record = StackParent(issue=7, task_id="GH-7", head=HEAD, started_at=STARTED)
    return StackFacts(
        record=record, parent_state=state, parent_in_upstream=in_upstream, child_has_parent=has
    )


# --- the parser ------------------------------------------------------------------------------


def test_a_document_without_the_key_is_absent():
    assert isinstance(parse_stack_parent({"status": "x"}), Absent)


def test_a_valid_record_is_returned_as_it_is():
    raw = _record()
    assert parse_stack_parent({"stack_parent": raw}) == raw


def test_a_present_key_with_a_falsy_value_is_unreadable_never_absent():
    assert isinstance(parse_stack_parent({"stack_parent": None}), Unreadable)
    assert isinstance(parse_stack_parent({"stack_parent": {}}), Unreadable)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("GH-7", id="not a dict"),
        pytest.param(["issue"], id="a list"),
        pytest.param(_record(issue="7"), id="issue a string"),
        pytest.param(_record(issue=True), id="issue a bool"),
        pytest.param(_record(issue=None), id="issue none"),
        pytest.param(_record(task_id=7), id="task_id not a str"),
        pytest.param(_record(head="abc1234"), id="head abbreviated"),
        pytest.param(_record(head="z" * 40), id="head not hex"),
        pytest.param(_record(head=None), id="head none"),
        pytest.param(_record(started_at="yesterday"), id="started_at not a date"),
        pytest.param(_record(started_at=5), id="started_at not a str"),
        pytest.param({k: v for k, v in _record().items() if k != "head"}, id="head missing"),
    ],
)
def test_each_unreadable_form(value):
    assert isinstance(parse_stack_parent({"stack_parent": value}), Unreadable)


# --- the table -------------------------------------------------------------------------------


def test_an_unreadable_record_is_a_base_orphaned():
    facts = StackFacts(None, "open", True, True)
    assert refusal(facts, False) == "stack_base_orphaned"


@pytest.mark.parametrize("state", States)
@pytest.mark.parametrize("conflicts", [None, False, True])
def test_a_parent_in_upstream_and_held_by_the_child_never_refuses(state, conflicts):
    assert refusal(_facts(state, True, True), conflicts) is None


@pytest.mark.parametrize("state", States)
@pytest.mark.parametrize("conflicts", [None, False])
def test_a_parent_in_upstream_the_child_lacks_is_a_restack(state, conflicts):
    assert refusal(_facts(state, True, False), conflicts) == "stack_restack_due"


@pytest.mark.parametrize("state", States)
def test_a_restack_that_conflicts_lets_merge_conflict_answer(state):
    assert refusal(_facts(state, True, False), True) is None


@pytest.mark.parametrize("state", States)
@pytest.mark.parametrize("conflicts", [None, False, True])
def test_a_parent_in_upstream_with_unknown_ancestry_is_unmerged(state, conflicts):
    assert refusal(_facts(state, True, None), conflicts) == "stack_parent_unmerged"


@pytest.mark.parametrize("has", [True, False, None])
@pytest.mark.parametrize("conflicts", [None, False, True])
def test_a_closed_parent_not_in_upstream_is_orphaned(has, conflicts):
    assert refusal(_facts("closed", False, has), conflicts) == "stack_base_orphaned"


@pytest.mark.parametrize("state", ["open", "unknown"])
@pytest.mark.parametrize("has", [True, False, None])
@pytest.mark.parametrize("conflicts", [None, False, True])
def test_an_open_or_unknown_parent_not_in_upstream_is_unmerged(state, has, conflicts):
    assert refusal(_facts(state, False, has), conflicts) == "stack_parent_unmerged"


@pytest.mark.parametrize("state", States)
@pytest.mark.parametrize("has", [True, False, None])
@pytest.mark.parametrize("conflicts", [None, False, True])
def test_an_unknown_upstream_fact_is_unmerged(state, has, conflicts):
    assert refusal(_facts(state, None, has), conflicts) == "stack_parent_unmerged"


def test_the_only_silent_cells_are_the_two_documented_rows():
    silent = [
        (up, has, conflicts)
        for state in States
        for up in (True, False, None)
        for has in (True, False, None)
        for conflicts in (None, False, True)
        if refusal(_facts(state, up, has), conflicts) is None
    ]
    assert all(up is True and (has is True or conflicts is True) for up, has, conflicts in silent)
    assert silent


# --- named histories -------------------------------------------------------------------------


def test_a_parent_that_got_a_landing_fix_and_then_merged_gives_no_refusal():
    """The fix only added commits on the recorded head, so it is still an ancestor of upstream."""
    assert refusal(_facts("closed", True, True), False) is None


def test_a_rebuilt_parent_is_orphaned():
    """The recorded head never reached upstream and the parent's work moved on: closed."""
    assert refusal(_facts("closed", False, False), False) == "stack_base_orphaned"


def test_a_squashed_parent_is_orphaned():
    """Merged with a squash: the issue is closed, the recorded head is not an ancestor."""
    assert refusal(_facts("closed", False, True), False) == "stack_base_orphaned"


def test_the_views_are_the_table():
    assert stacking.parent_unmerged(_facts("open", False, None), False) is True
    assert stacking.parent_unmerged(_facts("closed", True, True), False) is False
    assert stacking.base_landed(_facts("closed", True, True), False) is True
    assert stacking.base_landed(_facts("open", False, None), False) is False
    assert stacking.restack_due(_facts("closed", True, False), False) is True
    assert stacking.restack_due(_facts("closed", True, False), True) is False


def test_the_placeholder_of_the_probe_is_an_unmerged_parent_whatever_the_conflict():
    """What `rules.stack_probe_wanted` substitutes holds a record and no fact: it refuses."""
    placeholder = stacking.unread_facts()
    assert placeholder.record is not None
    for conflicts in (None, False, True):
        assert refusal(placeholder, conflicts) == "stack_parent_unmerged"


# --- the builder of unread facts and the parent state (GH-860) ---------------------------------


def test_facts_of_a_document_without_the_key_is_none():
    assert stacking.facts_of({"status": "x"}) is None


def test_facts_of_a_readable_record_is_every_fact_unknown():
    record = _record()
    assert stacking.facts_of({"stack_parent": record}) == StackFacts(
        record=StackParent(**record),
        parent_state="unknown",
        parent_in_upstream=None,
        child_has_parent=None,
    )


@pytest.mark.parametrize("value", [None, "GH-7", _record(head="short"), _record(issue=True)])
def test_facts_of_an_unreadable_record_has_no_record(value):
    assert stacking.facts_of({"stack_parent": value}) == StackFacts(
        record=None, parent_state="unknown", parent_in_upstream=None, child_has_parent=None
    )


@pytest.mark.parametrize(
    ("state", "closing", "expected"),
    [
        *[("OPEN", c, "open") for c in ("contains", "excludes", "none", "unread")],
        ("CLOSED", "contains", "unknown"),
        ("CLOSED", "excludes", "closed"),
        ("CLOSED", "none", "closed"),
        ("CLOSED", "unread", "unknown"),
        *[("closed", c, "unknown") for c in ("contains", "excludes", "none", "unread")],
        *[("unknown", c, "unknown") for c in ("contains", "excludes", "none", "unread")],
    ],
)
def test_parent_state_of(state, closing, expected):
    assert stacking.parent_state_of(state, closing) == expected


# --- the unfetchable-head clock (GH-885) -----------------------------------------------------

PR_HEAD = "b" * 40
OTHER_HEAD = "c" * 40
T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
WAIT = timedelta(hours=12)


def _record_at(since: datetime = T0, head: str = PR_HEAD) -> dict[str, str]:
    return {"head": head, "since": since.isoformat()}


def _fetched(
    value: bool | None, state: Literal["open", "closed", "unknown"] = "open"
) -> StackFacts:
    return replace(_facts(state), heads_fetched=value)


def _start(now: datetime = T0, head: str = PR_HEAD) -> dict[str, Any]:
    return {"stack_unfetchable": {"head": head, "since": now.isoformat()}}


CLEAR: dict[str, Any] = {"stack_unfetchable": None}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({}, id="empty"),
        pytest.param("x", id="not a mapping"),
        pytest.param({"head": 5, "since": T0.isoformat()}, id="head not a str"),
        pytest.param({"head": PR_HEAD}, id="since missing"),
        pytest.param({"head": PR_HEAD, "since": "yesterday"}, id="since not a date"),
        pytest.param({"head": PR_HEAD, "since": 5}, id="since not a str"),
        pytest.param({"head": PR_HEAD, "since": "2026-11-17T08:00:00"}, id="naive since"),
    ],
)
def test_an_unreadable_unfetchable_record_is_unreadable(value):
    assert isinstance(parse_unfetchable({"stack_unfetchable": value}), Unreadable)


def test_a_missing_or_none_unfetchable_record_is_no_record():
    assert parse_unfetchable({}) is None
    assert parse_unfetchable({"stack_unfetchable": None}) is None


def test_a_valid_unfetchable_record_is_returned_as_it_is():
    raw = _record_at()
    assert parse_unfetchable({"stack_unfetchable": raw}) == raw


def test_escalated_clears_a_record_and_an_absent_one_writes_nothing():
    raw = {"escalated": True, "stack_unfetchable": _record_at()}
    assert unfetchable_patch(raw, _fetched(False), PR_HEAD, T0) == CLEAR
    assert unfetchable_patch({"escalated": True}, _fetched(False), PR_HEAD, T0) == {}
    assert (
        unfetchable_patch(
            {"escalated": True, "stack_unfetchable": None}, _fetched(False), PR_HEAD, T0
        )
        == {}
    )


def test_fetched_clears_and_clearing_twice_writes_nothing():
    raw: dict[str, Any] = {"stack_unfetchable": _record_at()}
    patch = unfetchable_patch(raw, _fetched(True), PR_HEAD, T0)
    assert patch == CLEAR
    assert unfetchable_patch({**raw, **patch}, _fetched(True), PR_HEAD, T0) == {}
    assert unfetchable_patch({}, _fetched(True), PR_HEAD, T0) == {}


def test_heads_fetched_none_changes_nothing():
    for raw in ({}, {"stack_unfetchable": _record_at()}, {"stack_unfetchable": {}}):
        assert unfetchable_patch(raw, _fetched(None), PR_HEAD, T0 + WAIT) == {}


@pytest.mark.parametrize("state", States)
def test_each_parent_state_starts_the_clock(state):
    assert unfetchable_patch({}, _fetched(False, state), PR_HEAD, T0) == _start()


def test_a_new_head_that_is_still_unfetchable_restarts_the_clock():
    raw = {"stack_unfetchable": _record_at(head=OTHER_HEAD)}
    later = T0 + timedelta(hours=3)
    assert unfetchable_patch(raw, _fetched(False), PR_HEAD, later) == _start(later)


def test_a_standing_record_on_the_same_head_changes_nothing():
    raw = {"stack_unfetchable": _record_at()}
    assert unfetchable_patch(raw, _fetched(False), PR_HEAD, T0 + WAIT) == {}


def test_no_pr_head_writes_nothing():
    assert unfetchable_patch({}, _fetched(False), None, T0) == {}


def _released(since: datetime = T0) -> dict[str, Any]:
    entry = {
        "head": PR_HEAD,
        "step": "escalate",
        "refusal": "stack_parent_unmerged",
        "stack_since": since.isoformat(),
    }
    return {"stack_unfetchable": _record_at(since), "landing": [entry]}


def test_a_released_wait_restarts_once_and_the_next_call_holds():
    raw = _released()
    later = T0 + WAIT
    patch = unfetchable_patch(raw, _fetched(False), PR_HEAD, later)
    assert patch == _start(later)
    assert unfetchable_patch({**raw, **patch}, _fetched(False), PR_HEAD, later) == {}


def test_an_escalate_entry_of_another_clock_does_not_restart():
    raw = _released(since=T0 - WAIT)
    raw["stack_unfetchable"] = _record_at()
    assert unfetchable_patch(raw, _fetched(False), PR_HEAD, T0) == {}


def test_an_entry_hidden_by_a_restoration_does_not_restart():
    raw = _released()
    raw["restorations"] = [{"landing_cut": 1}]
    assert unfetchable_patch(raw, _fetched(False), PR_HEAD, T0 + WAIT) == {}


def test_a_naive_since_restarts_once_then_holds():
    raw: dict[str, Any] = {"stack_unfetchable": {"head": PR_HEAD, "since": "2026-11-17T08:00:00"}}
    patch = unfetchable_patch(raw, _fetched(False), PR_HEAD, T0)
    assert patch == _start()
    assert unfetchable_patch({**raw, **patch}, _fetched(False), PR_HEAD, T0) == {}


def test_expiry_is_exactly_at_twelve_hours():
    record = parse_unfetchable({"stack_unfetchable": _record_at()})
    assert unfetchable_expired(record, T0 + WAIT) is True
    assert unfetchable_expired(record, T0 + WAIT - timedelta(seconds=1)) is False


def test_no_record_or_an_unreadable_one_never_expires():
    assert unfetchable_expired(None, T0 + WAIT) is False
    assert unfetchable_expired(Unreadable(), T0 + WAIT) is False
