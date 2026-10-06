"""The interview between two roles (ADR-0014), against in-memory ports: rounds,
sessions, the cap, the closing, and what the interviewee cannot decide."""

import pytest

from agentic.core.process.application.define import define
from agentic.core.process.application.interview import interview
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain.roles import (
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    INTERVIEW_SOLUTION_PLATFORM,
)
from agentic.shared.configuration import Configuration
from tests.fakes import (
    ScriptedRunner,
    answers,
    consulted,
    defined,
    memory_ports,
    questions,
    tech_answers,
)

pytestmark = pytest.mark.anyio

TASK = {"id": "T-1", "title": "Export report", "description": "Users want CSV.", "type": "feature"}
ASK, ANSWER = "product_analyst_interviewer", "pm_interviewee"


@pytest.fixture
def spy_run_role(monkeypatch):
    """`run_role`'s `resume`/`prompt`/`cold_prompt` are `run_with_retry`'s own business
    from here on: a stale-session fallback never reaches `ScriptedRunner`, so the only
    seam that sees what `interview.turn`/`consult` actually built is the call to
    `run_role` itself. Records every call, by the role's slug, and still runs it for
    real."""
    import agentic.core.process.application.interview as interview_module

    recorded: list[dict] = []
    real_run_role = interview_module.run_role

    async def wrapper(state, role, cfg_, ports_, check=None, **kwargs):
        recorded.append(
            {
                "slug": role.slug,
                "resume": kwargs.get("resume"),
                "prompt": kwargs.get("prompt"),
                "cold_prompt": kwargs.get("cold_prompt"),
                "round": kwargs.get("round"),
                "max_rounds": kwargs.get("max_rounds"),
            }
        )
        return await real_run_role(state, role, cfg_, ports_, check, **kwargs)

    monkeypatch.setattr(interview_module, "run_role", wrapper)
    return recorded


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path, interview_max_rounds=4)


async def test_rounds_alternate_and_each_side_resumes_its_own_session(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [
                questions(1, session="q1"),
                questions(2, session="q1"),
                questions(done=True, session="q1"),
            ],
            ANSWER: [answers(1, session="a1"), answers(2, session="a1")],
        }
    )
    ports = memory_ports(runner)
    ports.knowledge.text = "## Product knowledge\n\nOld."
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, ports)

    assert transcript is not None
    assert runner.slugs == [ASK, ANSWER, ASK, ANSWER, ASK]
    calls = runner.calls
    # The interviewer opens with its context, then continues its session on the answers.
    assert calls[0]["resume"] is None and calls[0]["prompt"].startswith("## Product knowledge")
    assert calls[2]["resume"] == "q1" and calls[2]["prompt"] == "**Q1**: accepted"
    # The interviewee gets its context plus the questions once, then only the questions.
    assert calls[1]["resume"] is None
    assert calls[1]["prompt"].startswith("## Product knowledge")
    assert calls[1]["prompt"].endswith(
        "## Questions (round 1)\n\n❓ **Q1** - **Scope**: which?\n\n➡️ a"
    )
    assert calls[3]["resume"] == "a1" and calls[3]["prompt"].startswith("## Questions (round 2)")
    assert "This is round 2" in calls[2]["system_prompt"]
    assert "at most 4 rounds" in calls[0]["system_prompt"]

    section = patch["sections"]["Interview: Product Analyst→PM"]
    assert section.startswith("### Round 1: questions\n\n❓ **Q1**")
    assert "### Round 1: answers\n\n**Q1**: accepted" in section
    assert section.endswith("### Closing\n\nDecided.")
    assert transcript.sessions == {ASK: "q1", ANSWER: "a1"}
    assert [r["role"] for r in patch["runs"]] == ["Product Analyst", "PM"] * 2 + ["Product Analyst"]
    assert all(r["section"] == "Interview: Product Analyst→PM" for r in patch["runs"])
    assert patch["cost_usd"] == pytest.approx(0.05 * 3 + 0.1 * 2)
    assert "escalated" not in patch


