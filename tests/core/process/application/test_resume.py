"""What a resume decides before it spends, against in-memory ports (ADR-0003, addendum)."""

import pytest

from agentic.core.process.application import resume
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
    ports.workspace.branches.update(
        {
            "agentic/GH-12": ("i2", "b0"),
            "agentic/GH-12.api": ("a2", "b1"),
            "agentic/GH-12.domain": ("d2f00", "b1"),
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


def test_the_document_is_refused_when_git_moved_since():
    ports = memory_ports()
    ports.workspace.branches.update(
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


def test_a_resume_under_the_formula_goes_on_to_the_returned_integration(cfg):
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: " + CAP]
    ports.workspace.checkouts = [cfg.product_dir / ".agentic/worktrees/GH-12"]
    r = resume.prepare("GH-12", paused(cost_usd=60.0), "document", cfg, ports)
    assert r.decision is None and r.target == "develop"
    assert r.cap_usd == 30 + 15 * 3  # two subtasks and the integration
    assert r.resumed["escalated"] is None and r.resumed["status"] == "developing"
    text = resume.summary(r, cfg, ports)
    assert "reason:    " + CAP in text
    assert "spent:     60.00 USD (definition 0.00, plan 0.00, development 25.00, " in text
    assert "review 25.00, other 10.00)" in text
    assert "task cap:  75.00 USD (base 30 + 15 x 3 units, ceiling 250)" in text
    assert "resumes:   develop (integration)" in text
    assert "session:   Developer: integration: d1" in text
    assert "session:   Tech Lead: review integration: r1" in text
    assert "worktree:  " + str(cfg.product_dir / ".agentic/worktrees/GH-12") in text


def test_a_cap_already_spent_is_refused_and_the_human_can_raise_it(cfg, tmp_path):
    ports = memory_ports()
    with pytest.raises(ValueError, match="pass --task-budget above 106.27"):
        resume.prepare("GH-12", paused(), "checkpoint", cfg, ports)
    raised = Configuration(product_dir=tmp_path, task_budget_usd=125)
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
    r = resume.prepare("GH-12", state, "checkpoint", cfg, memory_ports(), ("develop",))
    assert r.resumed == state and r.target == "develop" and r.decision is None


def test_announcing_takes_the_task_out_of_the_queue(cfg):
    ports = memory_ports()
    raised = Configuration(product_dir=cfg.product_dir, task_budget_usd=125)
    ports.tracker.earlier = ["Escalated: " + CAP, "Go on, but keep MRR per active subscription."]
    r = resume.prepare("GH-12", paused(), "checkpoint", raised, ports)
    resume.announce(r, ports)
    assert ports.tracker.unlabeled == [("GH-12", "needs-human")]
    assert ports.tracker.comments == [
        ("GH-12", "Resumed: develop with your decision, under a task cap of 125.00 USD.")
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
    assert r.resumed["returns"] == {"Tech Lead→Developer:integration": 0}
    failed = paused(escalated="Developer failed 2 times: empty markdown")
    assert resume.prepare("GH-12", failed, "checkpoint", raised, memory_ports()).decision is None


def test_a_task_with_nothing_left_to_run_is_not_resumed(cfg):
    delivered = paused(escalated=None, pull_request="https://github.com/o/r/pull/7")
    with pytest.raises(ValueError, match="nothing left to run"):
        resume.prepare("GH-12", delivered, "document", raised(), memory_ports())
