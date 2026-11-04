"""GH-326: `rules.merge_conflicts_from`, the only place GitHub's mergeability becomes a fact."""

from __future__ import annotations

import inspect

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
    assert (
        rules.merge_conflicts_from(_pr("CONFLICTING"), HEAD, MASTER, probe, recorded=False) is True
    )


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
    assert rules.merge_conflicts_from(pull_request, HEAD, MASTER, probe, recorded=False) is probe


def test_a_pull_request_without_a_reading_is_still_constructible_as_before():
    plain = PullRequest(state="OPEN", created_at="t", merged_at=None, commits=())
    assert (plain.mergeable, plain.base_oid) == (None, None)


_MERGEABLE = ["CONFLICTING", "MERGEABLE", "UNKNOWN", None]


@pytest.mark.parametrize("probe", [True, False, None])
@pytest.mark.parametrize("recorded", [True, False])
@pytest.mark.parametrize("mergeable", _MERGEABLE)
@pytest.mark.parametrize("stale", [None, "head", "base"])
def test_truth_table_of_the_three_readings(mergeable, recorded, probe, stale):
    """GH-492: GitHub's `CONFLICTING` on this head and base, then the recorded conflict, then
    the probe; `UNKNOWN` is unread and never reads clean."""
    pull_request = _pr(
        mergeable,
        head_oid=OTHER if stale == "head" else HEAD,
        base_oid=OTHER if stale == "base" else MASTER,
    )
    expected = True if (mergeable == "CONFLICTING" and stale is None) or recorded else probe
    got = rules.merge_conflicts_from(pull_request, HEAD, MASTER, probe, recorded=recorded)
    assert got is expected


def test_unknown_with_no_record_and_a_clean_probe_is_never_a_conflict():
    assert rules.merge_conflicts_from(_pr("UNKNOWN"), HEAD, MASTER, False, recorded=False) is False


def test_unknown_with_no_record_and_an_unread_probe_stays_unread():
    assert rules.merge_conflicts_from(_pr("UNKNOWN"), HEAD, MASTER, None, recorded=False) is None


def test_a_recorded_conflict_beats_unknown_and_mergeable_whatever_the_probe_says():
    for mergeable in ("UNKNOWN", "MERGEABLE", None):
        assert (
            rules.merge_conflicts_from(_pr(mergeable), HEAD, MASTER, False, recorded=True) is True
        )
        assert rules.merge_conflicts_from(_pr(mergeable), HEAD, MASTER, None, recorded=True) is True


def test_recorded_has_no_default_and_is_keyword_only():
    parameter = inspect.signature(rules.merge_conflicts_from).parameters["recorded"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