async def test_the_argument_sequence_of_an_ordinary_interview_is_pinned(cfg, spy_run_role):
    """A retake (ADR-0014 addendum) resumes a session with a `cold_prompt` alongside it,
    because the interview's own section is already written by the time it happens. An
    ordinary interview's section is not written until the interview ends, so none of its
    turns may carry one, even once a side resumes its own session mid-run (round 2
    onward): a `cold_prompt` there would fall back to a context missing the very rounds
    that ran before it. This test locks the whole shape — `resume`, `prompt`,
    `cold_prompt`, `round`, `max_rounds` — of every turn `run_role` sees on the ordinary
    path; it must not move."""
    recorded = spy_run_role
    runner = ScriptedRunner(
        **{
            ASK: [
                questions(1, session="q1"),
                questions(2, session="q1"),
                questions(done=True, session="q1"),
            ],
            ANSWER: [answers(1, session="a1"), answers(2, session="a1")],
        }
    )
    transcript, _ = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is not None
    rounds = [
        (r["slug"], r["resume"], r["cold_prompt"], r["round"], r["max_rounds"]) for r in recorded
    ]
    assert rounds == [
        (ASK, None, None, 1, 4),
        (ANSWER, None, None, 1, 4),
        (ASK, "q1", None, 2, 4),
        (ANSWER, "a1", None, 2, 4),
        (ASK, "q1", None, 3, 4),
    ]
    assert recorded[1]["prompt"].endswith(
        "## Questions (round 1)\n\n❓ **Q1** - **Scope**: which?\n\n➡️ a"
    )
    assert recorded[2]["prompt"] == "**Q1**: accepted"
    assert recorded[3]["prompt"].startswith("## Questions (round 2)")
    assert recorded[4]["prompt"] == "**Q2**: accepted"


async def test_the_cap_makes_the_interviewer_close_with_what_it_has(cfg):
    cfg = Configuration(product_dir=cfg.product_dir, interview_max_rounds=2)
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1), answers(2)],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is not None and runner.slugs == [ASK, ANSWER] * 2 + [ASK]
    closing = runner.calls[-1]["prompt"]
    assert closing.startswith("**Q2**: accepted") and "The rounds are over" in closing
    section = patch["sections"]["Interview: Product Analyst→PM"]
    assert "### Round 2: answers" in section and section.endswith("### Closing\n\nDecided.")
    # The cap-forced closing round is not itself a completed round (issue #28): the two
    # that ran before it are.
    assert patch["interview_stats"] == {
        "interview": "definition",
        "rounds": 2,
        "questions_to_pm": 0,
        "questions_to_human": 0,
        "invariants_proposed": 0,
    }


async def test_questions_for_the_human_escalate_after_the_interview_ends(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1, for_human=["Which currency?"]), answers(2)],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None
    assert runner.slugs == [ASK, ANSWER, ASK, ANSWER, ASK]  # it ran to the end first
    assert patch["questions_for_human"] == ["Which currency?"]
    assert patch["status"] == "waiting_on_human" and "PM could not decide 1" in patch["escalated"]
    assert patch["escalation_class"] == "questions"
    assert "### Closing" in patch["sections"]["Interview: Product Analyst→PM"]


async def test_a_question_repeated_across_rounds_reaches_the_human_once(cfg):
    # Nobody answers the human's questions during the run, so PM may list one again in
    # the next round, reworded only in spacing or case (issue #4).
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [
                answers(1, for_human=["Which currency?", "Who pays the fee?"]),
                answers(2, for_human=["  which currency?  ", "Which currency?"]),
            ],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None
    assert patch["questions_for_human"] == ["Which currency?", "Who pays the fee?"]
    assert "PM could not decide 2" in patch["escalated"]
    # PM reads what is already with the human before the next round's questions.
    assert runner.calls[3]["prompt"].startswith(
        "## With the human\n\n1. Which currency?\n2. Who pays the fee?\n\n## Questions (round 2)"
    )


async def test_a_question_with_the_human_is_withdrawn_when_pm_answers_it_later(cfg):
    # Round 2 shows PM that the knowledge settles the first question (issue #12): PM
    # withdraws it by its number in the list it received, and leaves a new one.
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(3), questions(done=True)],
            ANSWER: [
                answers(1, for_human=["Which currency?", "Who pays the fee?"]),
                answers(2, withdrawn=[1], for_human=["Is there a fee cap?"]),
                answers(3),
            ],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None
    assert patch["questions_for_human"] == ["Who pays the fee?", "Is there a fee cap?"]
    assert "PM could not decide 2" in patch["escalated"]
    # The interviewer reads the list too, before the answers, so it can bring the
    # knowledge that closes a question; the list it reads is the one after the round.
    assert runner.calls[2]["prompt"] == (
        "## With the human\n\n1. Which currency?\n2. Who pays the fee?\n\n**Q1**: accepted"
    )
    assert runner.calls[4]["prompt"] == (
        "## With the human\n\n1. Who pays the fee?\n2. Is there a fee cap?\n\n**Q2**: accepted"
    )
    assert runner.calls[5]["prompt"].startswith(
        "## With the human\n\n1. Who pays the fee?\n2. Is there a fee cap?\n\n"
        "## Questions (round 3)"
    )


async def test_withdrawing_every_question_leaves_nothing_to_escalate(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1, for_human=["Which currency?"]), answers(2, withdrawn=[1])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is not None
    assert "escalated" not in patch and "questions_for_human" not in patch
    # Nothing with the human: the interviewer's closing prompt carries no list.
    assert runner.calls[4]["prompt"] == "**Q2**: accepted"


