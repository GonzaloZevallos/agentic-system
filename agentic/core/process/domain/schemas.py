"""Structured outputs: what PjM processes mechanically (README.md, 14).

Each schema reaches the model as a JSON schema. The Markdown section travels as
the `markdown` field, free text inside the same output: the harness ends the turn
on the structured-output call, so a separate text reply never arrives. A missing
or invalid structured output is a role failure, never an empty dict.

`flags` is typed loosely here and tightened per run to the product's flag
vocabulary, so the model can only mark names that exist.

A top-level `list[str]` reaches the model as one string, a line per item, and comes
back as the list (issue #8): in GH-12 every structured output the harness could not
parse as JSON came from a role with such a field, written as bare text or `<item>`
tags instead of an array, five times in a row in one case. Lists nested in objects
(a subtask's seams) never failed and stay arrays.
"""

from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from .task import INTEGRATION, FindingCategory, FindingScope, Severity

TaskType = Literal["bug", "feature", "platform_enhancement"]
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Subtasks a plan may hold: the task cap grows with each one (ADR-0004, fourth addendum),
# so the plan cannot raise it without bound.
MAX_SUBTASKS = 12


class RoleOutput(BaseModel):
    markdown: str = Field(description="The role's Markdown section, free text, `###` headings")


class Classification(RoleOutput):
    type: TaskType
    # Low confidence escalates to the human; PjM never guesses (README.md, 4).
    confidence: Literal["high", "low"]
    reason: str = Field(description="One line")


class PRD(RoleOutput):
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
    # A term belongs to a bounded context, not to a file: a task that changes behaviour
    # across modules leaves terms in each of their glossaries (issue #45).
    context: str = Field(
        default="",
        description="For a product with a CONTEXT-MAP.md, the context this term belongs "
        "to, as the map names it. Empty for a product with a single CONTEXT.md",
    )


class Verdict(RoleOutput):
    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what blocks building")
    flags: dict[str, bool] = Field(description="Every flag of the vocabulary, validated")
    # The consolidation (ADR-0006): PM returns the knowledge, the system writes it.
    definitions: list[CapabilityFile] = Field(
        description="On `approved` for a feature, one entry per capability the task "
        "touches. Empty on `returned`"
    )
    glossary: list[GlossaryTerm] = Field(
        description="Terms that are new or whose meaning changed. Empty if none"
    )


class Diagnosis(RoleOutput):
    """The Product Analyst's ruling on a bug (README.md, 2 and 4; issue #65): what it
    saw when it reproduced, against which definition, and what it makes of it.

    No `flags`: a bug changes no scope, it moves the code to the definition it already
    has. `undefined` is not a verdict but a hand-off: the product never wrote down what
    this capability does, so PM decides, and `questions_for_pm` is what PM must settle
    for the ruling to exist. A schema of its own and not `PRD` with the fields
    blanked: what PM validates on a bug is a ruling, not a definition."""

    ruling: Literal["bug", "expected_behaviour", "undefined"] = Field(
        description="`bug`: the product does not do what its definition says. "
        "`expected_behaviour`: it does, and the request asks for something else. "
        "`undefined`: no definition covers this case, so PM decides"
    )
    capability: str = Field(
        description="The file in docs/product/ the ruling was made against, as a slug "
        "(`check-ins`); an empty string when no definition covers the case"
    )
    questions_for_pm: list[str] = Field(
        description="On `undefined`, what PM has to decide for the ruling to exist, "
        "written for someone who has not read the reproduction. Empty on `bug` and on "
        "`expected_behaviour`"
    )


class Ruling(RoleOutput):
    """PM's word on a bug (issue #65): the verdict on the Product Analyst's diagnosis
    and the final ruling, which is PM's even when the Product Analyst had one.

    Two fields and not one: `verdict` is what the pair counts (a `returned` uses up a
    return, as on a feature), `ruling` is what routes the task (`bug` goes to the plan,
    `expected_behaviour` discards it). When the Product Analyst said `undefined`, PM
    decides here. `definitions` is optional, unlike a feature's: a bug against a written
    definition changes nothing, and only a bug the product never defined leaves PM
    writing the definition the Tech Lead will need as spec. Never on
    `expected_behaviour`: a discarded task delivers nothing, so a definition written
    there would go nowhere."""

    verdict: Literal["approved", "returned"]
    ruling: Literal["bug", "expected_behaviour"] = Field(
        description="The final word: `bug` when the product must move to the definition, "
        "`expected_behaviour` when the product already does what it should"
    )
    reason: str = Field(description="One line. On `returned`, what the Product Analyst must redo")
    definitions: list[CapabilityFile] = Field(
        description="On `approved` and `bug`, the definition this bug now fixes, or the "
        "one that was missing, each complete (not a diff) with its `## Jobs`. Empty when "
        "the written definition already governs the case, on `expected_behaviour` and on "
        "`returned`"
    )
    glossary: list[GlossaryTerm] = Field(
        description="Terms a ruling introduces or changes. Empty if none"
    )


