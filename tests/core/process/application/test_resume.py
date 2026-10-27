"""What a resume decides before it spends, against in-memory ports (ADR-0003, addendum)."""

import pytest

from agentic.core.process.application import resume
from agentic.core.process.application.plan import PART_BUILT_WHOLE
from agentic.core.process.domain import document, legacy_names, roles, rules, schemas
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

CAP = "Developer stopped by the task cap: 106.27 of 100.00 USD spent"
USD = {"cost_usd": 25.0}


def paused(**more):
    """GH-12 after run 4, reduced: two subtasks approved, the integration returned."""
    return {
        "id": "GH-12",
        "title": "Dashboard",
        "description": "How is my gym doing.",
        "type": "feature",
        "status": "waiting_on_human",
        "current_role": "Developer",
        "escalated": CAP,
        "branch": "agentic/GH-12",
        "cost_usd": 106.27,
        "runs": [
            {"role": "Developer", "section": "Developer: integration", "session_id": "d1", **USD},
            {
                "role": "Tech Lead",
                "section": "Tech Lead: review integration",
                "session_id": "r1",
                **USD,
            },
        ],
        "subtasks": {
            "api": sub("api", "approved", "agentic/GH-12.api", "b1", reviewed_head="a2"),
            "domain": sub("domain", "approved", "agentic/GH-12.domain", "b1", reviewed_head="d2"),
            "integration": sub(
                "integration", "returned", "agentic/GH-12", "b0", reviewed_head="i1"
            ),
        },
        **more,
    }


def sub(id_, status, branch, base, **more):
    return {"id": id_, "status": status, "branch": branch, "base": base, **more}


def git_as_recorded(ports):
    ports.workspace.existing_branches.update(
        {
            "agentic/GH-12": ("i2", "b0"),
            "agentic/GH-12.api": ("a2", "b1"),
            "agentic/GH-12.domain": ("d2", "b1"),
        }
    )


def raised():
    return Configuration(product_dir="/tmp/p", task_budget_usd=125)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def test_the_document_seeds_the_state_without_what_is_not_state():
    ports = memory_ports()
    git_as_recorded(ports)
    ports.documents.save({**paused(), "plugins": {"skills": "1.2.3"}, "system_commit": "abc"})
    ports.documents.saved[-1]["pull_request"] = None
    state = resume.from_document("GH-12", ports)
    assert state == paused()
    assert ports.workspace.pruned == 0  # reading is not resuming: a dry run touches nothing
    resume.announce(resume.prepare("GH-12", state, "document", raised(), ports), ports)
    assert ports.workspace.pruned == 1


def test_from_document_reads_back_the_proposed_invariants():
    # The rescue path itself, not a proxy: `document.render` writes the key,
    # `document.parse` reads it back, and `resume.from_document`'s generic
    # `STATE_FIELDS` filter carries it into the resumed state (issue #42).
    ports = memory_ports()
    git_as_recorded(ports)
    state = paused(proposed_invariants=["Existing rule."])
    ports.documents.save(document.parse(document.render(state)))
    resumed = resume.from_document("GH-12", ports)
    assert resumed["proposed_invariants"] == ["Existing rule."]


def test_from_document_reads_back_the_experiment_snapshot():
    # Same rescue path as `proposed_invariants` above: `document.render` writes
    # `experiment` only when non-empty, `document.parse` reads it back, and
    # `resume.from_document`'s generic `STATE_FIELDS` filter carries it into the
    # resumed state (issue #28).
    ports = memory_ports()
    git_as_recorded(ports)
    snapshot = {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-sonnet-5",
    }
    state = paused(experiment=snapshot)
    ports.documents.save(document.parse(document.render(state)))
    resumed = resume.from_document("GH-12", ports)
    assert resumed["experiment"] == snapshot


def test_from_document_leaves_a_default_experiment_without_the_key():
    # An empty snapshot is omitted by `document.render`; the rescue path must not
    # manufacture one either.
    ports = memory_ports()
    git_as_recorded(ports)
    state = paused(experiment={})
    ports.documents.save(document.parse(document.render(state)))
    resumed = resume.from_document("GH-12", ports)
    assert "experiment" not in resumed


def test_from_document_leaves_a_legacy_document_without_the_key():
    # A document written before `proposed_invariants` existed (issue #42) has no line
    # for it at all: the rescue path must not manufacture one, since `plan.py`'s
    # migration branch reads the key's absence, never an empty list.
    old = "---\nid: GH-1\ntitle: t\nstatus: open\n---\n\n# t\n\n## Original request\n\nd\n"
    ports = memory_ports()
    ports.documents.save(document.parse(old))
    resumed = resume.from_document("GH-1", ports)
    assert "proposed_invariants" not in resumed