async def test_a_number_outside_the_list_withdraws_nothing(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1, for_human=["Which currency?"]), answers(2, withdrawn=[0, 2])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None
    assert patch["questions_for_human"] == ["Which currency?"]


async def test_questions_for_the_human_survive_a_side_failing_later(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2)],
            ANSWER: [
                answers(1, for_human=["Which currency?"]),
                AttemptFailed("a", 0.02),
                AttemptFailed("b", 0.02),
            ],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None and "PM failed 2 times" in patch["escalated"]
    assert patch["questions_for_human"] == ["Which currency?"]


async def test_a_side_that_fails_twice_escalates_with_the_transcript_so_far(cfg):
    runner = ScriptedRunner(
        **{ASK: [questions(1)], ANSWER: [AttemptFailed("a", 0.02), AttemptFailed("b", 0.02)]}
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is None
    assert patch["status"] == "waiting_on_human" and "PM failed 2 times" in patch["escalated"]
    assert len(patch["failures"]) == 2 and patch["cost_usd"] == pytest.approx(0.05 + 0.04)
    # The round that did run is on record.
    assert patch["sections"]["Interview: Product Analyst→PM"].startswith("### Round 1: questions")
    # The interviewee never answered, so no round completed (issue #28): the stats key
    # is still present, at zero, on every return path including this failure one.
    assert patch["interview_stats"] == {
        "interview": "definition",
        "rounds": 0,
        "questions_to_pm": 0,
        "questions_to_human": 0,
        "invariants_proposed": 0,
    }


async def test_define_interviews_then_writes_from_the_interviewers_session(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1, session="q1"), questions(done=True, session="q1")],
            ANSWER: [answers(1)],
        },
        product_analyst=[defined()],
    )
    ports = memory_ports(runner)
    patch = await define(TASK, cfg, ports)
    assert patch["status"] == "defined"
    assert runner.slugs == [ASK, ANSWER, ASK, "product_analyst"]
    author = runner.calls[-1]
    assert author["resume"] == "q1"  # the author has the interview in context
    assert "## Interview: Product Analyst→PM" in author["prompt"]
    assert set(patch["sections"]) == {"Interview: Product Analyst→PM", "Product Analyst"}
    assert [r["role"] for r in patch["runs"]] == [
        "Product Analyst",
        "PM",
        "Product Analyst",
    ] * 1 + ["Product Analyst"]
    assert patch["cost_usd"] == pytest.approx(0.05 * 2 + 0.1 + 0.1)
    saved = ports.documents.last
    assert saved["status"] == "defined" and len(saved["runs"]) == 4


async def test_define_on_a_return_skips_the_interview(cfg):
    runner = ScriptedRunner(product_analyst=[defined(2)])
    state = {
        **TASK,
        "status": "returned",
        "sections": {"Product Analyst": "v1", "PM": "fix"},
        "runs": [{"role": "Product Analyst", "section": "Product Analyst", "version": 1}],
    }
    patch = await define(state, cfg, memory_ports(runner))
    assert runner.slugs == ["product_analyst"] and runner.calls[0]["resume"] is None
    assert patch["sections"]["Product Analyst (v1)"] == "v1"
    # No interview ran: no counts for a panel to read (`agentic.interview.*`).
    assert "interview_stats" not in patch


async def test_interview_stats_count_rounds_and_questions_by_destination(cfg):
    """Measurement-only key (like `subtask_id`): `interview()`'s own patch carries the
    counts a panel needs (issue #28), never rendered into the document. Three rounds,
    one consultation, two questions left for the human (net of nothing withdrawn)."""
    runner = ScriptedRunner(
        **{
            TL_ASK: [
                questions(1, invariants=["Every X does Y"]),
                questions(2),
                questions(3),
                questions(done=True),
            ],
            TL_ANSWER: [
                tech_answers(1, for_pm=["Stored end date or last day?"]),
                tech_answers(2),
                tech_answers(3),
            ],
            CONSULT: [consulted(1, for_human=["Which currency?", "Who pays the fee?"])],
        }
    )
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, memory_ports(runner))
    assert transcript is None  # the two questions for the human still escalate
    assert patch["interview_stats"] == {
        "interview": "solution",
        "rounds": 3,
        "questions_to_pm": 1,
        "questions_to_human": 2,
        "invariants_proposed": 1,
    }


async def test_interview_stats_use_the_definition_label_and_count_nothing_proposed(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1, session="q1"), questions(done=True, session="q1")],
            ANSWER: [answers(1, session="a1")],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert transcript is not None
    assert patch["interview_stats"] == {
        "interview": "definition",
        "rounds": 1,
        "questions_to_pm": 0,
        "questions_to_human": 0,
        "invariants_proposed": 0,
    }


