"""`landing.waits_on_human` and `landing.HUMAN_OWNED_REFUSALS` (GH-588): the one definition of
"this task belongs to a person", and the one list of refusals that leave it to one. Pure."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast, get_args

import pytest

from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.landing import HUMAN_OWNED_REFUSALS, waits_on_human
from agentic.core.process.domain.task import VERDICT_SYNC, TaskState
from tests.core.process.domain.test_landing import HEAD, _facts, _spent

ALL_REFUSALS: tuple[rules.MergeRefusal, ...] = get_args(rules.MergeRefusal)
LATER = "2026-11-11T10:00:00+00:00"


def _waits(
    state: dict[str, object] | None = None,
    refusal: rules.MergeRefusal | None = None,
    head: str | None = HEAD,
    *,
    spent: landing.LandingSpent | None = None,
    stopped: bool = False,
    needs_human: bool = False,
    stranded: bool = False,
) -> bool:
    return waits_on_human(
        cast(TaskState, state or {}),
        head,
        refusal,
        spent=spent or _spent(),
        stopped=stopped,
        needs_human=needs_human,
        stranded=stranded,
    )


def _entry(step: str, **over: object) -> dict[str, object]:
    return {"head": HEAD, "step": step, "at": LATER, **over}


def test_a_task_nobody_holds_does_not_wait_on_a_human():
    assert not _waits()
    assert not _waits({"landing": [_entry("sync_master")]}, "attempts_exhausted")


@pytest.mark.parametrize("flag", ["stopped", "needs_human", "stranded"])
def test_each_flag_the_caller_reads_alone_makes_it_true(flag):
    assert _waits(**{flag: True})


@pytest.mark.parametrize("refusal", sorted(HUMAN_OWNED_REFUSALS))
def test_a_human_owned_refusal_alone_makes_it_true(refusal):
    assert _waits(refusal=refusal)


def test_an_escalated_document_makes_it_true_and_a_released_one_does_not():
    assert _waits({"escalated": "ci is red on master"})
    assert not _waits({"escalated": None})


def test_an_unlandable_diagnosis_makes_it_true_and_a_sync_one_does_not():
    assert _waits(spent=_spent(diagnoses=1, last_verdict=None))
    assert not _waits(spent=_spent(diagnoses=1, last_verdict=VERDICT_SYNC))


@pytest.mark.parametrize("step", ["escalate", "escalate_adr", "escalate_ledger"])
def test_an_escalation_entry_of_the_head_without_release_makes_it_true(step):
    assert _waits({"landing": [_entry(step)]})


@pytest.mark.parametrize("step", ["escalate", "escalate_adr", "escalate_ledger"])
def test_a_released_escalation_entry_does_not(step):
    assert not _waits({"landing": [_entry(step, released_at=LATER)]})


def test_an_escalation_of_another_head_or_of_no_head_does_not():
    other = {"landing": [{"head": "f" * 40, "step": "escalate"}]}
    assert not _waits(other)
    assert not _waits({"landing": [_entry("escalate")]}, head=None)


def test_a_later_released_escalation_supersedes_an_earlier_one():
    entries = [_entry("escalate"), _entry("escalate_ledger", released_at=LATER)]
    assert not _waits({"landing": entries})
    assert _waits({"landing": entries[::-1]})


def test_an_entry_of_the_run_before_a_restoration_is_not_read():
    state = {"landing": [_entry("escalate")], "restorations": [{"at": LATER, "landing_cut": 1}]}
    assert not _waits(state)
    assert _waits({"landing": [_entry("escalate")]})


def _none_refusals_not_owned(owned: Iterable[str]) -> list[str]:
    """The refusals whose head step is `none` that `owned` does not account for: the ones the
    table silently leaves to nobody. `not_ready` is the one `none` that is nobody's."""
    return [
        refusal
        for refusal in ALL_REFUSALS
        if landing.landing_step(refusal, _facts(), _spent(), (), turn="head").kind == "none"
        and refusal not in owned
        and refusal != "not_ready"
    ]


def test_every_none_refusal_is_owned():
    assert _none_refusals_not_owned(HUMAN_OWNED_REFUSALS) == []


def test_the_check_rejects_a_none_refusal_the_set_forgot():
    assert _none_refusals_not_owned(HUMAN_OWNED_REFUSALS - {"eval_set"}) == ["eval_set"]


@pytest.mark.parametrize("refusal", sorted(HUMAN_OWNED_REFUSALS))
def test_the_table_answers_none_to_each_owned_refusal_whatever_the_budgets(refusal):
    for spent in (_spent(), _spent(passes=9, syncs=9, waited_s=1e6)):
        assert landing.landing_step(refusal, _facts(), spent, (), turn="head").kind == "none"