class InterviewTurn(RoleOutput):
    """One round of the interviewer (ADR-0014): the questions with a recommendation
    each, or the closing summary once the frontier is empty."""

    done: bool = Field(
        description="True when there is no question left to ask: shared understanding "
        "reached, or nothing the interviewee could still decide"
    )


class SolutionTurn(InterviewTurn):
    """The ephemeral Tech Lead's round: the same as any interviewer's, plus the
    invariants it proposes (issue #42). A schema of its own and not a field of
    `InterviewTurn`, which is also the definition interview's: the Product Analyst
    has no plan to set rules for."""

    invariants: list[str] = Field(
        default_factory=list,
        description="Rules you claim every subtask must honour or the plan breaks, from "
        "what this round uncovered. One per line, each checkable on one subtask's diff "
        "alone. The Tech Lead adopts or rejects each one; an empty string if none",
    )


class Answers(RoleOutput):
    """One round of the interviewee: a decision per question, what it leaves for the
    human and what it takes back from the human (issue #12)."""

    questions_for_human: list[str] = Field(
        description="Questions the definition cannot be settled without, that neither "
        "the request nor the product knowledge answer, written for a person who has "
        "not read the task"
    )
    withdrawn: list[int] = Field(
        default_factory=list,
        description="Numbers, from `## With the human` as received this round, of the "
        "questions this round's answers settle after all; empty if none",
    )


class TechAnswers(RoleOutput):
    """One round of the Tech Lead as interviewee on the feature path: a decision per
    question, and what it consults PM about (glossary: Consulta). It never asks the
    human directly; there is a PM between them."""

    questions_for_pm: list[str] = Field(
        description="Behaviour the definition does not settle or contradicts the code, "
        "written for someone who has not read this interview"
    )


class PlatformAnswers(RoleOutput):
    """One round of the Tech Lead as interviewee on the platform path, where there is no
    definition and no PM to consult (issue #46): what only a person can decide goes
    straight to the human.

    A schema of its own and not a flag on `TechAnswers`: which door a question takes is
    the path's decision, and a role that could pick would be picking who answers it."""

    questions_for_human: list[str] = Field(
        description="Product decisions this plan cannot be settled without, that neither "
        "the request nor the product knowledge answers, written for a person who has not "
        "read the task. A platform enhancement should need none: a question here is "
        "usually a sign the task is really a feature and was classified wrong"
    )
    withdrawn: list[int] = Field(
        default_factory=list,
        description="Numbers, from `## With the human` as received this round, of the "
        "questions this round's answers settle after all; empty if none",
    )