# A retake (ADR-0014 addendum): an interview that escalated resumes on the sessions
# `state["runs"]` already carries, appends to the section already on record, and gets
# only `INTERVIEW_RETAKE_ROUNDS` more rounds, never the ordinary cap again.

DEFINITION_SECTION = INTERVIEW_DEFINITION.section
OLD_TRANSCRIPT = "### Round 1: questions\n\n❓ **Q1**\n\n### Round 1: answers\n\n**Q1**: accepted"


def _closed_four_rounds(decision: str | None = "USD, always.") -> dict:
    """A definition interview closed at the ordinary cap (4 rounds: 3 questioned, the
    4th a closing with no interviewee turn), with the human's decision already recorded
    when `decision` is given — the "questions" escalation shape — or without one, the
    shape a role failure or the task cap leaves (point 4 of the request)."""
    runs = []
    for version in range(1, 4):
        runs.append({"role": "Product Analyst", "section": DEFINITION_SECTION, "session_id": "q4"})
        runs.append({"role": "PM", "section": DEFINITION_SECTION, "session_id": "a3"})
    runs.append({"role": "Product Analyst", "section": DEFINITION_SECTION, "session_id": "q4"})
    sections = {DEFINITION_SECTION: OLD_TRANSCRIPT}
    if decision:
        sections["Human"] = f"### Decision 1\n\n{decision}"
    return {**TASK, "branch": "agentic/T-1", "sections": sections, "runs": runs}


async def test_a_retake_resumes_the_sessions_the_first_interview_left(cfg, spy_run_role):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1, session="q4"), questions(done=True, session="q4")],
            ANSWER: [answers(1, session="a3")],
        }
    )
    transcript, patch = await interview(
        _closed_four_rounds(), INTERVIEW_DEFINITION, cfg, memory_ports(runner)
    )

    assert transcript is not None
    # Exactly two more rounds, resumed on the ids the first interview left.
    assert [c["slug"] for c in spy_run_role] == [ASK, ANSWER, ASK]
    assert [c["resume"] for c in spy_run_role] == ["q4", "a3", "q4"]
    # Strictly increasing, continuing from the 4 already run, never above the total
    # (rounds done plus the retake's own) rendered beside every one of them.
    assert [c["round"] for c in spy_run_role] == [5, 5, 6]
    assert [c["max_rounds"] for c in spy_run_role] == [6, 6, 6]
    # The interviewer reads the human's decision as its next message, not through the
    # context it never re-reads on a resumed session.
    ask = spy_run_role[0]
    assert ask["prompt"].startswith("Branch `agentic/T-1` is at `base`.")
    assert ask["prompt"].endswith("## Human\n\n### Decision 1\n\nUSD, always.")
    # A retake's session gets a `cold_prompt`: the section is already on record for
    # `with_context` to rebuild if it turns out stale.
    assert ask["cold_prompt"] is not None and ask["cold_prompt"].endswith(ask["prompt"])
    # The interviewee gets it before the next questions, not instead of them.
    answer = spy_run_role[1]
    assert answer["prompt"].startswith(ask["prompt"])
    assert "## Questions (round 5)" in answer["prompt"]
    assert answer["cold_prompt"] is not None
    # Every round of the retake, not only its first, still resumes with a `cold_prompt`.
    close = spy_run_role[2]
    assert close["cold_prompt"] is not None
    # The section is appended to, never shorter than what it replaced, and never
    # superseded into a `(vN)` copy (`_record` writes it directly).
    section = patch["sections"][DEFINITION_SECTION]
    assert section.startswith(OLD_TRANSCRIPT)
    assert len(section) > len(OLD_TRANSCRIPT)
    assert set(patch["sections"]) == {DEFINITION_SECTION}


async def test_a_retake_without_a_decision_still_resumes(cfg):
    # A role failure or the task cap also retakes (point 4): no `## Human` to carry, but
    # the sessions and the appended section behave the same.
    runner = ScriptedRunner(
        **{
            ASK: [questions(1, session="q4"), questions(done=True, session="q4")],
            ANSWER: [answers(1, session="a3")],
        }
    )
    transcript, patch = await interview(
        _closed_four_rounds(decision=None), INTERVIEW_DEFINITION, cfg, memory_ports(runner)
    )
    assert transcript is not None
    ask = runner.calls[0]
    assert "## Human" not in ask["prompt"]
    assert ask["prompt"].startswith("Branch `agentic/T-1` is at `base`.")


