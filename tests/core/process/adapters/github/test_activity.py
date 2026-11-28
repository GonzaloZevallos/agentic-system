"""`GhActivity` and `ActivityPage` (GH-1058): the Activity read over `gh`, with a fake `run`."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.activity import (
    ACTIVITY_READ_TIMEOUT_S,
    RAW_LIMIT,
    GhActivity,
)
from agentic.core.process.adapters.github.run import GhRetry
from agentic.core.process.application.ports import (
    ActivityFailed,
    ActivityPage,
    ActivityRow,
)

SINCE = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)


@dataclass
class FakeRun:
    """A `Run`: plays the outcomes in order (an exception is raised, a string is stdout) and
    answers the last one again; keeps every argv and kwargs."""

    outcomes: list[object]
    argv: list[list[str]] = field(default_factory=list)
    kwargs: list[dict] = field(default_factory=list)

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(args, 0, stdout=str(outcome), stderr="")


def pr(number, at, title="t"):
    return {"number": number, "title": title, "url": f"https://x/pull/{number}", "mergedAt": at}


def issue(number, at, title="t"):
    return {"number": number, "title": title, "url": f"https://x/issues/{number}", "closedAt": at}


def adapter(outcome, *, retry=None):
    run = FakeRun(
        [json.dumps(outcome) if not isinstance(outcome, (str, BaseException)) else outcome]
    )
    return GhActivity(Path("."), retry=retry, access=None, calls=None, run=run), run


def at(seconds, micro=0):
    return (SINCE + timedelta(seconds=seconds, microseconds=micro)).strftime("%Y-%m-%dT%H:%M:%SZ")


def numbers(page):
    return [row.number for row in page.rows]


# --- ordering, limit, cut -------------------------------------------------------------------


def test_rows_come_oldest_first_and_equal_instants_by_number():
    gh, _ = adapter([pr(9, at(30)), pr(7, at(20)), pr(5, at(20)), pr(3, at(10))])
    page = gh.merged_prs(SINCE, 50)
    assert numbers(page) == [3, 5, 7, 9]
    assert page.cut_at is None and not page.cut
    assert page.rows[0] == ActivityRow(3, "t", "https://x/pull/3", SINCE + timedelta(seconds=10))


def test_the_limit_cuts_and_cut_at_is_the_instant_of_the_next_row():
    gh, _ = adapter([pr(n, at(n)) for n in (5, 1, 4, 2, 3)])
    page = gh.merged_prs(SINCE, 3)
    assert numbers(page) == [1, 2, 3]
    assert page.cut and page.cut_at == SINCE + timedelta(seconds=4)


def test_exactly_the_limit_is_no_cut():
    gh, _ = adapter([pr(n, at(n)) for n in (1, 2, 3)])
    page = gh.merged_prs(SINCE, 3)
    assert numbers(page) == [1, 2, 3] and page.cut_at is None


# --- since --------------------------------------------------------------------------------


def test_since_is_exclusive_including_a_row_300_ms_after_it_in_the_same_second():
    since = SINCE + timedelta(milliseconds=300)
    gh, _ = adapter(
        [
            pr(1, at(0)),  # the floored second: before since, dropped
            pr(2, "2026-11-20T10:00:00.300000Z"),  # exactly since, dropped
            pr(3, "2026-11-20T10:00:00.600000Z"),  # after since in the same second, kept
            pr(4, at(1)),
        ]
    )
    assert numbers(gh.merged_prs(since, 50)) == [3, 4]


def test_the_query_floors_since_to_the_second_and_never_rounds_up():
    since = datetime(2026, 11, 20, 12, 0, 59, 999_999, tzinfo=timezone(timedelta(hours=2)))
    gh, run = adapter([])
    gh.merged_prs(since, 50)
    assert "merged:>=2026-11-20T10:00:59Z" in run.argv[0]


def test_a_since_with_no_time_zone_is_a_failure_with_the_type_name():
    gh, _ = adapter([])
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(datetime(2026, 11, 20), 50)
    assert raised.value.cause == "ValueError"


# --- instants -----------------------------------------------------------------------------


def test_rows_without_an_instant_are_dropped_and_z_is_utc():
    gh, _ = adapter([pr(1, None), pr(2, at(5))])
    page = gh.merged_prs(SINCE, 50)
    assert numbers(page) == [2]
    assert page.rows[0].instant.utcoffset() == timedelta(0)


def test_an_offset_instant_is_converted_to_utc():
    gh, _ = adapter([pr(1, "2026-11-20T12:00:05+02:00")])
    instant = gh.merged_prs(SINCE, 50).rows[0].instant
    assert instant == SINCE + timedelta(seconds=5) and instant.utcoffset() == timedelta(0)


def test_a_naive_instant_is_malformed():
    gh, _ = adapter([pr(1, "2026-11-20T10:00:05")])
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(SINCE, 50)
    assert raised.value.cause == "ValueError"


# --- the query ----------------------------------------------------------------------------


def test_merged_prs_asks_gh_pr_list_in_the_product_checkout_with_a_bound():
    gh, run = adapter([])
    gh.merged_prs(SINCE, 50)
    assert run.argv == [
        [
            "gh", "pr", "list", "--state", "merged", "--search", "merged:>=2026-11-20T10:00:00Z",
            "--json", "number,title,url,mergedAt", "--limit", "1000",
        ]
    ]  # fmt: skip
    assert run.kwargs[0]["timeout_s"] == ACTIVITY_READ_TIMEOUT_S == 60
    assert run.kwargs[0]["cwd"] == Path(".")


def test_closed_issues_asks_gh_issue_list_and_counts_a_not_planned_close():
    gh, run = adapter([issue(4, at(3)), issue(2, at(1))])
    page = gh.closed_issues(SINCE, 50)
    assert numbers(page) == [2, 4]
    assert run.argv[0][:3] == ["gh", "issue", "list"]
    assert "closed:>=2026-11-20T10:00:00Z" in run.argv[0]
    assert "number,title,url,closedAt" in run.argv[0]


def test_a_raw_answer_of_a_thousand_rows_is_the_ceiling():
    gh, _ = adapter([pr(n, at(n)) for n in range(1, RAW_LIMIT + 1)])
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(SINCE, 50)
    assert raised.value.cause == "ActivityCeiling"


def test_one_row_under_the_ceiling_is_read():
    gh, _ = adapter([pr(n, at(n)) for n in range(1, RAW_LIMIT)])
    assert gh.merged_prs(SINCE, 50).cut


# --- failures -----------------------------------------------------------------------------

SECRET = "token ghp_SECRET leaked: repository not found"


def test_a_failed_gh_is_the_type_name_only_with_no_output_status_or_chain():
    run = subprocess.CalledProcessError(1, ["gh"], output="", stderr=f"{SECRET} (HTTP 404)")
    gh, _ = adapter(run, retry=GhRetry(max_wait_s=0, attempts=1))
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(SINCE, 50)
    assert raised.value.cause == "TrackerRefused"
    assert SECRET not in str(raised.value) and "404" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_a_failure_names_the_tracker_failure_class():
    gh, _ = adapter(subprocess.TimeoutExpired(["gh"], 60))
    with pytest.raises(ActivityFailed) as raised:
        gh.closed_issues(SINCE, 50)
    assert str(raised.value) == "TrackerTimedOut"


@pytest.mark.parametrize(
    ("stdout", "cause"),
    [
        ("not json", "JSONDecodeError"),
        ('{"a": 1}', "TypeError"),
        ('[{"number": 1}]', "KeyError"),
        (
            '[{"number": "1", "title": "t", "url": "u", "mergedAt": "2026-11-21T00:00:00Z"}]',
            "TypeError",
        ),
        ('[{"number": 1, "title": "t", "url": "u", "mergedAt": "nonsense"}]', "ValueError"),
    ],
)
def test_a_malformed_answer_is_the_type_name_of_what_went_wrong(stdout, cause):
    gh, _ = adapter(stdout)
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(SINCE, 50)
    assert raised.value.cause == cause and raised.value.__cause__ is None


def test_a_rate_limit_over_the_bound_fails_without_sleeping():
    slept: list[float] = []
    limited = subprocess.CalledProcessError(
        1, ["gh"], output="", stderr="gh: API rate limit exceeded for user ID 1. (HTTP 403)"
    )
    probe = json.dumps({"resources": {"core": {"remaining": 0, "reset": 1_100}}})
    run = FakeRun([limited, probe])
    retry = GhRetry(
        max_wait_s=30, attempts=2, backoff_s=(5,), sleep=slept.append, now=lambda: 1_000
    )
    gh = GhActivity(Path("."), retry=retry, access=None, calls=None, run=run)
    with pytest.raises(ActivityFailed) as raised:
        gh.merged_prs(SINCE, 50)
    assert raised.value.cause == "TrackerRateLimited" and slept == []
    assert "rate limit" not in str(raised.value).lower() and "403" not in str(raised.value)


def test_a_gateway_error_is_retried_once_then_read():
    slept: list[float] = []
    bad = subprocess.CalledProcessError(1, ["gh"], output="", stderr="gh: HTTP 502")
    run = FakeRun([bad, json.dumps([pr(1, at(5))])])
    retry = GhRetry(max_wait_s=30, attempts=2, backoff_s=(5,), sleep=slept.append)
    gh = GhActivity(Path("."), retry=retry, access=None, calls=None, run=run)
    assert numbers(gh.merged_prs(SINCE, 50)) == [1] and sum(slept) == 5


# --- ActivityPage -------------------------------------------------------------------------


def row(number, instant):
    return ActivityRow(number, "t", "u", instant)


def test_a_page_accepts_ordered_utc_rows_and_a_cut_at_or_after_the_last():
    last = SINCE + timedelta(seconds=2)
    page = ActivityPage((row(1, SINCE), row(2, SINCE), row(3, last)), last)
    assert page.cut and ActivityPage((), None).cut is False


@pytest.mark.parametrize(
    "rows, cut_at",
    [
        ((row(1, datetime(2026, 11, 20, 10)),), None),
        ((row(1, SINCE.astimezone(timezone(timedelta(hours=2)))),), None),
        ((row(1, SINCE),), datetime(2026, 11, 20, 11)),
        ((row(1, SINCE),), SINCE.astimezone(timezone(timedelta(hours=1)))),
        ((row(2, SINCE), row(1, SINCE)), None),
        ((row(1, SINCE + timedelta(seconds=1)), row(2, SINCE)), None),
        ((row(1, SINCE + timedelta(seconds=5)),), SINCE),
    ],
)
def test_a_page_rejects_a_naive_or_offset_instant_unordered_rows_and_an_early_cut(rows, cut_at):
    with pytest.raises(ValueError):
        ActivityPage(rows, cut_at)
