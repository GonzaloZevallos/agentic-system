"""The escalation of a refused spec (GH-594): its closed causes, its closed `detail`, the
`honoured` switch and the clear on resume. Nothing calls `refusal_escalation` yet."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.spec_delivery import (
    SpecCause,
    SpecContract,
    SpecReadCause,
    SpecRouteCause,
    honoured,
)
from agentic.core.process.domain.spec_route import refusal_escalation
from agentic.core.process.domain.task import TaskState

CAUSES = (
    "spec_invalid",
    "spec_unapproved",
    "slice_unknown",
    "slice_missing",
    "spec_unreadable",
    "spec_line_malformed",
    "spec_type",
    "spec_changed",
)
AT = "2026-11-15T00:00:00Z"
SPEC: SpecContract = {
    "path": "docs/specs/a.md",
    "slice": "s1",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}


def test_the_route_causes_are_the_eight_in_order_and_hold_the_other_two():
    assert get_args(SpecRouteCause) == CAUSES
    assert set(get_args(SpecCause)) <= set(CAUSES)
    assert set(get_args(SpecReadCause)) <= set(CAUSES)


@pytest.mark.parametrize("cause", CAUSES)
def test_every_cause_escalates_as_classification_with_its_refusal(cause: SpecRouteCause):
    patch = refusal_escalation(cause, ["slice"], "the reason")
    assert patch["escalation_class"] == "classification"
    assert patch["escalated"] == "the reason"
    assert patch["spec_refusal"] == {"cause": cause, "detail": ["slice"]}
    assert {k: v for k, v in patch.items() if k != "spec_refusal"} == rules.escalation(
        "classification", "the reason"
    )


@pytest.mark.parametrize(
    "item",
    [
        "criteria",  # a SpecContract field
        "touches_copy",
        "oversize",  # a SpecReadRefusal
        "unreachable",
        "owner_login",  # the configuration key an unapproved route names
        "docs/specs/a-b_c.md",  # a spec path
        "slice-one",  # a slice id
        "risk-2",  # a risk id
        "a" * 64,
    ],
)
def test_detail_accepts_each_closed_class(item: str):
    assert refusal_escalation("spec_invalid", [item], "r")["spec_refusal"]["detail"] == [item]


def test_an_empty_detail_is_accepted():
    assert refusal_escalation("spec_type", [], "r")["spec_refusal"]["detail"] == []


@pytest.mark.parametrize(
    "item",
    [
        "docs/other/a.md",  # a path outside docs/specs/
        "docs/specs/../a.md",
        "touches_ui2",  # an invented field name
        "not_blob2",  # an invented reason
        "Slice-One",  # uppercase slug
        "slice one",
        "a\nb",
        "slice\n",
        "a" * 65,
        "",
        "-slice",
    ],
)
def test_detail_rejects_what_is_in_no_class(item: str):
    with pytest.raises(ValueError):
        refusal_escalation("spec_invalid", [item], "r")


def test_detail_rejects_free_text():
    with pytest.raises(ValueError):
        refusal_escalation("spec_unreadable", ["no such file"], "r")


def test_detail_rejects_more_than_sixteen_items():
    refusal_escalation("spec_invalid", ["s"] * 16, "r")
    with pytest.raises(ValueError, match="at most 16"):
        refusal_escalation("spec_invalid", ["s"] * 17, "r")


def test_an_unknown_cause_is_refused():
    with pytest.raises(ValueError, match="cause"):
        refusal_escalation("spec_other", [], "r")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reader", "validator", "expected"),
    [
        (None, None, False),
        (object(), None, False),
        (None, object(), False),
        (object(), object(), True),
    ],
)
def test_honoured_needs_both_ports(reader: object | None, validator: object | None, expected: bool):
    assert honoured(reader, validator) is expected


def test_resumed_clears_the_refusal_and_the_spec_cause():
    state: TaskState = {
        "escalated": "x",
        "spec": SPEC,
        "spec_refusal": {"cause": "spec_type", "detail": []},
    }
    patch = rules.resumed(state, None, at=AT)
    assert patch["spec_refusal"] is None
    assert patch["spec_cause"] is None


def test_resumed_clears_a_refusal_that_came_before_any_spec():
    state: TaskState = {
        "escalated": "x",
        "spec_refusal": {"cause": "spec_unreadable", "detail": ["missing"]},
    }
    patch = rules.resumed(state, None, at=AT)
    assert patch["spec_refusal"] is None
    assert "spec_cause" not in patch


def test_resumed_without_the_keys_is_the_patch_of_today():
    patch = rules.resumed({"escalated": "x"}, None, at=AT)
    assert "spec_refusal" not in patch
    assert "spec_cause" not in patch