def test_a_document_written_before_the_prd_rename_still_resumes():
    # `definition_ready` is frozen byte for byte (ADR-0026): a document written while
    # PM's deliverable was still called `Definition` carries this exact status, and
    # `resume_target` must send it where it always did -- the rename touched no
    # branch, no default and no runtime condition, only the identifiers around it.
    old = (
        "---\nid: GH-2\ntitle: t\nstatus: definition_ready\ntype: feature\n---\n\n"
        "# t\n\n## Original request\n\nd\n"
    )
    ports = memory_ports()
    ports.documents.save(document.parse(old))
    resumed = resume.from_document("GH-2", ports)
    assert resumed["status"] == "definition_ready"
    assert rules.resume_target(resumed) == "plan"


def test_a_document_with_old_role_names_still_resumes():
    # 2026-10-05 rename: `document.parse` (via `from_frontmatter`) translates the old
    # role name to the new one before `resume.from_document` ever sees the state, so a
    # task paused before the rename still resumes with the same section, pair and
    # return count -- under the new name, as the plan's own invariant requires. The old
    # name itself comes from `legacy_names`'s own constants, never typed again here.
    old = (
        "---\n"
        "id: GH-4\n"
        "title: t\n"
        "status: waiting_on_human\n"
        "type: feature\n"
        f"current_role: {legacy_names.OLD_NAME}\n"
        f"returns: {{'{legacy_names.OLD_NAME}→UI Designer': 1}}\n"
        "return_history: "
        f"[{{pair: '{legacy_names.OLD_NAME}→UI Designer', category: definition, reason: r}}]\n"
        "---\n"
        f"\n# t\n\n## Original request\n\nd\n\n## {legacy_names.OLD_NAME}\n\napproved\n"
    )
    ports = memory_ports()
    ports.documents.save(document.parse(old))
    resumed = resume.from_document("GH-4", ports)
    assert resumed["current_role"] == legacy_names.NEW_NAME
    assert resumed["changes_requested"] == {f"{legacy_names.NEW_NAME}→UI Designer": 1}
    assert resumed["change_history"][0]["pair"] == f"{legacy_names.NEW_NAME}→UI Designer"
    assert set(resumed["sections"]) == {legacy_names.NEW_NAME}
    # `resume_target` reads the same facts a routing node would: no crash, no stale
    # old-named section stops it from deciding where this task goes next.
    assert rules.resume_target(resumed) is None  # `status` still `waiting_on_human`


def test_a_document_written_before_system_baseline_existed_still_resumes():
    # No `system_baseline` line at all -- a document from before this task, carrying
    # only `system_version` -- still resumes (ADR-0030): neither key was ever part of
    # `TaskState`, so `STATE_FIELDS` already drops both, the same path a legacy
    # document without `proposed_invariants` takes above.
    old = (
        "---\nid: GH-3\ntitle: t\nstatus: open\nsystem_version: abc1234\n---\n\n"
        "# t\n\n## Original request\n\nd\n"
    )
    ports = memory_ports()
    ports.documents.save(document.parse(old))
    resumed = resume.from_document("GH-3", ports)
    assert "system_baseline" not in resumed
    assert "system_version" not in resumed


def test_the_document_is_refused_when_git_moved_since():
    ports = memory_ports()
    ports.workspace.existing_branches.update(
        {"agentic/GH-12.api": ("a3", "b1"), "agentic/GH-12.domain": ("d2", "b9")}
    )
    ports.documents.save(paused())
    with pytest.raises(ValueError) as info:
        resume.from_document("GH-12", ports)
    assert str(info.value).splitlines()[1:] == [
        "- task branch `agentic/GH-12` does not exist",
        "- api: approved at a2, but `agentic/GH-12.api` is at a3",
        "- domain: base b1 in the document, b9 in git",
        "- integration: branch `agentic/GH-12` does not exist",
    ]
    with pytest.raises(ValueError, match="no task document"):
        resume.from_document("GH-99", ports)


def test_the_decision_is_everything_the_human_said_after_the_system_last_spoke():
    assert resume.human_decision(["Escalated: x", " Use USD. ", "And ARS for Argentina."]) == (
        "Use USD.\n\nAnd ARS for Argentina."
    )
    earlier = ["Why MRR?", "Escalated: PM could not decide", "USD.", "Resumed: define."]
    assert resume.human_decision(earlier) is None
    assert resume.human_decision([*earlier, "Ready for review: https://x", "  "]) is None
    assert resume.human_decision(["Escalated: x", "Keep it\r\nsimple."]) == "Keep it\nsimple."
    assert resume.human_decision(["Discussed before any run."]) is None
    assert resume.human_decision([]) is None


def test_the_decision_reads_past_the_line_that_says_a_part_is_built_whole():
    marker = "<!-- agentic-part-whole: GH-346 300.00 44 -->"
    line = f"{PART_BUILT_WHOLE}GH-346 is part 2 of GH-316.\n\n{marker}"
    assert PART_BUILT_WHOLE in resume.SYSTEM_COMMENTS
    assert resume.human_decision(["Escalated: x", line, "Use USD."]) == "Use USD."
    assert resume.human_decision(["Escalated: x", "Use USD.", line]) is None


