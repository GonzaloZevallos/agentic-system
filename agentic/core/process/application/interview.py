"""An interview between two roles (ADR-0014): the interviewer asks a round, the
interviewee answers, until the interviewer closes or the rounds run out. Each side is
one session, resumed round after round, so the author of the deliverable that follows
has the whole conversation in context.

What the interviewee cannot decide goes to the human: the interview still runs to its
end, so the human answers everything in one go, and then the task escalates with the
list instead of a deliverable written over guesses. Whatever happens, the rounds that
did run reach the document: it is the raw record.

An interview may have a consultant (glossary: Consulta): what the interviewee asks it
in a round is answered before the next one, in the same run. The answers join the
transcript, the interviewer sees them, and they become clarifications of the
consultant's own section; the knowledge they change is distilled again (ADR-0006).
Only the consultant leaves questions for the human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import Interview, Role
from ..domain.task import WAITING_ON_HUMAN, TaskState, apply
from .ports import Ports
from .run_role import Output, context_for, preamble, run_role

CLOSE = (
    "The rounds are over. Do not ask anything else: close the interview with `done` and "
    "a short summary of what was decided and what stays open."
)


@dataclass
class Transcript:
    rounds: list[str] = field(default_factory=list)
    sessions: dict[str, str] = field(default_factory=dict)  # role slug -> session id
    questions_for_human: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.rounds)


async def interview(
    state: TaskState,
    spec: Interview,
    cfg: Configuration,
    ports: Ports,
    *,
    cwd: Path | None = None,
    **placeholders: object,
) -> tuple[Transcript | None, TaskState]:
    """Run the interview in `cwd` and build the state patch: the transcript as one
    section, a run per turn, the cost. `None` with an escalation patch when a side
    failed or when the interviewee left questions for the human."""
    transcript = Transcript()
    patch: TaskState = {}
    max_rounds = cfg.interview_max_rounds

    async def turn(role: Role, prompt: str | None, round_: int) -> tuple[Output | None, TaskState]:
        # Each turn sees the runs before it, so the record numbers them in order; the
        # transcript section itself is written once, at the end.
        output, turn_patch = await run_role(
            apply(state, patch),
            role,
            cfg,
            ports,
            section=spec.section,
            cwd=cwd,
            resume=transcript.sessions.get(role.slug),
            prompt=prompt,
            round=round_,
            max_rounds=max_rounds,
            **placeholders,
        )
        turn_patch = dict(turn_patch)
        turn_patch.pop("sections", None)
        if output is not None:
            transcript.sessions[role.slug] = output.session_id
        return output, apply(patch, turn_patch)

    async def consult(
        consultant: Role, questions: list[str], round_: int
    ) -> tuple[Output | None, TaskState]:
        # The consultant resumes the session in which it wrote its section, if any. Its
        # answers deliver that section again with the clarifications; the previous text
        # stays as history, like on a return.
        before = apply(state, patch)
        resume = rules.last_session(before, consultant.name, consultant.name)
        prompt = _consultation(before, consultant, spec.interviewee, ports, questions, round_, cwd)
        prompt = prompt if resume else _with_context(before, consultant, ports, cwd, prompt)
        output, turn_patch = await run_role(
            before,
            consultant,
            cfg,
            ports,
            check=ports.knowledge.clarification_check(),
            section=consultant.name,
            cwd=cwd,
            resume=resume,
            prompt=prompt,
            round=round_,
            max_rounds=max_rounds,
            **placeholders,
        )
        turn_patch = dict(turn_patch)
        turn_patch.pop("sections", None)
        if output is not None:
            clarified = _clarified(before, consultant.name, round_, output.text)
            turn_patch["sections"] = rules.sections_for(before, consultant.name, clarified)
        return output, apply(patch, turn_patch)

    # Whoever owns the decisions leaves the questions for the human: the consultant when
    # the interview has one, the interviewee otherwise.
    decider = spec.consultant or spec.interviewee
    reply: str | None = None  # what the interviewer reads next: answers, consultation
    consulted: str | None = None  # what the interviewee reads next, before the questions
    closed = False
    for round_ in range(1, max_rounds + 1):
        asked, patch = await turn(spec.interviewer, reply, round_)
        if asked is None:
            return None, _record(state, spec, transcript, patch)
        if asked.data["done"]:
            transcript.rounds.append(f"### Closing\n\n{asked.text}")
            closed = True
            break
        transcript.rounds.append(f"### Round {round_}: questions\n\n{asked.text}")

        prompt = _questions(state, spec, ports, asked, round_, cwd, consulted)
        answers, patch = await turn(spec.interviewee, prompt, round_)
        if answers is None:
            return None, _record(state, spec, transcript, patch)
        transcript.rounds.append(f"### Round {round_}: answers\n\n{answers.text}")
        reply, consulted = answers.text, None
        if spec.consultant is None:
            transcript.questions_for_human.extend(answers.data["questions_for_human"])
            continue

        to_pm = answers.data["questions_for_pm"]
        if not to_pm:
            continue
        answered, patch = await consult(spec.consultant, to_pm, round_)
        if answered is None:
            return None, _record(state, spec, transcript, patch)
        transcript.rounds.append(f"### Round {round_}: {decider.name} answers\n\n{answered.text}")
        transcript.questions_for_human.extend(answered.data["questions_for_human"])
        consulted = f"## {decider.name} answered (round {round_})\n\n{answered.text}"
        reply = f"{answers.text}\n\n{consulted}"
        if answered.data["definitions"] or answered.data["glossary"]:
            # Distilled again (ADR-0006); the caller commits the checkout after the interview.
            ports.knowledge.distill(answered.data, cwd)

    if not closed:
        # The cap hit with questions on the table: the interviewer closes with what it has.
        asked, patch = await turn(spec.interviewer, f"{reply}\n\n{CLOSE}", max_rounds)
        if asked is None:
            return None, _record(state, spec, transcript, patch)
        transcript.rounds.append(f"### Closing\n\n{asked.text}")

    patch = _record(state, spec, transcript, patch)
    if transcript.questions_for_human:
        patch["questions_for_human"] = transcript.questions_for_human
        patch["escalated"] = (
            f"{decider.name} could not decide {len(transcript.questions_for_human)} "
            "question(s) from the request and the product knowledge; see the questions"
        )
        patch["status"] = WAITING_ON_HUMAN
        return None, patch
    return transcript, patch


def _clarified(state: TaskState, section: str, round_: int, answers: str) -> str:
    current = state.get("sections", {}).get(section, "")
    return f"{current}\n\n### Clarifications (round {round_})\n\n{answers}".strip()


def _record(
    state: TaskState, spec: Interview, transcript: Transcript, patch: TaskState
) -> TaskState:
    """The transcript so far as the interview's section, if any round ran."""
    if transcript.rounds:
        patch = apply(patch, {"sections": rules.sections_for(state, spec.section, transcript.text)})
    return patch


