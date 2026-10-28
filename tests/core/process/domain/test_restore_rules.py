"""The restoration marker and the one cut it makes in the `landing` list (GH-406, ADR-0048
addendum): `rules.live_landing` hides what came before the last restoration, `landing_open`
and `resume_owed` read through it, and `restore_steps` says what a restore still owes from
the state observed. Pure: facts in, an answer out."""

from datetime import UTC, datetime

import pytest

from agentic.core.process.domain import landing, rules, task
from agentic.core.process.domain.rules import RestoreObserved
from agentic.core.process.domain.task import Restoration

HEAD = "a" * 40
OTHER = "c" * 40
NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
ARCHIVE = "archive/run1-93e1891"


def _entry(step="diagnose", head=HEAD, **over):
    return {"head": head, "step": step, **over}


def _restoration(cut, archive=ARCHIVE, **over) -> Restoration:
    return {
        "archive": archive,
        "head": HEAD,
        "upstream": "d" * 40,
        "at": "2026-10-28T10:00:00Z",
        "landing_cut": cut,
        **over,
    }


def _state(entries, *statuses, restorations=None):
    state = {
        "landing": entries,
        "subtasks": {f"s{n}": {"id": f"s{n}", "status": s} for n, s in enumerate(statuses)},
    }
    if restorations is not None:
        state["restorations"] = restorations
    return state


# --- live_landing ------------------------------------------------------------------------


def test_without_restorations_the_live_landing_is_the_whole_list():
    entries = [_entry(), _entry("sync_master")]

    assert rules.live_landing(_state(entries)) == entries
    assert rules.live_landing(_state(entries, restorations=[])) == entries
    assert rules.restored(_state(entries)) is False


def test_a_cut_hides_the_entries_before_it():
    entries = [_entry(), _entry("sync_master"), _entry("fix")]
    state = _state(entries, restorations=[_restoration(2)])

    assert rules.live_landing(state) == [entries[2]]
    assert rules.restored(state) is True


def test_the_last_restoration_makes_the_cut():
    entries = [_entry(), _entry("sync_master"), _entry("fix")]
    state = _state(entries, restorations=[_restoration(1), _restoration(3, archive="archive/x")])

    assert rules.live_landing(state) == []


def test_a_cut_past_the_end_hides_everything_and_a_negative_one_nothing():
    entries = [_entry(), _entry("fix")]

    assert rules.live_landing(_state(entries, restorations=[_restoration(9)])) == []
    assert rules.live_landing(_state(entries, restorations=[_restoration(-1)])) == entries


@pytest.mark.parametrize(
    "restorations",
    ["x", [None], [{"landing_cut": "1"}], [{"landing_cut": True}], [{"archive": "a"}]],
    ids=["not_a_list", "not_a_mapping", "text_cut", "bool_cut", "no_cut"],
)
def test_a_malformed_restoration_cuts_nothing(restorations):
    entries = [_entry(), _entry("fix")]

    assert rules.live_landing(_state(entries, restorations=restorations)) == entries


def test_a_malformed_landing_value_reads_as_empty_and_a_stray_entry_is_dropped():
    assert rules.live_landing({"landing": "x"}) == []
    assert rules.live_landing({}) == []
    # The cut counts positions of the raw list: a stray row before it still takes one.
    raw = {
        "landing": ["stray", _entry(), _entry("fix")],
        "restorations": [_restoration(2)],
    }
    assert rules.live_landing(raw) == [_entry("fix")]


# --- landing_open and resume_owed read through the cut ------------------------------------


def test_an_unresolved_entry_before_the_cut_is_no_open_landing():
    entries = [_entry("fix")]

    assert rules.landing_open(_state(entries, "approved", "returned")) is True
    state = _state(entries, "approved", "returned", restorations=[_restoration(1)])
    assert rules.landing_open(state) is False


def test_an_unresolved_entry_after_the_cut_is_an_open_landing():
    entries = [_entry("fix"), _entry("fix", head=OTHER)]
    state = _state(entries, "approved", "returned", restorations=[_restoration(1)])

    assert rules.landing_open(state) is True


def test_a_resume_is_not_owed_for_an_entry_before_the_cut_and_is_for_one_after():
    before = _state([_entry("re_review")], "approved", "returned")
    assert landing.resume_owed(before, NOW) is True

    cut = {**before, "restorations": [_restoration(1)]}
    assert landing.resume_owed(cut, NOW) is False

    after = {**cut, "landing": [*before["landing"], _entry("re_review", head=OTHER)]}
    assert landing.resume_owed(after, NOW) is True