def test_the_decision_skips_a_started_over_comment_too():
    # `archive.STARTED_OVER` is registered here too (ADR-0011, sixth addendum): a
    # `--fresh` archive must not read back as the human's own decision on the next
    # resume. The comment sits between the escalation and the decision -- exactly
    # where `--fresh` posts it, restarting a task nobody had answered yet -- and the
    # decision after it is still read clean, with none of the archive's own words in it.
    comments = [
        "Escalated: x",
        "Started over: previous run archived as archive/run4-abc/",
        "Use USD.",
    ]
    assert resume.human_decision(comments) == "Use USD."


def test_a_resume_under_the_flat_ceiling_goes_on_to_the_returned_integration(cfg):
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: " + CAP]
    ports.workspace.checkouts = [cfg.product_dir / ".agentic/worktrees/GH-12"]
    r = resume.prepare("GH-12", paused(cost_usd=60.0), "document", cfg, ports)
    assert r.decision is None and r.target == "integrate"
    assert r.cap_usd == 250  # the flat ceiling, whatever the plan's size
    assert r.resumed["escalated"] is None and r.resumed["status"] == "developing"
    text = resume.summary(r, cfg, ports)
    assert "reason:    " + CAP in text
    assert "spent:     60.00 USD (definition 0.00, plan 0.00, development 25.00, " in text
    assert "review 25.00, other 10.00)" in text
    assert "task cap:  250.00 USD (ceiling 250)" in text
    assert "resumes:   integrate (integration)" in text
    assert "session:   Developer: integration: d1" in text
    assert "session:   Tech Lead: review integration: r1" in text
    assert "worktree:  " + str(cfg.product_dir / ".agentic/worktrees/GH-12") in text


def test_a_task_paused_by_the_old_cap_resumes_under_the_flat_ceiling(cfg):
    # Spent 106.27, stopped by the old formula's 100: the stored reason is history, the
    # ceiling governs and no --task-budget is needed (ADR-0004, 2026-10-05 addendum).
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: " + CAP]
    r = resume.prepare("GH-12", paused(), "document", cfg, ports)
    assert r.cap_usd == 250
    text = resume.summary(r, cfg, ports)
    assert "reason:    " + CAP in text
    assert "task cap:  250.00 USD (ceiling 250)" in text


@pytest.mark.parametrize("subtask_count", [1, 12])
def test_the_resume_cap_is_the_same_for_any_plan_size(cfg, tmp_path, subtask_count):
    subtasks = {
        f"s{n}": sub(f"s{n}", "returned", f"agentic/GH-12.s{n}", "b1", reviewed_head="x")
        for n in range(subtask_count)
    }
    state = paused(cost_usd=60.0, subtasks=subtasks, runs=[])
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: " + CAP]
    assert resume.prepare("GH-12", state, "document", cfg, ports).cap_usd == 250
    raised = Configuration(product_dir=tmp_path, task_budget_usd=400)
    assert resume.prepare("GH-12", state, "document", raised, ports).cap_usd == 400


def test_a_cap_already_spent_is_refused_and_the_human_can_raise_it(tmp_path):
    ports = memory_ports()
    low = Configuration(product_dir=tmp_path, task_max_budget_usd=100)
    with pytest.raises(ValueError, match="pass --task-budget above 106.27"):
        resume.prepare("GH-12", paused(), "checkpoint", low, ports)
    raised = Configuration(product_dir=tmp_path, task_max_budget_usd=100, task_budget_usd=125)
    r = resume.prepare("GH-12", paused(), "checkpoint", raised, ports)
    assert r.cap_usd == 125
    assert "task cap:  125.00 USD (--task-budget)" in resume.summary(r, raised, ports)


def test_questions_without_an_answer_keep_the_task_waiting(cfg):
    state = {
        **paused(cost_usd=1.0, subtasks={}, runs=[]),
        "escalated": "PM could not decide 1 question(s)",
        "questions_for_human": ["Which currency?"],
    }
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: PM could not decide"]
    with pytest.raises(resume.WaitingOnHuman, match="1 question"):
        resume.prepare("GH-12", state, "checkpoint", cfg, ports)
    ports.tracker.earlier.append("USD, always.")
    r = resume.prepare("GH-12", state, "checkpoint", cfg, ports)
    assert r.decision == "USD, always." and r.target == "define"
    assert "Which currency?" not in str(r.resumed["questions_for_human"])
    assert "USD, always." in r.resumed["sections"]["Human"]
    assert "decision:  USD, always." in resume.summary(r, cfg, ports)
    ports.tracker.earlier.append("ARS in Argentina.")
    r = resume.prepare("GH-12", state, "checkpoint", cfg, ports)
    text = resume.summary(r, cfg, ports)
    assert "decision:  USD, always.\n\n           ARS in Argentina.\n" in text


