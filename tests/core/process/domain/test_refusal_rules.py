"""The pin of the 2026-11-19 table (GH-1011): every refusal it lists is raised as an
`OutputRefusal` with its own rule id, the exact field path and today's text, and the field
survives `run_role.refusal_field` against the schema of the role that delivered it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args

import pytest

from agentic.core.process.application.ports import Checkout
from agentic.core.process.application.run_role import refusal_field
from agentic.core.process.application.validate_prd import _with_draft_check
from agentic.core.process.domain import cost_history, drawing, refusal, schemas, spec_delivery, task
from agentic.core.process.domain.roles import ROLES, TECH_LEAD
from agentic.core.process.domain.task import OutputRefusal
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_validate_prd_draft_check import BODY, NOW
from tests.core.process.domain.test_drawing import DESIGN, HOME, Chk, Dep
from tests.fakes import FakeDraftSpecValidator, memory_ports

_ROLES = {role.slug: role for role in ROLES}
_INTERVIEWEES = [slug for slug, seat in drawing.DRAWING_SEATS.items() if seat == "interviewee"]
_LONG = "x" * 241


def _history() -> cost_history.History:
    return cost_history.build([])


def _plan(cost: float, files: int, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    subtasks = [{"id": "a", "title": "a", "brief": "b", "seams": ["x"], "depends_on": []}]
    return {
        "subtasks": [] if parts else subtasks,
        "parts": parts or [],
        "markdown": "### Solution",
        "ci_required": [],
        "ci_none_reason": "The issue requires no external check.",
        "estimated_cost_usd": cost,
        "estimated_files": files,
    }


def _part(depends_on: tuple[int, ...] = (), **more: Any) -> dict[str, Any]:
    return {
        "title": "T",
        "body": "B",
        "depends_on": list(depends_on),
        "depends_on_reasons": [f"needs what part {dep} creates" for dep in depends_on],
        "hand_files": 1,
        "hand_lines": 10,
        "test_lines": 10,
        "kind": "change",
        "guard": "",
        **more,
    }


def _plan_refusal(spent: float, data: dict[str, Any]) -> OutputRefusal:
    check = schemas.plan_check_for(_history(), spent)
    with pytest.raises(OutputRefusal) as caught:
        check(data)
    return caught.value


def _parts_refusal(parts: list[dict[str, Any]]) -> OutputRefusal:
    with pytest.raises(OutputRefusal) as caught:
        schemas.parts_check(parts)
    return caught.value


def _drawing_refusal(
    checks: list[Chk], departures: list[Dep], visible: tuple[int, ...] = (1,)
) -> OutputRefusal:
    found = drawing.interview_refusal(visible, checks, departures, DESIGN, {}, {})
    assert found is not None
    return found


def _spec_refusal(tmp_path: Any) -> OutputRefusal:
    ports = memory_ports(
        spec_validator=FakeDraftSpecValidator(preview="slice_missing"), now=lambda: NOW
    )
    config = Configuration(product_dir=tmp_path, owner_login="owner")
    check = _with_draft_check(lambda data: None, Checkout("b", tmp_path, "b"), config, ports)
    with pytest.raises(OutputRefusal) as caught:
        check({"verdict": "approved", "spec_draft": BODY})
    return caught.value


@dataclass(frozen=True)
class Row:
    rule: str
    field: str
    text: str
    refusal: Any  # a zero-argument callable that returns the raised OutputRefusal
    seats: tuple[str, ...] = ("tech_lead",)


_FITS = (
    "the estimate (20.00 USD, 3 files) fits: write subtasks and no parts; "
    "a task is split only when it does not fit"
)
_NOT_FIT = (
    "does not fit one task (or the history's reference for this size does not): write parts, "
    "at least two tasks, each a task of its own, and no subtasks. A plan that does not fit is "
    "split; a human's answer to this failure does not build it whole (ADR-0054)."
)
_NO_DECLARATION = "which the design does not declare; cite the full path, pantallas/<name>"
_ONE = _part()
_ROWS = {
    "parts_when_fits": Row(
        "parts_when_fits",
        "parts",
        _FITS,
        lambda: _plan_refusal(0.0, _plan(20.0, 3, [_ONE, _part((1,))])),
    ),
    "estimate_does_not_fit": Row(
        "estimate_does_not_fit",
        "estimated_cost_usd",
        f"the estimate (151.00 USD, 3 files) {_NOT_FIT}",
        lambda: _plan_refusal(0.0, _plan(151.0, 3)),
    ),
    "estimate_below_spent_floor": Row(
        "estimate_below_spent_floor",
        "estimated_cost_usd",
        "the estimate (raised by the system from 20.00 to 160.00 USD: below what the task "
        f"already spent 140.00, 3 files) {_NOT_FIT}",
        lambda: _plan_refusal(140.0, _plan(20.0, 3)),
    ),
    "part_over_budget": Row(
        "part_over_budget",
        "parts[1]",
        "part 2 does not fit the size budget: Do not lower a declaration to fit: if your "
        "honest estimate is higher, move a whole step, with its tests, into another part. "
        "hand_files declared 11, judged 17 at x1.5, cap 15: an honest estimate fits only at "
        "10 or less",
        lambda: _parts_refusal([_ONE, _part((1,), hand_files=11)]),
    ),
    "depends_on_reason_too_long": Row(
        "depends_on_reason_too_long",
        "parts[1].depends_on_reasons[0]",
        "part 2 depends_on_reasons[0] has 241 characters, over 240: one plain line naming "
        "what the other part creates",
        lambda: _parts_refusal([_ONE, _part((1,), depends_on_reasons=[_LONG])]),
    ),
    "estimated_files_empty": Row(
        "estimated_files_empty",
        "estimated_files",
        "estimated_files must be at least one file: every task changes one",
        lambda: _plan_refusal(0.0, _plan(20.0, 0)),
    ),
    "visible_without_drawing_check": Row(
        "visible_without_drawing_check",
        "drawing_checks",
        "Q1 is marked visible but has no drawing check",
        lambda: _drawing_refusal([], []),
        tuple(_INTERVIEWEES),
    ),
    "drawing_path_undeclared (check)": Row(
        "drawing_path_undeclared",
        "drawing_checks[1].drawing",
        f"Q2 cites other/Nope.png, {_NO_DECLARATION}",
        lambda: _drawing_refusal(
            [
                Chk("question", "Q1", "matches", HOME, "Title"),
                Chk("question", "Q2", "matches", "other/Nope.png", "Title"),
            ],
            [],
        ),
        tuple(_INTERVIEWEES),
    ),
    "drawing_path_undeclared (departure)": Row(
        "drawing_path_undeclared",
        "departures[0].drawing",
        f"a departure of other/Nope.png cites other/Nope.png, {_NO_DECLARATION}",
        lambda: _drawing_refusal(
            [Chk("question", "Q1", "departs", HOME, "Title")], [Dep("other/Nope.png", "Title")]
        ),
        tuple(_INTERVIEWEES),
    ),
}


@pytest.mark.parametrize("name", list(_ROWS))
def test_a_table_refusal_carries_its_rule_field_and_text(name):
    row = _ROWS[name]
    exc = row.refusal()
    assert (exc.rule, exc.field, str(exc), exc.detail) == (row.rule, row.field, row.text, row.text)
    for slug in row.seats:
        assert refusal_field(_ROWS_SCHEMA(slug), exc.field) == exc.field


def _ROWS_SCHEMA(slug: str) -> type[schemas.RoleOutput]:
    return TECH_LEAD.schema if slug == "tech_lead" else _ROLES[slug].schema


def test_the_second_part_decides_the_field_of_a_plan_refusal():
    assert _ROWS["depends_on_reason_too_long"].refusal().field == "parts[1].depends_on_reasons[0]"
    assert _ROWS["part_over_budget"].refusal().field == "parts[1]"


def test_the_departure_refusal_is_still_the_text_interview_check_returns():
    exc = _ROWS["drawing_path_undeclared (departure)"].refusal()
    assert drawing.interview_check(
        (1,),
        [Chk("question", "Q1", "departs", HOME, "Title")],
        [Dep("other/Nope.png", "Title")],
        DESIGN,
        {},
        {},
    ) == str(exc)


def test_a_refusal_outside_the_table_stays_unmapped_with_no_field():
    found = drawing.interview_refusal(
        (1,),
        [Chk("question", "Q1", "departs", HOME, "Title")],
        [Dep(HOME, "Other")],
        DESIGN,
        {},
        {},
    )
    assert found is not None
    assert (found.rule, found.field) == ("unmapped", "")


def test_the_preview_slice_refusal_names_the_spec_draft(tmp_path):
    exc = _spec_refusal(tmp_path)
    assert (exc.rule, exc.field) == ("preview_slice_missing", "spec_draft")
    assert str(exc) == spec_delivery.draft_refusal_message("preview_slice_missing")
    assert str(exc) == (
        "draft Spec refused: preview_slice_missing: the draft Spec must have exactly one "
        "slice; fold the work into one slice, or return it again with several and the system "
        "records a split proposal instead of a Spec file."
    )
    assert refusal_field(schemas.VerdictWithSpecDraft, exc.field) == "spec_draft"
    assert refusal_field(schemas.Verdict, exc.field) == ""


def test_the_other_draft_causes_stay_unmapped():
    exc = spec_delivery.draft_refusal("draft_empty")
    assert (exc.rule, exc.field) == ("unmapped", "")
    assert str(exc) == spec_delivery.draft_refusal_message("draft_empty")


def test_the_closed_set_of_ids_is_the_one_the_rows_cover():
    assert get_args(refusal.RefusalRule) == (
        "preview_slice_missing",
        "visible_without_drawing_check",
        "drawing_path_undeclared",
        "parts_when_fits",
        "estimate_does_not_fit",
        "part_over_budget",
        "depends_on_reason_too_long",
        "estimated_files_empty",
        "estimate_below_spent_floor",
        "system_category",
        "output_missing",
        "schema_invalid",
        "unmapped",
    )
    covered = {row.rule for row in _ROWS.values()} | {"preview_slice_missing"}
    # `system_category` (GH-1075) is a finding's category refused by `schemas.validate`, pinned in
    # `test_schemas`, not one of the plan and drawing rows above.
    not_in_rows = {"output_missing", "schema_invalid", "unmapped", "system_category"}
    assert set(get_args(refusal.RefusalRule)) - not_in_rows == covered


def test_task_re_exports_the_leaf_definitions():
    assert task.RefusalRule is refusal.RefusalRule
    assert task.OutputRefusal is refusal.OutputRefusal
