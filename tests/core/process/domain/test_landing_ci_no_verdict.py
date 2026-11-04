"""GH-463, subtask landing-and-docs: the landing's answer to the three refusals of the merge
gate's fresh GitHub run, and the release of `ci_no_verdict`. Pure: `landing.landing_step`,
`landing.ci_no_verdict_entry` and `landing.released_no_verdict`."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import landing
from agentic.core.process.domain.task import apply
from tests.core.process.domain.test_landing import HEAD, MASTER, _spent, _step

NEW_MASTER = "c" * 40
AT = "2026-11-03T10:00:00+00:00"


def _pair(**over) -> dict:
    return {
        "head": HEAD,
        "master": MASTER,
        "tested": "d" * 40,
        "mode": "push",
        "published_at": AT,
        "republished": True,
        "superseded": [],
        "outcome": "no_verdict",
        **over,
    }


def _escalated(*after: dict, **over) -> dict:
    escalate = {
        "head": HEAD,
        "step": "escalate",
        "refusal": "ci_no_verdict",
        "at": AT,
        "upstream": MASTER,
        "cause": "no_run",
    }
    return {
        "escalated": "no verdict",
        "escalation_class": "questions",
        "status": "waiting_on_human",
        "landing": [escalate, *after],
        "merge_ci": [_pair()],
        **over,
    }


@pytest.mark.parametrize(
    ("refusal", "kind"),
    [
        ("ci_stale_exhausted", "sync_master"),
        ("ci_no_verdict", "escalate"),
        ("ci_untrusted_no_dispatch", "none"),
    ],
)
def test_the_table_answers_each_new_refusal(refusal, kind):
    assert _step(refusal).kind == kind


def test_a_stale_exhaustion_syncs_without_a_conflict_and_not_past_the_sync_budget():
    assert _step("ci_stale_exhausted", _spent(syncs=landing.LANDING_SYNCS_MAX)).kind == "diagnose"


def test_a_released_no_verdict_waits_instead_of_escalating_again():
    assert _step("ci_no_verdict", _spent(no_verdict_released=True)).kind == "wait"


def test_the_entry_is_the_last_live_escalation_for_that_refusal_and_head():
    raw = _escalated()
    assert landing.ci_no_verdict_entry(raw, HEAD) is not None
    assert landing.ci_no_verdict_entry(raw, "e" * 40) is None
    assert landing.ci_no_verdict_entry({**raw, "escalated": None}, HEAD) is None
    other = _escalated()
    other["landing"][0]["refusal"] = "ci_failed"
    assert landing.ci_no_verdict_entry(other, HEAD) is None
    later = _escalated({"head": HEAD, "step": "rerun", "refusal": "ci_failed", "at": AT})
    assert landing.ci_no_verdict_entry(later, HEAD) is None


def test_the_release_stamps_the_entry_and_the_pair_and_grants_one_more_pair():
    raw = _escalated()
    patch = landing.released_no_verdict(raw, NEW_MASTER, AT)
    assert patch["escalated"] is None
    assert patch["landing"][0]["released_at"] == AT
    assert patch["landing"][0]["ci_released"] == NEW_MASTER
    assert patch["landing"][0]["cause"] == "no_run"
    assert patch["merge_ci"][0]["released_at"] == AT
    released = apply(raw, patch)
    assert landing.no_verdict_released(released["landing"], released["merge_ci"], HEAD)
    assert not landing.no_verdict_released(raw["landing"], raw["merge_ci"], HEAD)


def test_a_second_release_of_a_document_no_longer_escalated_writes_nothing():
    raw = _escalated()
    released = apply(raw, landing.released_no_verdict(raw, NEW_MASTER, AT))
    assert landing.released_no_verdict(released, NEW_MASTER, AT) == {}


def test_a_released_entry_does_not_keep_the_next_escalation_from_being_taken():
    raw = _escalated()
    entries = raw["landing"]
    assert landing.already_dispatched(entries, HEAD, "escalate", MASTER)
    released = apply(raw, landing.released_no_verdict(raw, NEW_MASTER, AT))
    assert not landing.already_dispatched(released["landing"], HEAD, "escalate", NEW_MASTER)


def test_a_cause_outside_the_closed_set_is_dropped_from_the_entry():
    assert landing.entry_of({"cause": "no_run"}).get("cause") == "no_run"
    assert "cause" not in landing.entry_of({"cause": "stderr: boom"})
