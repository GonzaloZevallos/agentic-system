"""The definitions of the Medición glossary, as functions over the state."""

import pytest

from agentic.core.measurement.domain import metrics


def run(role, version, cost):
    return {
        "role": role,
        "version": version,
        "session_id": "",
        "model": "m",
        "cost_usd": cost,
        "turns": 1,
    }


def test_rework_is_repeated_deliveries_plus_failed_attempts():
    state = {
        "runs": [run("PjM", 1, 0.01), run("PM", 1, 0.2), run("Product Analyst", 2, 0.1)],
        "failures": [{"role": "PM", "attempt": 1, "error": "e", "cost_usd": 0.05}],
    }
    assert metrics.rework_cost_usd(state) == pytest.approx(0.15)


def test_a_clean_task_has_no_rework():
    assert metrics.rework_cost_usd({"runs": [run("PjM", 1, 0.01)]}) == 0
    assert metrics.rework_cost_usd({}) == 0


def test_returns_total_sums_every_pair():
    assert metrics.returns_total({"returns": {"PM→Product Analyst": 2, "QA→Dev": 1}}) == 3
    assert metrics.returns_total({}) == 0


def test_prompts_fingerprint_ignores_order_and_is_short():
    a = metrics.prompts_fingerprint({"prompts": {"pjm": "aaa", "pm": "bbb"}})
    b = metrics.prompts_fingerprint({"prompts": {"pm": "bbb", "pjm": "aaa"}})
    assert a == b and len(a) == 8
    assert metrics.prompts_fingerprint({"prompts": {"pjm": "ccc", "pm": "bbb"}}) != a
    assert metrics.prompts_fingerprint({}) == ""
