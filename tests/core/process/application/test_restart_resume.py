"""A stopped run resumes a clean restart at the step that is missing (GH-593). Seam: `resume`
(`disagreements`, `from_document`) and the rebuild of the state from the task document, over the
in-memory ports: the document keeps what the restart wrote, a branch the archive renamed away is
not a disagreement, and a pending restart owes no publication."""

from agentic.core.process.application import resume
from agentic.core.process.domain import document, restart, rules
from agentic.core.process.domain.roles import pair_for
from agentic.core.process.domain.task import apply
from tests.fakes import MemoryWorkspace, memory_ports

PAIR = pair_for("api")
BRANCH = "agentic/GH-9.api"
HEAD = "r" * 40


def unit(name: str, **more) -> dict:
    return {
        "id": name,
        "title": name,
        "brief": f"build {name}",
        "seams": ["s"],
        "depends_on": [],
        "status": "returned",
        "branch": f"agentic/GH-9.{name}",
        "base": "base1",
        "reviewed_head": HEAD,
        **more,
    }


def restarted() -> dict:
    before = {
        "id": "GH-9",
        "title": "T",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "x"},
        "subtasks": {"api": unit("api")},
        "runs": [
            {"role": "Developer", "section": "Developer: api", "version": 1, "cost_usd": 0.1}
        ],
    }
    return apply(before, restart.restart_patch(before, "api", PAIR, "REWRITTEN", HEAD))


def test_the_document_keeps_what_the_restart_wrote_and_reads_it_back():
    state = restarted()

    back = resume.state_from(document.parse(document.render(state)))

    for key in ("restarts", "restart_pending", "session_floor", "restart_settled"):
        assert back[key] == state[key]
    assert back["subtasks"]["api"]["attempt"] == 2


def test_a_document_that_never_restarted_renders_none_of_the_restart_keys():
    text = document.render({"id": "GH-9", "title": "T", "description": "d", "subtasks": {}})

    assert "restart" not in text and "session_floor" not in text


def test_a_cleared_pending_is_not_written_back():
    state = {**restarted(), "restart_pending": None}

    back = resume.state_from(document.parse(document.render(state)))

    assert "restart_pending" not in back and back["restarts"] == {PAIR.key: 1}


def test_a_pending_restart_whose_branch_was_archived_is_not_a_disagreement():
    assert resume.disagreements(restarted(), MemoryWorkspace()) == []


def test_the_same_missing_branch_without_a_pending_restart_is_still_a_disagreement():
    state = {**restarted(), "restart_pending": None}

    problems = resume.disagreements(state, MemoryWorkspace())

    assert problems == [f"api: branch `{BRANCH}` does not exist"]


def test_a_pending_restart_of_one_subtask_does_not_excuse_another_missing_branch():
    state = restarted()
    state["subtasks"]["db"] = unit("db", status="approved")

    problems = resume.disagreements(state, MemoryWorkspace())

    assert problems == ["db: branch `agentic/GH-9.db` does not exist"]


def test_a_moved_head_of_the_restarted_subtask_is_not_a_disagreement_while_pending():
    state = restarted()
    workspace = MemoryWorkspace()
    workspace.existing_branches[BRANCH] = ("other", "base1")

    assert resume.disagreements(state, workspace) == []


def test_in_a_task_of_one_subtask_the_missing_task_branch_is_the_restart_not_a_problem():
    state = restarted()
    state["branch"] = "agentic/GH-9"
    state["subtasks"]["api"]["branch"] = "agentic/GH-9"

    assert resume.disagreements(state, MemoryWorkspace()) == []
    state["restart_pending"] = None
    assert resume.disagreements(state, MemoryWorkspace()) == [
        "task branch `agentic/GH-9` does not exist",
        "api: branch `agentic/GH-9` does not exist",
    ]


def test_from_document_resumes_a_pending_restart_and_refuses_the_same_document_without_it():
    ports = memory_ports()
    state = restarted()
    ports.documents.save(state)

    loaded = resume.from_document("GH-9", ports)

    assert loaded["restart_pending"] == {"subtask": "api", "archived_head": HEAD}
    ports.documents.save({**state, "restart_pending": None})
    try:
        resume.from_document("GH-9", ports)
    except ValueError as refused:
        assert "api: branch" in str(refused)
    else:
        raise AssertionError("a missing branch without a pending restart must be refused")


def test_no_publication_is_owed_while_a_restart_is_pending():
    head = "a" * 40
    owed = {
        "type": "feature",
        "status": "developing",
        "flags": {},
        "pull_request": "https://example/pull/1",
        "subtasks": {"integration": {"status": "approved", "reviewed_head": head}},
        "runs": [],
        "task_head": head,
        "full_checks": {
            "version": 1,
            "head": head,
            "passed": True,
            "duration_s": 1.0,
            "declared": {"tests": "pytest"},
            "results": [{"name": "tests", "passed": True}],
            "witness": {"passed": True},
        },
    }

    assert rules.publication_candidate(owed) == head
    pending = {"subtask": "api", "archived_head": HEAD}
    assert rules.publication_candidate({**owed, "restart_pending": pending}) is None
