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
from .run_role import Output, run_role, with_context

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
    failed or when the interviewee left questions for the human.

    An escalated interview is retaken, not repeated (ADR-0014 addendum): `state["runs"]`
    already says how many rounds ran and on which sessions (`rules.interview_progress`),
    so a retake seeds the transcript from the section already on record, resumes each
    seat's session, and runs only the rounds a retake still has, numbered on from where
    the last one stopped. `max_rounds` past this point is the total the roles are told
    about — rounds already run plus what a retake still has — never the ordinary cap
    alone, so a round a retake runs never reads as past the limit it was just given."""
    progress = rules.interview_progress(state, spec, cfg.interview_max_rounds)
    transcript = Transcript(sessions=dict(progress.sessions))
    if existing := state.get("sections", {}).get(spec.section):
        transcript.rounds = [existing]
    patch: TaskState = {}
    max_rounds = progress.rounds_done + progress.rounds_left
    start_round = progress.rounds_done + 1
    retaking = progress.rounds_done > 0
    retake_delta = (
        rules.interview_retake_delta(
            state, spec, state.get("branch", ""), ports.workspace.head(cwd or cfg.product_dir)
        )
        if retaking
        else None
    )

    async def turn(role: Role, delta: str | None, round_: int) -> tuple[Output | None, TaskState]:
        # Each turn sees the runs before it, so the record numbers them in order; the
        # transcript section itself is written once, at the end. Whether this seat gets
        # the full context or the shorter `delta` its session already covers for is
        # never `round_ == 1`: it is whether `transcript.sessions` holds a session for
        # it, the same gate `consult` already used. A retake's session gets a
        # `cold_prompt` alongside it (never an ordinary turn's, ADR-0004, sixth
        # addendum): only a retake's `state["sections"]` already holds this interview's
        # transcript for `with_context` to rebuild if the session turns out stale; an
        # ordinary turn's transcript is still only in `transcript`, not yet a section.
        current = apply(state, patch)
        resume = transcript.sessions.get(role.slug)
        cold_prompt = None
        if not resume:
            prompt = with_context(current, role, ports, cwd, delta) if delta is not None else None
        elif retaking:
            prompt = delta if delta is not None else ""
            cold_prompt = with_context(current, role, ports, cwd, prompt)
        else:
            prompt = delta
        output, turn_patch = await run_role(
            current,
            role,
            cfg,
            ports,
            section=spec.section,
            cwd=cwd,
            resume=resume,
            prompt=prompt,
            cold_prompt=cold_prompt,
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
        cold_prompt = None
        if not resume:
            prompt = with_context(before, consultant, ports, cwd, prompt)
        else:
            # A session on record may turn out stale (`run_with_retry`'s fallback):
            # the caller's own cold prompt is its own text, in full context.
            cold_prompt = with_context(before, consultant, ports, cwd, prompt)
            if not consulted_once and (human := before.get("sections", {}).get(HUMAN)):
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
            cold_prompt=cold_prompt,
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
    for round_ in range(start_round, max_rounds + 1):
        first_new_round = retaking and round_ == start_round
        ask_delta = retake_delta if first_new_round else _interviewer_prompt(transcript, reply)
        asked, patch = await turn(spec.interviewer, ask_delta, round_)
        if asked is None:
            return None, _record(state, spec, transcript, patch)
        transcript.propose(asked.data.get("invariants", []))
        if asked.data["done"]:
            transcript.rounds.append(f"### Closing\n\n{asked.text}")
            closed = True
            break
        transcript.rounds.append(f"### Round {round_}: questions\n\n{asked.text}")

        prompt = _questions(asked, round_, consulted, transcript)
        if first_new_round:
            prompt = f"{retake_delta}\n\n{prompt}"
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
    every return path: an interview that ran at all leaves them, even at zero.

    Written directly, never through `rules.sections_for`: a retake's `transcript.text`
    already opens with the section it is replacing (`interview`'s own seeding), so
    superseding it there would duplicate that same text into a `(vN)` copy, a verbatim
    prefix of what the new write already holds and nothing a return actually needs. An
    ordinary interview's own first write behaves the same, harmlessly: there is nothing
    to supersede yet."""
    if transcript.rounds:
        patch = apply(patch, {"sections": {spec.section: transcript.text}})
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


def _questions(asked: Output, round_: int, consulted: str | None, transcript: Transcript) -> str:
    """The interviewee's message: the questions, after what is already with the human
    so it is not raised again, and after what the consultant answered to the previous
    round, if anything. Whether this reaches the interviewee alone or wrapped in the
    full context (an interview's first round, a retake's first round on a seat with no
    session) is `turn`'s call, structural on whether its session already holds that
    context, never on `round_`."""
    questions = transcript.before(f"## Questions (round {round_})\n\n{asked.text}")
    return f"{consulted}\n\n{questions}" if consulted else questions


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
