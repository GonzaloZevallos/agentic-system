"""An escalation reaches the human through the issue (README.md, 3; ADR-0011): the
`needs-human` label puts the task in the queue, and a comment carries the reason,
after an interview the questions to answer, and how to resume. The state does not
change: it already says why the run stopped. The task pauses right after, in a node
of its own (ADR-0003, addendum), so resuming never tells the human twice.

An escalation by the task cap also breaks the spending down by phase and proposes a
new cap: raising it is the human's call (ADR-0004, 2026-10-05 addendum).

The document is saved once more, from the merged state this node receives. Each branch
of a fan-out saves its own view of it (`finish`), so the last writer loses its sisters'
deliveries until something saves again with the state merged: in the technical stage
that is `integrate`, which hands this node the escalation, and everywhere else it is
this one. A resume from the document must not redo a subtask already delivered."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import document, escalation_prompts, rules
from ..domain.task import EscalationClass, TaskState
from .ports import Ports

NEEDS_HUMAN = "needs-human"
# How the system's own comments start: a resume looks past them for the human's answer.
ESCALATED = "Escalated: "


async def escalate(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    reason = state.get("escalated")
    if not reason:
        return {}
    ports.documents.save(state)
    if rules.needs_a_human(state):
        ports.tracker.label(state["id"], NEEDS_HUMAN)
    ports.tracker.comment(state["id"], comment(state, cfg, ports))
    return {}


def comment(state: TaskState, cfg: Configuration, ports: Ports) -> str:
    reason = state.get("escalated")
    lines = [f"{ESCALATED}{reason}"]
    questions = state.get("questions_for_human", [])
    number = ports.inbox.issue_of(state["id"])
    cls = state.get("escalation_class")
    if cls is not None:
        usd = rules.suggested_budget(state, cfg.max_budget_usd) if cls == "cap" else None
        lines.extend(_prompt_block(state, cls, number, questions, usd))
    if questions and cls != "questions":
        lines.append("\nQuestions for you:\n")
        lines.extend(f"{n}. {q}" for n, q in enumerate(questions, 1))
    resume = ""
    if number is not None:
        resume = f"agentic resume --issue {number}"
    if rules.stopped_by_cap(state):
        lines.append("\nSpent by phase:\n")
        lines.extend(
            f"- {phase}: {usd:.2f} USD" for phase, usd in rules.spent_by_phase(state).items()
        )
        budget = rules.suggested_budget(state, cfg.max_budget_usd)
        resume = f"{resume} --task-budget {budget}" if resume else ""
    lines.append(f"\nTask document: `{document.relative_path(state['id'])}`")
    if resume:
        lines.append(
            f"\nTo resume, answer in comments if there is anything to decide, then run `{resume}`."
        )
    return "\n".join(lines)


def _code(command: str) -> str:
    """`command` as inline code, left bare when it carries a backtick of its own."""
    return command if "`" in command else f"`{command}`"


def _prompt_block(
    state: TaskState,
    cls: EscalationClass,
    number: int | None,
    questions: list[str],
    usd: float | None,
) -> list[str]:
    """The one-word block (GH-593): a question, the recommended answer and the options, from
    `escalation_prompts` and closed by the escalation's class and cause. A `returns` escalation of
    a restarted attempt asks what to change in the brief. For `questions`, the role's questions are
    the question, one per line, and answering them in free text is the recommended option.
    Presentation only: nothing here decides what happens next."""
    if cls == "returns" and any(state.get("restarts", {}).values()):
        question, recommended, options = escalation_prompts.restarted_returns_prompt()
    else:
        question, recommended, options = escalation_prompts.prompt_for(
            cls, state.get("escalation_cause")
        )
    fill = {
        "issue": "<issue>" if number is None else str(number),
        "usd": "<usd>" if usd is None else usd,
    }
    block = [""]
    if cls == "questions" and questions:
        block.append("Question:")
        block.extend(f"{n}. {q}" for n, q in enumerate(questions, 1))
    else:
        block.append(f"Question: {question}")
    block.append(f"Recommended: {_code(recommended.format(**fill))}")
    block.append("Options:")
    block.extend(f"- {_code(option.format(**fill))}" for option in options)
    return block
