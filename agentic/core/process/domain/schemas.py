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
from collections.abc import Callable
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from .cost_history import MIN_BUCKET, History, bucket_median, reference_usd
from .coverage import BRIEF_FORM, MAPPED_FORM, NOT_TOUCHED, UNSATISFIABLE
from .task import INTEGRATION, FindingCategory, FindingScope, Severity

TaskType = Literal["bug", "feature", "platform_enhancement"]
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Subtasks a plan may hold: it bounds the plan; the task cap does not depend on it
# (ADR-0004, 2026-10-05 addendum).
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
    screens: list[str] = Field(
        default_factory=list,
        description="For a subtask that touches UI: the screens of the design (ADR-0040) "
        "it builds, named as the diseño de pantallas names them. Empty for a subtask with "
        "no screen of its own",
    )


class Part(BaseModel):
    title: str = Field(description="One line: the title of the issue this part becomes")
    body: str = Field(
        description="What this part delivers, as the issue's description: a reader who has "
        "not seen the plan builds it from this alone, and it lands and shows in the product "
        "by itself"
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Numbers of the EARLIER parts that must be merged before this one starts "
        "(1 is the first part). Empty when it can start at once",
    )


class DesignItem(BaseModel):
    """One remark from a lead (UX Lead or UI Lead) or from PM's consolidation (ADR-0040),
    the unit `design_items` is made of. `blocking` means the job cannot be completed as
    the design stands; anything else is `observation`, friction that does not pause the
    task (glossary: Observación al diseño). Never written from section prose: only
    from this field, structured."""

    kind: Literal["observation", "blocking"]
    step: str = Field(description="The flow step or screen this remark is about")
    text: str = Field(description="The remark itself, one or two sentences")


class DesignDraft(RoleOutput):
    """UX Designer's flow or UI Designer's screens design (ADR-0040): the content is
    the `markdown` itself, nothing structured beyond the usual fields -- the next role
    reads it as a section, not as data."""


class DesignReview(RoleOutput):
    """UX Lead's walk or UI Lead's comparison judgement (ADR-0040), and the same
    shape for their final-validation counterparts: a verdict the design step
    (`rules.design_step`) reads, with the remarks that back it. Leads never return to
    a Developer (README.md, 4): a `returned` verdict here sends the thinker back, never
    the code."""

    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what the thinker must fix")
    items: list[DesignItem] = Field(
        description="Every remark, blocking or observation; empty only when there is none"
    )


class Consolidation(RoleOutput):
    """PM's consolidation of the validated flow and screens design into the PRD
    (ADR-0040): `markdown` is the consolidated PRD section. What UX marked as
    impeding the job becomes a question for the human; the rest of the friction
    becomes an observation."""

    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what Product Analyst must fix")
    questions_for_human: list[str] = Field(
        description="What UX or UI marked as impeding the job, written for a person who "
        "has not read the task. Empty if none"
    )
    observations: list[DesignItem] = Field(
        description="Friction that does not impede the job (kind is always "
        "`observation` here): it travels to the pull request, never pauses the task. "
        "Empty if none"
    )


class FinalValidation(RoleOutput):
    """PM's early, UI-only final validation (ADR-0040): the verdict over UX Lead's
    final walk and UI Lead's final comparisons, before Tech Lead's own close."""

    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what Tech Lead must fix first")
    blocking: list[str] = Field(
        description="On `returned`, one-line claims the integration review must settle, "
        "each checkable on the diff alone. Empty on `approved`"
    )


