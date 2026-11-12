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

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

from agentic.shared.configuration import DEFAULT_PLAN_SHAPE

from . import ci_workflow, plan_view, size_budget
from .ci_workflow import WorkflowNames
from .cost_history import History, Raise, bucket_median, raised_estimate, reference_usd
from .coverage import BRIEF_FORM, EXAMPLE, MAPPED_FORM, NOT_TOUCHED, UNSATISFIABLE
from .performance import UNIT_SUFFIX, Limit, Unit, VolumeUnit
from .task import (
    INTEGRATION,
    CopyCategory,
    DiagnosisVerdict,
    FindingCategory,
    FindingScope,
    Severity,
)
from .task_path import Size

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
    # Optional so a fake or an older output still validates; a missing one makes the path
    # `full` (`task_path.decide`). Shape only: which ids are valid is `decide`'s call.
    size: Size | None = Field(
        default=None,
        description="small, normal or large. A new screen or a new flow is never small",
    )
    risk: list[str] | None = Field(
        default=None,
        description="Ids from the risk menu only, [] when none applies",
    )


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


class PlanSize(RoleOutput):
    """The plan sizer's pre-estimate (GH-472): what the issue alone says the task will cost,
    before any plan exists. It decides the route and is recorded beside the plan's own
    estimate, which is how the threshold is recalibrated."""

    estimated_cost_usd: float = Field(
        ge=0,
        description="The TOTAL cost of the task in USD, from its start to its merge, "
        "calibrated on the history table in your prompt, from the request alone",
    )
    estimated_files: int = Field(
        ge=0,
        description="The files the pull request will show as changed: code, tests, ADRs, "
        "the glossary and generated files all count",
    )


class CritiqueFinding(BaseModel):
    """One objection of the plan critic, with what backs it (GH-472)."""

    place: str = Field(
        description="Where in the draft or the code it applies: a subtask id, a plan "
        "section, or `file:line`"
    )
    claim: str = Field(description="What is wrong with the plan, one sentence")
    evidence: str = Field(
        description="The command you ran and its output, or the line of the code, the "
        "standard or the ADR you cite. Empty only on an `observation`"
    )
    severity: Severity = Field(
        description="`blocking`: the plan breaks if it is not fixed. `important`: should be "
        "fixed. `observation`: a remark, or an objection you could not reproduce"
    )


