"""What an escalation asks the human (GH-593): one question, a recommended answer and the options.

Presentation only: the table is closed by (class, cause), nothing here decides whether a pause is
a human's (`rules.pause_kind` does). Every option is a command the CLI has, so the owner answers
in a word; `{issue}` is filled by the one that renders the comment. A class with no entry for
its cause falls back to the entry with no cause.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

from .task import EscalationClass, MechanicalCause

RESUME: Final = "agentic resume --issue {issue}"
RESUME_ESTIMATE: Final = "agentic resume --issue {issue} --estimate {usd}"
RESUME_BUDGET: Final = "agentic resume --issue {issue} --task-budget {usd}"
CLOSE_UNPLANNED: Final = "gh issue close {issue} --reason 'not planned'"
DECIDE: Final = RESUME + " (after commenting `Decision: <your decision>` on the issue)"

Prompt = tuple[str, str, tuple[str, ...]]

AskedCause = Literal["api_break_missing"]
"""The cause of a `questions` escalation the owner answers on the issue (GH-610): not a
`MechanicalCause`, because the poller never retakes it by itself -- it waits for the answer."""
PromptCause = MechanicalCause | AskedCause

_RETRY: Final[Prompt] = (
    "Should the task try again now?",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
_RESTARTED: Final[Prompt] = (
    "The restarted attempt was returned twice too. Which change to the brief should it try?",
    DECIDE,
    (DECIDE, CLOSE_UNPLANNED),
)

API_BREAK_QUESTION: Final = (
    "`docs` failed at step `{step}` on head `{sha}`. If it is an API break you approved, answer "
    "with one line `{prefix} <reason>`; if not, it is a failure of the job and stays with you."
)
"""The neutral question of `api_break_missing` (GH-610); `{step}`, `{sha}` and `{prefix}` are filled
by the one that posts it, from the stack's `api_break_line` and the head."""

ESCALATION_PROMPTS: Final[dict[tuple[EscalationClass, PromptCause | None], Prompt]] = {
    ("classification", None): (
        "Which type is this task?",
        DECIDE,
        (DECIDE, CLOSE_UNPLANNED),
    ),
    ("returns", None): (
        "The pair kept returning the same work. What should change?",
        DECIDE,
        (DECIDE, RESUME, CLOSE_UNPLANNED),
    ),
    ("failure", None): _RETRY,
    ("cap", None): (
        "The task reached its cap. Raise it?",
        RESUME_BUDGET,
        (RESUME_BUDGET, CLOSE_UNPLANNED),
    ),
    ("questions", None): (
        "Answer the questions above.",
        DECIDE,
        (DECIDE, CLOSE_UNPLANNED),
    ),
    ("unmergeable", None): (
        "Merge the two branches by hand in the subtask's worktree, then resume?",
        RESUME,
        (RESUME, CLOSE_UNPLANNED),
    ),
    ("setup", None): _RETRY,
    ("estimate", None): (
        "The estimate was exceeded. Accept the proposed one?",
        RESUME_ESTIMATE,
        (RESUME_ESTIMATE, RESUME, CLOSE_UNPLANNED),
    ),
    ("system_delivery", None): _RETRY,
    ("ci", None): _RETRY,
}
ESCALATION_PROMPTS[("questions", "api_break_missing")] = (
    API_BREAK_QUESTION,
    "comment on the issue with the one line the question names",
    ("comment on the issue with the one line the question names", CLOSE_UNPLANNED),
)
for _cause in get_args(MechanicalCause):
    ESCALATION_PROMPTS[("ci" if _cause.startswith("ci_") else "system_delivery", _cause)] = _RETRY


def prompt_for(cls: EscalationClass, cause: PromptCause | None = None) -> Prompt:
    """The question, the recommended answer and the options of an escalation. A cause with no row
    of its own reads as its class's."""
    return ESCALATION_PROMPTS.get((cls, cause)) or ESCALATION_PROMPTS[(cls, None)]


def restarted_returns_prompt() -> Prompt:
    """The prompt of a `returns` escalation of a restarted attempt: the recommended answer
    changes the brief (a `Decision:`) before resuming."""
    return _RESTARTED


def tracker_human_prompt() -> Prompt:
    """The prompt of a `questions` pause raised by typed tracker items (GH-620): the owner decides
    each item shown above, so it recommends and offers only a `Decision:` (no close)."""
    return ("Decide each item above; each recommendation is the plan's.", DECIDE, (DECIDE,))
