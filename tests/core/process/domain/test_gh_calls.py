"""`kind_of` (GH-990): the one mapping from `gh` arguments to a `GhCallKind`."""

from typing import get_args

import pytest

from agentic.core.process.domain.gh_calls import GH_CALL_KINDS, GhCallKind, kind_of


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        (("issue", "list", "--state", "open"), "issue_list"),
        (("issue", "view", "7"), "issue_view"),
        (("pr", "list"), "pr_list"),
        (("pr", "view", "3", "--json", "state"), "pr_view"),
        (("api", "graphql"), "api"),
        (("api", "-i", "-X", "POST"), "api"),
        (("api", "rate_limit"), "api"),
        (("api",), "api"),
        (("api", "--paginate", "repos/{owner}/{repo}/issues/12/comments?per_page=100"), "api"),
        (("run", "view"), "other"),
        (("issue", "close", "7"), "other"),
        (("issue",), "other"),
        (("pr", "merge", "3"), "other"),
        (("view", "issue"), "other"),
        ((), "other"),
    ],
)
def test_kind_table(args, kind):
    assert kind_of(args) == kind


def test_kind_reads_only_the_first_two_arguments():
    assert kind_of(("run", "view", "api")) == "other"
    assert kind_of(("issue", "view", "pr", "list")) == "issue_view"


def test_the_tuple_holds_every_kind():
    assert get_args(GhCallKind) == GH_CALL_KINDS
    assert len(set(GH_CALL_KINDS)) == 6
