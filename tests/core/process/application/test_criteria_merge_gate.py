"""GH-334, subtask `criteria-merge-gate`: the merge decision refuses a head that pushes a
criteria document past its cap, where it runs -- `poll.assess_merge` (through `gather_merges`
and `landing.land`) and the `agentic merge` verb. All on the in-memory ports: `MemoryWorkspace`
answers `merge_base` with `MERGE_BASE` unless scripted, and `show` from `files`."""

from __future__ import annotations

import pytest

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.application import landing, landing_plan, merge
from agentic.core.process.application.ports import Upstream
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    _finding,
    _HeldLocks,
    _merges,
    _ports,
)
from tests.fakes import MERGE_BASE

DOC = knowledge.DESIGN_STANDARDS
CAP = knowledge.CRITERIA_MAX_LINES


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def _lines(count: int) -> str:
    return "line\n" * count


def _holding(ports, base: int | None, head: int) -> None:
    """`DOC` as the merge base and the pull request's head hold it (None: absent at the base)."""
    if base is not None:
        ports.workspace.files[(MERGE_BASE, DOC)] = _lines(base)
    ports.workspace.files[(HEAD, DOC)] = _lines(head)


def _verb(ports, tmp_path):
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
    )


def test_a_document_master_already_holds_over_its_cap_does_not_refuse_a_pr_that_does_not_grow_it():
    ports = _ports(5)
    _holding(ports, base=CAP + 20, head=CAP + 20)

    assert _merges(ports) == ([5], [])


def test_a_pr_that_shrinks_an_over_cap_document_is_not_refused():
    ports = _ports(5)
    _holding(ports, base=CAP + 20, head=CAP + 5)

    assert _merges(ports) == ([5], [])


def test_a_pr_that_grows_a_document_past_the_cap_is_refused_with_the_file_and_the_count():
    ports = _ports(5)
    _holding(ports, base=CAP - 5, head=CAP + 5)

    found, lines = _merges(ports)

    assert found == []
    [line] = lines
    assert line.startswith("agentic: poll: auto-merge #5 -> left to the human: criteria_over_cap (")
    assert f"`{DOC}`: {CAP + 5} lines (cap {CAP})" in line


def test_a_document_absent_at_the_base_counts_zero():
    ports = _ports(5)
    _holding(ports, base=None, head=CAP + 1)

    assert _merges(ports)[1][0].endswith(
        f"`{DOC}`: {CAP + 1} lines (cap {CAP}), 0 at the base; "
        "cut the file or split it into `## ` sections)"
    )


def test_three_units_that_cross_the_cap_together_are_refused_at_the_merge():
    units = {
        name: {"id": name, "status": "approved", "reviewed_head": HEAD}
        for name in ("a", "b", "c", "integration")
    }
    ports = _ports(5, subtasks=units)
    # Each unit added a few lines (295 -> 305 in all): none crossed the cap alone.
    _holding(ports, base=CAP - 5, head=CAP + 5)

    found, lines = _merges(ports)

    assert found == []
    assert "left to the human: criteria_over_cap" in lines[0]


def test_documents_git_cannot_read_are_refused_as_unreadable():
    ports = _ports(5)
    ports.workspace.merge_base_error = "git merge-base failed: not available"

    assert _merges(ports) == (
        [],
        ["agentic: poll: auto-merge #5 -> left to the human: criteria_unreadable"],
    )


def test_the_criteria_are_refused_before_open_findings_and_after_a_head_nobody_reviewed():
    ports = _ports(5, findings=[_finding("blocking")])
    ports.workspace.merge_base_error = "must not be asked"
    unreviewed = _ports(6, subtasks={"only": {"id": "only", "status": "delivered"}})
    unreviewed.workspace.merge_base_error = "must not be asked"

    assert _merges(ports)[1][0].endswith("criteria_unreadable")  # blocking is behind it
    assert _merges(unreviewed)[1] == [
        "agentic: poll: auto-merge #6 -> left to the human: not_approved"
    ]
    assert unreviewed.workspace.merge_base_asks == []


def test_the_pull_request_is_read_once_and_the_base_is_taken_from_head_and_master():
    ports = _ports(5)
    _holding(ports, base=CAP - 5, head=CAP + 5)

    _merges(ports)

    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]


def test_land_leaves_a_criteria_refusal_to_the_human_and_spawns_nothing(tmp_path):
    ports = _ports(5)
    _holding(ports, base=CAP - 5, head=CAP + 5)
    spawned: list[int] = []
    lines: list[str] = []

    landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        live=frozenset({"sync_master"}),
        resume=lambda number: spawned.append(number) or 1,
    )

    assert spawned == []
    assert (ports.documents.load("GH-5") or {}).get("landing") is None


def test_the_merge_verb_refuses_on_the_same_facts_and_comments_the_files(tmp_path):
    ports = _ports(5)
    _holding(ports, base=CAP - 5, head=CAP + 5)

    result = _verb(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "criteria_over_cap")
    [(_, comment)] = ports.tracker.comments
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} criteria_over_cap"
    assert f"`{DOC}`: {CAP + 5} lines (cap {CAP})" in comment
    assert comment.endswith(merge.LEFT_TO_HUMAN)
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]


def test_the_merge_verb_refuses_unreadable_documents(tmp_path):
    ports = _ports(5)
    ports.workspace.merge_base_error = "git merge-base failed: not available"

    result = _verb(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "criteria_unreadable")


def test_the_merge_verb_merges_a_head_that_adds_nothing_past_the_cap(tmp_path):
    ports = _ports(5)
    _holding(ports, base=CAP + 20, head=CAP + 20)

    assert _verb(ports, tmp_path).kind == "merged"
