"""The words of a `Format hold:` Nota (GH-1019): what the poller tells the tracker when it holds a
task whose format retakes are spent.

The poller posts it (`poll.hold_format_failures`) through `Tracker.note`, and `resume.
SYSTEM_COMMENTS` registers its prefix so a person's answer is never read past it. What it says
lives here, so the poller owns when and this module owns what. It imports only `..domain*`: never
`poll`, `resume` or `escalate`, which import it."""

from __future__ import annotations

from ..domain import escalation_prompts, format_retakes
from ..domain.task import FormatHold

# How the Nota starts, registered into `resume.SYSTEM_COMMENTS`: a person's answer is the first
# comment after it that does not.
FORMAT_HOLD = "Format hold: "

NO_ISSUE = "no system issue was filed for {rule}; see tracker_failures in the task document"


def marker(hold: FormatHold) -> str:
    """The comment's last line, which says this hold was told: the rule and the instant of the
    hold, both of which `format_retakes.hold_patch` writes once."""
    return f"<!-- agentic-format-hold: {hold['rule']} {hold['at']} -->"


def cause_wording(hold: FormatHold) -> str:
    """Why the hold is due, in the words of the count that spent it."""
    if hold["cause"] == "rule_spent":
        return f"{format_retakes.FORMAT_RETAKE_MAX} retakes for {hold['rule']}"
    return f"{format_retakes.FORMAT_RETAKE_TASK_MAX} format retakes on this task"


def note(hold: FormatHold, issue: int) -> str:
    """The Nota of a hold: the rule, the cause, where the system's own issue about the rule is
    (or that there is none) and the commands of the `(failure, format_hold)` row, which `issue`
    fills. It ends with `marker`, the line a later round looks for before it posts again."""
    rule = hold["rule"]
    where = hold.get("issue_url") or NO_ISSUE.format(rule=rule)
    question, _, options = escalation_prompts.prompt_for("failure", "format_hold")
    commands = [f"- `{option.format(issue=issue)}`" for option in options]
    return "\n".join(
        [
            f"{FORMAT_HOLD}{rule}",
            "",
            f"The task is held after {cause_wording(hold)}.",
            f"Refusal issue: {where}",
            "",
            f"Question: {question}",
            "Options:",
            *commands,
            "",
            marker(hold),
        ]
    )
