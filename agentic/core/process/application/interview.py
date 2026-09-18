"""An interview between two roles (ADR-0014): the interviewer asks a round, the
interviewee answers, until the interviewer closes or the rounds run out. Each side is
one session, resumed round after round, so the author of the deliverable that follows
has the whole conversation in context.

What the interviewee cannot decide goes to the human: the interview still runs to its
end, so the human answers everything in one go, and then the task escalates with the
list instead of a deliverable written over guesses. A later round may settle a question
already on the list (the interviewer brings the knowledge that answers it): the decider
withdraws it by number, and only what is still open escalates. Whatever happens, the
rounds that did run reach the document: it is the raw record.

An interview may have a consultant (glossary: Consulta): what the interviewee asks it
in a round is answered before the next one, in the same run. The answers join the
transcript, the interviewer sees them, and they become clarifications of the
consultant's own section; the knowledge they change is consolidated again (ADR-0006).
Only the consultant leaves questions for the human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.document import HUMAN
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
    # What the interviewer proposes for the deliverable that follows, when its schema
    # has such a field: the solution interview's invariants (issue #42). The author
    # decides on each one; this only carries them across.
    invariants: list[str] = field(default_factory=list)
    # Question-answer cycles that reached an interviewee's answer, and consultations
    # that ran (Consulta): what `agentic.interview.rounds` and `.questions{to=pm}` read
    # (issue #28). Not the closing round, which asks nobody anything.
    completed_rounds: int = 0
    consultations: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(self.rounds)

    def ask_human(self, questions: list[str]) -> None:
        """Keep a question once. Nobody answers the human during the run, so the decider
        may list an open question again in a later round (issue #4); the prompt asks for
        new ones only and this catches the literal repeats. A rewording still gets through."""
        seen = {_key(q) for q in self.questions_for_human}
        for question in questions:
            if _key(question) not in seen:
                seen.add(_key(question))
                self.questions_for_human.append(question)

    def propose(self, invariants: list[str]) -> None:
        """Keep an invariant once, in the order it was proposed. A round may repeat what
        an earlier one already said; the author should see each rule once."""
        seen = {_key(line) for line in self.invariants}
        for line in invariants:
            if _key(line) not in seen:
                seen.add(_key(line))
                self.invariants.append(line)

    def withdraw(self, numbers: list[int]) -> None:
        """Take back the questions the decider settled this round, by their number in
        the list it read (issue #12). Runs before this round's new questions join, so
        the numbers are the ones the prompt showed; one outside the list withdraws
        nothing, which beats dropping the wrong question."""
        gone = set(numbers)
        self.questions_for_human = [
            q for n, q in enumerate(self.questions_for_human, 1) if n not in gone
        ]

    @property
    def with_human(self) -> str:
        """What is already with the human, numbered, for the roles' next prompt: the
        one place they can see it, since a consultation shows the Tech Lead only PM's
        prose. Numbered as the decider withdraws it. Empty when nothing is."""
        if not self.questions_for_human:
            return ""
        listed = "\n".join(f"{n}. {q}" for n, q in enumerate(self.questions_for_human, 1))
        return f"## With the human\n\n{listed}"

    def before(self, text: str) -> str:
        """`text` after what is with the human, when anything is."""
        return f"{self.with_human}\n\n{text}" if self.with_human else text


def _key(question: str) -> str:
    return " ".join(question.split()).casefold()


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

    consulted_once = False

    async def consult(
        consultant: Role, questions: list[str], round_: int
    ) -> tuple[Output | None, TaskState]:
        # The consultant resumes the session in which it wrote its section, if any. Its
        # answers deliver that section again with the clarifications; the previous text
        # stays as history, like on a return.
        nonlocal consulted_once
        before = apply(state, patch)
        resume = rules.last_session(before, consultant.name, consultant.name)
        prompt = _consultation(
            before, consultant, spec.interviewee, ports, questions, round_, cwd, transcript
        )
        if not resume:
            prompt = _with_context(before, consultant, ports, cwd, prompt)
        elif not consulted_once and (human := before.get("sections", {}).get(HUMAN)):
            # A session from before the escalation never read the human's decisions;
            # the consultation session keeps them from here on.
            prompt = f"## {HUMAN}\n\n{human}\n\n{prompt}"
        consulted_once = True
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
        asked, patch = await turn(spec.interviewer, _interviewer_prompt(transcript, reply), round_)
        if asked is None:
            return None, _record(state, spec, transcript, patch)
        transcript.propose(asked.data.get("invariants", []))
        if asked.data["done"]:
            transcript.rounds.append(f"### Closing\n\n{asked.text}")
            closed = True
            break
        transcript.rounds.append(f"### Round {round_}: questions\n\n{asked.text}")

        prompt = _questions(state, spec, ports, asked, round_, cwd, consulted, transcript)
        answers, patch = await turn(spec.interviewee, prompt, round_)
        if answers is None:
            return None, _record(state, spec, transcript, patch)
        transcript.rounds.append(f"### Round {round_}: answers\n\n{answers.text}")
        transcript.completed_rounds += 1
        reply, consulted = answers.text, None
        if spec.consultant is None:
            transcript.withdraw(answers.data["withdrawn"])
            transcript.ask_human(answers.data["questions_for_human"])
            continue

        to_pm = answers.data["questions_for_pm"]
        if not to_pm:
            continue
        transcript.consultations += 1
        answered, patch = await consult(spec.consultant, to_pm, round_)
        if answered is None:
            return None, _record(state, spec, transcript, patch)
        transcript.rounds.append(f"### Round {round_}: {decider.name} answers\n\n{answered.text}")
        transcript.withdraw(answered.data["withdrawn"])
        transcript.ask_human(answered.data["questions_for_human"])
        consulted = f"## {decider.name} answered (round {round_})\n\n{answered.text}"
        reply = f"{answers.text}\n\n{consulted}"
        if answered.data["definitions"] or answered.data["glossary"]:
            # Consolidated again (ADR-0006); the caller commits the checkout after the interview.
            ports.knowledge.consolidate(answered.data, cwd)

    if not closed:
        # The cap hit with questions on the table: the interviewer closes with what it has.
        asked, patch = await turn(
            spec.interviewer, _interviewer_prompt(transcript, f"{reply}\n\n{CLOSE}"), max_rounds
        )
        if asked is None:
            return None, _record(state, spec, transcript, patch)
        transcript.propose(asked.data.get("invariants", []))
        transcript.rounds.append(f"### Closing\n\n{asked.text}")

    patch = _record(state, spec, transcript, patch)
    if transcript.questions_for_human:
        patch |= rules.escalation(
            "questions",
            f"{decider.name} could not decide {len(transcript.questions_for_human)} "
            "question(s) from the request and the product knowledge; see the questions",
        )
        patch["status"] = WAITING_ON_HUMAN
        return None, patch
    return transcript, patch


def _interviewer_prompt(transcript: Transcript, reply: str | None) -> str | None:
    """The interviewer's prompt after the first round: what is with the human, so it
    can bring the knowledge that closes a question (issue #12), then the answers.
    `None` on the first round: the context goes instead."""
    return None if reply is None else transcript.before(reply)


def _clarified(state: TaskState, section: str, round_: int, answers: str) -> str:
    current = state.get("sections", {}).get(section, "")
    return f"{current}\n\n### Clarifications (round {round_})\n\n{answers}".strip()


def _record(
    state: TaskState, spec: Interview, transcript: Transcript, patch: TaskState
) -> TaskState:
    """The transcript so far as the interview's section, if any round ran, and the
    questions already with the human, so a side failing later does not lose them. The
    counts a panel would show (issue #28), measurement-only (`InterviewStats`), on
    every return path: an interview that ran at all leaves them, even at zero."""
    if transcript.rounds:
        patch = apply(patch, {"sections": rules.sections_for(state, spec.section, transcript.text)})
    if transcript.questions_for_human:
        patch["questions_for_human"] = transcript.questions_for_human
    patch["interview_stats"] = {
        "interview": spec.stage,
        "rounds": transcript.completed_rounds,
        "questions_to_pm": transcript.consultations,
        "questions_to_human": len(transcript.questions_for_human),
        "invariants_proposed": len(transcript.invariants),
    }
    return patch


def _questions(
    state: TaskState,
    spec: Interview,
    ports: Ports,
    asked: Output,
    round_: int,
    cwd: Path | None,
    consulted: str | None = None,
    transcript: Transcript | None = None,
) -> str:
    """The interviewee's prompt: the first round carries its context before the
    questions; the session holds it from then on, so later rounds are the questions,
    after what the consultant answered to the previous round, if anything, and what is
    already with the human, so it is not raised again."""
    questions = f"## Questions (round {round_})\n\n{asked.text}"
    if transcript is not None:
        questions = transcript.before(questions)
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
    transcript: Transcript,
) -> str:
    """The consultant's prompt: the questions, numbered, after what is already with
    the human."""
    listed = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
    return transcript.before(f"## Questions from {asker.name} (round {round_})\n\n{listed}")


def _with_context(state: TaskState, role: Role, ports: Ports, cwd: Path | None, text: str) -> str:
    """`text` after the role's context, for a role with no session to resume."""
    standards, knowledge = preamble(role, ports, cwd)
    return context_for(state, role, knowledge, standards) + "\n\n" + text
