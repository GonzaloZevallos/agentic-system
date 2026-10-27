"""`account_reset.reset_at`: the stated reset of an account limit, as a bounded datetime
(ADR-0039, addendum GH-381)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentic.core.process.domain.account_reset import reset_at
from agentic.core.process.domain.rules import SESSION_WINDOW, SESSION_WINDOW_MARGIN

BUENOS_AIRES = timezone(timedelta(hours=-3))
ISSUE_MESSAGE = (
    "You've hit your session limit · resets 5:40am (America/Argentina/Buenos_Aires) "
    "(terminal reason: api_error, HTTP 429, exit code 1)"
)


def test_the_window_constants_say_what_the_provider_does():
    assert SESSION_WINDOW == timedelta(hours=5)
    assert SESSION_WINDOW_MARGIN == timedelta(minutes=10)


def test_the_issue_message_observed_at_one_am_resets_at_540_that_day():
    observed = datetime(2026, 10, 27, 1, 0, tzinfo=BUENOS_AIRES)
    assert reset_at(ISSUE_MESSAGE, observed) == datetime(2026, 10, 27, 5, 40, tzinfo=BUENOS_AIRES)


def test_the_result_is_in_the_stated_zone_whatever_zone_observed_at_came_in():
    observed = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)  # 01:00 in Buenos Aires
    result = reset_at(ISSUE_MESSAGE, observed)
    assert result == datetime(2026, 10, 27, 8, 40, tzinfo=UTC)
    assert result is not None and result.utcoffset() == timedelta(hours=-3)


def test_observed_after_the_reset_the_next_occurrence_is_a_day_away_and_is_refused():
    observed = datetime(2026, 10, 27, 5, 41, tzinfo=BUENOS_AIRES)
    assert reset_at(ISSUE_MESSAGE, observed) is None


def test_observed_exactly_at_the_reset_is_refused_because_it_is_not_strictly_after():
    observed = datetime(2026, 10, 27, 5, 40, tzinfo=BUENOS_AIRES)
    assert reset_at(ISSUE_MESSAGE, observed) is None


def test_observed_the_evening_before_is_beyond_the_window_and_is_refused():
    observed = datetime(2026, 10, 26, 23, 0, tzinfo=BUENOS_AIRES)
    assert reset_at(ISSUE_MESSAGE, observed) is None


def test_the_window_ends_inclusive_at_five_hours_ten_minutes():
    reset = datetime(2026, 10, 27, 5, 40, tzinfo=BUENOS_AIRES)
    edge = reset - SESSION_WINDOW - SESSION_WINDOW_MARGIN
    assert reset_at(ISSUE_MESSAGE, edge) == reset
    assert reset_at(ISSUE_MESSAGE, edge - timedelta(minutes=1)) is None


def test_an_hour_without_minutes_and_pm_is_read():
    observed = datetime(2026, 10, 27, 14, 0, tzinfo=BUENOS_AIRES)
    text = "You've hit your usage limit · resets 7pm (America/Argentina/Buenos_Aires)"
    assert reset_at(text, observed) == datetime(2026, 10, 27, 19, 0, tzinfo=BUENOS_AIRES)


def test_a_reset_at_1040am_inside_the_window_is_accepted():
    observed = datetime(2026, 10, 27, 7, 0, tzinfo=BUENOS_AIRES)
    text = ISSUE_MESSAGE.replace("5:40am", "10:40am")
    assert reset_at(text, observed) == datetime(2026, 10, 27, 10, 40, tzinfo=BUENOS_AIRES)


def test_twelve_am_is_midnight_and_twelve_pm_is_noon():
    observed = datetime(2026, 10, 27, 20, 0, tzinfo=BUENOS_AIRES)
    midnight = reset_at("resets 12:30am (America/Argentina/Buenos_Aires)", observed)
    assert midnight == datetime(2026, 10, 28, 0, 30, tzinfo=BUENOS_AIRES)
    noon = reset_at("resets 12pm (America/Argentina/Buenos_Aires)", observed.replace(hour=9))
    assert noon == datetime(2026, 10, 27, 12, 0, tzinfo=BUENOS_AIRES)


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your session limit · resets 5:40am (Mars/Olympus_Mons)",
        "You've hit your session limit · resets 5:40am",
        "HTTP 429 rate limited, try again later",
        "",
        "resets 25:40am (America/Argentina/Buenos_Aires)",
        "resets 13pm (America/Argentina/Buenos_Aires)",
        "resets 5:99am (America/Argentina/Buenos_Aires)",
        "resets 0am (America/Argentina/Buenos_Aires)",
    ],
)
def test_an_unknown_zone_or_any_other_text_returns_none(text):
    observed = datetime(2026, 10, 27, 1, 0, tzinfo=BUENOS_AIRES)
    assert reset_at(text, observed) is None


def test_a_naive_observed_at_is_refused_loudly():
    with pytest.raises(ValueError, match="timezone-aware"):
        reset_at(ISSUE_MESSAGE, datetime(2026, 10, 27, 1, 0))