# --- the reducer --------------------------------------------------------------------------


def test_add_restorations_appends_and_keeps_one_row_per_archive():
    first, second = _restoration(1), _restoration(4, archive="archive/run2-aaaaaaa")

    assert task.add_restorations([], [first]) == [first]
    assert task.add_restorations([first], [first]) == [first]
    assert task.add_restorations([first], [second]) == [first, second]


def test_delta_carries_only_the_restorations_a_state_gained():
    first, second = _restoration(1), _restoration(4, archive="archive/run2-aaaaaaa")
    before: task.TaskState = {
        "id": "T",
        "title": "t",
        "description": "d",
        "restorations": [first],
    }
    after: task.TaskState = {**before, "restorations": [first, second]}

    patch = task.delta(before, after)

    assert patch["restorations"] == [second]
    assert task.apply(before, patch)["restorations"] == [first, second]


# --- restore_steps --------------------------------------------------------------------------

TASK = "agentic/GH-9"
SUB = "agentic/GH-9.api"


def _document(**over):
    """The archived document of a delivered task: one unit, approved, PR open at HEAD."""
    state: task.TaskState = {
        "id": "GH-9",
        "title": "t",
        "description": "d",
        "status": "ready_for_review",
        "pull_request": "https://example.test/pull/9",
        "subtasks": {
            "api": {
                "id": "api",
                "status": "approved",
                "branch": SUB,
                "reviewed_head": HEAD,
            }
        },
        **over,
    }
    return state


def _observed(**over) -> RestoreObserved:
    base = RestoreObserved(
        task_id="GH-9",
        archive=ARCHIVE,
        archived=(f"{ARCHIVE}/{TASK}", f"{ARCHIVE}/{SUB}"),
        state=_document(),
        held=(),
        live_heads={},
        reachable={TASK: True, SUB: True},
        remote_head=None,
        pr_state="closed",
        pr_head=HEAD,
        pr_base_exists=True,
        settled=False,
    )
    return RestoreObserved(**{**base.__dict__, **over})


def test_restore_heads_name_the_approved_head_and_each_subtask_reviewed_head():
    state = _document(
        subtasks={
            "api": {
                "id": "api",
                "status": "approved",
                "branch": "agentic/GH-9.api",
                "reviewed_head": HEAD,
            },
            "web": {
                "id": "web",
                "status": "approved",
                "branch": "agentic/GH-9.web",
                "reviewed_head": OTHER,
            },
            "gap": {"id": "gap", "status": "approved", "branch": "agentic/GH-9.gap"},
        }
    )

    assert rules.restore_heads(state, "GH-9") == {
        "agentic/GH-9.api": HEAD,
        "agentic/GH-9.web": OTHER,
    }


def test_restore_heads_of_a_single_unit_task_include_the_task_branch_at_the_approved_head():
    assert rules.restore_heads(_document(), "GH-9") == {TASK: HEAD, SUB: HEAD}


def _gh374_document(**over):
    """GH-374's shape: three subtasks on their own branches plus the integration on the task
    branch, at the head the task was approved at."""
    heads = {"layout": "1" * 40, "cards": "2" * 40, "hero": "3" * 40}
    units = {
        sid: {"id": sid, "status": "approved", "branch": f"agentic/GH-9.{sid}", "reviewed_head": h}
        for sid, h in heads.items()
    }
    units["integration"] = {
        "id": "integration",
        "status": "approved",
        "branch": TASK,
        "reviewed_head": HEAD,
    }
    return _document(subtasks=units, **over)


def test_restore_heads_of_the_gh374_shape_take_the_integration_branch_from_its_document():
    assert rules.restore_heads(_gh374_document(), "GH-9") == {
        TASK: HEAD,
        "agentic/GH-9.layout": "1" * 40,
        "agentic/GH-9.cards": "2" * 40,
        "agentic/GH-9.hero": "3" * 40,
    }


def test_restore_heads_of_a_single_unit_on_the_task_branch_is_only_the_task_branch():
    state = _document(
        subtasks={
            "api": {"id": "api", "status": "approved", "branch": TASK, "reviewed_head": HEAD}
        }
    )

    assert rules.restore_heads(state, "GH-9") == {TASK: HEAD}


