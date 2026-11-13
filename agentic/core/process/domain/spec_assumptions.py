"""The GH-594 part 2b helpers of a spec delivery (ADR-0095): which schema a role is asked for,
which assumptions it left, where an interview doubt goes, and whether a conflict was decided.

The GH-642 plan placed these in `spec_delivery`. They live here because `task` imports
`spec_delivery`, and a `spec_delivery` that imported `schemas` or `task` would close an import
cycle. Nothing in the domain imports this module yet."""

from collections.abc import Sequence
from typing import Any

from .schemas import (
    Consultation,
    ConsultationWithAssumptions,
    FinalValidation,
    FinalValidationWithAssumptions,
    PlatformAnswers,
    PlatformAnswersWithAssumptions,
    RoleOutput,
    TechAnswers,
    TechAnswersWithAssumptions,
    TechPlan,
    TechPlanWithAssumptions,
)
from .task import AssumptionRow, PauseEntry, SpecEscalationCause, assumption_text

SPEC_SCHEMAS: dict[type[RoleOutput], type[RoleOutput]] = {
    TechPlan: TechPlanWithAssumptions,
    FinalValidation: FinalValidationWithAssumptions,
    TechAnswers: TechAnswersWithAssumptions,
    PlatformAnswers: PlatformAnswersWithAssumptions,
    Consultation: ConsultationWithAssumptions,
}


def schema_for(schema: type[RoleOutput], has_spec: bool) -> type[RoleOutput]:
    """The spec subclass of `schema` when the task has a spec; otherwise `schema` itself. A
    class with no subclass, such as `Delivery`, is returned unchanged either way."""
    return SPEC_SCHEMAS.get(schema, schema) if has_spec else schema


def assumption_rows(role_name: str, data: Any, has_spec: bool) -> list[AssumptionRow]:
    """The assumptions `data` carries as `{role, text}` rows, each in the canonical text
    `assumption_text` returns; what it refuses is dropped. `[]` without a spec. Never raises."""
    if not has_spec or not isinstance(data, dict):
        return []
    items = data.get("assumptions", [])
    if not isinstance(items, list):
        return []
    rows: list[AssumptionRow] = []
    for item in items:
        text = assumption_text(item) if isinstance(item, str) else None
        if text is not None:
            rows.append({"role": role_name, "text": text})
    return rows


def interview_doubts(questions: Sequence[str], has_spec: bool) -> tuple[list[str], list[str]]:
    """`(to_human, assumption_texts)`: without a spec every doubt is asked; with one, none is
    asked and each is recorded as an assumption."""
    return (list(questions), []) if not has_spec else ([], list(questions))


def conflict_decided(pause_log: Sequence[PauseEntry]) -> bool:
    """True when some `questions` escalation is followed, at any later point, by a `resumed`
    entry. It does not pair escalations with resumes and does not look only at the last one.

    Known over-approximation: `EscalationPauseEntry` carries no spec cause, so any resumed
    `questions` escalation counts, whoever raised it. Narrowing it needs a cause field on
    `EscalationPauseEntry`, in a separate task."""
    asked = False
    for entry in pause_log:
        if entry["kind"] == "escalation" and entry["value"] == "questions":
            asked = True
        elif entry["kind"] == "resumed" and asked:
            return True
    return False


def conflict_reason(cause: SpecEscalationCause, round_: int) -> str:
    """One line naming the spec cause and the interview round of an escalation."""
    return f"Spec {cause} raised in interview round {round_}"