class TechPlan(RoleOutput):
    estimated_cost_usd: float = Field(
        description="The TOTAL cost of the task in USD, from its start to its merge, "
        "including what it already spent (the plan prompt says how much). Calibrate it "
        "on the history table, not on the scope alone"
    )
    estimated_files: int = Field(
        description="The files the pull request will show as changed (its `changedFiles`): "
        "code, tests, ADRs, the glossary, lockfiles and generated files all count"
    )
    subtasks: list[Subtask] = Field(
        default_factory=list,
        description="The subtasks, in the order to start them. Empty ONLY when the estimate "
        "does not fit and `parts` holds the split",
    )
    parts: list[Part] = Field(
        default_factory=list,
        description="When the estimate does not fit (over the size limit the system sets, or the "
        "history's reference says so): at least two chained parts, each a small task of its "
        "own, in the order to build them, and no subtasks. Empty when the plan fits",
    )
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
    """The stack review's own author output (ADR-0034): a verdict and three pieces a
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
    coverage: list[str] = Field(
        default_factory=list,
        description="The coverage map: one line per plan invariant (numbered as the prompt "
        "numbers them) and per item of your subtask brief, each mapped to the `file:line` "
        "locations in your diff that satisfy it. Forms: "
        f"`{MAPPED_FORM}`, `invariant N: {UNSATISFIABLE}`, "
        f"`invariant N: {NOT_TOUCHED}: <one phrase>`, "
        f"`{BRIEF_FORM}`. An item with no location is work still to "
        "do. Empty only when the plan has no invariants and the brief has no items, and "
        "always empty on the integration subtask",
        json_schema_extra={"lines_required": True},
    )


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
        "it needs a change to the plan or to another subtask's branch; `outside` is work the "
        "task did not do or the issue did not ask for: it never returns the subtask and goes "
        "to the task's single follow-up issue. On the integration "
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


class LandingDiagnosis(RoleOutput):
    """The landing diagnoser's one run per task (GH-239): why the pull request keeps being
    refused after every cheaper step, written for the fresh run that will start over (or
    for the human, when it is already the second one)."""

    reason: str = Field(
        description="One line, why this pull request cannot land, in terms a fresh run "
        "can act on: what to do differently, not what went wrong in general"
    )


class LessonDelta(BaseModel):
    """One change the reflector proposes to a role's playbook (ADR-0043). The curator,
    which is code (`playbook.apply`), decides whether it applies: a field a delta does not
    use stays an empty string."""

    op: Literal["add", "merge", "bump", "retire"] = Field(
        description="`add`: a new lesson. `merge`: reword an existing item (`item_id`) "
        "with `lesson`. `bump`: an existing item (`item_id`) happened again in `tasks`. "
        "`retire`: drop an existing item (`item_id`)"
    )
    slug: str = Field(description="The role the lesson is for, as the input names it")
    category: str = Field(
        description="The finding category the lesson targets, as the input names it. "
        "Empty on `retire`"
    )
    lesson: str = Field(
        description="On `add` and `merge`: one imperative line of at most 240 characters, "
        "with no line break, backtick, double opening brace or link. Empty otherwise"
    )
    item_id: str = Field(
        description="On `merge`, `bump` and `retire`: the id of the item, as the input "
        "shows it. Empty on `add`"
    )
    tasks: list[str] = Field(
        description="The ids of the closed tasks whose returns this delta cites. "
        "Required on `add` and `bump`"
    )


class Reflection(RoleOutput):
    deltas: list[LessonDelta] = Field(
        description="The playbook changes, at most a handful, each one citing real returns. "
        "Empty when no return teaches anything the role could do differently"
    )


def plan_check(data: dict[str, Any]) -> None:
    """A plan's subtasks are slugs, each with seams, depending only on subtasks that
    exist; `integration` is the system's own. Only the structured output is checked
    here: the markdown's own `### Subtasks` block is never a role failure (issue
    #152) -- the plan stage completes it from these same subtasks afterwards
    (`plan_view.with_subtasks_block`), so a model that gets the structure right never
    retries, or escalates, over the prose alone."""
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
        # A field that is never empty when there is something to map does not invite the
        # empty string; every other line list keeps its suffix byte for byte.
        required = out["properties"][name].get("lines_required", False)
        suffix = ". One per line" if required else ". One per line; an empty string if none"
        out["properties"][name] = {"type": "string", "description": f"{description}{suffix}"}
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


def parts_check(parts: list[dict[str, Any]]) -> None:
    """The structure of a split: at least two parts, each with a title and a body, each
    depending only on earlier parts, once. Earlier-only is what keeps the chain acyclic."""
    if len(parts) < 2:
        raise ValueError(
            f"a split needs at least two parts, this one has {len(parts)}: "
            "a plan that does not fit is cut into chained tasks"
        )
    for number, part in enumerate(parts, 1):
        if not part["title"].strip():
            raise ValueError(f"part {number} has no title")
        if not part["body"].strip():
            raise ValueError(f"part {number} has no body: it is the whole description of its issue")
        if len(set(part["depends_on"])) != len(part["depends_on"]):
            raise ValueError(f"part {number} lists the same dependency twice")
        for dep in part["depends_on"]:
            if not 1 <= dep <= len(parts):
                raise ValueError(f"part {number} depends on unknown part {dep}")
            if dep >= number:
                raise ValueError(
                    f"part {number} depends on part {dep}: a part depends only on earlier parts"
                )


def plan_check_for(
    history: History, spent_usd: float, *, fit_waived: bool = False
) -> Callable[[dict[str, Any]], None]:
    """The plan check with the refusals that need context (ADR-0054), built by the plan
    stage from the cost history, what the task already spent and whether a human waived
    the fit -- never carried in the task state. Each refusal is a legible reason: it is
    what the retry reads.

    A plan has exactly one of two shapes. (A) subtasks, no parts, and an estimate that
    fits -- or a waived fit. (B) parts, no subtasks, an estimate that does not fit, and no
    waiver. `rules.estimate_fits` decides which, over the history's reference for the
    bucket of the plan's own size: its subtasks in (A), its parts in (B)."""

    # `rules` imports this module for `TaskType`, so the one function that decides the fit
    # is imported where it is used, never copied here (ADR-0054).
    from .rules import estimate_fits

    def check(data: dict[str, Any]) -> None:
        parts = data.get("parts") or []
        subtasks = data.get("subtasks") or []
        if parts and subtasks:
            raise ValueError(
                "a plan holds subtasks or parts, never both: a plan that does not fit "
                "is split into parts and builds nothing itself"
            )
        if parts:
            parts_check(parts)
        else:
            plan_check(data)
        files, cost = data["estimated_files"], data["estimated_cost_usd"]
        if files < 1:
            raise ValueError("estimated_files must be at least one file: every task changes one")
        if cost < spent_usd:
            raise ValueError(
                f"estimated_cost_usd {cost:.2f} is below what the task already spent "
                f"{spent_usd:.2f}: the estimate is the TOTAL cost, spent included"
            )
        size = len(parts) if parts else len(subtasks)
        fits = estimate_fits(cost, files, reference_usd(history, size))
        if parts:
            if fit_waived:
                raise ValueError(
                    "a human waived the fit of this task: it is built whole, so write "
                    "subtasks and no parts"
                )
            if fits:
                raise ValueError(
                    f"the estimate ({cost:.2f} USD, {files} files) fits: write subtasks "
                    "and no parts; a task is split only when it does not fit"
                )
            return
        if not fits and not fit_waived:
            raise ValueError(
                f"the estimate ({cost:.2f} USD, {files} files) does not fit one task "
                "(or the history's reference for this size does not): write parts, "
                "at least two chained tasks, and no subtasks"
            )
        median = bucket_median(history, len(data["subtasks"]))
        if median is not None and cost < 0.5 * median:
            raise ValueError(
                f"estimated_cost_usd {cost:.2f} is below half the median {median:.2f} of "
                f"closed tasks with this many subtasks (at least {MIN_BUCKET} of them): "
                "estimate from the history table or explain the gap in the plan"
            )

    return check