def test_restore_heads_never_let_a_unit_on_the_task_branch_replace_the_approved_head():
    docs = "d" * 40
    state = _gh374_document(
        close_docs={"parent": HEAD, "head": docs, "paths": ["docs/product/x.md"]}
    )

    assert rules.approved_head(state) == docs
    assert rules.restore_heads(state, "GH-9")[TASK] == docs


def test_restore_heads_give_a_unit_without_a_branch_nothing_and_ignore_dict_order():
    units = _gh374_document()["subtasks"]
    del units["cards"]["branch"]
    forward = rules.restore_heads(_document(subtasks=units), "GH-9")
    backward = rules.restore_heads(_document(subtasks=dict(reversed(units.items()))), "GH-9")

    assert forward == backward
    assert "agentic/GH-9.cards" not in forward
    assert not any(branch.endswith(".integration") for branch in forward)


def test_restore_steps_of_the_gh374_shape_find_every_derived_branch_and_refuse_nothing():
    state = _gh374_document()
    names = tuple(rules.restore_heads(state, "GH-9"))
    plan = rules.restore_steps(
        _observed(
            state=state,
            archived=tuple(f"{ARCHIVE}/{name}" for name in names),
            reachable=dict.fromkeys(names, True),
        )
    )

    assert plan.refusal is None
    assert plan.steps == ("create_refs", "write_document", "push", "reopen", "settle")


def test_a_branch_the_derived_heads_name_but_the_archive_lacks_is_refused():
    state = _gh374_document()
    names = tuple(rules.restore_heads(state, "GH-9"))
    plan = rules.restore_steps(
        _observed(
            state=state,
            archived=tuple(f"{ARCHIVE}/{name}" for name in names if name != "agentic/GH-9.hero"),
            reachable=dict.fromkeys(names, True),
        )
    )

    assert plan.refusal == "head_not_reachable"


def test_a_delivered_archive_on_a_clean_slate_owes_every_step_in_order():
    plan = rules.restore_steps(_observed())

    assert plan.refusal is None
    assert plan.steps == ("create_refs", "write_document", "push", "reopen", "settle")


def test_an_archive_without_the_task_branch_refuses_no_archive():
    plan = rules.restore_steps(_observed(archived=(f"{ARCHIVE}/{SUB}",)))

    assert plan.refusal == "no_archive"
    assert plan.steps == ()


def test_the_spelling_without_agentic_is_the_same_archive():
    plan = rules.restore_steps(_observed(archived=(f"{ARCHIVE}/GH-9", f"{ARCHIVE}/GH-9.api")))

    assert plan.refusal is None


def test_a_missing_document_refuses_no_document():
    assert rules.restore_steps(_observed(state=None)).refusal == "no_document"


@pytest.mark.parametrize(
    "over",
    [
        {"status": "implementing"},
        {"pull_request": None},
        {"subtasks": {"api": {"id": "api", "status": "returned", "reviewed_head": HEAD}}},
        {"subtasks": {}},
    ],
    ids=["status", "no_pull_request", "not_approved", "no_unit"],
)
def test_a_document_that_was_not_delivered_and_approved_refuses_not_delivered(over):
    assert rules.restore_steps(_observed(state=_document(**over))).refusal == "not_delivered"


def test_a_merged_pull_request_refuses_pr_merged_and_a_missing_one_not_delivered():
    assert rules.restore_steps(_observed(pr_state="merged")).refusal == "pr_merged"
    assert rules.restore_steps(_observed(pr_state=None, pr_head=None)).refusal == "not_delivered"


def test_a_pull_request_whose_base_is_gone_refuses_pr_base_gone():
    assert rules.restore_steps(_observed(pr_base_exists=False)).refusal == "pr_base_gone"


def test_a_pull_request_that_does_not_carry_the_approved_head_refuses_head_disagrees():
    assert rules.restore_steps(_observed(pr_head=OTHER)).refusal == "head_disagrees"
    assert rules.restore_steps(_observed(pr_head=None)).refusal == "head_disagrees"


def test_a_remote_branch_at_another_commit_refuses_remote_diverged():
    assert rules.restore_steps(_observed(remote_head=OTHER)).refusal == "remote_diverged"


