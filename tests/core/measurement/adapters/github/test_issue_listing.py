import json
from pathlib import Path

import pytest

from agentic.core.measurement.adapters.github.issue_listing import GhIssueListing
from agentic.core.measurement.application.ports import Page, PageUnreadable
from agentic.core.measurement.domain.issue_listing import ListedIssue
from agentic.core.process.application.ports import (
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)

FIXTURE = Path(__file__).parent / "fixtures" / "issues_page.json"
PATH = "repos/{owner}/{repo}/issues?state=all&per_page=100&sort=created&direction=asc&page="


class FakeGh:
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args):
        self.calls.append(args)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def listing(reply):
    gh = FakeGh(reply)
    return GhIssueListing(gh), gh


def test_pages_one_and_two_ask_gh_for_the_literal_placeholders_and_nothing_else():
    reader, gh = listing("[]")
    reader.page(1)
    reader.page(2)
    assert gh.calls == [("api", PATH + "1"), ("api", PATH + "2")]


def test_the_recorded_page_parses_to_one_issue_an_open_pr_and_a_merged_pr():
    reader, _ = listing(FIXTURE.read_text())
    page = reader.page(1)
    assert isinstance(page, Page) and page.size == 3
    issue, open_pr, merged = page.items
    assert issue == ListedIssue(4, issue.created_at, False, None)
    assert open_pr == ListedIssue(587, open_pr.created_at, True, None)
    assert merged.is_pr and merged.number == 1 and merged.pr_merged_at == "2026-09-12T19:23:13Z"


def test_the_size_is_the_raw_item_count():
    items = [{"number": n, "created_at": "t"} for n in range(1, 4)]
    page = listing(json.dumps(items))[0].page(1)
    assert isinstance(page, Page) and page.size == 3 and len(page.items) == 3


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        "{}",
        '[{"number": 1}]',
        '[{"created_at": "t"}]',
        '["x"]',
        '[{"number": "1", "created_at": "t"}]',
        '[{"number": 1, "created_at": "t", "pull_request": {}}]',
        '[{"number": 1, "created_at": "t", "pull_request": null}]',
        '[{"number": 1, "created_at": "t", "pull_request": "x"}]',
        '[{"number": 1, "created_at": "t", "pull_request": {"merged_at": 5}}]',
    ],
)
def test_an_unparsable_page_or_a_malformed_item_is_refused(reply):
    assert listing(reply)[0].page(1) == PageUnreadable("refused")


def test_a_null_merged_at_is_present_and_means_unmerged():
    reply = '[{"number": 1, "created_at": "t", "pull_request": {"merged_at": null}}]'
    page = listing(reply)[0].page(1)
    assert isinstance(page, Page) and page.items[0].is_pr and page.items[0].pr_merged_at is None


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (TrackerRateLimited("x"), "rate_limited"),
        (TrackerTimedOut("x"), "timed_out"),
        (TrackerUnreachable("x"), "unreachable"),
        (TrackerTransient("x"), "transient"),
        (TrackerRefused("x"), "refused"),
        (ValueError("gh is not installed"), "refused"),
        (FileNotFoundError("gh"), "refused"),
        (OSError("boom"), "refused"),
    ],
)
def test_each_exception_maps_by_type_to_a_closed_cause_and_page_never_raises(error, cause):
    assert listing(error)[0].page(1) == PageUnreadable(cause)
