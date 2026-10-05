"""Role definitions and prompt rendering.

A role's prompt is part of its definition, like the sections it reads: the defaults
live next to this module in `prompts/<slug>.md`. Where a prompt is loaded from (the
product overlay of ADR-0005) is the `Prompts` port's business; rendering the
placeholders and hashing the raw text is the domain's, so a tuned prompt is
traceable even before it is committed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

from agentic.shared.configuration import RoleKind

from .document import ORIGINAL  # pseudo-section every role may declare: the task as it entered
from .schemas import (
    PRD,
    Answers,
    Classification,
    Consolidation,
    Consultation,
    Delivery,
    DesignDraft,
    DesignReview,
    Diagnosis,
    FinalValidation,
    InterviewTurn,
    PlatformAnswers,
    Review,
    RoleOutput,
    Ruling,
    SolutionTurn,
    StackProposal,
    TechAnswers,
    TechPlan,
    Verdict,
)
from .task import INTEGRATION

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    text: str
    hash: str
    source: Path


@dataclass(frozen=True)
class Role:
    name: str
    slug: str
    kind: RoleKind
    schema: type[RoleOutput]
    # Sections of the task document this role reads, in full (README.md, 6).
    # Declarative, so PjM can record it and see what a failing role was missing.
    # A role that lists itself reads its own previous section on a return. The human's
    # decisions (`HUMAN`) are not declared: every role reads them, last.
    reads: tuple[str, ...] = (ORIGINAL,)
    # Product roles also receive the product knowledge, whole (ADR-0006).
    reads_knowledge: bool = False
    # Budgets (README.md, 14): how hard the role thinks and how many turns it gets.
    effort: str = "medium"
    max_turns: int = 4
    # What the role may do while it runs (README.md, 4; ADR-0013). `tools` are the
    # built-in tools it sees; `allowed` are permission rules ("Bash(git diff:*)",
    # "Edit(docs/**)") that cage a role that does not execute; `denied` are rules that
    # hold even for a role that executes unattended (ADR-0011, addendum); `skills` are
    # the plugin skills it may invoke, by bare name. A role that `executes` writes
    # files and runs commands unattended, inside its working directory, under a money
    # cap: Developer.
    tools: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    executes: bool = False
    # Product roles also receive the engineering standards, whole (ADR-0012).
    reads_standards: bool = False


def render(raw: str, source: Path, **placeholders: object) -> Prompt:
    """Fill `{{name}}` placeholders; the hash is of the raw text, before filling."""
    values = {"date": date.today().isoformat(), **placeholders}

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"{source}: no value for placeholder {{{{{key}}}}}")
        return str(values[key])

    return Prompt(text=_PLACEHOLDER.sub(fill, raw), hash=prompt_hash(raw), source=source)


def prompt_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def describe_flags(vocabulary: dict[str, str]) -> str:
    """The flag vocabulary as the prompt shows it. Where the vocabulary comes from is
    the `FlagVocabulary` port's business (`core.knowledge`)."""
    return "\n".join(f"- `{name}`: {desc}" for name, desc in vocabulary.items())


# Tool sets. Reading the code is not executing (glossary: Ejecutar): a reviewer sees
# files and the diff, never writes anything. Running the product's checks is not
# executing either: the reviewer runs them to reproduce an objection (ADR-0016), and
# which commands those are the product declares (`reviewer_for`).
READ_CODE = ("Read", "Glob", "Grep", "Bash")
READ_CODE_RULES = (
    "Read",
    "Glob",
    "Grep",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
)
# An interviewer writes only the glossary and the ADRs of the product (ADR-0014).
WRITE_DOCS = ("Read", "Glob", "Grep", "Edit", "Write")
WRITE_DOCS_RULES = (
    "Read",
    "Glob",
    "Grep",
    "Edit(CONTEXT.md)",
    "Write(CONTEXT.md)",
    "Edit(docs/**)",
    "Write(docs/**)",
)
EXECUTE = ("Read", "Glob", "Grep", "Edit", "Write", "Bash", "Agent", "TodoWrite")
# What a role that commits may never do to the product's git (ADR-0011, addendum):
# rewrite its config (the identity comes in the environment), push, or touch other
# worktrees. `checkout` stays: `git checkout -- file` is legitimate.
GIT_DENIED = ("Bash(git config:*)", "Bash(git push:*)", "Bash(git worktree:*)")

PJM = Role(
    name="PjM",
    slug="pjm",
    kind="actor",
    schema=Classification,
    effort="low",
    max_turns=2,
)

