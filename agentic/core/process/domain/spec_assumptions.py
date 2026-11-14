"""The GH-594 part 2b helpers of a spec delivery (ADR-0095): which schema a role is asked for,
which assumptions it left, where an interview doubt goes, and whether a conflict was decided.

The GH-642 plan placed these in `spec_delivery`. They live here because `task` imports
`spec_delivery`, and a `spec_delivery` that imported `schemas` or `task` would close an import
cycle. Nothing in the domain imports this module; it imports `rules`, which builds the
escalation."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from . import rules
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
from .task import (
    WAITING_ON_HUMAN,
    AssumptionRow,
    PauseEntry,
    SpecEscalationCause,
    assumption_text,
)

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


def conflict_decided(pause_log: Sequence[PauseEntry], cause: SpecEscalationCause) -> bool:
    """Whether the owner decided the spec conflict of `cause`.

    A `questions` pause is never auto-retaken and always carries a `questions_for_human` line,
    so a `resumed` entry after it means a human's decision. When some escalation entry carries
    `spec_cause == cause`, those entries alone decide: True if and only if the last of them is
    followed by a `resumed` entry. Only when no entry carries that cause does the legacy reading
    apply, for documents written before the field: True if some `questions` escalation with no
    `spec_cause` is followed, at any later point, by a `resumed` entry."""
    last_asked: int | None = None
    legacy_asked: int | None = None
    for i, entry in enumerate(pause_log):
        if entry["kind"] != "escalation":
            continue
        entry_cause = entry.get("spec_cause")
        if entry_cause == cause:
            last_asked = i
        elif entry_cause is None and entry["value"] == "questions" and legacy_asked is None:
            legacy_asked = i
    asked = last_asked if last_asked is not None else legacy_asked
    if asked is None:
        return False
    return any(entry["kind"] == "resumed" for entry in pause_log[asked + 1 :])


ESCALATE = "escalate"


def conflict_step(state: Mapping[str, Any], cause: SpecEscalationCause) -> str:
    """What a site that found a spec conflict of `cause` does: `ESCALATE` when the owner has not
    decided it, otherwise the assumption text that records the decision."""
    if not conflict_decided(state.get("pause_log", []), cause):
        return ESCALATE
    return f"spec conflict ({cause}) settled by the owner's decision"


_CONFLICT_QUESTIONS: dict[SpecEscalationCause, str] = {
    "contradiction": "the spec contradicts itself; decide which clause holds.",
    "conflict": "the spec conflicts with current behaviour or an ADR; decide which wins.",
    "data_loss": "building the spec as written would lose stored data; decide whether that "
    "is accepted.",
}
_CONFLICT_POINTERS: dict[Literal["plan", "interview"], str] = {
    "plan": "see the Tech Lead section of the task document",
    "interview": "see the solution interview in the task document",
}


def conflict_question(cause: SpecEscalationCause, where: Literal["plan", "interview"]) -> str:
    """The one fixed `questions_for_human` line of a spec conflict: never model text. It names
    the cause and points to where the owner reads the detail."""
    return f"Spec {cause}: {_CONFLICT_QUESTIONS[cause]} ({_CONFLICT_POINTERS[where]})"


def conflict_escalation(
    state: Mapping[str, Any],
    cause: SpecEscalationCause,
    reason: str,
    where: Literal["plan", "interview"],
    at: str,
) -> dict[str, Any]:
    """The only builder of a spec `questions` escalation: the logged patch that carries
    `spec_cause`, the `waiting_on_human` status and the one fixed `questions_for_human` line that
    makes `resume` ask for a human comment."""
    escalation = rules.escalation("questions", reason, spec=state["spec"], spec_cause=cause)
    patch = rules.logged({**escalation}, at=at)
    return {
        **patch,
        "status": WAITING_ON_HUMAN,
        "questions_for_human": [conflict_question(cause, where)],
    }


def conflict_reason(cause: SpecEscalationCause, round_: int) -> str:
    """One line naming the spec cause and the interview round of an escalation."""
    return f"Spec {cause} raised in interview round {round_}"
