"""The planning split of `eval compare`: `core.measurement.domain.planning` (GH-472, ADR-0086)."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.measurement.domain import metrics, planning
from agentic.core.process.domain.roles import ROLES

_ORIGIN = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _iso(minutes):
    return (_ORIGIN + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def run(slug, cost, start=None, end=None):
    out = {"role": "r", "slug": slug, "section": "s", "version": 1, "cost_usd": cost, "turns": 1}
    if start is not None:
        out["started_at"] = _iso(start)
        out["ended_at"] = _iso(end)
    return out


def route(pre, route_, *, misrouted=False, critic="skipped"):
    return {
        "pre_estimate_usd": pre,
        "files": 3,
        "threshold_usd": 55.0,
        "route": route_,
        "misrouted": misrouted,
        "critic": critic,
    }


def doc(*runs, plan_route=None, plan=None, calibration=0, other_returns=0, arm=None):
    out = {
        "runs": list(runs),
        "return_history": [
            *({"pair": "p", "category": "plan-calibration", "reason": "x"},) * calibration,
            *({"pair": "p", "category": "spec-behaviour", "reason": "x"},) * other_returns,
        ],
    }
    if plan_route is not None:
        out["plan_route"] = plan_route
    if plan is not None:
        out["estimate"] = {"cost_usd": plan, "files": 3}
    if arm is not None:
        out["assignment"] = {"experiment": "e", "arm": "a", "switches": arm}
    return out


CONTROL = {"plan_review": "interview"}
CRITIC = {"plan_review": "critic"}


def test_the_tuple_is_the_planning_roles_of_roles_py_and_neither_sizer_nor_consultant():
    known = {role.slug for role in ROLES}
    assert set(planning.PLANNING_SLUGS) <= known
    assert "plan_sizer" in known and "pm_consultant" in known
    assert not {"plan_sizer", "pm_consultant"} & set(planning.PLANNING_SLUGS)
    assert {"tech_lead_critic", "tech_lead_critic_platform"} <= set(planning.PLANNING_SLUGS)


def test_two_arms_both_routes_a_misroute_and_a_critic_failure_are_every_figure():
    documents = [
        # control arm, interview route: 2 plan-calibration returns, planning 0-10 and 5-20 min
        doc(
            run("tech_lead_ephemeral", 3.0, 0, 10),
            run("tech_lead_interviewee", 2.0, 5, 20),
            run("tech_lead", 5.0, 20, 30),
            run("plan_sizer", 0.25, -2, -1),
            run("developer", 99.0, 40, 50),
            plan_route=route(70.0, "interview"),
            plan=80.0,
            calibration=2,
            other_returns=1,
            arm=CONTROL,
        ),
        # critic arm, critic route, ran fine
        doc(
            run("tech_lead", 4.0, 0, 6),
            run("tech_lead_critic", 1.5, 6, 9),
            run("plan_sizer", 0.5, -2, -1),
            plan_route=route(20.0, "critic", critic="ran"),
            plan=25.0,
            calibration=1,
            arm=CRITIC,
        ),
        # critic arm, critic route, critic failed
        doc(
            run("tech_lead", 4.0, 0, 6),
            run("tech_lead_critic", 0.5, 6, 7),
            run("plan_sizer", 0.5),
            plan_route=route(10.0, "critic", critic="failed"),
            plan=12.0,
            arm=CRITIC,
        ),
        # critic arm, misrouted to the interview
        doc(
            run("tech_lead", 4.0, 0, 6),
            run("tech_lead_ephemeral", 3.0, 6, 8),
            run("tech_lead_interviewee", 2.0, 8, 12),
            run("tech_lead", 6.0, 12, 20),
            run("plan_sizer", 0.5),
            run("pm_consultant", 7.0, 0, 30),
            plan_route=route(30.0, "interview", misrouted=True),
            plan=90.0,
            arm=CRITIC,
        ),
    ]

    table = planning.table(documents)

    assert set(table) == {"default", metrics.arm_label(CRITIC)}
    control = table["default"]
    critic = table[metrics.arm_label(CRITIC)]

    assert set(control) == {"interview"}
    interview = control["interview"]
    assert interview.tasks == 1
    assert interview.plan_returns == 2
    assert interview.planning_cost_usd == pytest.approx(10.0)
    assert interview.planning_time_s == pytest.approx(30 * 60)  # union 0-20 and 20-30
    assert interview.sizing_cost_usd == pytest.approx(0.25)
    assert interview.misrouted == 0
    assert interview.critic_failed == 0
    assert interview.pairs == ((70.0, 80.0),)

    assert set(critic) == {"critic", "interview"}
    on_critic = critic["critic"]
    assert on_critic.tasks == 2
    assert on_critic.plan_returns == 1
    assert on_critic.planning_cost_usd == pytest.approx(10.0)
    assert on_critic.planning_time_s == pytest.approx((9 + 7) * 60)
    assert on_critic.sizing_cost_usd == pytest.approx(1.0)
    assert on_critic.critic_failed == 1
    assert on_critic.misrouted == 0
    assert on_critic.pairs == ((20.0, 25.0), (10.0, 12.0))

    misrouted = critic["interview"]
    assert misrouted.tasks == 1
    assert misrouted.misrouted == 1
    assert misrouted.planning_cost_usd == pytest.approx(15.0)  # the consultant is no planning
    assert misrouted.planning_time_s == pytest.approx(20 * 60)
    assert misrouted.pairs == ((30.0, 90.0),)


def test_a_document_without_plan_route_is_unsized_and_default():
    old = {
        "runs": [run("tech_lead", 2.0, 0, 5), run("tech_lead_ephemeral", 1.0, 5, 6)],
        "estimate": {"cost_usd": 40.0, "files": 2},
        "return_history": [{"pair": "p", "category": "plan-calibration", "reason": "x"}],
    }

    table = planning.table([old])

    assert list(table) == ["default"]
    assert list(table["default"]) == [planning.UNSIZED]
    figures = table["default"][planning.UNSIZED]
    assert figures.tasks == 1
    assert figures.plan_returns == 1
    assert figures.planning_cost_usd == pytest.approx(3.0)
    assert figures.sizing_cost_usd == 0.0
    assert figures.pairs == ()


def test_a_run_without_both_bounds_costs_but_adds_no_time_and_a_landing_is_no_return():
    document = doc(
        run("tech_lead", 2.0, 0, 5),
        run("tech_lead_critic", 1.0),
        plan_route=route(5.0, "critic", critic="ran"),
        arm=CRITIC,
    )
    document["return_history"] = [
        {"pair": "p", "category": "plan-calibration", "reason": "x", "by": "landing"}
    ]

    figures = planning.table([document])[metrics.arm_label(CRITIC)]["critic"]

    assert figures.planning_cost_usd == pytest.approx(3.0)
    assert figures.planning_time_s == pytest.approx(5 * 60)
    assert figures.plan_returns == 0
    assert figures.pairs == ()  # no plan estimate on the document: no pair, never a zero


def test_the_means_are_per_task_and_empty_is_none():
    a = doc(run("tech_lead", 4.0, 0, 2), plan_route=route(5.0, "critic"), calibration=2, arm=CRITIC)
    b = doc(run("tech_lead", 2.0, 0, 4), plan_route=route(5.0, "critic"), arm=CRITIC)

    figures = planning.table([a, b])[metrics.arm_label(CRITIC)]["critic"]

    assert figures.returns_per_task == pytest.approx(1.0)
    assert figures.cost_per_task == pytest.approx(3.0)
    assert figures.time_per_task_s == pytest.approx(3 * 60)
    assert planning.PlanningFigures().cost_per_task is None


def test_the_lines_print_every_figure_per_bucket_and_the_pairs():
    documents = [
        doc(
            run("tech_lead", 4.0, 0, 6),
            run("tech_lead_critic", 1.0, 6, 9),
            run("plan_sizer", 0.5),
            plan_route=route(20.0, "critic", critic="failed"),
            plan=25.0,
            calibration=1,
            arm=CRITIC,
        ),
        doc(run("tech_lead", 2.0, 0, 4), plan=40.0),
    ]
    table = planning.table(documents)

    critic_lines = planning.lines(table[metrics.arm_label(CRITIC)])
    old_lines = planning.lines(table["default"])

    text = "\n".join(critic_lines)
    assert "critic" in critic_lines[1]
    for expected in ("5.0000", "540", "0.5000", "20.00->25.00"):
        assert expected in text
    assert critic_lines[1].split()[:3] == ["critic", "1", "1"]
    assert old_lines[1].split()[0] == "unsized"
    assert "pairs" not in "\n".join(old_lines)
    assert planning.lines({}) == []


def test_the_pre_estimate_pair_reads_the_declared_figure_when_present():
    raised = doc(plan_route=route(30.0, "interview"), plan=16.0)
    raised["estimate"]["declared_usd"] = 6.0
    plain = doc(plan_route=route(30.0, "interview"), plan=16.0)
    assert planning.table([raised])["default"]
    pairs = [
        figures.pairs
        for arm in planning.table([raised, plain]).values()
        for figures in arm.values()
    ]
    assert sorted(pair for group in pairs for pair in group) == [(30.0, 6.0), (30.0, 16.0)]
