"""GH-462: `rules.ci_dispatched` and `rules.ci_trusted`, and what `auto_merge_decision` does
with them. Pure: a `FullChecksRecord` and a pull request head in, a verdict out. The merge
gate trusts a CI whose definition the change touched only when a dispatched run of the base's
workflow passed on this very head."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.task import FullChecksRecord

HEAD = "a" * 40
OTHER = "c" * 40
MASTER = "b" * 40


def _record(
    head: str = HEAD, passed: bool = True, ran_on: str | None = "ci_dispatch"
) -> FullChecksRecord:
    """A `full_checks` record; `ran_on=None` leaves the key out, as a record written before it."""
    record: FullChecksRecord = {
        "version": 1,
        "head": head,
        "passed": passed,
        "duration_s": 12.0,
        "declared": {},
        "results": [],
        "witness": None,
    }
    if ran_on == "ci_dispatch":
        record["ran_on"] = "ci_dispatch"
        record["run_id"] = 77
    elif ran_on == "ci":
        record["ran_on"] = "ci"
    elif ran_on == "local":
        record["ran_on"] = "local"
    return record


def _facts(**over) -> MergeFacts:
    """A pull request whose CI is declared, green on this head and fresh, but whose definition
    the change touched."""
    base = MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(".github/workflows/ci.yml",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("web",),
        ci_untrusted=True,
        ci_head=HEAD,
        ci_verdict=CiVerdict("green", (), ()),
        ci_fresh=True,
        ci_waited_s=10.0,
    )
    return replace(base, **over)


def test_a_passed_dispatched_record_of_this_head_stands():
    assert rules.ci_dispatched(_record(), HEAD) is True


def test_a_record_written_at_a_longer_id_of_the_same_commit_stands():
    assert rules.ci_dispatched(_record(head=HEAD + "0" * 24), HEAD) is True


@pytest.mark.parametrize(
    ("record", "pr_head"),
    [
        (_record(head=OTHER), HEAD),
        (_record(head=HEAD[:12]), HEAD[:12]),
        (_record(head=HEAD[:12]), HEAD),
        (_record(ran_on="ci"), HEAD),
        (_record(ran_on="local"), HEAD),
        (_record(ran_on=None), HEAD),
        (_record(passed=False), HEAD),
        (None, HEAD),
        (_record(), None),
    ],
    ids=[
        "another_sha",
        "short_sha_both",
        "short_record_sha",
        "ran_on_ci",
        "ran_on_local",
        "no_ran_on",
        "failed_record",
        "no_record",
        "no_pr_head",
    ],
)
def test_a_dispatched_verdict_stands_for_nothing_else(record, pr_head):
    assert rules.ci_dispatched(record, pr_head) is False


@pytest.mark.parametrize(
    ("untrusted", "dispatched", "trusted"),
    [
        (False, None, True),
        (False, False, True),
        (True, True, True),
        (None, True, True),
        (True, False, False),
        (True, None, False),
        (None, None, False),
        (None, False, False),
    ],
)
def test_the_ci_is_trusted_when_its_definition_is_or_a_dispatch_judged_the_head(
    untrusted, dispatched, trusted
):
    assert rules.ci_trusted(untrusted, dispatched) is trusted


def test_a_green_fresh_ci_on_an_untrusted_head_merges_when_the_dispatch_passed():
    assert rules.auto_merge_decision(_facts(ci_dispatched=True)) == MergeDecision("merge", None)


@pytest.mark.parametrize("dispatched", [False, None])
def test_the_same_head_does_not_merge_on_the_ci_without_a_dispatched_verdict(dispatched):
    assert rules.auto_merge_decision(_facts(ci_dispatched=dispatched)) == MergeDecision(
        "run_checks", None
    )


def test_a_dispatched_verdict_never_lifts_a_stale_or_red_ci():
    stale = _facts(ci_dispatched=True, ci_fresh=False)
    assert rules.auto_merge_decision(stale) == MergeDecision("run_checks", None)
    red = _facts(ci_dispatched=True, ci_verdict=CiVerdict("red", (), ()))
    assert rules.auto_merge_decision(red) == MergeDecision("refuse", "ci_failed")
