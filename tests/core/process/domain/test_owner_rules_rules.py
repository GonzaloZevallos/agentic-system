"""The process predicates of the owner-rules step (GH-431): when the `owner_rules` node is due
(`rules.owner_rules_due`, by observing the head, never by a record of rules written) and how the
close-time commit record grows (`rules.extend_close_docs`). Pure: states and ids in, a bool or a
record out. The document round-trip of the two state fields is checked with the same states."""

from typing import get_type_hints

import pytest

from agentic.core.process.domain import document, rules
from agentic.core.process.domain.task import TaskState

HEAD = "a" * 40
MOVED = "b" * 40
DOCS = "d" * 40
LATER = "e" * 40
NEXT = "f" * 40


def unit(status="approved", reviewed_head=HEAD):
    return {"status": status, "reviewed_head": reviewed_head}


def state(**more):
    base = {
        "type": "feature",
        "flags": {},
        "subtasks": {"api": unit()},
        "runs": [],
    }
    return {**base, **more}


# --- owner_rules_due --------------------------------------------------------------------------


def test_a_single_subtask_task_is_due_on_its_reviewed_head():
    assert rules.owner_rules_due(state())


def test_the_integration_is_the_reviewed_unit_when_there_is_one():
    several = state(
        subtasks={"api": unit(reviewed_head=MOVED), "integration": unit(reviewed_head=HEAD)}
    )
    assert rules.owner_rules_due(several)
    assert not rules.owner_rules_due({**several, "owner_rules_seen": HEAD})


def test_the_task_head_wins_over_the_reviewed_head_when_set():
    seen_reviewed = state(task_head=MOVED, owner_rules_seen=HEAD)
    assert rules.owner_rules_due(seen_reviewed)
    assert not rules.owner_rules_due(state(task_head=MOVED, owner_rules_seen=MOVED))


def test_a_head_already_seen_is_not_due_whatever_the_length_of_the_ids():
    assert not rules.owner_rules_due(state(owner_rules_seen=HEAD))
    assert not rules.owner_rules_due(state(owner_rules_seen=HEAD[:12]))


def test_a_head_change_makes_it_due_again():
    seen = state(owner_rules_seen=HEAD)
    assert not rules.owner_rules_due(seen)
    moved = state(owner_rules_seen=HEAD, task_head=MOVED)
    assert rules.owner_rules_due(moved)


def test_a_return_moving_the_reviewed_head_makes_it_due_again():
    returned = state(owner_rules_seen=HEAD, subtasks={"api": unit(reviewed_head=MOVED)})
    assert rules.owner_rules_due(returned)


@pytest.mark.parametrize(
    "unapproved",
    [
        state(subtasks={"api": unit(status="delivered")}),
        state(subtasks={"api": unit(), "web": unit(status="returned")}),
        state(subtasks={}),
    ],
    ids=["delivered", "one-open", "no-subtasks"],
)
def test_it_waits_until_every_unit_is_approved(unapproved):
    assert not rules.owner_rules_due(unapproved)


def test_with_no_head_to_observe_nothing_is_due():
    assert not rules.owner_rules_due(state(subtasks={"api": {"status": "approved"}}))


def test_the_important_pass_goes_first(monkeypatch):
    monkeypatch.setattr(rules, "pre_pr_important_due", lambda _state: True)
    assert not rules.owner_rules_due(state())


# --- extend_close_docs ------------------------------------------------------------------------


def record(parent=HEAD, head=DOCS, paths=("docs/product/a.md",)):
    return {"parent": parent, "head": head, "paths": list(paths)}


def test_a_first_commit_starts_a_record_on_the_reviewed_head():
    got = rules.extend_close_docs(None, HEAD, DOCS, ["docs/decisiones-del-dueño.md"])
    assert got == {"parent": HEAD, "head": DOCS, "paths": ["docs/decisiones-del-dueño.md"]}


def test_a_record_on_the_same_reviewed_head_is_extended_not_replaced():
    previous = record(paths=["docs/product/a.md"])
    got = rules.extend_close_docs(previous, HEAD, LATER, ["docs/decisiones-del-dueño.md"])
    assert got == {
        "parent": HEAD,
        "head": LATER,
        "paths": ["docs/product/a.md", "docs/decisiones-del-dueño.md"],
    }
    assert previous["head"] == DOCS  # the argument is not mutated


def test_a_path_already_recorded_is_not_listed_twice():
    previous = record(paths=["docs/product/a.md", "docs/decisiones-del-dueño.md"])
    got = rules.extend_close_docs(previous, HEAD[:12], LATER, ["docs/decisiones-del-dueño.md"])
    assert got is not None
    assert got["paths"] == ["docs/product/a.md", "docs/decisiones-del-dueño.md"]


def test_a_record_on_a_stale_parent_is_discarded():
    """The reviewed head moved: the earlier commit sits on another head and covers nothing."""
    stale = record(parent=MOVED, paths=["docs/product/old.md"])
    got = rules.extend_close_docs(stale, HEAD, LATER, ["docs/decisiones-del-dueño.md"])
    assert got == {"parent": HEAD, "head": LATER, "paths": ["docs/decisiones-del-dueño.md"]}


def test_without_a_head_there_is_no_record():
    assert rules.extend_close_docs(record(), HEAD, None, ["docs/decisiones-del-dueño.md"]) is None
    assert rules.extend_close_docs(None, HEAD, None, []) is None


def test_the_extended_record_covers_the_new_head_through_head_reviewed():
    got = rules.extend_close_docs(
        record(), HEAD, LATER, ["docs/decisiones-del-dueño.md", "docs/orquestacion.md"]
    )
    assert not rules.head_reviewed(HEAD, got, LATER)  # orquestacion.md is no close path
    ok = rules.extend_close_docs(record(), HEAD, LATER, ["docs/decisiones-del-dueño.md"])
    assert rules.head_reviewed(HEAD, ok, LATER)


# --- the document round-trip of the two fields ------------------------------------------------


def test_the_fields_are_part_of_the_state():
    assert {"owner_rules_seen", "owner_rules_elsewhere"} <= set(get_type_hints(TaskState))


def test_the_two_fields_round_trip_and_are_absent_when_unset():
    base = {"id": "T-1", "title": "t", "description": "d", "status": "in_progress"}
    plain = document.render({**state(), **base})
    assert "owner_rules" not in plain
    elsewhere = [{"rule": "No mezclar productos", "cls": "system"}]
    with_both = state(**base, owner_rules_seen=HEAD, owner_rules_elsewhere=elsewhere)
    text = document.render(with_both)
    back = document.parse(text)
    assert back["owner_rules_seen"] == HEAD
    assert back["owner_rules_elsewhere"] == elsewhere