class Consultation(RoleOutput):
    """PM's answers to a consultation: clarifications of the definition, the knowledge
    they change (consolidated again, ADR-0006) and what only a person can decide."""

    questions_for_human: list[str] = Field(
        description="Questions only a person can decide, written for a person who has "
        "not read the task"
    )
    withdrawn: list[int] = Field(
        default_factory=list,
        description="Numbers, from `## With the human` as received this consultation, "
        "of the questions these answers settle after all; empty if none",
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
    invariants: list[str] = Field(
        default_factory=list,
        description="Rules every subtask honours, each checkable on one subtask's diff "
        "alone, e.g. `every query over subscriptions applies the duplicate fold`. The "
        "Developers build to them and the reviewer verifies them; empty when the "
        "subtasks share nothing",
    )
    integration: str = Field(
        default="",
        description="What has to be done to make the subtasks work together beyond "
        "merging their branches, when the plan already knows of something. An empty "
        "string when merging is enough: then no integrator runs",
    )


class StackProposal(RoleOutput):
    """The stack review's own author output (ADR-0032): a verdict and three pieces a
    human acts on without reading the interview, each its own field so the schema
    guard can name the exact one a delivery omitted -- never one Markdown blob."""

    verdict: str = Field(
        description="Keep the stack or change it, weighed against the Brief and the "
        "product's own decision records"
    )
    stack_diff: str = Field(
        description="The exact change to the product's stack declaration, as a diff "
        "against what is on disk, or the whole proposed file when the product has "
        "none yet"
    )
    standards: str = Field(
        description="The complete engineering standards this stack implies, "
        "materialised for every practice the system names, never a diff"
    )
    adr_draft: str = Field(
        description="A decision record for the product's own history, explaining the "
        "choice against the Brief, with the alternatives it beat"
    )


class Delivery(RoleOutput):
    summary: str = Field(description="What changed, in a few lines, for the reviewer")
    assumptions: list[str] = Field(description="Decisions taken where the brief was silent")


CATEGORIES: tuple[str, ...] = get_args(FindingCategory)


class Finding(BaseModel):
    """One thing the reviewer found, with what it saw (ADR-0016). Only a `blocking`
    finding returns the subtask; `important` and `observation` travel with an approval."""

    location: str = Field(description="`file:line`, or the file when it is the whole file")
    claim: str = Field(description="What is wrong, one sentence")
    evidence: str = Field(
        description="The command you ran and its output, or the line of the standard or "
        "the brief you cite. Empty only on an `observation`"
    )
    severity: Severity = Field(
        description="`blocking`: the Developer must fix it before the subtask is done. "
        "`important`: should be fixed, does not block. `observation`: a remark, or an "
        "objection you could not reproduce"
    )
    scope: FindingScope = Field(
        description="`subtask` when it can be fixed on the branch under review; `plan` when "
        "it needs a change to the plan or to another subtask's branch. On the integration "
        "branch a problem between subtasks is `subtask`"
    )
    category: FindingCategory


class Review(RoleOutput):
    reason: str = Field(
        description="One line, why, not the verdict (the system writes it). On a return, "
        "what the Developer must fix first; on an approval, what you verified"
    )
    findings: list[Finding] = Field(description="Every finding, most severe first")
    settled: list[str] = Field(
        default_factory=list,
        description="Locations (`file:line`) from `## Tech Lead: findings` that this "
        "delivery settles: you looked and the objection no longer holds. One per line; "
        "an empty string when none. A row you simply do not repeat stays open, because "
        "an incremental review not mentioning something usually means it did not look",
    )


def plan_check(data: dict[str, Any]) -> None:
    """A plan's subtasks are slugs, each with seams, depending only on subtasks that
    exist; `integration` is the system's own."""
    subtasks = data.get("subtasks", [])
    if not subtasks:
        raise ValueError("a plan needs at least one subtask")
    if len(subtasks) > MAX_SUBTASKS:
        raise ValueError(
            f"a plan holds at most {MAX_SUBTASKS} subtasks, this one has {len(subtasks)}: "
            "join the smallest ones"
        )
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
    for name in line_lists(schema):
        description = out["properties"][name].get("description", "")
        out["properties"][name] = {
            "type": "string",
            "description": f"{description}. One per line; an empty string if none",
        }
    return out


def line_lists(schema: type[RoleOutput]) -> list[str]:
    """The top-level `list[str]` fields, the ones the model wrote as text instead of an
    array (issue #8): lines to the model, lists to the system. Nested lists are not
    touched; they never failed."""
    return [name for name, info in schema.model_fields.items() if info.annotation == list[str]]


def validate(schema: type[RoleOutput], raw: Any, vocabulary: dict[str, str]) -> dict[str, Any]:
    """Pydantic validation plus the vocabulary check. Raises `ValueError` on any gap
    (`ValidationError` is one). A line-list field arrives as a string from the model and
    as a list from anything older or in-process; both validate."""
    if isinstance(raw, dict):
        raw = {
            **raw,
            **{
                name: _lines(raw[name])
                for name in line_lists(schema)
                if isinstance(raw.get(name), str)
            },
        }
    data = schema.model_validate(raw).model_dump()
    if "flags" in data:
        got, expected = set(data["flags"]), set(vocabulary)
        if got != expected:
            raise ValueError(
                f"flags must be exactly the vocabulary; unknown: {sorted(got - expected)}, "
                f"missing: {sorted(expected - got)}"
            )
    return data


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]