# The definition stage (ADR-0014): the Product Analyst interviews PM, then writes.
PRODUCT_ANALYST_INTERVIEWER = Role(
    name="Product Analyst",
    slug="product_analyst_interviewer",
    kind="actor",
    schema=InterviewTurn,
    reads=(ORIGINAL, "PjM"),
    reads_knowledge=True,
    tools=WRITE_DOCS,
    allowed=WRITE_DOCS_RULES,
    skills=("grilling", "domain-modeling"),
    max_turns=12,
)

PM_INTERVIEWEE = Role(
    name="PM",
    slug="pm_interviewee",
    kind="verifier",
    schema=Answers,
    reads=(ORIGINAL,),
    reads_knowledge=True,
)

# Section names of the interviews (ADR-0014); the `Interview` values below own them.
PRD_INTERVIEW = "Interview: Product Analyst→PM"
SOLUTION_INTERVIEW = "Interview: Tech Lead efímero→Tech Lead"
# The findings table (issue #42): every review's findings in one section, written by
# the system at the fan-in, where the branches' work is merged. Not a role's delivery,
# so it is never versioned: it is a live list, and the raw record is the frontmatter.
FINDINGS = "Tech Lead: findings"

PRODUCT_ANALYST = Role(
    name="Product Analyst",
    slug="product_analyst",
    kind="actor",
    schema=PRD,
    reads=(ORIGINAL, "PjM", PRD_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

PM = Role(
    name="PM",
    slug="pm",
    kind="verifier",
    schema=Verdict,
    reads=(ORIGINAL, PRD_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

# The design branch (ADR-0040): on the feature path, only when `touches_ui` is set,
# after PM validates the PRD. UX Designer writes the flow of each job; UX Lead
# validates it with a walk of the prototype. UI Designer writes the screens design
# over the validated flow; UI Lead validates it. PM then consolidates both into
# the PRD. No routing lives here (`rules.design_step` and the nodes that call it are
# another subtask's): these are the roles and the outputs the branch runs.
#
# Read-only, like `TECH_LEAD_INTERVIEWEE`: a design role never edits the product's
# code or its own knowledge, it only reads and writes its structured output. The
# `compare` rule a product's `design` block adds is `design_rules`' business, not a
# difference declared here (the plan's own invariant: design roles differ from other
# roles only in `allowed`, and only `design_rules` adds to it).
UX_DESIGNER = Role(
    name="UX Designer",
    slug="ux_designer",
    kind="actor",
    schema=DesignDraft,
    reads=(ORIGINAL, "Product Analyst", "PM", "UX Designer"),
    reads_knowledge=True,
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    max_turns=12,
)

UX_LEAD = Role(
    name="UX Lead",
    slug="ux_lead",
    kind="verifier",
    schema=DesignReview,
    reads=(ORIGINAL, "Product Analyst", "PM", "UX Designer", "UX Lead"),
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    max_turns=12,
)

UI_DESIGNER = Role(
    name="UI Designer",
    slug="ui_designer",
    kind="actor",
    schema=DesignDraft,
    reads=(ORIGINAL, "Product Analyst", "PM", "UX Designer", "UX Lead", "UI Designer"),
    reads_knowledge=True,
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    max_turns=12,
)

UI_LEAD = Role(
    name="UI Lead",
    slug="ui_lead",
    kind="verifier",
    schema=DesignReview,
    reads=(
        ORIGINAL,
        "Product Analyst",
        "PM",
        "UX Designer",
        "UX Lead",
        "UI Designer",
        "UI Lead",
    ),
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    max_turns=12,
)

# PM's consolidation (ADR-0040): the PRD plus the validated flow and screens design.
# `replace(PM, ...)`, never an edit to it: same schema shape otherwise (a verdict, a
# reason), a section of its own, and its own output (`Consolidation` carries
# `questions_for_human` and `observations` instead of `Verdict`'s `definitions`).
PM_CONSOLIDATOR = replace(
    PM,
    name="PM: consolidation",
    slug="pm_consolidator",
    schema=Consolidation,
    reads=(
        ORIGINAL,
        PRD_INTERVIEW,
        "Product Analyst",
        "PM",
        "UX Designer",
        "UX Lead",
        "UI Designer",
        "UI Lead",
        "PM: consolidation",
    ),
    max_turns=20,
)

# PM's early, UI-only final validation (ADR-0040): the leads below walk the sample app
# and judge the comparisons; PM decides and, on `returned`, hands Tech Lead the claims
# the integration review must settle. `replace(UX_LEAD, ...)`/`replace(UI_LEAD,
# ...)`/`replace(PM, ...)`: same tools and schema shape, their own slugs, sections and
# a wider `reads` that reaches the plan (`Tech Lead`) and the earlier design sections.
UX_LEAD_FINAL = replace(
    UX_LEAD,
    name="UX Lead: final walk",
    slug="ux_lead_final",
    reads=(
        ORIGINAL,
        "Product Analyst",
        "PM",
        "UX Designer",
        "UX Lead",
        "UI Designer",
        "UI Lead",
        "PM: consolidation",
        "Tech Lead",
        "UX Lead: final walk",
    ),
)

UI_LEAD_FINAL = replace(
    UI_LEAD,
    name="UI Lead: final comparisons",
    slug="ui_lead_final",
    reads=(
        ORIGINAL,
        "Product Analyst",
        "PM",
        "UX Designer",
        "UX Lead",
        "UI Designer",
        "UI Lead",
        "PM: consolidation",
        "Tech Lead",
        "UI Lead: final comparisons",
    ),
)

PM_FINAL = replace(
    PM,
    name="PM: final validation",
    slug="pm_final",
    schema=FinalValidation,
    reads=(
        ORIGINAL,
        PRD_INTERVIEW,
        "Product Analyst",
        "PM",
        "PM: consolidation",
        "UX Lead: final walk",
        "UI Lead: final comparisons",
        "Tech Lead",
        "PM: final validation",
    ),
    max_turns=20,
)

# The bug path (README.md, 2; issue #65): the same two roles, without the interview.
# The Product Analyst reproduces instead of interviewing: it reads the code and runs
# the product's checks in the task's checkout, which is not executing (glossary:
# Ejecutar), and rules against the written definition; PM validates the ruling, or
# decides when there was no definition to rule against. Same `name` as the feature
# roles: the section and the resume read the document by it, on either path.
PRODUCT_ANALYST_BUG = replace(
    PRODUCT_ANALYST,
    slug="product_analyst_bug",
    schema=Diagnosis,
    reads=(ORIGINAL, "PjM", "Product Analyst", "PM"),
    tools=READ_CODE,
    # Plus the product's checks, per task: `diagnoser_for`.
    allowed=READ_CODE_RULES,
    # Sized for the work reproducing a bug takes, not for the number that happened to
    # be enough once (issues #69, #81): several modules, task documents and existing
    # tests of a repo this size, read cold, before there is anything to rule on. Two
    # runs exhausted 12 across four attempts with zero rulings, one of them reading
    # productively the whole way; the Tech Lead's own 12 covers a diff, not a repo read
    # from nothing, so it is not the comparison either. 30 is `TECH_LEAD_REVIEWER`'s 20
    # (the other role that reads a whole repo, not a diff) plus margin.
    max_turns=30,
)

PM_BUG = replace(PM, slug="pm_bug", schema=Ruling, reads=(ORIGINAL, "Product Analyst", "PM"))

# The technical stage: the ephemeral Tech Lead interviews Tech Lead, who then writes
# the plan; Developers build; Tech Lead reviews each subtask and the integration.
TECH_LEAD_EPHEMERAL = Role(
    name="Tech Lead efímero",
    slug="tech_lead_ephemeral",
    kind="verifier",
    schema=SolutionTurn,
    reads=(ORIGINAL, "PM"),
    reads_knowledge=True,
    reads_standards=True,
    tools=WRITE_DOCS + ("Bash",),
    allowed=WRITE_DOCS_RULES + ("Bash(git log:*)",),
    skills=("grilling", "domain-modeling"),
    max_turns=12,
)

TECH_LEAD_INTERVIEWEE = Role(
    name="Tech Lead",
    slug="tech_lead_interviewee",
    kind="verifier",
    schema=TechAnswers,
    reads=(ORIGINAL, "PM"),
    reads_knowledge=True,
    reads_standards=True,
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    skills=("codebase-design", "domain-modeling"),
    max_turns=12,
)

# PM answers what the Tech Lead consults during the solution interview (ADR-0014,
# addendum), resuming its validation session; its section is PM's, so the
# clarifications version it like a new delivery.
PM_CONSULTANT = Role(
    name="PM",
    slug="pm_consultant",
    kind="verifier",
    schema=Consultation,
    reads=(ORIGINAL, PRD_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

TECH_LEAD = Role(
    name="Tech Lead",
    slug="tech_lead",
    kind="verifier",
    schema=TechPlan,
    reads=(ORIGINAL, "PM", SOLUTION_INTERVIEW),
    reads_knowledge=True,
    reads_standards=True,
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    skills=("codebase-design", "domain-modeling"),
    max_turns=12,
)

# The platform path (issue #46, ADR-0017): the same solution stage with no PM behind
# it. Variants and not flags on the roles above, because what changes is structural:
# there is no definition to govern behaviour, and a question that a PM would have
# answered has only the human left. They read PjM, the one role upstream, so the grill
# can see on what grounds the task was called a platform enhancement at all.
TECH_LEAD_EPHEMERAL_PLATFORM = replace(
    TECH_LEAD_EPHEMERAL, slug="tech_lead_ephemeral_platform", reads=(ORIGINAL, PJM.name)
)

TECH_LEAD_INTERVIEWEE_PLATFORM = replace(
    TECH_LEAD_INTERVIEWEE,
    slug="tech_lead_interviewee_platform",
    reads=(ORIGINAL, PJM.name),
    schema=PlatformAnswers,
)

TECH_LEAD_PLATFORM = replace(
    TECH_LEAD, slug="tech_lead_platform", reads=(ORIGINAL, PJM.name, SOLUTION_INTERVIEW)
)

# The stack review path (ADR-0034, issue #40): the Tech Lead in proactive mode, over a
# product, with no task behind it at all -- no PjM, no issue, no branch. `replace(...)`
# of the platform variants, never an edit to them: the diff shows added tuples and zero
# edited ones. The ephemeral gets `READ_CODE`/`READ_CODE_RULES`, narrower than the
# platform variant's `WRITE_DOCS`: there is no task branch for its ADR draft to land on
# (the draft is an issue body instead), so `grilling` degrades to not editing files.
# `reads=(ORIGINAL,)` on the two interview seats and `(ORIGINAL, SOLUTION_INTERVIEW)` on
# the author: there is no `PjM` section on this path, so the platform variants' own
# `PJM.name` entry is dropped rather than inherited. No `{{brief}}`, no `{{stack}}`, no
# signals placeholder: the Brief and the ADRs arrive through `## Product knowledge`
# (ADR-0025), the standards through `standards()`, and the signals are the synthetic
# state's own `ORIGINAL` (`application/stack_review.py`).
TECH_LEAD_EPHEMERAL_STACK = replace(
    TECH_LEAD_EPHEMERAL_PLATFORM,
    slug="tech_lead_ephemeral_stack",
    reads=(ORIGINAL,),
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
)

TECH_LEAD_INTERVIEWEE_STACK = replace(
    TECH_LEAD_INTERVIEWEE_PLATFORM,
    slug="tech_lead_interviewee_stack",
    reads=(ORIGINAL,),
)

# `max_turns=20`, not the plan's own 12: the author emits three whole artefacts in one
# delivery (a stack diff, a complete `CODING_STANDARDS.md`, an ADR draft), which is
# `TECH_LEAD_REVIEWER`'s own budget for reading a whole repo, not a diff.
TECH_LEAD_STACK = replace(
    TECH_LEAD_PLATFORM,
    slug="tech_lead_stack",
    schema=StackProposal,
    reads=(ORIGINAL, SOLUTION_INTERVIEW),
    max_turns=20,
)

TECH_LEAD_REVIEWER = Role(
    name="Tech Lead",
    slug="tech_lead_review",
    kind="verifier",
    schema=Review,
    reads=(ORIGINAL, "PM", "Tech Lead", FINDINGS),
    reads_standards=True,
    tools=READ_CODE + ("Agent",),
    # Plus the product's checks, per task: `reviewer_for`.
    allowed=READ_CODE_RULES + ("Agent",),
    skills=("code-review",),
    max_turns=20,
)

# The light reviewer of the review-mode experiment (issue #28, ADR-0013 addendum): a
# Validador too, `kind="verifier"` unchanged, so the model it runs on still goes
# through `Configuration.model_overrides`/`model_for`, never a `kind` change. It reads
# neither `ORIGINAL` nor `PM`: the arm's whole point is a narrower, cheaper pass, and a
# reviewer with no PRD in front of it cannot ground a `spec-*` finding (ADR-0016,
# addendum) -- `reviewer_for` never hands it the integration, where that would matter.
# No `code-review`, no `Agent`: it runs the product's own checks to back an objection
# (ADR-0016) the same way the full reviewer does, on a strict subset of its tools.
TECH_LEAD_REVIEWER_LIGHT = replace(
    TECH_LEAD_REVIEWER,
    slug="tech_lead_review_light",
    skills=(),
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    reads=("Tech Lead", FINDINGS),
)

DEVELOPER = Role(
    name="Developer",
    slug="developer",
    kind="actor",
    schema=Delivery,
    reads=(ORIGINAL, "PM", "Tech Lead", FINDINGS),
    reads_standards=True,
    tools=EXECUTE,
    denied=GIT_DENIED,
    # `resolving-merge-conflicts` was the integrator's alone (ADR-0013); this task's
    # own reversal (ADR-0013, addendum) gives it to a subtask's own Developer too, for
    # the dependency-merge path in `develop.py` (gym #16). No prompt file is edited, so
    # `prompts.fingerprint` for `developer` does not move.
    skills=("tdd", "resolving-merge-conflicts"),
    executes=True,
    effort="high",
    max_turns=80,
)

DEVELOPER_INTEGRATOR = Role(
    name="Developer",
    slug="developer_integrator",
    kind="actor",
    schema=Delivery,
    reads=(ORIGINAL, "PM", "Tech Lead", FINDINGS),
    reads_standards=True,
    tools=EXECUTE,
    denied=GIT_DENIED,
    skills=("tdd", "resolving-merge-conflicts"),
    executes=True,
    effort="high",
    max_turns=80,
)


def check_rules(checks: Mapping[str, str]) -> tuple[str, ...]:
    """A permission rule per declared check. A prefix: the reviewer runs the command on
    one file or one test by appending to it."""
    return tuple(f"Bash({command}:*)" for command in checks.values())


def reviewer_for(
    checks: Mapping[str, str],
    subtask_id: str = INTEGRATION,
    mode: Literal["full", "light"] = "full",
) -> Role:
    """The reviewer of a task whose product declares `checks` (ADR-0016, addendum).

    `mode` is the review-mode experiment's switch (issue #28, `cfg.review_mode`):
    `full` is today's only value, and `subtask_id` defaults to `INTEGRATION`, so a
    caller that passes neither keyword -- `reviewer_for(checks)` -- returns exactly
    what it returns today, whatever `mode` a caller elsewhere might type. Under
    `light`, every subtask but the integration gets `TECH_LEAD_REVIEWER_LIGHT`; the
    integration always gets the full reviewer, in every mode, because its branch holds
    every subtask and arm B needs one expensive pass to test the hypothesis at all."""
    base = (
        TECH_LEAD_REVIEWER
        if mode == "full" or subtask_id == INTEGRATION
        else TECH_LEAD_REVIEWER_LIGHT
    )
    return replace(base, allowed=base.allowed + check_rules(checks))


def diagnoser_for(checks: Mapping[str, str]) -> Role:
    """The Product Analyst of a bug whose product declares `checks` (issue #65): it
    reproduces by running them, the way the reviewer backs an objection."""
    return replace(PRODUCT_ANALYST_BUG, allowed=PRODUCT_ANALYST_BUG.allowed + check_rules(checks))


ROLES = (
    PJM,
    PRODUCT_ANALYST_INTERVIEWER,
    PM_INTERVIEWEE,
    PRODUCT_ANALYST,
    PM,
    UX_DESIGNER,
    UX_LEAD,
    UI_DESIGNER,
    UI_LEAD,
    PM_CONSOLIDATOR,
    UX_LEAD_FINAL,
    UI_LEAD_FINAL,
    PM_FINAL,
    PRODUCT_ANALYST_BUG,
    PM_BUG,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_INTERVIEWEE,
    PM_CONSULTANT,
    TECH_LEAD,
    TECH_LEAD_EPHEMERAL_PLATFORM,
    TECH_LEAD_INTERVIEWEE_PLATFORM,
    TECH_LEAD_PLATFORM,
    TECH_LEAD_EPHEMERAL_STACK,
    TECH_LEAD_INTERVIEWEE_STACK,
    TECH_LEAD_STACK,
    TECH_LEAD_REVIEWER,
    TECH_LEAD_REVIEWER_LIGHT,
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
)


@dataclass(frozen=True)
class Pair:
    """Who returns and who receives the return; the limit counts per pair (README.md, 3).
    Not verifier→actor: QA returns to PM, to Tech Lead or to a Developer's subtask."""

    by: Role
    to: Role
    subtask: str | None = None

    @property
    def key(self) -> str:
        base = f"{self.by.name}→{self.to.name}"
        return base if self.subtask is None else f"{base}:{self.subtask}"


PAIR_PM_PA = Pair(by=PM, to=PRODUCT_ANALYST)

# The design branch's own pairs (ADR-0040): a lead returns to its own thinker, never
# to a Developer (README.md, 4; §13). Same shape as `PAIR_PM_PA`, same return limit,
# counted on its own key so a UX Lead return never shares a counter with UI
# Lead's, or with PM's own PRD returns.
PAIR_UX_LEAD = Pair(by=UX_LEAD, to=UX_DESIGNER)
PAIR_UI_LEAD = Pair(by=UI_LEAD, to=UI_DESIGNER)

# PM's early final validation return (ADR-0040, §13): PM reopens the integrator's own
# subtask, never the Developer's, so the pair is PM's own, to Tech Lead -- the role
# that leads the plan and the subtask Tech Lead hands the claims to, never a lead's own
# pair and never `pair_for`'s per-subtask one.
PAIR_PM_TECH_LEAD = Pair(by=PM, to=TECH_LEAD)


def pair_for(subtask_id: str) -> Pair:
    """Tech Lead returns to the Developer of that subtask, never to another one."""
    return Pair(by=TECH_LEAD, to=DEVELOPER, subtask=subtask_id)


@dataclass(frozen=True)
class Interview:
    """Rounds of questions from one role to another before a decision is written
    (ADR-0014): the interviewer asks and recommends, the interviewee decides, the
    author writes the deliverable afterwards from its own session of the interview."""

    interviewer: Role
    interviewee: Role
    author: Role
    # Which stage this interview belongs to (README.md, 4): the definition stage, run
    # by Product Analyst, or the solution stage, run by Tech Lead. The closed label
    # `interview()`'s own counts carry (`agentic.interview.*`; issue #28).
    stage: Literal["definition", "solution"]
    # Who the interviewee consults, in the same run, about what the definition does not
    # settle (glossary: Consulta). Only the consultant may leave questions for the human.
    consultant: Role | None = None

    @property
    def section(self) -> str:
        return f"Interview: {self.interviewer.name}→{self.interviewee.name}"

    def __post_init__(self) -> None:
        # The author and the verifier after it read the transcript by this name.
        if self.section not in self.author.reads:
            raise ValueError(f"{self.author.slug} must read {self.section!r}")

    @property
    def author_side(self) -> Role:
        """Whose session the author resumes: the side of the interview it played."""
        return self.interviewer if self.author.name == self.interviewer.name else self.interviewee


INTERVIEW_DEFINITION = Interview(
    interviewer=PRODUCT_ANALYST_INTERVIEWER,
    interviewee=PM_INTERVIEWEE,
    author=PRODUCT_ANALYST,
    stage="definition",
)
INTERVIEW_SOLUTION = Interview(
    interviewer=TECH_LEAD_EPHEMERAL,
    interviewee=TECH_LEAD_INTERVIEWEE,
    author=TECH_LEAD,
    stage="solution",
    consultant=PM_CONSULTANT,
)
# No consultant: on the platform path the questions the Tech Lead cannot settle go to
# the human, which is what `interview` already does when there is nobody to consult
# (issue #46). The section name is the same, so a document reads the same on both paths.
INTERVIEW_SOLUTION_PLATFORM = Interview(
    interviewer=TECH_LEAD_EPHEMERAL_PLATFORM,
    interviewee=TECH_LEAD_INTERVIEWEE_PLATFORM,
    author=TECH_LEAD_PLATFORM,
    stage="solution",
)
# The stack review path (ADR-0034): no consultant either, the same reason the platform
# path has none -- there is no PM here, and no task for a question to pause.
INTERVIEW_SOLUTION_STACK = Interview(
    interviewer=TECH_LEAD_EPHEMERAL_STACK,
    interviewee=TECH_LEAD_INTERVIEWEE_STACK,
    author=TECH_LEAD_STACK,
    stage="solution",
)

# Every `Interview` spec the system runs, named once so `INTERVIEW_PARTICIPANT_SLUGS`
# and its own pin (`tests/core/process/domain/test_rules.py`) can walk this instead of
# a second, hand-kept tuple that could drift from it (issue #40's own invariant).
INTERVIEWS = (
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    INTERVIEW_SOLUTION_PLATFORM,
    INTERVIEW_SOLUTION_STACK,
)