class PlanCritique(RoleOutput):
    """The plan critic's one pass over a cold draft (GH-472): findings with evidence and
    the invariants it claims every subtask must honour. The Tech Lead revises once."""

    findings: list[CritiqueFinding] = Field(
        description="Every objection to the draft, most severe first. Empty when you found "
        "none you can back"
    )
    invariants_proposed: list[str] = Field(
        default_factory=list,
        description="Rules you claim every subtask must honour or the plan breaks. One per "
        "line, each checkable on one subtask's diff alone. The Tech Lead adopts or rejects "
        "each one; an empty string if none",
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


PartKind = Literal["change", "migration"]


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
    hand_files: int = Field(
        ge=0,
        description="The hand-written non-test files this part changes: at most "
        f"{size_budget.HAND_FILES}. What counts as hand-written is the size budget in your "
        "prompt",
    )
    hand_lines: int = Field(
        ge=0,
        description="The hand-written non-test lines this part adds: at most "
        f"{size_budget.HAND_LINES}. What counts as hand-written is the size budget in your "
        "prompt",
    )
    test_lines: int = Field(
        ge=0,
        description=f"The test lines this part adds: at most {size_budget.TEST_LINES}. The size "
        "budget in your prompt names what a test is",
    )
    kind: PartKind = Field(
        description="`migration` for a part that converts or moves existing data, and only "
        "for that: it is always a part of its own, ordered next to the part it depends on or "
        "the part that depends on it. `change` for everything else"
    )
    guard: str = Field(
        description="Empty when this part leaves no user-visible behaviour unfinished. "
        "Otherwise how it stays unreachable until the last part that finishes it: out of the "
        "navigation, or behind a flag, and where"
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


class CopyDraft(RoleOutput):
    """Tech Writer's texts (README.md, 4): the content is the `markdown` itself, every
    person-facing text of the change in the product's language and criteria. The next
    roles read it as a section, not as data, and the system never rewrites it."""


class CopyFinding(BaseModel):
    """One thing Tech Writer's review found in a text of the diff. Never `blocking`: a
    text does not return a subtask, and an `important` one is fixed inside the task."""

    location: str = Field(description="`file:line`, or the file when it is the whole file")
    claim: str = Field(description="What is wrong with the text, one sentence")
    evidence: str = Field(
        description="The line of the writing criteria or of the text Tech Writer wrote that "
        "it breaks. Empty only on an `observation`"
    )
    proposed: str = Field(description="The corrected text, ready to paste; empty if none")
    severity: Literal["important", "observation"] = Field(
        description="`important`: should be fixed inside the task. `observation`: a remark"
    )
    scope: FindingScope = Field(
        description="`subtask` when the branch under review can fix it; `plan` when it needs "
        "a change to the plan; `outside` is work the task did not ask for"
    )
    category: CopyCategory


class CopyReview(RoleOutput):
    findings: list[CopyFinding] = Field(description="Every finding, most severe first")
    settled: list[str] = Field(
        default_factory=list,
        description="Identifiers (`copy:<n>.<i>:<location>`) of the rows in the input that "
        "this delivery settles: you looked and the text now meets the criteria. One per "
        "line; empty when none. A row you do not repeat stays open",
    )
    proposed_standards: str = Field(
        default="",
        description="Only when the product has no writing criteria document: a starting "
        "`WRITING_STANDARDS.md` for the product. Empty otherwise",
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
    # The close-time capability pass: PM returns the knowledge, the system writes it.
    definitions: list[CapabilityFile] = Field(
        description="On `approved`, the complete file of each capability the task changed, "
        "reconciled with the delivered diff and the final reports, each opening with its "
        "`## Jobs`. Empty when no capability changed, and always empty on `returned`"
    )


class PerfBudget(BaseModel):
    """One limit of a QA Performance scenario (GH-318, ADR-0060). `source` says whose
    number it is: `issue`, stated by the request, or `proposed`, set by the Tech Lead now,
    with its `basis`. Fixed at planning: nothing edits it afterwards."""

    metric: str = Field(
        description="The metric the scenario's `measure` prints, ending with the unit's "
        "suffix: `_ms`, `_queries` or `_kb`, e.g. `list_ms`"
    )
    limit: float = Field(description="The most the metric may be, in `unit`")
    unit: Unit = Field(description="`ms`, `queries` or `kb`")
    source: Literal["issue", "proposed"] = Field(
        description="`issue` when the request states this number, `proposed` when you set it"
    )
    basis: str = Field(description="One line: where the number comes from")

    @model_validator(mode="after")
    def _check(self) -> PerfBudget:
        if not self.metric.endswith(UNIT_SUFFIX[self.unit]):
            raise ValueError(
                f"metric {self.metric!r} must end with {UNIT_SUFFIX[self.unit]!r} for the unit "
                f"{self.unit!r}"
            )
        if not math.isfinite(self.limit) or self.limit < 0:
            raise ValueError(f"the limit of {self.metric!r} must be a finite number, 0 or more")
        _one_line(self.basis, f"the basis of {self.metric!r}")
        return self


class PerfScenario(BaseModel):
    """A scenario the Tech Lead convenes QA Performance for (GH-318, ADR-0060): the data
    volume the system measures at and the budgets it judges. The commands that seed and
    measure come from the stack's `performance` key at the integrated head; these numbers
    come only from the plan."""

    name: str = Field(description="Slug, unique in the plan: the name in the stack's `performance`")
    volume: float = Field(description="The data volume to measure at, a positive number")
    volume_unit: VolumeUnit = Field(description="`rows`, `days`, `months` or `years`")
    basis: str = Field(description="One line: why this volume is the one that matters")
    budgets: list[PerfBudget] = Field(description="At least one budget")

    @model_validator(mode="after")
    def _check(self) -> PerfScenario:
        if not SLUG.match(self.name):
            raise ValueError(f"scenario name {self.name!r} must be a slug")
        if not math.isfinite(self.volume) or self.volume <= 0:
            raise ValueError(f"the volume of {self.name!r} must be a positive finite number")
        _one_line(self.basis, f"the basis of {self.name!r}")
        if not self.budgets:
            raise ValueError(f"scenario {self.name!r} has no budget")
        metrics = [b.metric for b in self.budgets]
        if len(set(metrics)) != len(metrics):
            raise ValueError(f"scenario {self.name!r} names a metric twice")
        return self


def _one_line(text: str, what: str) -> None:
    if not text.strip() or "\n" in text.strip():
        raise ValueError(f"{what} must be one non-empty line")


def plan_limits(scenario: PerfScenario) -> tuple[float, VolumeUnit, dict[str, Limit]]:
    """The volume, its unit and the limits by metric a measurement of `scenario` uses: the
    plan's numbers, the only ones a verdict compares against (GH-318 invariant 3)."""
    return (
        scenario.volume,
        scenario.volume_unit,
        {b.metric: Limit(b.limit, b.unit) for b in scenario.budgets},
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
    hand_files: int = Field(
        ge=0,
        description="The hand-written non-test files the pull request will change: a task may "
        f"declare at most {size_budget.HAND_FILES}. What counts as hand-written is the size "
        "budget in your prompt",
    )
    hand_lines: int = Field(
        ge=0,
        description="The hand-written non-test lines the pull request will add: a task may "
        f"declare at most {size_budget.HAND_LINES}. What counts as hand-written is the size "
        "budget in your prompt",
    )
    test_lines: int = Field(
        ge=0,
        description=f"The test lines the pull request will add: at most {size_budget.TEST_LINES}. "
        "A path is a test by the directory or the file name the size budget in your prompt names",
    )
    subtasks: list[Subtask] = Field(
        default_factory=list,
        description="The subtasks, in the order to start them. A task that is a part of a "
        "split always writes subtasks, never `parts`, whatever its estimate; otherwise empty "
        "only when `parts` holds the split",
    )
    parts: list[Part] = Field(
        default_factory=list,
        description="Only for a task that is not itself a part of a split, and only when the "
        "estimate does not fit (over the size limit the system sets, or the history's "
        "reference says so): at least two chained parts, each a small task of its own, in the "
        "order to build them, and no subtasks. A part of a split never writes `parts`. Empty "
        "when the plan fits",
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
    performance: list[PerfScenario] = Field(
        default_factory=list,
        description="Convenes QA Performance (ADR-0060) for a task whose cost scales with "
        "data or traffic, or whose issue states a performance budget: the scenarios to "
        "measure, each with its volume and budgets. Empty when not convened",
    )

    tracker_checklist: list[str] = Field(
        default_factory=list,
        description="What the human must do on the issue tracker (post a verdict, close or "
        "retitle issues), one line per item. Never a subtask: no role has `gh`. A task whose "
        "only work is this has no subtasks, no parts and `estimated_files: 0`; it may also sit "
        "next to subtasks. Empty when there is none",
    )

    ci_required: list[str] = Field(
        description="The job/check names the issue's done criteria need green on the pull "
        "request head: the check-run names CI reports (a matrix job's id stands for all its "
        "expansions); empty when the issue requires no external check (then `ci_none_reason` "
        "says why)"
    )
    ci_none_reason: str = Field(
        description="When `ci_required` is empty: one line saying why the issue requires no "
        "external check. An empty string when `ci_required` is not empty"
    )
    required_paths: list[str] = Field(
        default_factory=list,
        description="Paths of which the integrated diff must touch at least one (for "
        "example `.agentic/stack.yaml` when the issue asks for a change in it). Empty when "
        "the issue names none",
    )

    @model_validator(mode="after")
    def _ci_named_or_excused(self) -> TechPlan:
        for name in self.ci_required:
            _one_line(name, "a required CI check name")
        for path in self.required_paths:
            _one_line(path, "a required path")
        # At call time: `rules` imports this module, so a module-level import is a cycle.
        from .rules import forbidden_paths

        if forbidden := forbidden_paths(self.required_paths):
            raise ValueError(
                f"required_paths names {', '.join(f'`{p}`' for p in forbidden)}: the system "
                "never versions them on a delivery branch, because the presubmit refuses them, "
                "so no diff could touch them. Require a path outside those directories instead"
            )
        if self.ci_required and self.ci_none_reason.strip():
            raise ValueError("ci_none_reason must be empty when ci_required names checks")
        if not self.ci_required:
            _one_line(self.ci_none_reason, "ci_none_reason, when ci_required is empty,")
        return self

    @model_validator(mode="after")
    def _scenarios_once(self) -> TechPlan:
        names = [s.name for s in self.performance]
        if len(set(names)) != len(names):
            raise ValueError("the plan names a performance scenario twice")
        return self

    @model_validator(mode="after")
    def _checklist_lines(self) -> TechPlan:
        for item in self.tracker_checklist:
            _one_line(item, "a tracker checklist item")
        return self


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
        f"`{BRIEF_FORM}`. For example:\n{EXAMPLE}\nAn item with no location is work still to "
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
    landing_checks: list[str] = Field(
        default_factory=list,
        description="The CI check names, preferably from the landing gate listed in your "
        "prompt, whose evidence only the pull request's CI can give. Filling it never "
        "returns the subtask: the system turns the finding into an `observation` and "
        "the delivery's pull request body asks for those checks at landing. One "
        "objection per finding, never mixed with a defect the Developer can fix. An "
        "empty list when the finding needs no such evidence",
    )


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
    revised_required_paths: list[str] = Field(
        default_factory=list,
        description="Only when a human decision moves or drops one of the plan's required "
        "paths: the list that replaces them, never longer than the plan's. Empty when you do "
        "not revise (with `revision_decision` 0) or to drop every path",
    )
    revision_decision: int = Field(
        default=0,
        description="The number of the latest `### Decision N` in `## Human` that no revision "
        "cites yet, when you revise the required paths. 0 when you do not revise",
    )
    revision_reason: str = Field(
        default="",
        description="One line quoting the human decision that moves or drops the path. "
        "Required when `revision_decision` is above 0, empty otherwise",
    )

    @model_validator(mode="after")
    def _revision_is_cited_and_versionable(self) -> Review:
        if self.revision_decision < 0:
            raise ValueError("revision_decision must be 0 (no revision) or a decision number")
        if self.revision_decision > 0:
            _one_line(self.revision_reason, "revision_reason, which quotes the decision,")
        # At call time: `rules` imports this module, so a module-level import is a cycle.
        from .rules import forbidden_paths

        if forbidden := forbidden_paths(self.revised_required_paths):
            raise ValueError(
                f"revised_required_paths names {', '.join(f'`{p}`' for p in forbidden)}: the "
                "system never versions them on a delivery branch, so no diff could touch them"
            )
        return self


class LandingDiagnosis(RoleOutput):
    """The landing diagnoser's one run per task (GH-239): why the pull request keeps being
    refused after every cheaper step, written for the fresh run that will start over (or
    for the human, when it is already the second one)."""

    reason: str = Field(
        description="One line, why this pull request cannot land, in terms a fresh run "
        "can act on: what to do differently, not what went wrong in general"
    )
    verdict: DiagnosisVerdict = Field(
        description="`sync` when merging the current base into the existing branch and "
        "resolving the conflicts would land the approved work; `unlandable` when the "
        "approved work itself cannot land. The system acts on this field alone, never on "
        "`reason`"
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
    confidence: Literal["", "low", "medium", "high"] = Field(
        default="",
        description="Required on `add`, empty otherwise: how sure you are the lesson is general, "
        "not specific to this task. `high` only when it plainly holds for any task of the "
        "role; `low` or `medium` when the pattern needs to recur before it is kept",
    )
    scope: Literal["product", "system"] = Field(
        default="product",
        description="`system` only for a lesson that holds for any product the system "
        "builds; `product` otherwise, and whenever in doubt",
    )
    source: Literal["", "return", "finding", "design", "human_decision"] = Field(
        default="",
        description="Where the lesson comes from, as the input names the section. Empty "
        "when it cannot be told",
    )
    decision: int = Field(
        default=0,
        description="The number N of the `Decision N` a `human_decision` lesson cites. "
        "Required when `source` is `human_decision`, 0 otherwise",
    )

    @model_validator(mode="after")
    def _decision_is_cited(self) -> LessonDelta:
        if self.op == "add" and not self.confidence:
            raise ValueError("an `add` states its confidence: low, medium or high")
        if self.source == "human_decision" and self.decision < 1:
            raise ValueError("a human_decision lesson cites the number of its decision")
        return self


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


def parts_check(
    parts: list[dict[str, Any]],
    ratio: size_budget.Ratio = size_budget.UNPROVEN_RATIO,
    *,
    one_terminal: bool = False,
) -> None:
    """The structure of a split: at least two parts, each with a title and a body, each
    depending only on earlier parts, once. Earlier-only is what keeps the chain acyclic.

    A part that declares its size (GH-592) is judged on it times `ratio`, the same floor the
    plan is judged with, and must fit the size budget: a part is a task of its own. A part
    of kind `migration` has a neighbour in `depends_on`, before it or after it, so a data
    conversion never stands alone. With `one_terminal`, set when the plan is written by a part,
    exactly one part is depended on by none: its pull request closes the parent part's issue,
    and every other part reaches it."""
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
        _part_size_check(number, part, ratio)
    _migration_check(parts)
    if one_terminal:
        _terminal_check(parts)


def _part_size_check(number: int, part: dict[str, Any], ratio: size_budget.Ratio) -> None:
    size = declared_size(part)
    if size is None:
        return
    # The fit is decided where every fit is (invariant 1): `rules.estimate_fits`, imported
    # where it is used because `rules` imports this module. A part declares no cost of its
    # own, so only its size is judged; `crossed` only names the limits in the refusal.
    from .rules import estimate_fits

    if not estimate_fits(0.0, 0, None, size, ratio):
        over = size_budget.crossed(size_budget.judged(size, ratio))
        raise ValueError(
            f"part {number} does not fit the size budget ({', '.join(over)} judged over its "
            "cap): split it into smaller parts, each a task of its own"
        )


def _migration_check(parts: list[dict[str, Any]]) -> None:
    listed = {dep for part in parts for dep in part["depends_on"]}
    for number, part in enumerate(parts, 1):
        if part.get("kind") == "migration" and not part["depends_on"] and number not in listed:
            raise ValueError(
                f"part {number} is a migration with no neighbour: it must depend on the part "
                "that writes both shapes, or be depended on by the part that reads the new one"
            )


def _terminal_check(parts: list[dict[str, Any]]) -> None:
    listed = {dep for part in parts for dep in part["depends_on"]}
    sinks = [number for number in range(1, len(parts) + 1) if number not in listed]
    if len(sinks) != 1:
        found = ", ".join(str(number) for number in sinks)
        raise ValueError(
            "a part that splits needs exactly one terminal part, the one no other part depends "
            f"on, so that its pull request closes this part's issue; this plan has {len(sinks)} "
            f"(parts {found}): make the last part depend on the others"
        )


def _tracker_only_check(data: dict[str, Any]) -> None:
    """Shape (C): the plan's only work is on the issue tracker. It changes no file, so a
    positive `estimated_files` contradicts the checklist. Its cost has no fit to judge; a
    figure below what the task spent is raised by the plan stage, never refused (GH-529)."""
    if data["estimated_files"] != 0:
        raise ValueError(
            f"a plan whose only work is on the tracker changes no file: estimated_files must "
            f"be 0, this one says {data['estimated_files']}. If the task changes files, write "
            "subtasks and keep the checklist next to them"
        )


def floor_size(data: dict[str, Any]) -> int | None:
    """The subtask count a plan's estimate floor is read at: its subtasks, `None` for a
    plan with none (parts, tracker-only), which has the spent floor only. The check and the
    plan stage both ask here, so the figure a plan is judged on is the one it is stored as."""
    return len(data.get("subtasks") or []) or None


def _words(
    declared: float, raised: Raise | None, files: int, words: tuple[float, float | None]
) -> str:
    """How a refusal names the estimate: its own figure, or the system's raised one with the
    cause, so the retry can read a figure the Tech Lead never wrote. `words` are the spent
    and median figures the cause quotes."""
    if raised is None:
        return f"the estimate ({declared:.2f} USD, {files} files)"
    cause = plan_view.raise_cause(raised.cause, *words)
    return (
        f"the estimate (raised by the system from {declared:.2f} to {raised.cost_usd:.2f} USD: "
        f"{cause}, {files} files)"
    )


@dataclass(frozen=True)
class FitDecision:
    """Whether a plan's estimate fits one task, with the figures the decision used: the
    declared cost and files and the history's reference, so a text about the decision
    quotes what was decided on and never recomputes it (ADR-0054). A plan that declares its
    hand-written size (GH-592) also carries it as declared (`size`) and as judged after the
    floor (`judged_size`), and the fields of the size budget the judged figures crossed;
    all three are empty for a plan written before the size fields."""

    fits: bool
    cost_usd: float
    files: int
    reference_usd: float | None
    size: size_budget.PlanSize | None = None
    judged_size: size_budget.PlanSize | None = None
    crossed: tuple[str, ...] = ()


def declared_size(data: dict[str, Any]) -> size_budget.PlanSize | None:
    """The size a plan declares, or `None` for a plan written before the size fields: that
    one is judged by its cost and its total files alone."""
    hand_files = data.get(size_budget.HAND_FILES_FIELD)
    hand_lines = data.get(size_budget.HAND_LINES_FIELD)
    test_lines = data.get(size_budget.TEST_LINES_FIELD)
    if isinstance(hand_files, int) and isinstance(hand_lines, int) and isinstance(test_lines, int):
        return size_budget.PlanSize(hand_files, hand_lines, test_lines)
    return None


# What a refusal of a plan that does not fit says after the action, once: the split is the
# only way out, and a human's answer to this failure does not change that (ADR-0054).
SPLIT_NOT_WAIVED = (
    "A plan that does not fit is split; a human's answer to this failure does not build "
    "it whole (ADR-0054)."
)


class FitBucket:
    """The reference bucket of one plan run, pinned to the largest subtask count seen
    (GH-462). The bucket used to follow the output's shape, so one estimate was judged as
    subtasks against one bucket and as parts against another, and the two refusals
    contradicted each other. A run owns one; nothing about it is stored."""

    def __init__(self) -> None:
        self._pinned = 0

    def observe(self, subtasks: int) -> None:
        self._pinned = max(self._pinned, subtasks)

    def size(self, data: dict[str, Any]) -> int:
        """The size the reference is read at for this output: its subtasks, never below the
        pin; its parts, the pin when there is one and their own count otherwise."""
        parts = len(data.get("parts") or [])
        if parts:
            return self._pinned or parts
        return max(len(data.get("subtasks") or []), self._pinned)


def plan_fits(
    data: dict[str, Any],
    history: History,
    bucket: FitBucket | None = None,
    size_ratio: size_budget.Ratio = size_budget.UNPROVEN_RATIO,
) -> FitDecision:
    """The one place that decides whether a plan's estimate fits one task: the larger of
    the declared cost and the history's reference for the plan's own size (its parts, else
    its subtasks) against the limits `rules.estimate_fits` holds. With a `bucket`, the size
    is the run's pinned one (`FitBucket.size`); subtasks still answer to the reference of
    their own count, so the pin never makes a plan fit that its own bucket refuses.

    A plan that declares its hand-written size is judged on it times `size_ratio`, the floor
    factor (GH-592); one that does not is judged on its total files, as before."""

    # `rules` imports this module for `TaskType`, so the one function that decides the fit
    # is imported where it is used, never copied here (ADR-0054).
    from .rules import estimate_fits

    cost, files = data["estimated_cost_usd"], data["estimated_files"]
    own = len(data.get("parts") or []) or len(data.get("subtasks") or [])
    if bucket is None:
        reference = reference_usd(history, own)
    else:
        references = [reference_usd(history, bucket.size(data))]
        if not data.get("parts"):
            references.append(reference_usd(history, own))
        known = [value for value in references if value is not None]
        reference = max(known) if known else None
    size = declared_size(data)
    if size is None:
        return FitDecision(estimate_fits(cost, files, reference), cost, files, reference)
    judged = size_budget.judged(size, size_ratio)
    return FitDecision(
        estimate_fits(cost, files, reference, size, size_ratio),
        cost,
        files,
        reference,
        size,
        judged,
        size_budget.crossed(judged),
    )


def _single_check(subtasks: Sequence[Any], integration: str) -> None:
    """The refusals of `plan_shape: single` for a plan of subtasks (GH-471)."""
    if len(subtasks) != 1:
        raise ValueError(
            f"{PLAN_SHAPE_REFUSAL} you wrote {len(subtasks)} subtasks and the arm takes "
            "exactly one: merge them into one brief, keeping their dependency order. If the "
            "work does not fit one task, write parts instead"
        )
    if integration.strip():
        raise ValueError(
            f"{PLAN_SHAPE_REFUSAL} write no `integration`: move that work into the single "
            "subtask's brief"
        )


def _ci_names_check(
    data: dict[str, Any], workflow: WorkflowNames | None, stack_ci: Sequence[str]
) -> None:
    """Refuses the `ci_required` names the workflows prove are not check-run names
    (`ci_workflow.refusals`), all of them in one reason."""
    texts = [*(data.get("required_paths") or [])]
    for subtask in data.get("subtasks") or []:
        texts.append(subtask.get("title", ""))
        texts.append(subtask.get("brief", ""))
        texts.extend(subtask.get("seams") or [])
    reasons = ci_workflow.refusals(
        data.get("ci_required") or [], workflow, stack_ci, ci_workflow.touched_workflows(texts)
    )
    if reasons:
        raise ValueError("; ".join(reasons))


# The start of both refusals of the `plan_shape: single` arm (GH-471): the one text a
# reader, a test and the failure report match on.
PLAN_SHAPE_REFUSAL = "plan_shape single:"


def plan_check_for(
    history: History,
    spent_usd: float,
    *,
    fit_waived: bool = False,
    part_of: tuple[str, int, int] | None = None,
    workflow: WorkflowNames | None = None,
    stack_ci: Sequence[str] = (),
    plan_shape: str = DEFAULT_PLAN_SHAPE,
    size_ratio: size_budget.Ratio = size_budget.UNPROVEN_RATIO,
) -> Callable[[dict[str, Any]], None]:
    """The plan check with the refusals that need context (ADR-0054), built by the plan
    stage from the cost history, what the task already spent and whether a human waived
    the fit -- never carried in the task state. Each refusal is a legible reason: it is
    what the retry reads.

    A plan has exactly one of three shapes. (A) subtasks, no parts, and an estimate that
    fits -- or a waived fit. (B) parts, no subtasks, an estimate that does not fit, and no
    waiver. `rules.estimate_fits` decides which, over the history's reference for the
    bucket of the plan's own size: its subtasks in (A), its parts in (B). (C) no subtasks,
    no parts, a non-empty `tracker_checklist` and `estimated_files == 0` (GH-336): the
    only work is on the issue tracker, so there is nothing to fit and no reference to
    consult. A checklist next to subtasks (A) or next to parts (B) is allowed and changes
    nothing here: its items were checked one by one when the plan was read, and next to parts
    the split posts it on the issue (GH-350).

    A task that is itself a part (`part_of`, the `(task id, part, depth)` of its marker)
    splits again like any task while its depth is under `rules.MAX_SPLIT_DEPTH` (GH-592):
    subtasks over the line are refused and `parts` are asked for. At the cap, any `parts` is
    refused first, with the rule and no remedy that names parts, and subtasks over the line
    are not refused here -- the plan stage asks the human once instead (`plan_fits` decides,
    ADR-0054).

    A `ci_required` name the base's workflows (`workflow`, `None` when there is none to
    read) prove no check run carries is refused with the names to use instead, unless the
    plan declares the workflow it would change (GH-485); the stack's `ci:` names are always
    names a check run carries.

    Under `plan_shape` `single` (GH-471) a plan of shape (A) must have exactly one subtask
    and no integration, so the integration step the arm exists to remove never runs. It is
    judged after the fit, so a plan over the line is still sent toward parts first, and
    never for shape (B) or (C): a split or a tracker-only plan is not refused for its shape.

    A plan that declares its hand-written size (GH-592) is judged on it times `size_ratio`,
    the floor factor of the size budget; a plan with no size fields is judged as before."""

    # `rules` imports this module, so the one text is imported where it is used.
    from .rules import MAX_SPLIT_DEPTH, TRACKER_WITH_PARTS

    at_cap = part_of is not None and part_of[2] >= MAX_SPLIT_DEPTH

    bucket = FitBucket()

    def check(data: dict[str, Any]) -> None:
        parts = data.get("parts") or []
        subtasks = data.get("subtasks") or []
        _ci_names_check(data, workflow, stack_ci)
        if parts and part_of is not None and at_cap:
            raise ValueError(
                f"this task is part {part_of[1]} of {part_of[0]}, at split depth {part_of[2]}; "
                f"a split goes at most {MAX_SPLIT_DEPTH} levels deep: write subtasks only, "
                "and drop `parts`"
            )
        if parts and subtasks:
            raise ValueError(
                "a plan holds subtasks or parts, never both: a plan that does not fit "
                "is split into parts and builds nothing itself, so drop `subtasks` (the "
                f"parts carry the work). {TRACKER_WITH_PARTS}"
            )
        if data.get("tracker_checklist") and not subtasks and not parts:
            _tracker_only_check(data)
            return
        if parts:
            parts_check(parts, size_ratio, one_terminal=part_of is not None)
        else:
            plan_check(data)
        files, declared = data["estimated_files"], data["estimated_cost_usd"]
        if files < 1:
            raise ValueError("estimated_files must be at least one file: every task changes one")
        # A floor never refuses a plan: the system raises the figure and fit is judged once,
        # on it (GH-529).
        size = floor_size(data)
        raised = raised_estimate(declared, spent_usd, history, size)
        judged = data if raised is None else {**data, "estimated_cost_usd": raised.cost_usd}
        median = None if size is None else bucket_median(history, size)
        words = _words(declared, raised, files, (spent_usd, median))
        if subtasks:
            bucket.observe(len(subtasks))
        fits = plan_fits(judged, history, bucket, size_ratio).fits
        if parts:
            if fit_waived:
                raise ValueError(
                    "a human waived the fit of this task: it is built whole, so write "
                    "subtasks and no parts"
                )
            if fits:
                raise ValueError(
                    f"{words} fits: write subtasks "
                    "and no parts; a task is split only when it does not fit"
                )
            return
        if not fits and not fit_waived and not at_cap:
            raise ValueError(
                f"{words} does not fit one task "
                "(or the history's reference for this size does not): write parts, "
                f"at least two chained tasks, and no subtasks. {SPLIT_NOT_WAIVED}"
            )
        if plan_shape != DEFAULT_PLAN_SHAPE:
            _single_check(subtasks, data.get("integration") or "")

    return check
