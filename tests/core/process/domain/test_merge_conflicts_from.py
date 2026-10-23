"""GH-326: `rules.merge_conflicts_from`, the only place GitHub's mergeability becomes a fact."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.task import PullRequest

HEAD = "a" * 40
MASTER = "b" * 40
OTHER = "c" * 40


def _pr(mergeable, head_oid=HEAD, base_oid=MASTER):
    return PullRequest(
        state="OPEN",
        created_at="t",
        merged_at=None,
        commits=(),
        head_oid=head_oid,
        mergeable=mergeable,
        base_oid=base_oid,
    )


@pytest.mark.parametrize("probe", [True, False, None])
def test_conflicting_on_matching_tips_is_a_conflict_whatever_the_probe_says(probe):
    assert rules.merge_conflicts_from(_pr("CONFLICTING"), HEAD, MASTER, probe) is True


@pytest.mark.parametrize("probe", [True, False, None])
@pytest.mark.parametrize(
    "pull_request",
    [
        _pr("MERGEABLE"),
        _pr("UNKNOWN"),
        _pr(None),
        _pr("CONFLICTING", head_oid=OTHER),
        _pr("CONFLICTING", head_oid=None),
        _pr("CONFLICTING", base_oid=OTHER),
        _pr("CONFLICTING", base_oid=None),
        None,
    ],
    ids=["mergeable", "unknown", "none", "stale-head", "no-head", "other-base", "no-base", "no-pr"],
)
def test_every_other_reading_is_the_probes_value(pull_request, probe):
    assert rules.merge_conflicts_from(pull_request, HEAD, MASTER, probe) is probe


def test_a_pull_request_without_a_reading_is_still_constructible_as_before():
    plain = PullRequest(state="OPEN", created_at="t", merged_at=None, commits=())
    assert (plain.mergeable, plain.base_oid) == (None, None)
