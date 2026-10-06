"""`deliver`'s pull request body (ADR-0011), and the design branch's own
"Observations on the design" section (ADR-0040): present only when at least one
`observation` design item exists, absent otherwise, byte for byte as today."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.ports import BranchPullRequest
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


# --- GH-204: a diverged remote task branch is replaced, or delivery says why not ------

BRANCH = "agentic/GH-12"
OLD = "b30329e0000000000000000000000000000000aa"
NEW = "f00dfeed0000000000000000000000000000bbbb"


def _pr(number, state):
    return BranchPullRequest(number, state, f"https://github.com/o/r/pull/{number}")


def _diverged(prs=(), tip=OLD):
    ports = memory_ports()
    ports.workspace.remote_tips[BRANCH] = tip
    ports.tracker.on_branch[BRANCH] = list(prs)
    return ports


async def test_a_diverged_remote_with_only_closed_pull_requests_is_replaced_with_a_lease(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    patch = await deliver(TASK, cfg, ports)

    assert ports.workspace.leases == [OLD]
    assert ports.workspace.pushed == [BRANCH]
    assert ports.workspace.remote_tip_calls == [(ports.workspace.ROOT / "GH-12", BRANCH)]
    body = ports.tracker.pull_requests[0][2]
    assert body.startswith(f"Replaced remote `{BRANCH}` at `{OLD}` (closed PR #95;")
    assert body.endswith("## PM\n\n### Consolidated definition\n\nSix numbers.")
    [(_, comment)] = ports.tracker.comments
    assert comment.startswith("Ready for review: ") and f"`{OLD}`" in comment
    assert patch["replaced_remote_tip"] == OLD
    assert ports.documents.last["replaced_remote_tip"] == OLD
    assert patch["status"] == "ready_for_review"


async def test_the_replaced_sha_is_saved_before_the_push_runs(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.workspace.push_failures = 1
    patch = await deliver(TASK, cfg, ports)

    assert ports.documents.saved[0]["replaced_remote_tip"] == OLD
    assert len(ports.documents.saved) == 2  # the pre-push save, then the escalation's
    assert ports.workspace.leases == [OLD]
    assert patch["escalation_class"] == "failure"
    assert "git push -u origin failed: scripted" in patch["escalated"]
    assert f"`{OLD}`" in patch["escalated"]
    assert patch["replaced_remote_tip"] == OLD
    assert ports.tracker.pull_requests == []


async def test_a_lease_the_remote_rejected_escalates_naming_the_sha_that_was_observed(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.workspace.push_failures = 1
    patch = await deliver(TASK, cfg, ports)
    assert ports.workspace.leases == [OLD] and ports.workspace.pushed == []
    assert OLD in patch["escalated"] and patch["status"] == "waiting_on_human"


@pytest.mark.parametrize(
    ("prs", "word"),
    [
        ([_pr(95, "CLOSED"), _pr(96, "OPEN")], "open pull request #96"),
        ([_pr(90, "MERGED")], "#90"),
        ([_pr(90, "MERGED"), _pr(91, "OPEN")], "open pull request #91"),
    ],
)
async def test_a_diverged_remote_with_an_open_or_merged_pull_request_is_never_pushed(
    cfg, prs, word
):
    ports = _diverged(prs)
    patch = await deliver(TASK, cfg, ports)

    assert ports.workspace.events == []  # not even a document commit, no push at all
    assert ports.workspace.leases == [] and ports.workspace.pushed == []
    assert ports.tracker.pull_requests == []
    assert patch["escalation_class"] == "failure"
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"].startswith("push refused: ")
    assert BRANCH in patch["escalated"] and OLD in patch["escalated"] and word in patch["escalated"]
    assert ports.documents.last["escalated"] == patch["escalated"]


async def test_a_diverged_remote_with_no_pull_request_needs_one_of_its_own_archives(cfg):
    unowned = _diverged([])
    refused = await deliver(TASK, cfg, unowned)
    assert unowned.workspace.leases == []
    assert "not in any archive of this task" in refused["escalated"]

    archived = _diverged([])
    archive = "archive/run1-abc1234/agentic/GH-12"
    archived.workspace.existing_branches[archive] = (OLD, "base")
    # Somebody else's archive, and a subtask's, never count.
    archived.workspace.existing_branches["archive/run1-abc1234/agentic/GH-1"] = (OLD, "base")
    archived.workspace.ancestors_by_ref = {archive: {OLD}}
    await deliver(TASK, cfg, archived)
    assert archived.workspace.leases == [OLD]
    assert archived.tracker.pull_requests[0][2].startswith(f"Replaced remote `{BRANCH}`")

    other = _diverged([])
    other.workspace.existing_branches["archive/run1-abc1234/agentic/GH-1"] = (OLD, "base")
    other.workspace.existing_branches["archive/run1-abc1234/agentic/GH-12.api"] = (OLD, "base")
    other.workspace.ancestors_by_ref = {
        "archive/run1-abc1234/agentic/GH-1": {OLD},
        "archive/run1-abc1234/agentic/GH-12.api": {OLD},
    }
    await deliver(TASK, cfg, other)
    assert other.workspace.leases == []


async def test_a_failed_pull_request_lookup_escalates_and_pushes_nothing(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.tracker.lookup_error = "gh pr list failed: rate limited"
    patch = await deliver(TASK, cfg, ports)

    assert "gh pr list failed: rate limited" in patch["escalated"]
    assert ports.workspace.events == [] and ports.workspace.leases == []
    assert patch["escalation_class"] == "failure"
    assert "replaced_remote_tip" not in patch


async def test_a_failed_remote_read_escalates_and_pushes_nothing(cfg):
    ports = memory_ports()
    ports.workspace.remote_tip_error = "git ls-remote failed: unreachable"
    patch = await deliver(TASK, cfg, ports)

    assert "git ls-remote failed: unreachable" in patch["escalated"]
    assert ports.workspace.events == [] and ports.tracker.lookups == []
    assert ports.tracker.pull_requests == []


async def test_a_first_delivery_and_a_fast_forward_never_look_up_pull_requests(cfg):
    first = memory_ports()
    await deliver(TASK, cfg, first)
    assert first.tracker.lookups == [] and first.workspace.leases == [None]

    ahead = _diverged([_pr(1, "OPEN")])
    ahead.workspace.ancestors = {OLD}
    patch = await deliver(TASK, cfg, ahead)
    assert ahead.tracker.lookups == [] and ahead.workspace.leases == [None]
    assert patch["status"] == "ready_for_review"
    assert not ahead.tracker.pull_requests[0][2].startswith("Replaced")
    assert "replaced_remote_tip" not in patch


async def test_a_replace_whose_pull_request_fails_keeps_the_sha_and_a_resume_names_it(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.tracker.open_failures = 1
    first = await deliver(TASK, cfg, ports)

    assert ports.workspace.leases == [OLD] and ports.workspace.pushed == [BRANCH]
    assert f"`{OLD}`" in first["escalated"] and first["replaced_remote_tip"] == OLD
    assert ports.documents.last["replaced_remote_tip"] == OLD

    # The resume: the remote is now our pushed head, which fast-forwards and does not
    # hold the old sha, so the plan is a plain push and the record is kept.
    resumed = {**TASK, **first, **rules.resumed({**TASK, **first}, None)}
    ports.workspace.remote_tips[BRANCH] = NEW
    ports.workspace.ancestors = {NEW}
    second = await deliver(resumed, cfg, ports)
    assert second["replaced_remote_tip"] == OLD
    assert ports.workspace.leases == [OLD, None]
    assert ports.tracker.lookups == [BRANCH]  # only the first attempt's
    body = ports.tracker.pull_requests[0][2]
    assert body.startswith(f"Replaced remote `{BRANCH}` at `{OLD}` (recover with")
    assert f"`{OLD}`" in ports.tracker.comments[0][1]


async def test_a_lost_lease_followed_by_a_plain_push_clears_the_record(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.workspace.push_failures = 1
    first = await deliver(TASK, cfg, ports)
    assert first["replaced_remote_tip"] == OLD

    # The remote is still the old sha, but now HEAD contains it: nothing was replaced.
    resumed = {**TASK, **first, **rules.resumed({**TASK, **first}, None)}
    ports.workspace.ancestors = {OLD}
    second = await deliver(resumed, cfg, ports)

    assert second["replaced_remote_tip"] is None
    assert ports.workspace.leases == [OLD, None]
    assert not ports.tracker.pull_requests[0][2].startswith("Replaced")
    assert ports.documents.last.get("replaced_remote_tip") is None
    assert "Replaced" not in ports.tracker.comments[0][1]


async def test_a_refusal_after_a_replace_clears_the_record(cfg):
    ports = _diverged([_pr(96, "OPEN")])
    state = {**TASK, "replaced_remote_tip": OLD}
    patch = await deliver(state, cfg, ports)
    assert patch["replaced_remote_tip"] is None
    assert ports.documents.last.get("replaced_remote_tip") is None


async def test_a_failing_comment_still_leaves_the_sha_in_the_pull_request_body(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    ports.tracker.comment_failures = 1
    patch = await deliver(TASK, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert ports.tracker.comments == []
    assert f"`{OLD}`" in ports.tracker.pull_requests[0][2]


async def test_a_bounded_body_keeps_the_note_and_commits_the_document_with_the_sha(cfg):
    ports = _diverged([_pr(95, "CLOSED")])
    state = _big_state()
    await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert body.startswith(f"Replaced remote `{BRANCH}` at `{OLD}`")
    assert github_length(f"Closes #12\n\n{body}") < GITHUB_LIMIT
    assert ports.workspace.events == ["commit_paths", "push"]
    assert ports.documents.saved[0]["replaced_remote_tip"] == OLD
    assert ports.workspace.leases == [OLD]
