"""GH-371, subtask landing-escalate: a required check red on the head and also red on the base
branch ends the landing in an escalation, with no model step. Pure: `landing.landing_step`
and `ci.red_on_base`."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci, landing
from tests.core.process.domain.test_landing import _facts, _spent


def _red(name: str) -> ci.CiCheck:
    return ci.CiCheck(name, "COMPLETED", "FAILURE", f"https://ci/{name}")


def _verdict(*names: str) -> ci.CiVerdict:
    return ci.CiVerdict("red", tuple(_red(n) for n in names), ())


def _step(on_base: tuple[str, ...], failing: tuple[str, ...], **spent):
    facts = _facts(
        ci_required=("web", "e2e"), ci_verdict=_verdict(*failing), ci_red_on_base=on_base
    )
    return landing.landing_step("ci_failed", facts, _spent(**spent), ("api",))


def test_every_failing_name_red_on_the_base_escalates():
    assert _step(("web", "e2e"), ("web", "e2e")).kind == "escalate"


def test_a_name_red_on_the_base_only_does_not_keep_the_escalation_from_the_failing_ones():
    assert _step(("web", "e2e"), ("web",)).kind == "escalate"


@pytest.mark.parametrize(
    ("spent", "expected"),
    [({}, "rerun"), ({"reruns_on_head": 1}, "fix")],
)
def test_only_some_failing_names_red_on_the_base_goes_the_old_way(spent, expected):
    assert _step(("web",), ("web", "e2e"), **spent).kind == expected


@pytest.mark.parametrize(
    ("spent", "expected"),
    [({}, "rerun"), ({"reruns_on_head": 1}, "fix")],
)
def test_nothing_red_on_the_base_goes_the_old_way(spent, expected):
    assert _step((), ("web",), **spent).kind == expected


def test_a_red_on_the_base_without_a_verdict_to_cover_goes_the_old_way():
    facts = _facts(ci_required=("web",), ci_red_on_base=("web",))
    assert landing.landing_step("ci_failed", facts, _spent(), ()).kind == "rerun"


def test_the_escalation_spends_no_model_and_its_child_is_a_checks_one():
    assert "escalate" in landing.LandingStepKind.__args__  # type: ignore[attr-defined]
    assert landing.run_kind_of("escalate", "ci_failed") == "land_checks"
    assert landing.run_kind_admits("land_checks", "escalate", "ci_failed")


def test_a_spent_budget_does_not_turn_the_escalation_into_a_pass():
    step = _step(("web",), ("web",), passes=landing.LANDING_PASSES_MAX, reruns_on_head=1)
    assert step.kind == "escalate" and step.refusal == "ci_failed"


def _report(*checks: ci.CiCheck) -> ci.CiReport:
    return ci.CiReport("a" * 40, "master", "b" * 40, None, checks)


def test_red_on_base_names_the_failing_head_names_that_also_fail_on_the_base():
    head = _report(_red("web"), _red("e2e"), ci.CiCheck("test", "COMPLETED", "SUCCESS", "u"))
    base = _report(_red("web"), ci.CiCheck("e2e", "COMPLETED", "SUCCESS", "u"), _red("test"))
    assert ci.red_on_base(head, base, ("web", "e2e", "test")) == ("web",)


def test_red_on_base_without_a_base_report_is_empty():
    assert ci.red_on_base(_report(_red("web")), None, ("web",)) == ()


def test_red_on_base_ignores_a_pending_base_run_and_an_undeclared_name():
    pending = ci.CiCheck("web", "IN_PROGRESS", None, "u")
    assert ci.red_on_base(_report(_red("web")), _report(pending), ("web",)) == ()
    assert ci.red_on_base(_report(_red("web")), _report(_red("web")), ("e2e",)) == ()