def test_a_run_that_crashed_resumes_its_pending_nodes_as_they_are(cfg):
    state = {**paused(cost_usd=1.0), "escalated": None, "status": "developing"}
    r = resume.prepare("GH-12", state, "checkpoint", cfg, memory_ports(), ("integrate",))
    assert r.resumed == state and r.target == "integrate" and r.decision is None
    assert r.interrupted is True  # pending nodes with no interrupt: the run crashed


def test_an_interrupted_resume_says_whether_its_worktree_has_uncommitted_changes(cfg):
    # Interrupted (pending nodes, `RunLock` plan): the summary names what a killed
    # role may have left uncommitted, never derived from the lock itself.
    dirty = cfg.product_dir / ".agentic/worktrees/GH-12"
    state = {**paused(cost_usd=1.0), "escalated": None, "status": "developing"}

    ports = memory_ports()
    ports.workspace.checkouts = [dirty]
    ports.workspace.uncommitted.add(dirty)
    r = resume.prepare("GH-12", state, "checkpoint", cfg, ports, ("integrate",))
    text = resume.summary(r, cfg, ports)
    assert f"worktree:  {dirty}  (uncommitted changes)" in text

    clean_ports = memory_ports()
    clean_ports.workspace.checkouts = [dirty]
    r = resume.prepare("GH-12", state, "checkpoint", cfg, clean_ports, ("integrate",))
    text = resume.summary(r, cfg, clean_ports)
    assert text.splitlines()[-1] == f"worktree:  {dirty}"

    # Not interrupted: a returned pair's worktree may be just as dirty, but the note
    # is specific to a run that crashed, so a normal resume says nothing about it.
    normal_ports = memory_ports()
    normal_ports.workspace.checkouts = [dirty]
    normal_ports.workspace.uncommitted.add(dirty)
    r = resume.prepare("GH-12", paused(cost_usd=60.0), "document", cfg, normal_ports)
    assert r.interrupted is False
    text = resume.summary(r, cfg, normal_ports)
    assert text.splitlines()[-1] == f"worktree:  {dirty}"


def test_announcing_an_interrupted_resume_names_the_crashed_role(cfg):
    # `announce`'s existing `Resumed: ` gains one clause on the crashed branch alone
    # (subtask `signals`): no new comment prefix, `SYSTEM_COMMENTS` untouched, the
    # human learns which role a killed process was in the middle of.
    state = {**paused(cost_usd=1.0), "escalated": None, "status": "developing"}
    ports = memory_ports()
    r = resume.prepare("GH-12", state, "checkpoint", cfg, ports, ("integrate",))
    resume.announce(r, ports)
    expected = (
        f"Resumed: integrate, interrupted at Developer, under a task cap of {r.cap_usd:.2f} USD."
    )
    assert ports.tracker.comments == [("GH-12", expected)]


def test_announcing_takes_the_task_out_of_the_queue(cfg):
    ports = memory_ports()
    raised = Configuration(
        product_dir=cfg.product_dir, task_max_budget_usd=100, task_budget_usd=125
    )
    ports.tracker.earlier = ["Escalated: " + CAP, "Go on, but keep MRR per active subscription."]
    r = resume.prepare("GH-12", paused(), "checkpoint", raised, ports)
    resume.announce(r, ports)
    assert ports.tracker.unlabeled == [("GH-12", "needs-human")]
    assert ports.tracker.comments == [
        ("GH-12", "Resumed: integrate with your decision, under a task cap of 125.00 USD.")
    ]


def test_a_pair_at_its_limit_waits_for_an_answer_but_the_cap_does_not(cfg):
    # Resetting the counter with nothing new repeats the same return (README.md, 3); a
    # cap or a role failure has nothing to decide.
    state = paused(escalated="Tech Lead→Developer:integration hit 2 returns: MRR twice")
    raised = Configuration(product_dir=cfg.product_dir, task_budget_usd=125)
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: Tech Lead→Developer:integration hit 2 returns"]
    with pytest.raises(resume.WaitingOnHuman, match="hit its return limit"):
        resume.prepare("GH-12", state, "checkpoint", raised, ports)
    ports.tracker.earlier.append("Count each subscription once.")
    r = resume.prepare("GH-12", state, "checkpoint", raised, ports)
    assert r.resumed["changes_requested"] == {"Tech Lead→Developer:integration": 0}
    failed = paused(escalated="Developer failed 2 times: empty markdown")
    assert resume.prepare("GH-12", failed, "checkpoint", raised, memory_ports()).decision is None


