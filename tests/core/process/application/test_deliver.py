"""`deliver`'s pull request body (ADR-0011), and the design branch's own
"Observations on the design" section (ADR-0040): present only when at least one
`observation` design item exists, absent otherwise, byte for byte as today."""

from datetime import datetime

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.ports import BranchPullRequest, GitError
from agentic.core.process.domain import document, rules
from agentic.core.process.domain.pull_request import github_length
from agentic.shared.configuration import Configuration
from tests.fakes import FakeReaper, MemoryStack, memory_ports

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


# --- GH-365: the checks over budget, only when the full run recorded some ---------------


def _full_checks(**more):
    return {
        "version": 1,
        "head": "abc",
        "passed": True,
        "duration_s": 9.0,
        "declared": {"tests": "pytest", "lint": "ruff check ."},
        "results": [{"name": "tests", "passed": True}, {"name": "lint", "passed": True}],
        "witness": None,
        **more,
    }


async def test_the_body_lists_the_checks_over_budget_exactly_when_the_record_in_force_has_some(cfg):
    breaches = [
        {"name": "tests", "duration_s": 61.5, "budget_s": 60.0},
        {"name": "lint", "duration_s": 9.0, "budget_s": 5.0},
    ]
    ports = memory_ports()
    await deliver({**TASK, "full_checks": _full_checks(over_budget=breaches)}, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert "## Checks over budget" in body
    assert "| Check | Seconds | Budget (s) |" in body
    assert "| tests | 61.5 | 60.0 |" in body
    assert "| lint | 9.0 | 5.0 |" in body
    assert "repeats across tasks" in body and "`stack.yaml`" in body


async def test_a_record_without_over_budget_leaves_the_body_as_it_was(cfg):
    plain = memory_ports()
    await deliver(TASK, cfg, plain)
    within = memory_ports()
    await deliver({**TASK, "full_checks": _full_checks()}, cfg, within)
    assert within.tracker.pull_requests[0][2] == plain.tracker.pull_requests[0][2]
    assert "budget" not in within.tracker.pull_requests[0][2].lower()


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


async def test_a_body_over_the_limit_fits_and_points_to_the_product_checkouts_document(cfg):
    ports = memory_ports()
    state = _big_state()
    patch = await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert github_length(f"Closes #12\n\n{body}") < GITHUB_LIMIT
    assert "## PM\n\n" in body and "## Tech Lead" not in body
    assert "## Task document" in body
    assert f"in the product checkout at `{PATH}`; the system does not version it." in body
    assert "on branch" not in body and "agentic/GH-12" not in body
    assert "Tech Lead" in body.split("## Task document")[1]
    # The delivery commits nothing: the document is saved in the product checkout before
    # the push, holding the section the body dropped, rendered from the state it received.
    assert ports.workspace.path_commits == []
    assert ports.workspace.commits == []
    assert ports.workspace.events == ["push"]
    assert ports.documents.saved[0] is state
    assert "t" * 70_000 in ports.documents.saved[0]["sections"]["Tech Lead"]
    assert ports.documents.written_into == []
    assert ports.workspace.ignored_checks == []
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
THIRD = "c0ffee00000000000000000000000000000000cc"


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
    ports.tracker.on_lookup = lambda _branch: ports.workspace.remote_tips.__setitem__(BRANCH, THIRD)
    first = await deliver(TASK, cfg, ports)

    assert ports.workspace.leases == [OLD]
    assert ports.workspace.pushed == []
    assert ports.workspace.remote_tips[BRANCH] == THIRD
    assert ports.workspace.push_failures == 0
    assert first["escalation_class"] == "failure"
    assert first["status"] == "waiting_on_human"
    assert "git push -u origin failed" in first["escalated"]
    assert "stale info (lease)" in first["escalated"]
    assert f"`{OLD}`" in first["escalated"]
    assert first["replaced_remote_tip"] == OLD
    assert ports.tracker.pull_requests == []
    assert ports.documents.last["escalated"] == first["escalated"]

    # The resume re-observes the moved tip and leases that one, never pushing blind.
    ports.tracker.on_lookup = None
    resumed = {**TASK, **first, **rules.resumed({**TASK, **first}, None)}
    second = await deliver(resumed, cfg, ports)

    assert ports.workspace.leases == [OLD, THIRD]
    assert ports.workspace.pushed == [BRANCH]
    assert ports.tracker.lookups == [BRANCH, BRANCH]
    assert second["replaced_remote_tip"] == THIRD
    assert ports.documents.last["replaced_remote_tip"] == THIRD
    body = ports.tracker.pull_requests[0][2]
    assert body.startswith(f"Replaced remote `{BRANCH}` at `{THIRD}`")
    assert f"`{OLD}`" not in body
    assert f"`{OLD}`" not in ports.tracker.comments[0][1]


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
    assert ports.workspace.events == ["push"]
    assert ports.documents.saved[0]["replaced_remote_tip"] == OLD
    assert ports.workspace.leases == [OLD]


async def test_the_pushed_head_and_time_are_saved_and_nothing_is_committed_after_the_push(cfg):
    ports = memory_ports()
    ports.workspace.moved.add(ports.workspace.ROOT / "GH-12")
    state = _big_state()  # a body that commits the document before the push

    patch = await deliver(state, cfg, ports)

    assert ports.workspace.events == ["push"]  # no commit after the push
    assert ports.workspace.commits == []
    # The first look at the task checkout after the last commit, taken before the push.
    assert patch["pushed_head"] == "head1"
    assert datetime.fromisoformat(patch["pushed_at"]).tzinfo is not None
    saved = ports.documents.load("GH-12") or {}
    assert (saved["pushed_head"], saved["pushed_at"]) == (patch["pushed_head"], patch["pushed_at"])


async def test_a_failed_push_saves_neither_key(cfg):
    ports = memory_ports()
    ports.workspace.push_failures = 1

    patch = await deliver(TASK, cfg, ports)

    assert "pushed_head" not in patch and "pushed_at" not in patch
    saved = ports.documents.load("GH-12") or {}
    assert "pushed_head" not in saved and "pushed_at" not in saved


async def test_a_redelivery_with_a_landing_list_survives_the_record_closure_after_finish(
    cfg, tmp_path
):
    """GH-242: `finish` saves the document -- `landing` rendered by the writer -- and the
    `record_closure` that follows rewrites it; on the real adapter neither raises and the
    entry keeps its resume keys and cost base."""
    ports = memory_ports(documents=FilesystemTaskDocuments(tmp_path))
    entry = {
        "head": "a" * 40,
        "step": "re_review",
        "refusal": "head_not_reviewed",
        "at": "t",
        "cost_base": 1.0,
        "resume_spawns": 1,
        "resume_spawned_at": "2026-10-14T12:00:00+00:00",
    }
    state = {
        **TASK,
        "pull_request": "https://github.com/o/r/pull/12",
        "landing": [entry],
        "cost_usd": 3.5,
    }

    await deliver(state, cfg, ports)

    [closed] = ports.documents.load("GH-12")["landing"]
    assert closed["resolved_at"]
    assert closed["cost_usd"] == pytest.approx(2.5)
    assert closed["cost_base"] == 1.0
    assert closed["resume_spawns"] == 1
    assert closed["resume_spawned_at"] == "2026-10-14T12:00:00+00:00"


# --- GH-267: a sync is resolved only when the delivered head holds its upstream ----

UPSTREAM = "b" * 40
HEAD = "a" * 40


def _landing_state(**entry_extra):
    entry = {
        "head": HEAD,
        "step": "sync_master",
        "refusal": "merge_conflict",
        "at": "t",
        "cost_base": 1.0,
        **entry_extra,
    }
    return {
        **TASK,
        "pull_request": "https://github.com/o/r/pull/12",
        "landing": [entry],
        "cost_usd": 3.5,
    }


async def test_a_sync_whose_head_lacks_its_upstream_is_closed_not_resolved_and_still_delivered(
    cfg,
):
    ports = memory_ports()
    state = _landing_state(upstream=UPSTREAM)

    patch = await deliver(state, cfg, ports)

    [closed] = patch["landing"]
    assert "resolved_at" not in closed
    assert closed["closed_at"]
    assert closed["lacks"] == UPSTREAM
    assert closed["cost_usd"] == pytest.approx(2.5)
    assert patch["status"] == "ready_for_review"
    assert ports.workspace.pushes


async def test_a_sync_whose_head_holds_its_upstream_is_resolved(cfg):
    ports = memory_ports()
    ports.workspace.ancestors.add(UPSTREAM)

    patch = await deliver(_landing_state(upstream=UPSTREAM), cfg, ports)

    [resolved] = patch["landing"]
    assert resolved["resolved_at"]
    assert "lacks" not in resolved and "closed_at" not in resolved


async def test_the_upstream_the_entry_was_dispatched_for_governs_not_the_one_master_has_now(
    cfg,
):
    ports = memory_ports()
    ports.workspace.ancestors.add("c" * 40)  # master moved on and the head holds only that

    patch = await deliver(_landing_state(upstream=UPSTREAM), cfg, ports)

    assert patch["landing"][0]["lacks"] == UPSTREAM
    assert (ports.workspace.ancestor_checks[-1][1:]) == (UPSTREAM, "HEAD")


async def test_an_ancestry_that_cannot_be_read_closes_the_sync_with_lacks(cfg):
    ports = memory_ports()
    ports.workspace.ancestor_errors.add(UPSTREAM)

    patch = await deliver(_landing_state(upstream=UPSTREAM), cfg, ports)

    assert patch["landing"][0]["lacks"] == UPSTREAM
    assert "resolved_at" not in patch["landing"][0]
    assert patch["status"] == "ready_for_review"


@pytest.mark.parametrize("step", ["re_review", "fix"])
async def test_a_step_that_is_not_a_sync_resolves_after_redelivery_as_before(cfg, step):
    ports = memory_ports()
    state = _landing_state(upstream=UPSTREAM)
    state["landing"][0]["step"] = step

    patch = await deliver(state, cfg, ports)

    assert patch["landing"][0]["resolved_at"]
    assert "lacks" not in patch["landing"][0]


async def test_a_legacy_sync_entry_without_upstream_resolves_as_before(cfg):
    ports = memory_ports()

    patch = await deliver(_landing_state(), cfg, ports)

    assert patch["landing"][0]["resolved_at"]
    assert "lacks" not in patch["landing"][0]


REAPED = ({"pid": 7, "command": "uv run sample_app.py", "outcome": "kill"},)


async def test_what_the_release_reaped_is_in_the_patch_and_the_saved_document(cfg):
    ports = memory_ports(reaper=FakeReaper(REAPED))

    patch = await deliver(TASK, cfg, ports)

    assert patch["reaped_at_cleanup"] == list(REAPED)
    assert (ports.documents.load("GH-12") or {})["reaped_at_cleanup"] == list(REAPED)
    assert ports.workspace.cleaned == ["GH-12"]


async def test_nothing_reaped_leaves_the_key_out_of_the_patch_and_the_document(cfg):
    ports = memory_ports()

    patch = await deliver(TASK, cfg, ports)

    assert "reaped_at_cleanup" not in patch
    assert "reaped_at_cleanup" not in (ports.documents.load("GH-12") or {})


async def test_a_cleanup_that_fails_still_delivers_after_the_reap_was_asked(cfg):
    reaper = FakeReaper(REAPED)
    ports = memory_ports(reaper=reaper)
    ports.workspace.cleanup_error = "worktree locked"

    patch = await deliver(TASK, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert reaper.calls == [("task", "GH-12", None, None)]


# --- GH-295: criteria documents past the line cap are said in the body and the record ---

BRANCH_REF = rules.task_branch("GH-12")
NO_CRITERIA_BODY = "## PM\n\n### Consolidated definition\n\nSix numbers."


def _doc(count: int) -> str:
    return "".join(f"rule {n}\n" for n in range(count))


async def test_a_product_with_no_criteria_files_has_a_body_identical_to_today(cfg):
    """The system default `CODING_STANDARDS.md` is 90 lines and is never measured."""
    ports = memory_ports()

    patch = await deliver(TASK, cfg, ports)

    assert ports.tracker.pull_requests[0][2] == NO_CRITERIA_BODY
    assert "criteria_over_cap" not in patch
    assert "criteria_over_cap" not in (ports.documents.load("GH-12") or {})


async def test_a_file_over_the_cap_at_the_head_is_in_the_body_and_the_record_not_the_base(cfg):
    ports = memory_ports()
    ports.workspace.files[("HEAD", "CODING_STANDARDS.md")] = _doc(200)
    ports.workspace.files[(BRANCH_REF, "CODING_STANDARDS.md")] = _doc(301)

    patch = await deliver(TASK, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert "## Criteria over cap" in body and "CODING_STANDARDS.md" in body and "301" in body
    assert NO_CRITERIA_BODY in body
    assert len(patch["criteria_over_cap"]) == 1
    saved = ports.documents.load("GH-12") or {}
    assert saved["criteria_over_cap"] == patch["criteria_over_cap"]


async def test_the_block_comes_after_the_guarded_changes_block(cfg):
    ports = memory_ports()
    ports.workspace.diff_paths = ("CODING_STANDARDS.md",)
    ports.workspace.files[(BRANCH_REF, "DESIGN_STANDARDS.md")] = _doc(301)

    await deliver(TASK, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert body.index("## Guarded changes") < body.index("## Criteria over cap")


async def test_exactly_the_cap_adds_no_line(cfg):
    ports = memory_ports()
    ports.workspace.files[(BRANCH_REF, "CODING_STANDARDS.md")] = _doc(300)

    patch = await deliver(TASK, cfg, ports)

    assert ports.tracker.pull_requests[0][2] == NO_CRITERIA_BODY
    assert "criteria_over_cap" not in patch


async def test_a_file_the_delivery_deleted_is_not_measured(cfg):
    """Over the cap at the base, gone at the head: `show` reads None there."""
    ports = memory_ports()
    ports.workspace.files[("HEAD", "DESIGN_STANDARDS.md")] = _doc(400)

    patch = await deliver(TASK, cfg, ports)

    assert ports.tracker.pull_requests[0][2] == NO_CRITERIA_BODY
    assert "criteria_over_cap" not in patch


async def test_a_head_that_cannot_be_read_delivers_with_no_block(cfg):
    ports = memory_ports()

    def unreadable(ref, relative):
        raise GitError("bad object")

    ports.workspace.show = unreadable

    patch = await deliver(TASK, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert ports.tracker.pull_requests[0][2] == NO_CRITERIA_BODY


# --- GH-318: a convened task whose QA Performance run is `unverified` says so ------------

QA_SECTION = (
    "### Measurements\n\n"
    "| scenario | metric | median | limit | outcome | reason | spread | load |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| list | wall_ms | 180 | 200ms | inconclusive | spread | 0.45 | 0.3 -> 0.4 |\n\n"
    "Not a table line."
)
PERF_PLAN = [
    {
        "name": "list",
        "volume": 100,
        "volume_unit": "rows",
        "basis": "b",
        "budgets": [
            {"metric": "wall_ms", "limit": 200, "unit": "ms", "source": "issue", "basis": "b"}
        ],
    }
]


def _perf_state(verdict, *, performance=PERF_PLAN):
    run = {
        "role": "QA Performance",
        "cost_usd": 0.1,
        "perf_verdict": verdict,
        "perf_head": "a" * 40,
    }
    return {
        **TASK,
        "performance": performance,
        "runs": [run],
        "sections": {**TASK["sections"], "QA Performance": QA_SECTION},
    }


async def test_an_unverified_convened_task_gets_the_heading_the_table_rows_and_a_comment(cfg):
    ports = memory_ports()
    await deliver(_perf_state("unverified"), cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert "### Performance not verified" in body
    assert "| list | wall_ms | 180 | 200ms | inconclusive | spread | 0.45 | 0.3 -> 0.4 |" in body
    assert "Not a table line." not in body
    assert body.index("### Performance not verified") < body.index("## PM")
    notes = [text for _, text in ports.tracker.comments if "Performance not verified" in text]
    assert len(notes) == 1 and notes[0].splitlines()[-1].startswith("Performance not verified")
    assert sum(line.startswith("Performance not verified") for line in notes[0].splitlines()) == 1
    assert ports.tracker.labels == [] and not any(ports.tracker.opened_labels)


@pytest.mark.parametrize(
    "state",
    [
        _perf_state("approved"),
        _perf_state("returned"),
        _perf_state("unverified", performance=[]),
    ],
    ids=["approved", "returned", "not_convened"],
)
async def test_any_other_task_is_delivered_byte_for_byte_as_today(cfg, state):
    ports = memory_ports()
    await deliver(state, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert "Performance not verified" not in body
    assert all("Performance not verified" not in text for _, text in ports.tracker.comments)
    assert body == "## PM\n\n### Consolidated definition\n\nSix numbers."


async def test_an_unverified_run_with_no_table_still_says_so(cfg):
    state = _perf_state("unverified")
    state["sections"] = TASK["sections"]
    ports = memory_ports()
    await deliver(state, cfg, ports)
    assert "### Performance not verified" in ports.tracker.pull_requests[0][2]


# --- GH-336: a plan with a tracker checklist posts it, then opens the PR (GH-344: it closes) ---

CHECKLIST = ["Post the verdict table on #12", "Close the follow-ups that are done"]
WITH_CHECKLIST = {**TASK, "tracker_checklist": CHECKLIST}


def _resumed(state, patch):
    """What the graph hands the node after a resume: the escalation cleared."""
    merged = {**state, **patch}
    resumed = {**merged, **rules.resumed(merged, None)}
    resumed.pop("pause_log", None)
    return resumed


async def test_a_checklist_is_posted_once_before_the_pull_request_that_closes_the_issue(cfg):
    ports = memory_ports()
    patch = await deliver(WITH_CHECKLIST, cfg, ports)

    posted = [text for _, text in ports.tracker.comments if text.startswith("Tracker work for you")]
    assert len(posted) == 1
    # The checklist comment comes first; the "Ready for review" one follows the pull request.
    assert ports.tracker.comments[0][1].startswith("Tracker work for you")
    assert ports.tracker.comments[1][1].startswith("Ready for review")
    assert ports.tracker.closes_issue_calls == [True]  # GH-344: was [False] (ADR-0062 §4)
    assert len(ports.tracker.pull_requests) == 1
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(CHECKLIST)
    assert ports.documents.saved[0]["tracker_checklist_posted"] == patch["tracker_checklist_posted"]


async def test_a_split_part_with_a_checklist_opens_its_pull_request_with_the_closing_line(cfg):
    """GH-348, apostrophe #377's shape: a split part (issue #340, part 3 of GH-301) with a
    tracker checklist and criteria over the cap. The body was opened by pre-GH-344 code with
    `closes_issue=not checklist`; today every delivery closes."""
    part = {
        **WITH_CHECKLIST,
        "id": "GH-340",
        "description": "Part 3.\n\n<!-- agentic-split: GH-301 part 3 -->",
    }
    ports = memory_ports()
    ports.workspace.files[("HEAD", "CODING_STANDARDS.md")] = _doc(200)
    ports.workspace.files[(rules.task_branch("GH-340"), "CODING_STANDARDS.md")] = _doc(301)

    patch = await deliver(part, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert ports.tracker.closes_issue_calls == [True]
    assert ports.tracker.pull_requests[0][0] == rules.task_branch("GH-340")
    assert "## Criteria over cap" in ports.tracker.pull_requests[0][2]


async def test_a_task_without_a_checklist_opens_its_pull_request_as_ever(cfg):
    ports = memory_ports()
    patch = await deliver(TASK, cfg, ports)

    assert ports.tracker.closes_issue_calls == [True]
    assert "tracker_checklist_posted" not in patch
    assert all(not text.startswith("Tracker work for you") for _, text in ports.tracker.comments)


async def test_a_failing_checklist_comment_still_opens_the_pull_request_with_the_list(cfg):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    patch = await deliver(WITH_CHECKLIST, cfg, ports)

    assert patch["status"] == "ready_for_review" and "escalation_class" not in patch
    assert ports.tracker.closes_issue_calls == [True]  # GH-344: was [False] (ADR-0062 §4)
    body = ports.tracker.pull_requests[0][2]
    (ready,) = [t for _, t in ports.tracker.comments if t.startswith("Ready for review")]
    for text in (body, ready):
        assert "could not be confirmed (`ValueError`)" in text
        assert "- [ ] Post the verdict table on #12" in text
        assert "agentic-tracker:" not in text and "scripted" not in text
    assert "tracker_checklist_posted" not in patch
    assert "tracker_checklist_posted" not in ports.documents.saved[0]
    assert [e["value"] for e in patch["pause_log"]] == ["tracker checklist comment: ValueError"]


@pytest.mark.parametrize("record", [{}, None, "absent"])
async def test_an_empty_or_absent_record_posts_the_list_once(cfg, record):
    state = dict(WITH_CHECKLIST)
    if record != "absent":
        state["tracker_checklist_posted"] = record
    ports = memory_ports()
    patch = await deliver(state, cfg, ports)

    posted = [t for _, t in ports.tracker.comments if t.startswith("Tracker work for you: ")]
    assert len(posted) == 1
    assert ports.tracker.closes_issue_calls == [True]  # GH-344: was [False] (ADR-0062 §4)
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(CHECKLIST)


async def test_a_failing_mid_flow_save_keeps_the_record_and_tells_the_human_nothing(cfg):
    ports = memory_ports()
    real = ports.documents.save
    calls = []

    def save(state):
        calls.append(state)
        if len(calls) == 1:
            raise OSError("disk")
        return real(state)

    ports.documents.save = save
    patch = await deliver(WITH_CHECKLIST, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(CHECKLIST)
    assert all("could not be confirmed" not in t for _, t in ports.tracker.comments)
    assert all("could not be confirmed" not in b for *_, b in ports.tracker.pull_requests)
    assert len([t for _, t in ports.tracker.comments if t.startswith("Tracker work")]) == 1
    assert [e["value"] for e in patch["pause_log"]] == ["tracker record save: OSError"]


async def test_a_hundred_item_list_in_a_failed_comment_keeps_the_body_bounded(cfg):
    items = [f"item {i} " + "x" * 400 for i in range(100)]
    sections = {"PM": "### Definition\n\nSix numbers.", "Tech Lead": "### Plan\n\nHex."}
    state = {**TASK, "sections": sections, "tracker_checklist": items}
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    await deliver(state, cfg, ports)

    body = ports.tracker.pull_requests[0][2]
    assert github_length(body) <= GITHUB_LIMIT
    assert "and 80 more, in the task document at" in body
    assert "item 20 " not in body and "item 19 " in body
    assert "## PM\n\n" + sections["PM"] in body
    assert "## Tech Lead\n\n" + sections["Tech Lead"] in body
    assert "Full sections" not in body


async def test_a_redelivery_with_a_failing_comment_leaves_the_body_alone(cfg):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    state = {**WITH_CHECKLIST, "pull_request": "https://github.com/o/r/pull/3"}
    patch = await deliver(state, cfg, ports)

    assert ports.tracker.pull_requests == []
    (ready,) = [t for _, t in ports.tracker.comments if t.startswith("Ready for review")]
    assert "could not be confirmed" in ready
    assert "tracker_checklist_posted" not in patch


async def test_an_oserror_from_the_ready_comment_and_from_release_still_ends_ready(cfg):
    ports = memory_ports()
    real = ports.tracker.comment

    def comment(task_id, text):
        if text.startswith("Ready for review"):
            raise OSError("gh down")
        return real(task_id, text)

    def cleanup(task_id):
        raise OSError("disk")

    ports.tracker.comment = comment
    ports.workspace.cleanup = cleanup
    patch = await deliver(WITH_CHECKLIST, cfg, ports)

    assert patch["status"] == "ready_for_review" and patch["pull_request"]
    assert ports.documents.saved[-1]["pull_request"] == patch["pull_request"]
    assert [e["value"] for e in patch["pause_log"]] == [
        "ready comment: OSError",
        "release task: OSError",
    ]


async def test_a_failing_pull_request_after_the_checklist_does_not_post_it_again(cfg):
    ports = memory_ports()
    ports.tracker.open_failures = 1
    first = await deliver(WITH_CHECKLIST, cfg, ports)

    assert "gh pr create failed" in first["escalated"]
    assert first["tracker_checklist_posted"]["hash"] == rules.tracker_hash(CHECKLIST)

    second = await deliver(_resumed(WITH_CHECKLIST, first), cfg, ports)
    assert second["pull_request"] == "https://github.com/o/r/pull/7"
    tracker_comments = [t for _, t in ports.tracker.comments if t.startswith("Tracker work")]
    assert len(tracker_comments) == 1
    assert len(ports.tracker.pull_requests) == 1


async def test_a_lost_record_does_not_post_the_checklist_twice_because_of_the_marker(cfg):
    ports = memory_ports()
    ports.tracker.open_failures = 1
    first = await deliver(WITH_CHECKLIST, cfg, ports)
    without_record = {k: v for k, v in first.items() if k != "tracker_checklist_posted"}
    lost = _resumed(WITH_CHECKLIST, without_record)

    await deliver(lost, cfg, ports)
    tracker_comments = [t for _, t in ports.tracker.comments if t.startswith("Tracker work")]
    assert len(tracker_comments) == 1


# --- GH-344: the delivery pull request always closes its issue ---


@pytest.mark.parametrize("checklist", [False, True])
@pytest.mark.parametrize("over_cap", [False, True])
async def test_the_delivery_pull_request_closes_its_issue_whatever_the_body_carries(
    cfg, over_cap, checklist
):
    ports = memory_ports()
    if over_cap:
        ports.workspace.files[("HEAD", "CODING_STANDARDS.md")] = _doc(200)
        ports.workspace.files[(BRANCH_REF, "CODING_STANDARDS.md")] = _doc(301)

    patch = await deliver(WITH_CHECKLIST if checklist else TASK, cfg, ports)

    assert patch["status"] == "ready_for_review"
    assert ports.tracker.closes_issue_calls == [True]
    assert ("## Criteria over cap" in ports.tracker.pull_requests[0][2]) is over_cap


# --- GH-373: every exit of deliver ends the debt of the publication the stopped run owed --------

OWED = "f08dfc738de2" + "0" * 28
OWING = {**TASK, "publish_owed": OWED}


def _no_longer_owed(ports):
    assert not ports.documents.last.get("publish_owed")


async def test_a_delivery_that_publishes_clears_the_publication_owed(cfg):
    ports = memory_ports()
    patch = await deliver(OWING, cfg, ports)
    assert patch["status"] == "ready_for_review" and patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_a_replacing_delivery_clears_the_publication_owed(cfg):
    ports = _diverged([])
    archive = "archive/run1-abc1234/agentic/GH-12"
    ports.workspace.existing_branches[archive] = (OLD, "base")
    ports.workspace.ancestors_by_ref = {archive: {OLD}}
    patch = await deliver(OWING, cfg, ports)
    assert ports.workspace.leases == [OLD]
    assert patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_a_refused_push_escalates_as_ever_and_clears_the_publication_owed(cfg):
    ports = _diverged([_pr(91, "OPEN")])
    patch = await deliver(OWING, cfg, ports)
    assert patch["escalated"].startswith("push refused: ")
    assert patch["status"] == "waiting_on_human" and patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_a_remote_read_that_fails_escalates_and_clears_the_publication_owed(cfg):
    ports = memory_ports()
    ports.workspace.remote_tip_error = "git ls-remote failed: unreachable"
    patch = await deliver(OWING, cfg, ports)
    assert patch["escalated"].startswith("remote branch read failed: ")
    assert patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_a_push_that_fails_escalates_and_clears_the_publication_owed(cfg):
    ports = memory_ports()
    ports.workspace.push_failures = 1
    patch = await deliver(OWING, cfg, ports)
    assert patch["escalated"].startswith("push failed: ")
    assert patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_a_tiered_refusal_heals_and_clears_the_publication_owed(cfg):
    ports = memory_ports(stack=MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",)))
    patch = await deliver(OWING, cfg, ports)  # no full_checks record: nothing is pushed
    assert ports.workspace.pushed == [] and patch["full_checks_healed"]
    assert patch["publish_owed"] is None
    _no_longer_owed(ports)


async def test_the_checklist_save_keeps_the_publication_owed_and_the_last_save_clears_it(cfg):
    ports = memory_ports()
    await deliver({**WITH_CHECKLIST, "publish_owed": OWED}, cfg, ports)
    first, last = ports.documents.saved[0], ports.documents.last
    assert "tracker_checklist_posted" in first and first["publish_owed"] == OWED
    assert not last.get("publish_owed")


async def test_a_task_that_owes_nothing_gets_the_patch_it_always_got(cfg):
    patch = await deliver(TASK, cfg, memory_ports())
    assert "publish_owed" not in patch


async def test_a_setup_that_fails_escalates_as_ever_and_clears_the_publication_owed(cfg):
    from agentic.core.process.application.ports import CommandResult
    from tests.fakes import MemoryCommands

    ports = memory_ports(
        stack=MemoryStack(setup={"env": "make env"}),
        commands=MemoryCommands(**{"make env": [CommandResult(1, "no env")]}),
    )
    patch = await deliver(OWING, cfg, ports)
    assert patch["escalation_class"] == "setup" and patch["publish_owed"] is None
    _no_longer_owed(ports)
