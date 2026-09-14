"""The interview between two roles (ADR-0014), against in-memory ports: rounds,
sessions, the cap, the closing, and what the interviewee cannot decide."""

import pytest

from agentic.core.process.application.define import define
from agentic.core.process.application.interview import interview
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain.roles import INTERVIEW_DEFINITION
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, answers, defined, memory_ports, questions

pytestmark = pytest.mark.anyio

TASK = {"id": "T-1", "title": "Export report", "description": "Users want CSV.", "type": "feature"}
ASK, ANSWER = "product_analyst_interviewer", "pm_interviewee"


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
    assert "### Closing" in patch["sections"]["Interview: Product Analyst→PM"]


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