def _questions(
    state: TaskState,
    spec: Interview,
    ports: Ports,
    asked: Output,
    round_: int,
    cwd: Path | None,
    consulted: str | None = None,
) -> str:
    """The interviewee's prompt: the first round carries its context before the
    questions; the session holds it from then on, so later rounds are the questions,
    after what the consultant answered to the previous round, if anything."""
    questions = f"## Questions (round {round_})\n\n{asked.text}"
    if consulted:
        questions = f"{consulted}\n\n{questions}"
    if round_ > 1:
        return questions
    standards, knowledge = preamble(spec.interviewee, ports, cwd)
    return context_for(state, spec.interviewee, knowledge, standards) + "\n\n" + questions


def _consultation(
    state: TaskState,
    consultant: Role,
    asker: Role,
    ports: Ports,
    questions: list[str],
    round_: int,
    cwd: Path | None,
) -> str:
    """The consultant's prompt: the questions, numbered."""
    listed = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
    return f"## Questions from {asker.name} (round {round_})\n\n{listed}"


def _with_context(state: TaskState, role: Role, ports: Ports, cwd: Path | None, text: str) -> str:
    """`text` after the role's context, for a role with no session to resume."""
    standards, knowledge = preamble(role, ports, cwd)
    return context_for(state, role, knowledge, standards) + "\n\n" + text
