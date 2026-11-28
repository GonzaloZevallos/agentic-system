"""What an escalation asks the human (GH-593): one question, a recommended answer and the options.

Presentation only: the table is closed by (class, cause), nothing here decides whether a pause is
a human's (`rules.pause_kind` does). Every option is a command the CLI has, so the owner answers
in a word; `{issue}` is filled by the one that renders the comment. A class with no entry for
its cause falls back to the entry with no cause.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, get_args

from .spec_delivery import SpecRouteCause
from .task import EscalationClass, MechanicalCause, OwnerDecisionCause, PauseEntry

RESUME: Final = "agentic resume --issue {issue}"
RESUME_ESTIMATE: Final = "agentic resume --issue {issue} --estimate {usd}"
RESUME_BUDGET: Final = "agentic resume --issue {issue} --task-budget {usd}"
CLOSE_UNPLANNED: Final = "gh issue close {issue} --reason 'not planned'"
DECIDE: Final = RESUME + " (after commenting `Decision: <your decision>` on the issue)"

Prompt = tuple[str, str, tuple[str, ...]]

AskedCause = Literal["api_break_missing", "drawing_departure", "drawing_cap"]
"""The cause of a `questions` escalation the owner answers on the issue (GH-610): not a
`MechanicalCause`, because the poller never retakes it by itself -- it waits for the answer.
`drawing_departure` and `drawing_cap` (GH-766) are answered by number: `drawing_answer.prompt_cause`
selects their rows and `drawing_answer.prompt_lines` builds the per-number `--drawing` commands."""
FormatCause = Literal["format_refusal", "format_hold"]
"""The cause of a `failure` escalation that a format failure raised (GH-1019): `format_refusal`
while the poller retakes it or when it escalates, `format_hold` once the task is held. Picked by
`escalate`, never stored: it is not a `MechanicalCause`."""
StallCause = Literal["session_stalled"]
"""The cause of a `failure` escalation whose last two attempts stalled (GH-978): picked by
`escalate` from `stalled_batch`, never stored, and not a `MechanicalCause`: the pause stays a
human's."""
PromptCause = (
    MechanicalCause | AskedCause | SpecRouteCause | OwnerDecisionCause | FormatCause | StallCause
)

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
# The per-number commands: built only by `drawing_answer.prompt_lines`, never offered as options.
DRAWING_ACCEPT: Final = "agentic resume --issue {issue} --drawing <n>=accept"
DRAWING_REJECT: Final = "agentic resume --issue {issue} --drawing <n>=reject"
_DRAWING_ANSWER: Final = (
    "comment on the issue with one line per number: `Drawing <n>: accept` or `Drawing <n>: reject`"
)
ESCALATION_PROMPTS[("questions", "drawing_departure")] = (
    "Decide each departure from the approved drawing above, by its number: comment "
    "`Drawing <n>: accept` or `Drawing <n>: reject`.",
    _DRAWING_ANSWER,
    (_DRAWING_ANSWER,),
)
ESCALATION_PROMPTS[("questions", "drawing_cap")] = (
    "The task reached the cap of rounds on drawing departures. Decide each one above, by its "
    "number (comment `Drawing <n>: accept` or `Drawing <n>: reject`), or close the issue.",
    _DRAWING_ANSWER,
    (_DRAWING_ANSWER, CLOSE_UNPLANNED),
)
ESCALATION_PROMPTS[("questions", "owner_decision")] = (
    "The decision was not applied: this task cannot be routed back automatically. Comment a new "
    "`Decision:` and the task is planned again with it.",
    DECIDE,
    (DECIDE, CLOSE_UNPLANNED),
)
ESCALATION_PROMPTS[("questions", "spec_owned")] = (
    "The Spec is the owner's file: edit it and resume.",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
ESCALATION_PROMPTS[("failure", "format_refusal")] = (
    "A role's output was refused by a format rule and the retakes did not fix it. Resume the task "
    "once the rule or the brief is fixed, or close it?",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
ESCALATION_PROMPTS[("failure", "format_hold")] = (
    "The task is held: its format retakes are spent. Resume it once the refused rule is fixed, "
    "or close it?",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
SESSION_STALLED_PROMPT: Final[Prompt] = (
    "The session stalled twice in a row with no sign of life. The failure rows in the task "
    "document say how long it was silent, the last event and whether a hook was in flight. If a "
    "hook was, look at that check before resuming. Resume?",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
ESCALATION_PROMPTS[("failure", "session_stalled")] = SESSION_STALLED_PROMPT
ESCALATION_PROMPTS[("ci", "check_timeout")] = (
    "A check timed out twice with no failing test, on the same head and with the same workers. "
    "Is the machine at fault, or does the check need a longer timeout? Resume once decided.",
    RESUME,
    (RESUME, CLOSE_UNPLANNED),
)
for _cause in get_args(MechanicalCause):
    if _cause == "check_timeout":
        continue
    ESCALATION_PROMPTS[("ci" if _cause.startswith("ci_") else "system_delivery", _cause)] = _RETRY
for _spec_cause in get_args(SpecRouteCause):
    ESCALATION_PROMPTS[("classification", _spec_cause)] = (
        (
            "A Spec: line is kept only for a feature, and PjM typed this task otherwise. "
            "Remove the line, or decide that it is a feature?",
            DECIDE,
            (DECIDE, RESUME, CLOSE_UNPLANNED),
        )
        if _spec_cause == "spec_type"
        else (
            f"The task's Spec: route was refused ({_spec_cause}, see the reason above). Fix the "
            "issue's Spec: line, the spec file or owner_login, then resume?",
            RESUME,
            (RESUME, CLOSE_UNPLANNED),
        )
    )


def prompt_for(cls: EscalationClass, cause: PromptCause | None = None) -> Prompt:
    """The question, the recommended answer and the options of an escalation. A cause with no row
    of its own reads as its class's."""
    return ESCALATION_PROMPTS.get((cls, cause)) or ESCALATION_PROMPTS[(cls, None)]


def stalled_batch(pause_log: Sequence[PauseEntry]) -> bool:
    """Whether the escalation that closes `pause_log` follows two stalled attempts (GH-978): the
    batch is the run of `failure` entries just before the last `escalation` entry, whatever their
    section, and it counts when it has at least two entries and the last two are `stall`. A log
    with no escalation entry has no batch. Reads nothing but the log: no epoch, no terminal
    failures."""
    anchor = next(
        (i for i in range(len(pause_log) - 1, -1, -1) if pause_log[i]["kind"] == "escalation"),
        None,
    )
    if anchor is None:
        return False
    batch: list[str] = []
    for entry in reversed(pause_log[:anchor]):
        if entry["kind"] != "failure":
            break
        batch.append(entry["value"])
    # `batch` is newest first: the last two attempts are its first two.
    return len(batch) >= 2 and batch[0] == "stall" and batch[1] == "stall"


def restarted_returns_prompt() -> Prompt:
    """The prompt of a `returns` escalation of a restarted attempt: the recommended answer
    changes the brief (a `Decision:`) before resuming."""
    return _RESTARTED


def tracker_human_prompt() -> Prompt:
    """The prompt of a `questions` pause raised by typed tracker items (GH-620): the owner decides
    each item shown above, so it recommends and offers only a `Decision:` (no close)."""
    return ("Decide each item above; each recommendation is the plan's.", DECIDE, (DECIDE,))
