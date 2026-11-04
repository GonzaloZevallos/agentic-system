"""`landing.landing_step` and `landing.already_dispatched` for `ci_unknown_names` (GH-485):
one `escalate` per (head, names), no budget spent, never a diagnose or a restart."""

from __future__ import annotations

from agentic.core.process.domain import landing
from tests.core.process.domain.test_landing import _facts, _spent

NAMES = ("e2e", "pytest")


def _step(names_escalated=False, **spent):
    facts = _facts(ci_unreported=NAMES)
    return landing.landing_step(
        "ci_unknown_names", facts, _spent(**spent), (), names_escalated=names_escalated
    )


def test_unknown_names_escalate_and_carry_the_names():
    step = _step()
    assert (step.kind, step.refusal, step.names) == ("escalate", "ci_unknown_names", NAMES)


def test_the_same_head_and_names_are_not_asked_twice():
    assert _step(names_escalated=True).kind == "none"


def test_no_budget_is_read_or_spent_even_when_all_are_exhausted():
    spent = {"passes": landing.LANDING_PASSES_MAX, "syncs": 9, "diagnoses": 1, "waited_s": 1e6}
    assert _step(**spent).kind == "escalate"


def test_an_escalate_entry_is_taken_only_for_the_same_names():
    entries = [{"head": "h", "step": "escalate", "refusal": "ci_unknown_names", "names": ["e2e"]}]
    assert landing.already_dispatched(entries, "h", "escalate", None, names=("e2e",))
    assert not landing.already_dispatched(entries, "h", "escalate", None, names=("e2e", "pytest"))
    assert landing.already_dispatched(entries, "h", "escalate", None)
