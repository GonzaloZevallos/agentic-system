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
    assert landing_plan.step_for({}, "ci_unknown_names", facts).kind == "escalate"
    done = {
        "landing": [
            {"head": HEAD, "step": "escalate", "refusal": "ci_unknown_names", "names": ["e2e"]}
        ]
    }
    assert landing_plan.step_for(done, "ci_unknown_names", facts).kind == "none"
    other = _facts(ci_unreported=("e2e", "pytest"))
    assert landing_plan.step_for(done, "ci_unknown_names", other).kind == "escalate"