async def test_a_retake_seat_with_no_recorded_session_gets_full_context_and_no_cold_prompt(
    cfg, spy_run_role
):
    # The interviewee never delivered before the escalation (a role failure mid-answer):
    # its seat has no session, so it takes the cold path like an ordinary first round,
    # never a `cold_prompt` (there is no session that could turn out stale).
    state = _closed_four_rounds()
    state["runs"] = [r for r in state["runs"] if r["role"] != "PM"]
    ports = memory_ports(
        ScriptedRunner(
            **{
                ASK: [questions(1, session="q4"), questions(done=True, session="q4")],
                ANSWER: [answers(1, session="a3")],
            }
        )
    )
    ports.knowledge.text = "## Product knowledge\n\nOld."
    transcript, patch = await interview(state, INTERVIEW_DEFINITION, cfg, ports)
    assert transcript is not None
    answer = spy_run_role[1]
    assert answer["resume"] is None
    assert answer["cold_prompt"] is None
    assert answer["prompt"].startswith("## Product knowledge")


# The solution interview has a consultant (glossary: Consulta): the Tech Lead asks PM what
# the definition does not settle, PM answers in the same run, and only PM asks the human.

TL_ASK, TL_ANSWER, CONSULT = "tech_lead_ephemeral", "tech_lead_interviewee", "pm_consultant"
PLANNED = {
    **TASK,
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
    "runs": [{"role": "PM", "section": "PM", "version": 1, "session_id": "pm-1", "cost_usd": 0.2}],
}


async def test_a_consultation_is_answered_by_pm_before_the_next_round(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1, session="e1"), questions(2, session="e1"), questions(done=True)],
            TL_ANSWER: [tech_answers(1, for_pm=["Stored end date or last day?"]), tech_answers(2)],
            CONSULT: [
                consulted(1, definitions=[{"capability": "dashboard", "content": "## Jobs"}])
            ],
        }
    )
    ports = memory_ports(runner)
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, ports)

    assert transcript is not None
    assert runner.slugs == [TL_ASK, TL_ANSWER, CONSULT, TL_ASK, TL_ANSWER, TL_ASK]
    pm = runner.calls[2]
    # PM resumes its validation session and gets only the questions, numbered.
    assert pm["resume"] == "pm-1"
    assert (
        pm["prompt"] == "## Questions from Tech Lead (round 1)\n\n1. Stored end date or last day?"
    )
    # The interviewer reads the answers and the consultation together; the interviewee
    # reads the consultation before the next questions.
    assert runner.calls[3]["prompt"] == (
        "**Q1**: accepted\n\n## PM answered (round 1)\n\n**Q1**: the stored date"
    )
    assert runner.calls[4]["prompt"] == (
        "## PM answered (round 1)\n\n**Q1**: the stored date\n\n## Questions (round 2)\n\n"
        "❓ **Q2** - **Scope**: which?\n\n➡️ a"
    )
    section = patch["sections"]["Interview: Tech Lead efímero→Tech Lead"]
    assert "### Round 1: PM answers\n\n**Q1**: the stored date" in section
    # The answers clarify PM's section; the previous text stays as history.
    assert patch["sections"]["PM"] == (
        "### Consolidated definition\n\nSix numbers.\n\n### Clarifications (round 1)\n\n"
        "**Q1**: the stored date"
    )
    assert patch["sections"]["PM (v1)"] == "### Consolidated definition\n\nSix numbers."
    roles = [r["role"] for r in patch["runs"]]
    assert roles == ["Tech Lead efímero", "Tech Lead", "PM", "Tech Lead efímero", "Tech Lead"] + [
        "Tech Lead efímero"
    ]
    assert patch["runs"][2]["section"] == "PM"
    # What the answers change is consolidated again.
    assert ports.knowledge.definitions == {"dashboard": "## Jobs"}
    assert "escalated" not in patch


async def test_a_resumed_consultant_gets_the_human_decisions_its_session_never_saw(cfg):
    # Its validation session predates the escalation, so it would answer without them;
    # once told, the consultation session holds them and they are not repeated.
    state = {**PLANNED, "sections": {**PLANNED["sections"], "Human": "### Decision 1\n\nUSD."}}
    ask = [tech_answers(1, for_pm=["Currency?"]), tech_answers(2, for_pm=["Rounding?"])]
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(2), questions(done=True)],
            TL_ANSWER: ask,
            CONSULT: [consulted(1, session="pm-2"), consulted(2, session="pm-2")],
        }
    )
    await interview(state, INTERVIEW_SOLUTION, cfg, memory_ports(runner))
    first, second = runner.prompts(CONSULT)
    assert first.startswith("## Human\n\n### Decision 1\n\nUSD.\n\n## Questions from Tech Lead")
    assert "## Human" not in second


async def test_no_question_for_pm_means_no_consultation(cfg):
    runner = ScriptedRunner(
        **{TL_ASK: [questions(1), questions(done=True)], TL_ANSWER: [tech_answers(1)]}
    )
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, memory_ports(runner))
    assert transcript is not None and runner.slugs == [TL_ASK, TL_ANSWER, TL_ASK]
    assert "PM (v1)" not in patch["sections"]