def test_a_task_with_nothing_left_to_run_is_not_resumed(cfg):
    delivered = paused(escalated=None, pull_request="https://github.com/o/r/pull/7")
    with pytest.raises(ValueError, match="nothing left to run"):
        resume.prepare("GH-12", delivered, "document", raised(), memory_ports())
    # A discarded bug, seeded from its document, with the human's comment after the
    # `Discarded:` one: its runs must not read as a definition ready to plan (issue #65).
    discarded = {
        "id": "GH-12",
        "title": "Weekly report",
        "type": "bug",
        "status": "discarded",
        "discard_reason": "weekly by definition",
        "cost_usd": 1.0,
        "runs": [
            {"role": "PjM", "section": "PjM", "session_id": "p", "cost_usd": 0.1},
            {"role": "Product Analyst", "section": "Product Analyst", "session_id": "a", **USD},
            {"role": "PM", "section": "PM", "session_id": "m", **USD},
        ],
    }
    ports = memory_ports()
    ports.tracker.earlier = ["Discarded: weekly by definition", "ok, closing"]
    with pytest.raises(ValueError, match=r"nothing left to run \(discarded\)"):
        resume.prepare("GH-12", discarded, "document", raised(), ports)


def test_from_document_keeps_a_decided_task_decided_and_capped():
    # The `--from-document` rescue of a state `rules.entering` marked: `render` writes the
    # key, `parse` reads it back, `STATE_FIELDS` carries it, and the cap is still one.
    ports = memory_ports()
    git_as_recorded(ports)
    state = paused(description="Body\n\n## Decided\n- a\n", decided=True)
    ports.documents.save(document.parse(document.render(state)))
    resumed = resume.from_document("GH-12", ports)
    assert resumed["decided"] is True
    assert rules.interview_cap(resumed, roles.INTERVIEW_SOLUTION, 4) == 1


def test_from_document_of_a_task_from_before_the_cap_keeps_the_configured_cap():
    # Pre-merge shape: the request holds the heading (demoted in the document), the key
    # is absent. A rescue never infers it from prose.
    ports = memory_ports()
    git_as_recorded(ports)
    state = paused(description="Body\n\n## Decided\n- a\n")
    ports.documents.save(document.parse(document.render(state)))
    resumed = resume.from_document("GH-12", ports)
    assert "decided" not in resumed
    assert rules.decided(resumed["description"], level=3)
    assert rules.interview_cap(resumed, roles.INTERVIEW_SOLUTION, 4) == 4
    assert rules.interview_cap(resumed, roles.INTERVIEW_DEFINITION, 4) == 4


FULL_SHA = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def _approved_at(reviewed: str, head: str, base: str = "b1", recorded: str = "b1"):
    ports = memory_ports()
    ports.workspace.existing_branches["agentic/GH-9"] = (head, recorded)
    state = {
        "subtasks": {
            "only": sub("only", "approved", "agentic/GH-9", base, reviewed_head=reviewed),
        }
    }
    return resume.disagreements(state, ports.workspace)


def test_a_full_reviewed_head_against_a_short_branch_head_is_no_disagreement():
    assert _approved_at(FULL_SHA, FULL_SHA[:7]) == []


def test_a_short_reviewed_head_against_a_full_branch_head_is_no_disagreement():
    assert _approved_at(FULL_SHA[:7], FULL_SHA) == []


def test_a_different_reviewed_head_is_a_disagreement():
    problems = _approved_at(FULL_SHA, "1234567")
    assert len(problems) == 1 and "approved at" in problems[0]


def test_a_base_recorded_short_against_a_full_one_in_git_is_no_disagreement():
    assert _approved_at(FULL_SHA, FULL_SHA, base=FULL_SHA[:7], recorded=FULL_SHA) == []


def test_a_different_base_is_a_disagreement():
    problems = _approved_at(FULL_SHA, FULL_SHA, base="1234567", recorded=FULL_SHA)
    assert len(problems) == 1 and "base" in problems[0]


def test_an_all_approved_task_paused_at_delivery_still_resumes_through_integrate():
    # GH-203: a delivery that failed (push, `gh pr create`) leaves every subtask
    # approved and no pull request. `resume_target` is unchanged: it sends the task
    # through the fan-in, whose merges are no-ops, and the route reaches `deliver`.
    paused = {
        "id": "GH-12",
        "title": "t",
        "type": "feature",
        "status": "waiting_on_human",
        "subtasks": {
            "api": {"id": "api", "status": "approved"},
            "domain": {"id": "domain", "status": "approved"},
            "integration": {"id": "integration", "status": "approved"},
        },
        "sections": {"PM": "p", "Tech Lead": "t"},
        **rules.escalation("failure", "gh pr create failed: Body is too long"),
    }
    resumed = {**paused, **rules.resumed(paused, None)}
    assert resumed["escalated"] is None
    assert rules.resume_target(resumed) == "integrate"


def test_an_estimate_escalation_waits_for_the_humans_decision(cfg):
    # Only a human decision resumes it (ADR-0054): with none, resume refuses; with one,
    # it goes on. A stale cap or a role failure keeps its own behaviour (tests above).
    state = paused(
        escalated="stopped at the cap of the estimate: 220.00 of 110.00 USD",
        escalation_class="estimate",
    )
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: stopped at the cap of the estimate"]
    with pytest.raises(resume.WaitingOnHuman, match="estimate"):
        resume.prepare("GH-12", state, "checkpoint", cfg, ports)
    ports.tracker.earlier.append("Go on, the estimate is 200.")
    r = resume.prepare("GH-12", state, "checkpoint", cfg, ports)
    assert r.decision == "Go on, the estimate is 200."


