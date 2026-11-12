"""GH-485: the poll waiting line and `with_ci` over a required name no check run carries."""

from agentic.core.process.application import poll
from agentic.core.process.domain import ci, rules

HEAD = "a" * 40


def _facts(**over) -> rules.MergeFacts:
    base = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        ci_required=("e2e",),
        ci_verdict=ci.CiVerdict("pending", (), ("e2e",)),
        ci_waited_s=30.0,
    )
    return rules.MergeFacts(**{**base.__dict__, **over})


def test_the_waiting_line_names_an_unreported_name_and_never_calls_it_pending_or_missing():
    line = poll.ci_wait_line(7, _facts(ci_unreported=("e2e",)))
    assert "unreported e2e" in line and "pending" not in line and "missing" not in line


def test_without_an_unreported_name_the_line_is_as_before():
    assert "missing e2e" in poll.ci_wait_line(7, _facts())


def test_escalation_recorded_matches_the_names_only_when_asked():
    from agentic.core.process.application import landing_plan

    raw = {
        "landing": [
            {"head": HEAD, "step": "escalate", "refusal": "ci_unknown_names", "names": ["e2e"]}
        ]
    }
    ask = landing_plan.escalation_recorded
    assert ask(raw, HEAD, "ci_unknown_names", "escalate")
    assert ask(raw, HEAD, "ci_unknown_names", "escalate", names=["e2e"])
    assert not ask(raw, HEAD, "ci_unknown_names", "escalate", names=["e2e", "pytest"])


def test_step_for_escalates_once_per_head_and_names():
    from agentic.core.process.application import landing_plan

    facts = _facts(ci_unreported=("e2e",))
    assert landing_plan.step_for({}, "ci_unknown_names", facts, turn="head").kind == "escalate"
    done = {
        "landing": [
            {"head": HEAD, "step": "escalate", "refusal": "ci_unknown_names", "names": ["e2e"]}
        ]
    }
    assert landing_plan.step_for(done, "ci_unknown_names", facts, turn="head").kind == "none"
    other = _facts(ci_unreported=("e2e", "pytest"))
    assert landing_plan.step_for(done, "ci_unknown_names", other, turn="head").kind == "escalate"


def _matrix_report(*, shards=("pytest (areas-1, a)", "pytest (areas-2, b)"), pending=(), done=True):
    """GH-546: a head whose plan-required `pytest` is a matrix job id, so no run carries that
    exact name: its expansions, and a check nobody asked for (`docs`), are what reported."""
    checks = tuple(
        ci.CiCheck(
            name,
            "IN_PROGRESS" if name in pending else "COMPLETED",
            None if name in pending else "SUCCESS",
            f"link-{index}",
        )
        for index, name in enumerate((*shards, "docs"))
    )
    return ci.CiReport(HEAD, "master", "b" * 40, None, checks, runs_completed=done)


def test_matrix_expansions_all_green_are_facts_with_nothing_unreported_and_a_green_verdict():
    facts = poll.with_ci(_facts(), {"ci_required": ["pytest"]}, (), _matrix_report())

    assert facts.ci_required == ("pytest",)
    assert facts.ci_unreported == ()
    assert facts.ci_verdict is not None and facts.ci_verdict.state == "green"


def test_a_pending_matrix_shard_is_a_pending_line_not_an_unreported_name():
    report = _matrix_report(pending=("pytest (areas-2, b)",), done=False)
    facts = poll.with_ci(_facts(), {"ci_required": ["pytest"]}, (), report)

    line = poll.ci_wait_line(7, facts, report)

    assert "pending pytest" in line
    assert "unreported" not in line and "not a check-run name" not in line
    assert "missing" not in line
