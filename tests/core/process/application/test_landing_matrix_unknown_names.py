"""GH-546: the landing never escalates `ci_unknown_names` for a plan-required matrix job id
(`pytest`) whose expansions `pytest (<values>)` reported. The facts are `poll.with_ci`'s over a
reduced GH-399 report; the refusal is `rules.auto_merge_decision`'s and the step
`landing_plan.step_for`'s (`landing.landing_step` over the document's budgets). It lives in
`application` because `poll.with_ci` does, the one place the facts' `ci_unreported` is read from
a report, so the escalation can only name what that reading left."""

from __future__ import annotations

from agentic.core.process.application import landing_plan, poll
from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.landing import LandingStep
from tests.core.process.application.test_poll_unreported import HEAD, _facts

SHARDS = ("pytest (areas-1, a)", "pytest (areas-2, b)", "pytest (areas-3, c)")


def _report(names, *, completed: bool | None) -> ci.CiReport:
    checks = tuple(
        ci.CiCheck(name, "COMPLETED", "SUCCESS", f"link-{index}")
        for index, name in enumerate(names)
    )
    return ci.CiReport(HEAD, "master", "b" * 40, None, checks, runs_completed=completed)


def _read(report: ci.CiReport) -> rules.MergeFacts:
    """The merge facts of a plan that requires `pytest` and nothing else the stack declares."""
    facts = poll.with_ci(
        _facts(ci_untrusted=False, ci_head=HEAD),
        {"ci_required": ["pytest"]},
        ("docs",),
        report,
        fresh=True,
    )
    assert facts.ci_required == ("docs", "pytest")
    return facts


def _answer(facts: rules.MergeFacts) -> tuple[rules.MergeDecision, LandingStep | None]:
    """The merge decision of `facts`, and the landing step when it is a refusal."""
    decision = rules.auto_merge_decision(facts)
    if decision.kind != "refuse" or decision.reason is None:
        return decision, None
    return decision, landing_plan.step_for({}, decision.reason, facts, turn="head")


def test_every_expansion_reported_and_completed_names_nothing_to_escalate():
    facts = _read(_report((*SHARDS, "docs"), completed=True))

    decision, step = _answer(facts)

    assert facts.ci_unreported == ()
    assert decision.kind == "merge" and step is None


def test_a_part_of_the_shards_with_the_runs_not_known_complete_waits_and_escalates_nothing():
    facts = _read(_report((*SHARDS[:2], "docs"), completed=None))

    decision, step = _answer(facts)

    assert facts.ci_unreported == ()
    assert decision.kind == "wait" and step is None
