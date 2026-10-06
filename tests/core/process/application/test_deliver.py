"""`deliver`'s pull request body (ADR-0011), and the design branch's own
"Observations on the design" section (ADR-0040): present only when at least one
`observation` design item exists, absent otherwise, byte for byte as today."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.domain import document, rules
from agentic.core.process.domain.pull_request import github_length
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_a_pull_request_with_no_design_items_is_byte_identical_to_today(cfg):
    ports = memory_ports()
    await deliver(TASK, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert body == "## PM\n\n### Consolidated definition\n\nSix numbers."
    assert "design" not in body.lower()


async def test_an_observation_item_lands_in_its_own_pr_section_blocking_items_do_not(cfg):
    state = {
        **TASK,
        "design_items": [
            {"role_slug": "ux_lead", "kind": "observation", "step": "Pay", "text": "slow"},
            {
                "role_slug": "ui_lead",
                "kind": "blocking",
                "step": "Cart",
                "text": "never here",
            },
        ],
    }
    ports = memory_ports()
    await deliver(state, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert "## Observations on the design" in body
    assert "| ux_lead | Pay | slow |" in body
    # A `blocking` item already escalated as a question for the human, or settled by
    # the integration review (`rules.return_section`'s own subtask): it never reaches
    # the pull request body as an observation.
    assert "never here" not in body


# --- GH-203: the body always fits, and a delivery that fails says so ---------------

GITHUB_LIMIT = 65_536
PATH = document.relative_path("GH-12").as_posix()


def _big_state(**extra):
    paragraphs = "\n\n".join(f"paragraph {i} " + "w" * 990 for i in range(30))
    return {
        **TASK,
        "sections": {"PM": paragraphs, "Tech Lead": "### Plan\n\n" + "t" * 70_000},
        **extra,
    }


async def test_a_body_over_the_limit_fits_and_points_to_the_committed_document(cfg):
    ports = memory_ports()
    state = _big_state()
    patch = await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert github_length(f"Closes #12\n\n{body}") < GITHUB_LIMIT
    assert "## PM\n\n" in body and "## Tech Lead" not in body
    assert "## Task document" in body
    assert f"`{PATH}` on branch `agentic/GH-12`" in body
    assert "Tech Lead" in body.split("## Task document")[1]
    # One commit of the document, before the push, and the document it commits holds the
    # section the body dropped, rendered from the state the node received.
    [(path, relatives, message)] = ports.workspace.path_commits
    assert (path, relatives, message) == (
        ports.workspace.ROOT / "GH-12",
        (PATH,),
        "GH-12: task document",
    )
    assert ports.workspace.events == ["commit_paths", "push"]
    assert ports.documents.saved[0] is state
    assert "t" * 70_000 in ports.documents.saved[0]["sections"]["Tech Lead"]
    assert ports.documents.written_into == [("GH-12", ports.workspace.ROOT / "GH-12")]
    assert patch["pull_request"] == "https://github.com/o/r/pull/7"
    assert patch["status"] == "ready_for_review"


async def test_a_small_body_writes_no_document_and_commits_nothing(cfg):
    ports = memory_ports()
    await deliver(TASK, cfg, ports)
    assert ports.workspace.path_commits == []
    assert ports.documents.written_into == []
    assert ports.workspace.ignored_checks == []
    assert ports.workspace.commits == []
    assert ports.workspace.events == ["push"]


async def test_a_product_that_ignores_the_document_gets_a_pointer_that_says_so(cfg):
    ports = memory_ports()
    ports.workspace.ignored.add(PATH)
    await deliver(_big_state(), cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert f"in the product checkout at `{PATH}`; this repository does not version it." in body
    assert "on branch" not in body
    assert ports.workspace.path_commits == []
    assert github_length(f"Closes #12\n\n{body}") < GITHUB_LIMIT


async def test_a_failing_open_pull_request_escalates_with_the_error_text(cfg):
    ports = memory_ports()
    ports.tracker.open_failures = 1
    patch = await deliver(_big_state(), cfg, ports)

    assert "gh pr create failed: Body is too long" in patch["escalated"]
    assert patch["escalated"].endswith("the branch is integrated, resume to deliver again")
    assert patch["escalation_class"] == "failure"
    assert patch["status"] == "waiting_on_human"
    assert "pull_request" not in patch
    assert ports.workspace.cleaned == []
    assert ports.tracker.comments == []
    assert rules.auto_resumable({**_big_state(), **patch}) is None
    assert patch["pause_log"] == [{"kind": "escalation", "value": "failure"}]
    assert ports.documents.last["escalated"] == patch["escalated"]


async def test_a_failing_push_escalates_and_never_opens_the_pull_request(cfg):
    ports = memory_ports()
    ports.workspace.push_failures = 1
    patch = await deliver(TASK, cfg, ports)

    assert patch["escalated"].startswith("push failed: git push -u origin failed")
    assert patch["escalation_class"] == "failure"
    assert ports.tracker.pull_requests == []
    assert rules.auto_resumable({**TASK, **patch}) is None


async def test_a_failing_document_commit_escalates_naming_the_step_and_pushes_nothing(cfg):
    ports = memory_ports()
    ports.workspace.commit_paths_error = "git commit failed: hook said no"
    patch = await deliver(_big_state(), cfg, ports)

    assert patch["escalated"].startswith("task document commit failed: git commit failed")
    assert patch["escalation_class"] == "failure"
    assert ports.workspace.pushed == [] and ports.tracker.pull_requests == []
    assert rules.auto_resumable({**_big_state(), **patch}) is None


async def test_a_failing_comment_or_cleanup_does_not_undo_the_pull_request(cfg):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    ports.workspace.cleanup_error = "git worktree remove failed: busy"
    patch = await deliver(TASK, cfg, ports)

    assert patch["pull_request"] == "https://github.com/o/r/pull/7"
    assert patch["status"] == "ready_for_review"
    assert "escalated" not in patch and "escalation_class" not in patch
    assert patch["branch"] == "agentic/GH-12"
    # The document written at the end is the one with the pull request, and no section
    # was added for what was swallowed.
    assert ports.documents.last["pull_request"] == patch["pull_request"]
    assert ports.documents.last["sections"] == TASK["sections"]


async def test_a_failed_push_then_a_second_delivery_commits_a_document_with_no_escalation(cfg):
    ports = memory_ports()
    ports.workspace.push_failures = 1
    state = _big_state()
    first = await deliver(state, cfg, ports)
    assert "push failed" in first["escalated"]

    # What the graph hands the node after a resume: the escalation cleared.
    resumed = {**state, **first, **rules.resumed({**state, **first}, None)}
    resumed.pop("pause_log")
    second = await deliver(resumed, cfg, ports)

    assert second["pull_request"] == "https://github.com/o/r/pull/7"
    committed = ports.documents.saved[2]  # first attempt's commit, its finish, the second's
    assert committed.get("escalated") is None and committed.get("escalation_class") is None
    fresh = memory_ports()
    await deliver(state, cfg, fresh)
    assert ports.tracker.pull_requests[0][2] == fresh.tracker.pull_requests[0][2]
    assert len(ports.tracker.pull_requests) == 1
