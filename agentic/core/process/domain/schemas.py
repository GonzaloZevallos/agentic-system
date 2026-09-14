"""Structured outputs: what PjM processes mechanically (README.md, 14).

Each schema reaches the model as a JSON schema. The Markdown section travels as
the `markdown` field, free text inside the same output: the harness ends the turn
on the structured-output call, so a separate text reply never arrives. A missing
or invalid structured output is a role failure, never an empty dict.

`flags` is typed loosely here and tightened per run to the product's flag
vocabulary, so the model can only mark names that exist.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .task import INTEGRATION

TaskType = Literal["bug", "feature", "platform_enhancement"]
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RoleOutput(BaseModel):
    markdown: str = Field(description="The role's Markdown section, free text, `###` headings")


class Classification(RoleOutput):
    type: TaskType
    # Low confidence escalates to the human; PjM never guesses (README.md, 4).
    confidence: Literal["high", "low"]
    reason: str = Field(description="One line")


class Definition(RoleOutput):
    flags: dict[str, bool] = Field(description="Every flag of the vocabulary, marked yes/no")


class CapabilityFile(BaseModel):
    capability: str = Field(
        description="File name in docs/product/, a slug named after a glossary concept, "
        "e.g. `check-ins`, `metrics`"
    )
    content: str = Field(
        description="The complete updated file, not a diff: everything current plus what "
        "this task adds or changes, metrics included"
    )


class GlossaryTerm(BaseModel):
    term: str
    definition: str = Field(description="One to three sentences")
    avoid: str = Field(description="Comma-separated words not to use for it; may be empty")


class Verdict(RoleOutput):
    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what blocks building")
    flags: dict[str, bool] = Field(description="Every flag of the vocabulary, validated")
    # The distillation (ADR-0006): PM returns the knowledge, the system writes it.
    definitions: list[CapabilityFile] = Field(
        description="On `approved` for a feature, one entry per capability the task "
        "touches. Empty on `returned`"
    )
    glossary: list[GlossaryTerm] = Field(
        description="Terms that are new or whose meaning changed. Empty if none"
    )


class InterviewTurn(RoleOutput):
    """One round of the interviewer (ADR-0014): the questions with a recommendation
    each, or the closing summary once the frontier is empty."""

    done: bool = Field(
        description="True when there is no question left to ask: shared understanding "
        "reached, or nothing the interviewee could still decide"
    )


class Answers(RoleOutput):
    """One round of the interviewee: a decision per question."""

    questions_for_human: list[str] = Field(
        description="Questions neither the request nor the product knowledge settle, one "
        "line each, written for a person who has not read the task. Empty if none"
    )


class TechAnswers(RoleOutput):
    """One round of the Tech Lead as interviewee: a decision per question, and what it
    consults PM about (glossary: Consulta). It never asks the human directly."""

    questions_for_pm: list[str] = Field(
        description="Behaviour the definition does not settle or contradicts the code, one "
        "question per line, written for someone who has not read this interview. Empty "
        "if none"
    )


class Consultation(RoleOutput):
    """PM's answers to a consultation: clarifications of the definition, the knowledge
    they change (distilled again, ADR-0006) and what only a person can decide."""

    questions_for_human: list[str] = Field(
        description="Questions only a person can decide, one line each, written for a "
        "person who has not read the task. Empty if none"
    )
    definitions: list[CapabilityFile] = Field(
        description="The capability files whose behaviour an answer changes or completes, "
        "each complete (not a diff) with its `## Jobs`. Empty when no answer changes the "
        "written knowledge"
    )
    glossary: list[GlossaryTerm] = Field(
        description="Terms an answer introduces or changes. Empty if none"
    )


class Subtask(BaseModel):
    id: str = Field(description="Slug, unique in the plan, e.g. `domain-model`, `api`")
    title: str = Field(description="One line")
    brief: str = Field(
        description="What to build and where, acceptance criteria, files likely touched; "
        "everything the Developer needs and nothing else"
    )
    seams: list[str] = Field(
        description="Public boundaries the tests are written at (a function, a class, an "
        "HTTP endpoint). The Developer writes no test at any other seam"
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of subtasks that must be approved before this one starts. Empty "
        "when it can run in parallel with the rest",
    )


class TechPlan(RoleOutput):
    subtasks: list[Subtask] = Field(description="The subtasks, in the order to start them")


class Delivery(RoleOutput):
    summary: str = Field(description="What changed, in a few lines, for the reviewer")
    assumptions: list[str] = Field(
        description="Decisions taken where the brief was silent; empty if none"
    )


class Review(RoleOutput):
    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what the Developer must fix")


def plan_check(data: dict) -> None:
    """A plan's subtasks are slugs, each with seams, depending only on subtasks that
    exist; `integration` is the system's own."""
    subtasks = data.get("subtasks", [])
    if not subtasks:
        raise ValueError("a plan needs at least one subtask")
    ids = [s["id"] for s in subtasks]
    for item in subtasks:
        if not SLUG.match(item["id"]):
            raise ValueError(f"subtask id {item['id']!r} must be a slug (lower-case, hyphens)")
        if item["id"] == INTEGRATION:
            raise ValueError(f"subtask id {INTEGRATION!r} is reserved for the integration")
        if not item["seams"]:
            raise ValueError(f"subtask {item['id']!r} declares no seams; tests need them")
        for dep in item["depends_on"]:
            if dep not in ids:
                raise ValueError(f"subtask {item['id']!r} depends on unknown subtask {dep!r}")
    if len(set(ids)) != len(ids):
        raise ValueError("subtask ids must be unique")


def json_schema(schema: type[RoleOutput], vocabulary: dict[str, str]) -> dict[str, Any]:
    """The schema as the model must fill it, with `flags` closed over the vocabulary
    when present. Pydantic's `$ref`/`$defs` are kept; a harness that wants one
    self-contained tree flattens them on its side."""
    out = schema.model_json_schema()
    if "flags" in out.get("properties", {}):
        out["properties"]["flags"] = {
            "type": "object",
            "properties": {
                name: {"type": "boolean", "description": desc} for name, desc in vocabulary.items()
            },
            "required": list(vocabulary),
            "additionalProperties": False,
        }
    return out


def validate(schema: type[RoleOutput], raw: Any, vocabulary: dict[str, str]) -> dict[str, Any]:
    """Pydantic validation plus the vocabulary check. Raises `ValueError` on any gap
    (`ValidationError` is one)."""
    data = schema.model_validate(raw).model_dump()
    if "flags" in data:
        got, expected = set(data["flags"]), set(vocabulary)
        if got != expected:
            raise ValueError(
                f"flags must be exactly the vocabulary; unknown: {sorted(got - expected)}, "
                f"missing: {sorted(expected - got)}"
            )
    return data
