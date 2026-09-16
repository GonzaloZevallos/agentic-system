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

from agentic.shared.configuration import RoleKind

from .document import ORIGINAL  # pseudo-section every role may declare: the task as it entered
from .schemas import (
    Answers,
    Classification,
    Consultation,
    Definition,
    Delivery,
    InterviewTurn,
    Review,
    RoleOutput,
    TechAnswers,
    TechPlan,
    Verdict,
)

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
    kind="thinker",
    schema=Classification,
    effort="low",
    max_turns=2,
)

# The definition stage (ADR-0014): the Product Analyst interviews PM, then writes.
PRODUCT_ANALYST_INTERVIEWER = Role(
    name="Product Analyst",
    slug="product_analyst_interviewer",
    kind="thinker",
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
    kind="validator",
    schema=Answers,
    reads=(ORIGINAL,),
    reads_knowledge=True,
)

# Section names of the interviews (ADR-0014); the `Interview` values below own them.
DEFINITION_INTERVIEW = "Interview: Product Analyst→PM"
SOLUTION_INTERVIEW = "Interview: Tech Lead efímero→Tech Lead"

PRODUCT_ANALYST = Role(
    name="Product Analyst",
    slug="product_analyst",
    kind="thinker",
    schema=Definition,
    reads=(ORIGINAL, "PjM", DEFINITION_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

PM = Role(
    name="PM",
    slug="pm",
    kind="validator",
    schema=Verdict,
    reads=(ORIGINAL, DEFINITION_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

# The technical stage: the ephemeral Tech Lead interviews Tech Lead, who then writes
# the plan; Developers build; Tech Lead reviews each subtask and the integration.
TECH_LEAD_EPHEMERAL = Role(
    name="Tech Lead efímero",
    slug="tech_lead_ephemeral",
    kind="validator",
    schema=InterviewTurn,
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
    kind="validator",
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
    kind="validator",
    schema=Consultation,
    reads=(ORIGINAL, DEFINITION_INTERVIEW, "Product Analyst", "PM"),
    reads_knowledge=True,
)

TECH_LEAD = Role(
    name="Tech Lead",
    slug="tech_lead",
    kind="validator",
    schema=TechPlan,
    reads=(ORIGINAL, "PM", SOLUTION_INTERVIEW),
    reads_knowledge=True,
    reads_standards=True,
    tools=READ_CODE,
    allowed=READ_CODE_RULES,
    skills=("codebase-design", "domain-modeling"),
    max_turns=12,
)

TECH_LEAD_REVIEWER = Role(
    name="Tech Lead",
    slug="tech_lead_review",
    kind="validator",
    schema=Review,
    reads=(ORIGINAL, "PM", "Tech Lead"),
    reads_standards=True,
    tools=READ_CODE + ("Agent",),
    # Plus the product's checks, per task: `reviewer_for`.
    allowed=READ_CODE_RULES + ("Agent",),
    skills=("code-review",),
    max_turns=20,
)

DEVELOPER = Role(
    name="Developer",
    slug="developer",
    kind="thinker",
    schema=Delivery,
    reads=(ORIGINAL, "PM", "Tech Lead"),
    reads_standards=True,
    tools=EXECUTE,
    denied=GIT_DENIED,
    skills=("tdd",),
    executes=True,
    effort="high",
    max_turns=80,
)

DEVELOPER_INTEGRATOR = Role(
    name="Developer",
    slug="developer_integrator",
    kind="thinker",
    schema=Delivery,
    reads=(ORIGINAL, "PM", "Tech Lead"),
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


def reviewer_for(checks: Mapping[str, str]) -> Role:
    """The reviewer of a task whose product declares `checks` (ADR-0016, addendum)."""
    return replace(TECH_LEAD_REVIEWER, allowed=TECH_LEAD_REVIEWER.allowed + check_rules(checks))


ROLES = (
    PJM,
    PRODUCT_ANALYST_INTERVIEWER,
    PM_INTERVIEWEE,
    PRODUCT_ANALYST,
    PM,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_INTERVIEWEE,
    PM_CONSULTANT,
    TECH_LEAD,
    TECH_LEAD_REVIEWER,
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
)


@dataclass(frozen=True)
class Pair:
    """Who returns and who receives the return; the limit counts per pair (README.md, 3).
    Not validator→thinker: QA returns to PM, to Tech Lead or to a Developer's subtask."""

    by: Role
    to: Role
    subtask: str | None = None

    @property
    def key(self) -> str:
        base = f"{self.by.name}→{self.to.name}"
        return base if self.subtask is None else f"{base}:{self.subtask}"


PAIR_PM_PA = Pair(by=PM, to=PRODUCT_ANALYST)


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
    # Who the interviewee consults, in the same run, about what the definition does not
    # settle (glossary: Consulta). Only the consultant may leave questions for the human.
    consultant: Role | None = None

    @property
    def section(self) -> str:
        return f"Interview: {self.interviewer.name}→{self.interviewee.name}"

    def __post_init__(self) -> None:
        # The author and the validator after it read the transcript by this name.
        if self.section not in self.author.reads:
            raise ValueError(f"{self.author.slug} must read {self.section!r}")

    @property
    def author_side(self) -> Role:
        """Whose session the author resumes: the side of the interview it played."""
        return self.interviewer if self.author.name == self.interviewer.name else self.interviewee


INTERVIEW_DEFINITION = Interview(
    interviewer=PRODUCT_ANALYST_INTERVIEWER, interviewee=PM_INTERVIEWEE, author=PRODUCT_ANALYST
)
INTERVIEW_SOLUTION = Interview(
    interviewer=TECH_LEAD_EPHEMERAL,
    interviewee=TECH_LEAD_INTERVIEWEE,
    author=TECH_LEAD,
    consultant=PM_CONSULTANT,
)