async def test_only_pm_escalates_to_the_human_from_a_consultation(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(done=True)],
            TL_ANSWER: [tech_answers(1, for_pm=["Does a script count as live?"])],
            CONSULT: [consulted(1, for_human=["Where do the metrics go?"])],
        }
    )
    ports = memory_ports(runner)
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, ports)
    assert transcript is None and runner.slugs == [TL_ASK, TL_ANSWER, CONSULT, TL_ASK]
    assert patch["questions_for_human"] == ["Where do the metrics go?"]
    assert patch["status"] == "waiting_on_human" and "PM could not decide 1" in patch["escalated"]
    assert ports.knowledge.definitions == {}  # nothing changed, nothing consolidated


async def test_a_consultation_repeated_across_rounds_reaches_the_human_once(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(2), questions(done=True)],
            TL_ANSWER: [
                tech_answers(1, for_pm=["Where do the metrics go?"]),
                tech_answers(2, for_pm=["Where do the metrics go?"]),
            ],
            CONSULT: [
                consulted(1, for_human=["Where do the metrics go?"]),
                consulted(2, for_human=["where do the metrics go?"]),
            ],
        }
    )
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, memory_ports(runner))
    assert transcript is None
    assert patch["questions_for_human"] == ["Where do the metrics go?"]
    assert "PM could not decide 1" in patch["escalated"]
    # The Tech Lead and PM both read what is already with the human.
    with_human = "## With the human\n\n1. Where do the metrics go?"
    tech_lead_round_2, consult_round_2 = runner.calls[4]["prompt"], runner.calls[5]["prompt"]
    assert tech_lead_round_2.startswith("## PM answered (round 1)")
    assert f"\n\n{with_human}\n\n## Questions (round 2)" in tech_lead_round_2
    assert consult_round_2.startswith(f"{with_human}\n\n## Questions from Tech Lead (round 2)")


async def test_pm_withdraws_a_consulted_question_it_answers_later(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(2), questions(done=True)],
            TL_ANSWER: [
                tech_answers(1, for_pm=["Where do the metrics go?"]),
                tech_answers(2, for_pm=["Does a script count as live?"]),
            ],
            CONSULT: [
                consulted(1, for_human=["Where do the metrics go?"]),
                consulted(2, withdrawn=[1]),
            ],
        }
    )
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, memory_ports(runner))
    assert transcript is not None
    assert "escalated" not in patch and "questions_for_human" not in patch
    # The interviewer reads the list before the answers; PM reads it in the second
    # consultation, before withdrawing from it.
    with_human = "## With the human\n\n1. Where do the metrics go?"
    assert runner.calls[3]["prompt"].startswith(f"{with_human}\n\n**Q1**: accepted")
    assert runner.calls[5]["prompt"].startswith(with_human)


async def test_a_consultant_without_a_session_gets_its_context_first(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(done=True)],
            TL_ANSWER: [tech_answers(1, for_pm=["Which?"])],
            CONSULT: [consulted(1)],
        }
    )
    ports = memory_ports(runner)
    ports.knowledge.text = "## Product knowledge\n\nOld."
    state = {**PLANNED, "runs": []}
    await interview(state, INTERVIEW_SOLUTION, cfg, ports)
    pm = runner.calls[2]
    assert pm["resume"] is None and pm["prompt"].startswith("## Product knowledge")
    assert pm["prompt"].endswith("## Questions from Tech Lead (round 1)\n\n1. Which?")


async def test_a_clarification_that_consolidates_a_file_without_jobs_is_a_role_failure(cfg):
    cfg = Configuration(product_dir=cfg.product_dir, role_retries=0)
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1)],
            TL_ANSWER: [tech_answers(1, for_pm=["Which?"])],
            CONSULT: [
                consulted(1, definitions=[{"capability": "dashboard", "content": "no jobs"}])
            ],
        }
    )
    ports = memory_ports(runner)
    transcript, patch = await interview(PLANNED, INTERVIEW_SOLUTION, cfg, ports)
    assert transcript is None and patch["status"] == "waiting_on_human"
    assert "PM failed" in patch["escalated"] and "## Jobs" in patch["failures"][0]["error"]
    assert ports.knowledge.definitions == {}


# The platform path has no consultant (issue #46): the same two Tech Leads, and what the
# Tech Lead cannot settle goes to the human itself, the door `interview` already had for
# an interviewee with nobody to consult.

TL_ASK_P, TL_ANSWER_P = "tech_lead_ephemeral_platform", "tech_lead_interviewee_platform"
CLASSIFIED = {**TASK, "type": "platform_enhancement", "sections": {"PjM": "No behaviour change."}}


