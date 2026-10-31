"""The walls of a declared verification and the readiness rule (ADR-0065, addendum GH-451)."""

import pytest

from agentic.core.process.domain.run_check import (
    DESCRIPTION,
    Outcome,
    WallOverCeiling,
    call_timeout_s,
    description,
    resolve_wall,
    saturated,
)

CHECK_S = 600.0
CEILING = 3600.0


def test_an_undeclared_name_resolves_to_the_check_timeout():
    assert resolve_wall("tests", {}, CHECK_S, CEILING) == CHECK_S
    assert resolve_wall("tests", {"lint": 30.0}, CHECK_S, CEILING) == CHECK_S


def test_a_declared_wall_is_returned_as_declared_below_or_above_the_check_timeout():
    declared = {"lint": 30.0, "compare": 1500.0}
    assert resolve_wall("lint", declared, CHECK_S, CEILING) == 30.0
    assert resolve_wall("compare", declared, CHECK_S, CEILING) == 1500.0


def test_a_wall_equal_to_the_ceiling_is_allowed():
    assert resolve_wall("compare", {"compare": CEILING}, CHECK_S, CEILING) == CEILING


def test_a_wall_over_the_ceiling_is_rejected_with_the_name_and_both_figures():
    with pytest.raises(WallOverCeiling) as raised:
        resolve_wall("compare", {"compare": CEILING + 1}, CHECK_S, CEILING)
    message = str(raised.value)
    assert "'compare'" in message
    assert "3601" in message
    assert "3600" in message
    assert "verification_wall_max_s" in message
    # a ValueError, so the CLI refuses with it like every other preflight refusal
    assert isinstance(raised.value, ValueError)


def test_a_wall_over_the_ceiling_is_never_clipped_to_it():
    for over in (CEILING + 0.5, CEILING * 2, CEILING * 100):
        with pytest.raises(WallOverCeiling):
            resolve_wall("compare", {"compare": over}, CHECK_S, CEILING)


def test_the_call_timeout_covers_a_retry_and_both_waits_and_the_witness():
    assert call_timeout_s([60.0, 1500.0, 30.0], 120.0, 10.0) == 1500.0 * 2 + 2 * 120.0 + 10.0


def test_the_call_timeout_follows_the_largest_wall_only():
    small = call_timeout_s([10.0, 20.0], 0.0, 0.0)
    assert small == 40.0
    assert call_timeout_s([20.0, 10.0], 0.0, 0.0) == small


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (1.0, 1.0, False),  # idle before and after
        (20.0, 1.0, True),  # saturated before only
        (1.0, 20.0, True),  # saturated after only (the check itself raised the load)
        (20.0, 20.0, True),  # saturated both times
    ],
)
def test_saturation_over_the_four_quadrants_of_load(before, after, expected):
    assert saturated(before, after, 16) is expected


def test_saturation_is_at_least_the_core_count():
    assert saturated(15.99, 15.99, 16) is False
    assert saturated(16.0, 0.0, 16) is True
    assert saturated(0.0, 16.0, 16) is True


def test_an_unread_load_counts_as_not_saturated_but_the_other_reading_still_counts():
    assert saturated(None, None, 16) is False
    assert saturated(None, 3.0, 16) is False
    assert saturated(40.0, None, 16) is True
    assert saturated(None, 40.0, 16) is True


def test_the_outcomes_are_the_closed_set_the_plan_names():
    assert [outcome.value for outcome in Outcome] == [
        "passed",
        "failed",
        "not_ready",
        "unverified",
        "not_declared",
    ]


def test_the_description_is_byte_for_byte_the_same_without_verifications():
    assert description([]) == DESCRIPTION
    assert description(()) == DESCRIPTION


def test_the_description_lists_the_declared_verifications_after_the_fixed_text():
    text = description(["compare", "e2e"])
    assert text.startswith(DESCRIPTION)
    assert "compare" in text
    assert "e2e" in text
    assert text != DESCRIPTION