def stopped_at_the_estimate(**more):
    return paused(
        **{
            "estimate": {"cost_usd": 40.0, "files": 10},
            "proposed_estimate": 150.0,
            **rules.escalation("estimate", "Developer stopped by the estimate cap: 106.27 of 80"),
            **more,
        },
    )


def decided(*comments):
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: Developer stopped by the estimate cap", *comments]
    return ports


def test_a_decision_without_the_flag_puts_the_proposed_estimate_in_force(cfg):
    r = resume.prepare(
        "GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided("Go on, same plan.")
    )
    assert r.resumed["estimate_revisions"] == [{"cost_usd": 150.0, "source": "proposed"}]
    assert rules.estimate_in_force(r.resumed) == 150.0
    assert r.resumed["estimate"] == {"cost_usd": 40.0, "files": 10}  # the plan's, untouched
    assert r.resumed.get("proposed_estimate") is None


def test_the_flag_puts_the_humans_own_estimate_in_force(cfg):
    r = resume.prepare(
        "GH-12",
        stopped_at_the_estimate(),
        "checkpoint",
        cfg,
        decided("Go on."),
        estimate=200.0,
    )
    assert r.resumed["estimate_revisions"] == [{"cost_usd": 200.0, "source": "human"}]
    assert rules.estimate_in_force(r.resumed) == 200.0
    assert r.resumed["estimate"] == {"cost_usd": 40.0, "files": 10}