def test_a_short_remote_head_of_the_same_commit_is_the_same_branch():
    plan = rules.restore_steps(_observed(remote_head=HEAD[:12]))

    assert plan.refusal is None
    assert "push" not in plan.steps


@pytest.mark.parametrize("branch", [TASK, SUB])
def test_a_head_that_is_not_an_ancestor_of_its_archived_branch_refuses_head_not_reachable(branch):
    reachable = {TASK: True, SUB: True, branch: False}

    assert rules.restore_steps(_observed(reachable=reachable)).refusal == "head_not_reachable"
    assert rules.restore_steps(_observed(reachable={})).refusal == "head_not_reachable"


def test_a_subtask_whose_archived_branch_is_missing_refuses_head_not_reachable():
    plan = rules.restore_steps(_observed(archived=(f"{ARCHIVE}/{TASK}",)))

    assert plan.refusal == "head_not_reachable"


def test_a_restoration_from_another_archive_refuses_already_restored():
    held = (_restoration(2, archive="archive/run2-bbbbbbb"),)

    assert rules.restore_steps(_observed(held=held)).refusal == "already_restored"


def test_the_refusals_take_the_order_the_set_lists_them_in():
    """No archive wins over a missing document, which wins over everything after it."""
    broken = _observed(archived=(), state=None, pr_state="merged", pr_head=OTHER)

    assert rules.restore_steps(broken).refusal == "no_archive"
    assert rules.restore_steps(_observed(state=None, pr_state="merged")).refusal == "no_document"


# A retry resumes from what it sees: each step is owed until its observable effect is there.


def test_a_retry_after_the_refs_were_created_owes_the_rest():
    live = {TASK: HEAD, SUB: HEAD}

    plan = rules.restore_steps(_observed(live_heads=live))

    assert plan.steps == ("write_document", "push", "reopen", "settle")


def test_a_retry_after_the_document_was_written_owes_the_rest():
    live = {TASK: HEAD, SUB: HEAD}
    plan = rules.restore_steps(_observed(live_heads=live, held=(_restoration(2),)))

    assert plan.steps == ("push", "reopen", "settle")


def test_a_retry_after_the_push_owes_reopen_and_settle():
    live = {TASK: HEAD, SUB: HEAD}
    plan = rules.restore_steps(
        _observed(live_heads=live, held=(_restoration(2),), remote_head=HEAD)
    )

    assert plan.steps == ("reopen", "settle")


def test_a_retry_after_the_reopen_owes_only_settle():
    live = {TASK: HEAD, SUB: HEAD}
    plan = rules.restore_steps(
        _observed(live_heads=live, held=(_restoration(2),), remote_head=HEAD, pr_state="open")
    )

    assert plan.steps == ("settle",)


def test_a_restore_with_everything_done_owes_nothing():
    live = {TASK: HEAD, SUB: HEAD}
    plan = rules.restore_steps(
        _observed(
            live_heads=live,
            held=(_restoration(2),),
            remote_head=HEAD,
            pr_state="open",
            settled=True,
        )
    )

    assert plan.refusal is None
    assert plan.steps == ()


def test_a_retry_whose_pull_request_head_moved_since_refuses_pr_head_moved():
    held = (_restoration(2),)
    moved = _observed(held=held, live_heads={TASK: HEAD, SUB: HEAD}, pr_head=OTHER)

    assert rules.restore_steps(moved).refusal == "pr_head_moved"


# A live branch at another head is archived first; one at the recorded head is the restored one.


def test_a_live_task_branch_at_another_head_is_archived_before_the_refs_are_created():
    plan = rules.restore_steps(_observed(live_heads={TASK: OTHER}))

    assert plan.steps == (
        "archive_live",
        "create_refs",
        "write_document",
        "push",
        "reopen",
        "settle",
    )


def test_a_live_subtask_branch_at_another_head_is_archived_too():
    plan = rules.restore_steps(_observed(live_heads={TASK: HEAD, SUB: OTHER}))

    assert plan.steps[:2] == ("archive_live", "create_refs")


def test_a_live_task_branch_at_the_recorded_head_is_not_archived():
    plan = rules.restore_steps(_observed(live_heads={TASK: HEAD[:10]}))

    assert "archive_live" not in plan.steps
    assert plan.steps[0] == "create_refs"


def test_an_open_pull_request_is_not_reopened():
    assert "reopen" not in rules.restore_steps(_observed(pr_state="open")).steps
