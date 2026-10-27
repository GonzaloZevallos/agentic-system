"""GH-392: a resume from the document that finds the task branch strictly past the head the
integration review approved takes the commits after it as an increment and reviews only
those; every other moved head is a refusal that names a safe command. Against in-memory
ports whose ancestry is scripted."""

from pathlib import Path

import pytest

from agentic.core.process.application import resume
from agentic.core.process.application.resume import Increment
from agentic.core.process.domain import rules
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

H = "i1"
X = "i2"
BRANCH = "agentic/GH-12"
PREFIX = "the document of GH-12 and git disagree:"
CHECKOUT = Path("/p/.agentic/worktrees/GH-12")


def sub(status, branch, base="b1", **more):
    return {"status": status, "branch": branch, "base": base, **more}


def stopped(**more):
    """GH-373's shape: every subtask approved, the integration approved at H, the run died."""
    return {
        "id": "GH-12",
        "title": "Dashboard",
        "type": "feature",
        "status": "waiting_on_human",
        "current_role": "Developer",
        "escalated": "Developer failed: no space left on device",
        "escalation_class": "failure",
        "branch": BRANCH,
        "cost_usd": 20.0,
        "runs": [],
        "subtasks": {
            "api": sub("approved", "agentic/GH-12.api", reviewed_head="a2"),
            "integration": sub("approved", BRANCH, "b0", reviewed_head=H),
        },
        **more,
    }


def world(head=X, document=None, ancestors=(H,)):
    ports = memory_ports()
    ports.workspace.existing_branches.update(
        {BRANCH: (head, "b0"), "agentic/GH-12.api": ("a2", "b1")}
    )
    ports.workspace.ancestors.update(ancestors)
    ports.documents.save(document or stopped())
    return ports


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path, task_budget_usd=125)


def refusal(ports, cfg):
    with pytest.raises(ValueError) as info:
        resume.seed_increment("GH-12", cfg, ports)
    return str(info.value)


def lines(message):
    assert message.startswith(PREFIX + "\n")
    return message.splitlines()[1:]


def test_a_descendant_of_the_approved_head_seeds_the_unit_back_to_review(cfg):
    ports = world()
    seed = resume.seed_increment("GH-12", cfg, ports)
    unit = seed.state["subtasks"]["integration"]
    assert (unit["status"], unit["reviewed_head"]) == ("delivered", H)
    assert seed.state["subtasks"]["api"]["status"] == "approved"
    assert seed.increment == Increment(H, X, BRANCH, None, False)
    # Ancestry is asked once, on the product root, for the approved head against the branch.
    assert ports.workspace.ancestor_checks == [(cfg.product_dir, H, X)]
    # Nothing but the unit's status moved: no head, no checks, no findings are written.
    others = {k: v for k, v in seed.state.items() if k != "subtasks"}
    assert others == {k: v for k, v in stopped().items() if k != "subtasks"}
    assert ports.workspace.commits == []


def test_the_seed_resumes_into_the_fan_in_and_the_summary_says_what_will_be_reviewed(cfg):
    ports = world()
    seed = resume.seed_increment("GH-12", cfg, ports)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    assert r.target == "integrate"
    assert r.increment == seed.increment
    text = resume.summary(r, cfg, ports)
    assert f"integration: approved at {H}, `{BRANCH}` is at {X}, a descendant: " in text
    assert f"reviewing the increment {H}..{X}" in text
    assert "will be committed" not in text


def test_a_head_equal_to_the_approved_one_is_no_increment_and_asks_nothing(cfg):
    ports = world(head=H)
    seed = resume.seed_increment("GH-12", cfg, ports)
    assert seed.increment is None
    assert seed.state == resume.from_document("GH-12", ports)
    assert ports.workspace.ancestor_checks == []


def test_a_head_that_does_not_contain_the_approved_one_is_refused_with_a_safe_way_out(cfg):
    message = refusal(world(ancestors=()), cfg)
    (line,) = lines(message)
    assert line.startswith(f"- integration: `{BRANCH}` is at {X}, which does not contain")
    assert "history rewritten" in line
    assert "agentic run --issue 12 --fresh" in line
    assert "branch -f" not in message and "reset" not in message and "delete" not in message


def test_ancestry_that_could_not_be_read_says_so_and_tells_to_retry(cfg):
    ports = world()
    ports.workspace.ancestor_errors.add(H)
    message = refusal(ports, cfg)
    (line,) = lines(message)
    assert "could not check whether" in line and f"`{BRANCH}` descends from {H}" in line
    assert "retry the resume" in line
    assert "--fresh" not in message


def test_a_pull_request_sends_the_human_to_the_landing(cfg):
    ports = world(document=stopped(pull_request="https://example.test/pr/9"))
    (line,) = lines(refusal(ports, cfg))
    assert "has a pull request" in line and "agentic land --issue 12" in line
    assert "branch -f" not in line


def test_another_approved_subtask_that_moved_keeps_todays_line(cfg):
    ports = world()
    ports.workspace.existing_branches["agentic/GH-12.api"] = ("a3", "b1")
    assert lines(refusal(ports, cfg)) == ["- api: approved at a2, but `agentic/GH-12.api` is at a3"]


def test_a_task_waiting_on_questions_is_not_an_increment_case(cfg):
    ports = world(document=stopped(escalation_class="questions", questions_for_human=["which?"]))
    assert lines(refusal(ports, cfg)) == [
        f"- integration: approved at {H}, but `{BRANCH}` is at {X}"
    ]