def test_the_proposal_alone_is_not_a_decision(cfg):
    # A proposed estimate with no answer from the human: nothing resumes, nothing is revised.
    with pytest.raises(resume.WaitingOnHuman, match="estimate"):
        resume.prepare("GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided())


def test_a_resume_refuses_an_estimate_whose_cap_is_already_spent(cfg):
    # 106.27 spent: an estimate of 50 caps at 100 and would stop at the first attempt.
    with pytest.raises(ValueError, match="already spent.*--estimate above"):
        resume.prepare(
            "GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided("Go on."), estimate=50.0
        )
    # The same when the proposal is the one in force and it is already spent at 2x.
    low = stopped_at_the_estimate(proposed_estimate=50.0)
    with pytest.raises(ValueError, match="already spent"):
        resume.prepare("GH-12", low, "checkpoint", cfg, decided("Go on."))


def test_the_estimate_flag_on_another_escalation_is_a_usage_error(cfg):
    with pytest.raises(ValueError, match="--estimate.*estimate"):
        resume.prepare("GH-12", paused(), "checkpoint", cfg, memory_ports(), estimate=200.0)


def test_the_flat_task_cap_still_governs_the_flat_cap_of_a_resume(cfg):
    # --task-budget governs only the flat cap: the estimate in force changes nothing there.
    with pytest.raises(ValueError, match="task cap in force"):
        resume.prepare(
            "GH-12",
            stopped_at_the_estimate(),
            "checkpoint",
            Configuration(product_dir="/tmp/p", task_max_budget_usd=100.0),
            decided("Go on."),
            estimate=200.0,
        )


def test_the_answer_the_graph_resumes_with_carries_the_flag_only_when_given(cfg):
    without = resume.prepare(
        "GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided("Go on.")
    )
    assert without.answer() == {"decision": "Go on."}
    given = resume.prepare(
        "GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided("Go on."), estimate=200.0
    )
    assert given.answer() == {"decision": "Go on.", "estimate": 200.0}


# --- GH-328: the decision a landing escalation reads ------------------------------------------


def test_decision_since_survives_the_system_comments_that_come_after_the_escalation():
    comments = ["Escalated: x", "merge master in", "Auto-merge refused: ci_missing"]
    assert resume.decision_since(comments, "Escalated: ") == "merge master in"
    assert resume.human_decision(comments) is None  # the role path is unchanged


def test_decision_since_joins_every_human_comment_after_the_last_escalation():
    comments = [
        "Escalated: first",
        "old answer",
        "Escalated: second",
        " merge master in ",
        "Landing: sync_master",
        "then fix the test\r\nfile",
        "  ",
    ]
    assert resume.decision_since(comments, "Escalated: ") == (
        "merge master in\n\nthen fix the test\nfile"
    )


def test_decision_since_is_none_without_a_human_comment_after_the_escalation():
    assert resume.decision_since(["a human before", "Escalated: x"], "Escalated: ") is None
    assert resume.decision_since(["Escalated: x", "Landing: rerun", "  "], "Escalated: ") is None
    assert resume.decision_since(["Only the task says this"], "Escalated: ") is None
    assert resume.decision_since([], "Escalated: ") is None


# A part whose plan is over the line asked "build it whole or stop" (ADR-0054): the pause is
# the plan stage's `rules.part_over_line`, kept with its subtasks, and a resume builds it whole.


def asked_to_build_whole():
    figures = schemas.FitDecision(False, 20.0, 5, 300.0)
    return {
        "id": "GH-338",
        "title": "Second half",
        "description": f"Second half.\n\n{rules.split_marker('GH-316', 2)}",
        "type": "feature",
        "cost_usd": 3.0,
        "estimate": {"cost_usd": 20.0, "files": 5},
        "subtasks": {"api": sub("api", "pending", None, None)},
        **rules.part_over_line("GH-338", ("GH-316", 2), figures),
    }


def test_a_comment_on_the_part_over_the_line_builds_it_whole_through_the_fan_in(cfg):
    r = resume.prepare(
        "GH-338",
        asked_to_build_whole(),
        "checkpoint",
        cfg,
        decided("Build it whole."),
    )
    assert r.target == "integrate"
    assert r.resumed["fit_waived"] is True and r.resumed["fit_question"] is False
    assert r.resumed.get("estimate_revisions") is None
    assert r.resumed["estimate"] == {"cost_usd": 20.0, "files": 5}


def test_the_estimate_flag_on_the_part_over_the_line_is_refused(cfg):
    with pytest.raises(ValueError, match="build the part whole.*comment on the issue and resume"):
        resume.prepare(
            "GH-338",
            asked_to_build_whole(),
            "checkpoint",
            cfg,
            decided("Build it whole."),
            estimate=200.0,
        )


def test_no_comment_on_the_part_over_the_line_repeats_the_question_and_never_resumes(cfg):
    state = asked_to_build_whole()
    with pytest.raises(resume.WaitingOnHuman) as asked:
        resume.prepare("GH-338", state, "checkpoint", cfg, decided())
    message = str(asked.value)
    assert "not planned" in message and "do not resume" in message
    assert message == state["escalated"]


def test_the_cap_at_twice_the_estimate_keeps_its_message_and_its_flag_usage(cfg):
    with pytest.raises(resume.WaitingOnHuman) as stopped:
        resume.prepare("GH-12", stopped_at_the_estimate(), "checkpoint", cfg, decided())
    assert str(stopped.value) == (
        "GH-12: the estimate does not fit; decide in comments on the issue "
        "whether to go on, with which estimate, or to stop, then resume"
    )


# --- an owed publication (GH-373): prepare reads the facts, rules.resume_target decides ----------

APPROVED = "f08dfc738de2" + "0" * 28
REMOTE = "6269eea" + "0" * 33
OWED_BRANCH = "agentic/GH-361"
OWED_DOCUMENT = ".agentic/tasks/GH-361.md"


def owed_full_checks(head=APPROVED, ok=True):
    return {
        "version": 1,
        "head": head,
        "passed": ok,
        "duration_s": 1.0,
        "declared": {"tests": "pytest"},
        "results": [{"name": "tests", "passed": ok}],
        "witness": {"passed": True},
    }


def stopped_before_publishing(**more):
    """GH-361 as its document says: integration approved at H, full checks passed on H, a pull
    request from an earlier delivery, and a run that died before pushing H."""
    return {
        "id": "GH-361",
        "title": "Speed",
        "description": "Make it fast.",
        "type": "feature",
        "status": "developing",
        "current_role": "Tech Lead",
        "branch": OWED_BRANCH,
        "pull_request": "https://github.com/o/r/pull/370",
        "task_head": APPROVED,
        "full_checks": owed_full_checks(),
        "cost_usd": 20.0,
        "runs": [],
        "subtasks": {
            "api": sub("api", "approved", "agentic/GH-361.api", "b1", reviewed_head="a2"),
            "integration": sub(
                "integration", "approved", OWED_BRANCH, "b0", reviewed_head=APPROVED
            ),
        },
        **more,
    }


def git_at_the_stop(ports, *, remote=REMOTE, branch_head=APPROVED[:7], remote_holds_head=False):
    ports.workspace.existing_branches[OWED_BRANCH] = (branch_head, "b0")
    ports.workspace.remote_tips[OWED_BRANCH] = remote
    ports.workspace.ancestors = {APPROVED} if remote_holds_head else set()
    # The remote tip is an ancestor of the head, which `is_ancestor(root, H, tip)` does not ask.


def test_an_unpushed_approved_head_resumes_into_deliver_alone(tmp_path):
    ports = memory_ports()
    git_at_the_stop(ports)
    paused_state = stopped_before_publishing()
    r = resume.prepare(
        "GH-361", paused_state, "document", Configuration(product_dir=tmp_path), ports
    )
    assert r.target == "deliver"
    assert r.resumed["publish_owed"] == APPROVED
    assert r.resumed["status"] == "developing"
    # Read on the product's root checkout; no worktree is made and no setup runs.
    assert ports.workspace.remote_tip_calls == [(tmp_path, OWED_BRANCH)]
    assert {path for path, _, _ in ports.workspace.ancestor_checks} == {tmp_path}
    assert ports.workspace.checkouts == []
    assert ports.commands.ran == []


def test_a_head_the_remote_holds_with_status_developing_is_still_owed(tmp_path):
    ports = memory_ports()
    git_at_the_stop(ports, remote=APPROVED, remote_holds_head=True)
    r = resume.prepare(
        "GH-361",
        stopped_before_publishing(),
        "document",
        Configuration(product_dir=tmp_path),
        ports,
    )
    # Pushed but the pull request and the status never moved: the resume finishes the delivery.
    assert (r.target, r.resumed["publish_owed"]) == ("deliver", APPROVED)


@pytest.mark.parametrize("status", ["ready_for_review", "waiting_on_human"])
def test_a_head_the_remote_holds_and_a_delivered_status_owes_nothing(tmp_path, status):
    ports = memory_ports()
    git_at_the_stop(ports, remote=APPROVED, remote_holds_head=True)
    delivered = stopped_before_publishing(status=status)
    with pytest.raises(ValueError, match="nothing left to run"):
        resume.prepare("GH-361", delivered, "document", Configuration(product_dir=tmp_path), ports)


def test_a_branch_that_moved_beyond_the_document_owes_nothing_and_says_what_it_said(tmp_path):
    ports = memory_ports()
    git_at_the_stop(ports, branch_head="9999999")
    ports.workspace.outside_differs = True
    with pytest.raises(ValueError, match=r"nothing left to run \(developing\)"):
        resume.prepare(
            "GH-361",
            stopped_before_publishing(),
            "document",
            Configuration(product_dir=tmp_path),
            ports,
        )
    # The document is the only path the question lets through, relative to the task's own.
    assert ports.workspace.outside_reads == [(tmp_path, APPROVED, "9999999", OWED_DOCUMENT)]


def test_a_branch_that_differs_from_the_head_only_in_the_task_document_still_owes_it(tmp_path):
    ports = memory_ports()
    git_at_the_stop(ports, branch_head="9999999")
    ports.workspace.outside_differs = False
    r = resume.prepare(
        "GH-361",
        stopped_before_publishing(),
        "document",
        Configuration(product_dir=tmp_path),
        ports,
    )
    assert (r.target, r.resumed["publish_owed"]) == ("deliver", APPROVED)


@pytest.mark.parametrize("failing", ["remote", "ancestry"])
def test_a_read_that_fails_says_the_head_could_not_be_checked_and_never_suggests_fresh(
    tmp_path, failing
):
    ports = memory_ports()
    git_at_the_stop(ports)
    if failing == "remote":
        ports.workspace.remote_tip_error = "git ls-remote failed: unreachable"
    else:
        ports.workspace.ancestor_errors = {APPROVED}
    with pytest.raises(ValueError) as refusal:
        resume.prepare(
            "GH-361",
            stopped_before_publishing(),
            "document",
            Configuration(product_dir=tmp_path),
            ports,
        )
    message = str(refusal.value)
    assert (
        f"GH-361: approved head {APPROVED[:7]} could not be checked against origin: retry"
        in message
    )
    assert "--fresh" not in message


def test_without_a_candidate_prepare_makes_no_remote_call_and_clears_publish_owed(tmp_path):
    ports = memory_ports()
    git_at_the_stop(ports)
    # The integration was not the head the full checks passed on: nobody approved this head.
    stale = stopped_before_publishing(
        full_checks=owed_full_checks(head="c" * 40), publish_owed=APPROVED
    )
    with pytest.raises(ValueError, match="nothing left to run"):
        resume.prepare("GH-361", stale, "document", Configuration(product_dir=tmp_path), ports)
    assert ports.workspace.remote_tip_calls == []
    assert ports.workspace.ancestor_checks == []
    assert ports.workspace.outside_reads == []


def test_a_resume_from_the_checkpoint_never_reads_the_remote_and_drops_a_stale_publish_owed(
    tmp_path,
):
    ports = memory_ports()
    git_at_the_stop(ports)
    r = resume.prepare(
        "GH-12",
        paused(publish_owed=APPROVED),
        "checkpoint",
        Configuration(product_dir=tmp_path, task_budget_usd=125),
        ports,
    )
    assert r.resumed["publish_owed"] is None
    assert ports.workspace.remote_tip_calls == []


def test_announcing_an_account_limit_only_pause_unlabels_nothing_it_never_labelled(cfg):
    log = [
        {"kind": "failure", "value": "account_limit", "section": "Developer"},
        {"kind": "escalation", "value": "failure"},
    ]
    ports = memory_ports()
    r = resume.prepare("GH-12", paused(pause_log=log), "checkpoint", cfg, ports)
    resume.announce(r, ports)
    assert ports.tracker.unlabeled == []
    assert len(ports.tracker.comments) == 1