async def test_on_the_platform_path_the_tech_lead_asks_the_human_itself(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK_P: [questions(1, session="e1"), questions(done=True)],
            TL_ANSWER_P: [answers(1, for_human=["Should the old endpoint stay?"])],
        }
    )
    ports = memory_ports(runner)
    transcript, patch = await interview(CLASSIFIED, INTERVIEW_SOLUTION_PLATFORM, cfg, ports)

    assert transcript is None  # questions for the human: the interview ends in an escalation
    assert runner.slugs == [TL_ASK_P, TL_ANSWER_P, TL_ASK_P]  # nobody consulted in between
    assert patch["questions_for_human"] == ["Should the old endpoint stay?"]
    assert patch["escalated"].startswith("Tech Lead could not decide 1 question(s)")
    # PjM's reason is what they read upstream; no definition is claimed.
    assert "## PjM\n\nNo behaviour change." in runner.calls[1]["prompt"]
    assert "## PM" not in runner.calls[1]["prompt"]
    assert "## With the human\n\n1. Should the old endpoint stay?" in runner.calls[2]["prompt"]
    section = patch["sections"]["Interview: Tech Lead efímero→Tech Lead"]
    assert "### Round 1: answers" in section and "PM answers" not in section
    # The platform path has no consultant: `questions_to_pm` stays at zero even though
    # the human got a question, and the stage label is still "solution".
    assert patch["interview_stats"] == {
        "interview": "solution",
        "rounds": 1,
        "questions_to_pm": 0,
        "questions_to_human": 1,
        "invariants_proposed": 0,
    }


async def test_on_the_platform_path_a_settled_question_is_withdrawn_by_the_tech_lead(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK_P: [questions(1), questions(2), questions(done=True)],
            TL_ANSWER_P: [
                answers(1, for_human=["Keep the old endpoint?"]),
                answers(2, withdrawn=[1]),
            ],
        }
    )
    transcript, patch = await interview(
        CLASSIFIED, INTERVIEW_SOLUTION_PLATFORM, cfg, memory_ports(runner)
    )
    assert transcript is not None
    assert "escalated" not in patch and "questions_for_human" not in patch


# What the ephemeral proposed across the whole interview survives the pause (issue #42):
# a retake seeds its transcript from `state["proposed_invariants"]` instead of starting
# empty, and only counts what its own rounds add on top of that.

SOLUTION_SECTION = INTERVIEW_SOLUTION.section
OLD_SOLUTION_TRANSCRIPT = (
    "### Round 1: questions\n\n❓ **Q1**\n\n### Round 1: answers\n\n**Q1**: ok"
)


def _closed_solution(proposed_invariants=None):
    runs = []
    for _ in range(3):
        runs.append({"role": "Tech Lead efímero", "section": SOLUTION_SECTION, "session_id": "e4"})
        runs.append({"role": "Tech Lead", "section": SOLUTION_SECTION, "session_id": "t3"})
    runs.append({"role": "Tech Lead efímero", "section": SOLUTION_SECTION, "session_id": "e4"})
    state = {
        **PLANNED,
        "branch": "agentic/T-1",
        "sections": {**PLANNED["sections"], SOLUTION_SECTION: OLD_SOLUTION_TRANSCRIPT},
        "runs": [*PLANNED["runs"], *runs],
    }
    if proposed_invariants is not None:
        state["proposed_invariants"] = proposed_invariants
    return state


async def test_a_retake_seeds_invariants_and_counts_only_the_new_ones(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [
                questions(1, invariants=["New rule."], session="e4"),
                questions(done=True, session="e4"),
            ],
            TL_ANSWER: [tech_answers(1, session="t3")],
        }
    )
    state = _closed_solution(proposed_invariants=["Existing rule."])
    transcript, patch = await interview(state, INTERVIEW_SOLUTION, cfg, memory_ports(runner))

    assert transcript is not None
    # The whole accumulated list, seeded ones first, never started empty.
    assert transcript.invariants == ["Existing rule.", "New rule."]
    # `_record` carries the accumulated list forward, for a later retake to seed from.
    assert patch["proposed_invariants"] == ["Existing rule.", "New rule."]
    # Only this invocation's own round proposed anything new; the seeded one is not
    # counted again.
    assert patch["interview_stats"]["invariants_proposed"] == 1


# A request that carries `## Decided` runs its interviews in one round (ADR-0014,
# addendum of 2026-10-06): the closing turn is the one the cap already forces.


def _decided(state: dict) -> dict:
    return {**state, "decided": True}


