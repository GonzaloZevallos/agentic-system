"""GH-364: a task whose pipeline crashed resumes from its document, because the loop's
branches leave no checkpoint of their own. The document is written only through the loop's
folding decorator, so what is read back here is what the loop would have left."""

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import resume
from agentic.core.process.application.schedule import FoldingDocuments
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

RUN = {
    "role": "Developer",
    "section": "Developer (b)",
    "version": 1,
    "session_id": "s1",
    "model": "m",
    "cost_usd": 0.5,
    "cost_by_model": {"m": 0.5},
    "turns": 3,
}
FINDING = {
    "subtask": "a",
    "version": 1,
    "location": "x.py:1",
    "claim": "c",
    "severity": "important",
    "scope": "subtask",
    "category": "spec-behaviour",
    "status": "open",
}


def folded():
    return {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "status": "open",
        "branch": "agentic/GH-1",
        "task_head": "h-task",
        "integration_brief": "wire a into b",
        "estimate": {"cost_usd": 12.5, "files": 7},
        "cost_usd": 1.0,
        "runs": [{**RUN, "section": "Developer (a)"}],
        "findings": [FINDING],
        "subtasks": {
            "a": {
                "id": "a",
                "status": "approved",
                "branch": "t/a",
                "base": "b0",
                "reviewed_head": "h-a",
            },
            "b": {
                "id": "b",
                "status": "pending",
                "branch": "t/b",
                "base": "b0",
                "depends_on": ["a"],
            },
            "c": {"id": "c", "status": "pending", "branch": "t/c", "base": "b0"},
        },
    }


def branches(**heads):
    return {f"t/{k}": (v, "b0") for k, v in heads.items()} | {"agentic/GH-1": ("h-task", "b0")}


def saved_through_the_loop(tmp_path):
    """The document after branch `b` delivered and `c` ran, written by the decorator while
    `b` and `c` were in flight."""
    documents = FilesystemTaskDocuments(tmp_path)
    base = folded()
    decorator = FoldingDocuments(documents, base)
    b_state = apply(
        base,
        {
            # A branch patches its whole subtask (`merge` replaces per id).
            "subtasks": {"b": {**base["subtasks"]["b"], "status": "delivered"}},
            "runs": [RUN],
            "cost_usd": 0.5,
            "findings": [{**FINDING, "subtask": "b"}],
        },
    )
    decorator.branch("b", base).save(b_state)
    decorator.branch("c", base).save(
        apply(base, {"subtasks": {"c": {**base["subtasks"]["c"], "status": "pending"}}})
    )
    return documents


def ports_for(documents, **heads):
    ports = memory_ports(documents=documents)
    ports.workspace.existing_branches = branches(**heads)
    return ports


def test_every_key_the_loop_and_the_turn_read_survives_the_decorators_document(tmp_path):
    ports = ports_for(saved_through_the_loop(tmp_path), a="h-a", b="h-b", c="h-c")

    state = resume.from_document("GH-1", ports)

    assert state["subtasks"]["a"] == folded()["subtasks"]["a"]  # branch, base, reviewed_head
    assert state["subtasks"]["b"]["status"] == "delivered"
    assert state["subtasks"]["b"]["depends_on"] == ["a"]
    assert state["task_head"] == "h-task" and state["integration_brief"] == "wire a into b"
    assert state["estimate"] == {"cost_usd": 12.5, "files": 7}
    assert state["cost_usd"] == pytest.approx(1.5)
    assert [r["section"] for r in state["runs"]] == ["Developer (a)", "Developer (b)"]
    assert [f["subtask"] for f in state["findings"]] == ["a", "b"]


def test_a_crash_with_three_in_flight_and_one_approved_resumes_without_rebuilding_it(tmp_path):
    ports = ports_for(saved_through_the_loop(tmp_path), a="h-a", b="h-b", c="h-c")
    paused = resume.from_document("GH-1", ports)

    r = resume.prepare(
        "GH-1",
        paused,
        "document",
        Configuration(product_dir=tmp_path),
        ports,
        (rules.PIPELINE_NODE,),
    )

    assert (r.source, r.interrupted, r.target) == ("document", True, "pipeline")
    # `a` is approved and never rebuilt; `b` was delivered and enters at review.
    assert "a" not in rules.dispatched_subtasks(r.resumed)
    assert {"b", "c"} <= set(rules.dispatched_subtasks(r.resumed))
    assert rules.pipeline_entry({**r.resumed, "subtask_id": "b"}) == "review"
    resume.announce(r, ports)
    [(_, comment)] = ports.tracker.comments
    assert ", interrupted at " in comment


def test_a_commit_on_a_pending_subtasks_branch_does_not_stop_the_resume(tmp_path):
    ports = ports_for(saved_through_the_loop(tmp_path), a="h-a", b="h-b", c="h-c-after-the-crash")

    assert resume.from_document("GH-1", ports)["subtasks"]["c"]["status"] == "pending"


def test_an_approval_whose_reviewed_head_is_not_the_branch_head_is_refused(tmp_path):
    ports = ports_for(saved_through_the_loop(tmp_path), a="h-a-moved", b="h-b", c="h-c")

    with pytest.raises(ValueError, match=r"a: approved at h-a, but `t/a` is at h-a-moved"):
        resume.from_document("GH-1", ports)