def test_a_unit_not_on_the_task_branch_keeps_todays_line_without_asking_ancestry(cfg):
    document = stopped()
    document["subtasks"]["integration"]["branch"] = "agentic/GH-12.other"
    ports = world(document=document)
    ports.workspace.existing_branches["agentic/GH-12.other"] = (X, "b0")
    assert lines(refusal(ports, cfg)) == [
        f"- integration: approved at {H}, but `agentic/GH-12.other` is at {X}"
    ]
    assert ports.workspace.ancestor_checks == []


@pytest.mark.parametrize("problem", ["missing branch", "base mismatch"])
def test_any_other_disagreement_is_the_text_from_document_raises(cfg, problem):
    ports = world(head=H)
    if problem == "missing branch":
        del ports.workspace.existing_branches["agentic/GH-12.api"]
    else:
        ports.workspace.existing_branches["agentic/GH-12.api"] = ("a2", "b9")
    with pytest.raises(ValueError) as expected:
        resume.from_document("GH-12", ports)
    assert refusal(ports, cfg) == str(expected.value)
    # With an increment as well, the same problem is listed and only the exempt unit's line goes.
    ports.workspace.existing_branches[BRANCH] = (X, "b0")
    assert lines(refusal(ports, cfg)) == str(expected.value).splitlines()[1:]


def test_a_missing_document_is_the_same_error_as_from_document(cfg):
    with pytest.raises(ValueError, match="GH-99 has no task document to resume from"):
        resume.seed_increment("GH-99", cfg, memory_ports())


def test_the_issue_flag_names_the_task_when_the_issue_is_unknown():
    ports = memory_ports()
    assert resume.issue_flag("GH-12", ports) == "--issue 12"
    assert resume.issue_flag("T-1", ports) == "--issue N  # T-1"


# --- the checkout of what a stopped run left ---------------------------------------------------


def with_checkout(ports, dirty=False):
    ports.workspace.checkouts = [CHECKOUT, Path("/p/.agentic/worktrees/GH-12.api")]
    if dirty:
        ports.workspace.dirty.add(CHECKOUT)


def test_a_dirty_task_checkout_is_noted_in_the_seed_but_never_committed_there(cfg):
    ports = world()
    with_checkout(ports, dirty=True)
    seed = resume.seed_increment("GH-12", cfg, ports)
    assert seed.increment == Increment(H, X, BRANCH, CHECKOUT, True)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    assert f"uncommitted files in `{CHECKOUT}` will be committed into the increment" in (
        resume.summary(r, cfg, ports)
    )
    assert ports.workspace.commits == []  # seeding and a dry run commit nothing


def test_announce_commits_what_the_stopped_run_left_once_before_the_comment(cfg):
    ports = world()
    with_checkout(ports, dirty=True)
    seed = resume.seed_increment("GH-12", cfg, ports)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    resume.announce(r, ports)
    assert ports.workspace.commits == [
        (CHECKOUT, "GH-12: commit what the stopped run left uncommitted")
    ]
    comment = ports.tracker.comments[-1][1]
    assert comment.startswith(resume.RESUMED) and f"{H}..{X}" in comment


def test_a_clean_checkout_commits_nothing(cfg):
    ports = world()
    with_checkout(ports)
    seed = resume.seed_increment("GH-12", cfg, ports)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    resume.announce(r, ports)
    assert ports.workspace.commits == []


@pytest.mark.parametrize("dirty", [True, False])
def test_a_merge_in_progress_refuses_the_seed_and_commits_nothing(cfg, dirty):
    ports = world()
    with_checkout(ports, dirty=dirty)
    ports.workspace.merges_in_progress.add(CHECKOUT)
    (line,) = lines(refusal(ports, cfg))
    assert f"the integration checkout `{CHECKOUT}` holds an unfinished merge or conflicts" in line
    assert "finish or abort it by hand, then resume" in line
    assert ports.workspace.commits == []


def test_a_merge_that_began_after_the_seed_refuses_the_announce_before_anything_is_said(cfg):
    ports = world()
    with_checkout(ports, dirty=True)
    seed = resume.seed_increment("GH-12", cfg, ports)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    ports.workspace.merges_in_progress.add(CHECKOUT)
    with pytest.raises(ValueError, match="unfinished merge or conflicts"):
        resume.announce(r, ports)
    assert ports.workspace.commits == []
    assert ports.tracker.comments == [] and ports.tracker.unlabeled == []


def test_a_merge_state_that_could_not_be_read_says_to_retry(cfg):
    ports = world()
    with_checkout(ports)
    ports.workspace.merge_in_progress_error = "git status failed"
    (line,) = lines(refusal(ports, cfg))
    assert "git status failed" in line and "retry the resume" in line


def test_a_resume_without_an_increment_emits_the_same_comment_as_before(cfg):
    ports = world(head=H)
    seed = resume.seed_increment("GH-12", cfg, ports)
    r = resume.prepare("GH-12", seed.state, "document", cfg, ports, increment=seed.increment)
    resume.announce(r, ports)
    assert ports.tracker.comments[-1][1] == (
        f"{resume.RESUMED}{r.target}, under a task cap of {r.cap_usd:.2f} USD."
    )
    assert ports.workspace.merge_in_progress_asks == []


def test_the_rule_is_the_domain_s_alone(cfg):
    # The seed decides nothing the rule does not: the same facts through the rule agree.
    assert rules.integration_increment(stopped(), "GH-12", X, True) == "increment"