async def test_a_decided_interviewer_that_closes_in_round_one_keeps_its_invariants(cfg):
    runner = ScriptedRunner(**{TL_ASK: [questions(done=True, invariants=["Every X does Y."])]})
    transcript, patch = await interview(
        _decided(PLANNED), INTERVIEW_SOLUTION, cfg, memory_ports(runner)
    )

    assert transcript is not None and transcript.invariants == ["Every X does Y."]
    assert runner.slugs == [TL_ASK]  # no answer turn
    assert patch["proposed_invariants"] == ["Every X does Y."]
    assert "escalated" not in patch


async def test_a_decided_interviewer_that_asks_gets_one_answer_then_the_forced_close(
    cfg, spy_run_role
):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(done=True, invariants=["Late rule."])],
            TL_ANSWER: [tech_answers(1)],
        }
    )
    transcript, patch = await interview(
        _decided(PLANNED), INTERVIEW_SOLUTION, cfg, memory_ports(runner)
    )

    assert transcript is not None
    assert runner.slugs == [TL_ASK, TL_ANSWER, TL_ASK]
    assert "The rounds are over" in runner.calls[-1]["prompt"]
    assert [c["max_rounds"] for c in spy_run_role] == [1, 1, 1]
    assert [c["round"] for c in spy_run_role] == [1, 1, 1]
    # The forced closing turn can still propose an invariant.
    assert patch["proposed_invariants"] == ["Late rule."]
    assert "escalated" not in patch


async def test_a_decided_interview_with_a_question_for_the_human_escalates_as_today(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(done=True)],
            TL_ANSWER: [tech_answers(1, for_pm=["Which currency?"])],
            CONSULT: [consulted(1, for_human=["Which currency, really?"])],
        }
    )
    transcript, patch = await interview(
        _decided(PLANNED), INTERVIEW_SOLUTION, cfg, memory_ports(runner)
    )

    assert transcript is None
    assert patch["questions_for_human"] == ["Which currency, really?"]
    assert patch["escalation_class"] == "questions"


async def test_a_decided_consultation_still_runs_in_round_one(cfg):
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1), questions(done=True)],
            TL_ANSWER: [tech_answers(1, for_pm=["Stored end date?"])],
            CONSULT: [consulted(1)],
        }
    )
    transcript, _ = await interview(
        _decided(PLANNED), INTERVIEW_SOLUTION, cfg, memory_ports(runner)
    )

    assert transcript is not None
    assert runner.slugs == [TL_ASK, TL_ANSWER, CONSULT, TL_ASK]


async def test_a_decided_definition_interview_is_capped_at_one_round_too(cfg):
    runner = ScriptedRunner(
        **{ASK: [questions(1), questions(done=True)], ANSWER: [answers(1)]}
    )
    transcript, _ = await interview(_decided(TASK), INTERVIEW_DEFINITION, cfg, memory_ports(runner))

    assert transcript is not None and runner.slugs == [ASK, ANSWER, ASK]


async def test_a_state_without_decided_runs_the_configured_rounds_whatever_its_request_holds(cfg):
    state = {**TASK, "description": "Users want CSV.\n\n### Decided\n- csv"}
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1), answers(2)],
        }
    )
    transcript, _ = await interview(state, INTERVIEW_DEFINITION, cfg, memory_ports(runner))

    assert transcript is not None and runner.slugs == [ASK, ANSWER, ASK, ANSWER, ASK]


async def test_a_decided_retake_gets_one_new_round_then_the_forced_close(cfg, spy_run_role):
    # After a decided interview escalated: one interviewer run already on record, the
    # forced close (two rounds done), and the human's decision. The retake reads
    # "round 3 of 3": rounds_done 2 plus the one round it is given.
    section = INTERVIEW_SOLUTION.section
    done = [
        {"role": "Tech Lead efímero", "section": section, "session_id": "e1"},
        {"role": "Tech Lead", "section": section, "session_id": "t1"},
        {"role": "Tech Lead efímero", "section": section, "session_id": "e1"},
    ]
    state = _decided(
        {
            **PLANNED,
            "branch": "agentic/T-1",
            "sections": {
                **PLANNED["sections"],
                section: OLD_SOLUTION_TRANSCRIPT,
                "Human": "### Decision 1\n\nUSD, always.",
            },
            "runs": [*PLANNED["runs"], *done],
        }
    )
    runner = ScriptedRunner(
        **{
            TL_ASK: [questions(1, session="e1"), questions(done=True, session="e1")],
            TL_ANSWER: [tech_answers(1, session="t1")],
        }
    )
    transcript, _ = await interview(state, INTERVIEW_SOLUTION, cfg, memory_ports(runner))

    assert transcript is not None
    assert [c["slug"] for c in spy_run_role] == [TL_ASK, TL_ANSWER, TL_ASK]
    assert [c["round"] for c in spy_run_role] == [3, 3, 3]
    assert [c["max_rounds"] for c in spy_run_role] == [3, 3, 3]
