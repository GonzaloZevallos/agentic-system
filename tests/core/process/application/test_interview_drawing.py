"""The drawing policy of the definition interview (GH-766 part 3b), recorded with fakes: what
each seat is checked for, what escalates, and what the retake owes."""

from typing import Any

import pytest

from agentic.core.process.application import interview_drawing
from agentic.core.process.application.interview import _escalate_drawing, interview
from agentic.core.process.application.ports import AttemptFailed, RawResult
from agentic.core.process.domain import drawing, drawing_answer, spec_assumptions, task
from agentic.core.process.domain.design import Design, PrototypeListing
from agentic.core.process.domain.roles import (
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    INTERVIEW_SOLUTION_PLATFORM,
    INTERVIEW_SOLUTION_STACK,
)
from agentic.core.process.domain.schemas import Answers, DrawingCheck
from agentic.core.process.domain.task import SpecConflictCause
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_interview import SPEC_TASK, TASK, _closed_four_rounds
from tests.fakes import MemoryStack, ScriptedRunner, memory_ports, questions

pytestmark = pytest.mark.anyio

ASK, ANSWER = "product_analyst_interviewer", "pm_interviewee"
DESIGN = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
PATH = "pantallas/Home.png"
KEY = "pantallas/Home.png:date format"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path, interview_max_rounds=4)


def _ports(runner, design=DESIGN):
    return memory_ports(runner, stack=MemoryStack(design=design))


def _ask(n=1, visible=(1,), done=False, session="q"):
    base = questions(n, done=done, session=session)
    return RawResult({**base.structured_output, "visible": list(visible)}, 0.05, 2, session)


def _check(ref="Q1", verdict="matches", element="date format"):
    return {
        "subject": "question",
        "ref": ref,
        "verdict": verdict,
        "drawing": PATH,
        "element": element,
    }


def _departure(element="date format", raised_by="somebody else"):
    return {
        "drawing": PATH,
        "element": element,
        "shows": "DD/MM/YYYY",
        "would_ship": "MM/DD/YYYY",
        "recommendation": "keep DD/MM/YYYY",
        "reason": "the drawing is approved",
        "raised_by": raised_by,
    }


def _answer(checks=(), departures=(), n=1, session="a"):
    data = {
        "markdown": f"**Q{n}**: decided",
        "questions_for_human": [],
        "withdrawn": [],
        "drawing_checks": list(checks),
        "departures": list(departures),
    }
    return RawResult(data, 0.1, 1, session)


async def test_a_departure_escalates_with_the_path_and_never_as_an_assumption(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))

    assert transcript is None and patch["status"] == "waiting_on_human"
    assert PATH in patch["escalated"] and "DD/MM/YYYY" in patch["escalated"]
    assert "MM/DD/YYYY" in patch["escalated"] and KEY not in patch["escalated"]
    (row,) = patch["drawing_departures"]
    assert row["raised_by"] == ANSWER and row["answer"] is None  # the system, never the model
    assert "assumptions" not in patch


async def test_an_answer_that_matches_the_drawing_does_not_escalate(cfg):
    runner = ScriptedRunner(
        **{ASK: [_ask(), _ask(done=True, visible=())], ANSWER: [_answer([_check()])]}
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is not None and "escalated" not in patch
    assert "drawing_departures" not in patch


async def test_an_answer_that_skips_a_visible_question_is_refused_in_the_turn(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer(), _answer([_check()])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is not None and "escalated" not in patch
    assert runner.slugs.count(ANSWER) == 2

    thrice = ScriptedRunner(**{ASK: [_ask()], ANSWER: [_answer(), _answer(), _answer()]})
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(thrice))
    assert transcript is None and "PM failed 3 times" in patch["escalated"]
    assert "Q1 is marked visible but has no drawing check" in patch["failures"][0]["error"]


async def test_the_interviewer_that_closes_with_visible_questions_is_refused(cfg):
    runner = ScriptedRunner(**{ASK: [_ask(done=True)] * 3})
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is None and "Product Analyst failed 3 times" in patch["escalated"]


async def test_a_rejected_key_that_comes_back_is_refused_with_its_number_and_no_key(cfg):
    rows = task.departure_records(
        None, [drawing.Departure(PATH, "date format", "a", "b", "c", "d", ANSWER)], "T0"
    )
    rejected = [{**rows[0], "answer": {"verdict": "reject", "by": "comment", "at": "T1"}}]
    state = {**TASK, "drawing_departures": rejected}
    back = _answer([_check(verdict="departs")], [_departure()])
    runner = ScriptedRunner(**{ASK: [_ask()], ANSWER: [back, back, back]})
    transcript, patch = await interview(state, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is None
    error = patch["failures"][0]["error"]
    assert "Drawing #1" in error and KEY not in error and "date format:" not in error
    assert KEY not in patch["escalated"]


def _rejected_state() -> dict[str, Any]:
    row = task.departure_records(
        None,
        [drawing.Departure(PATH, "date format", "a", "b", "c", "d", ANSWER)],
        "T0",
    )[0]
    row = task.DepartureRecord(
        **{**row, "answer": {"verdict": "reject", "by": "comment", "at": "T1"}}
    )
    return {**_closed_four_rounds(), "drawing_departures": [row]}


async def test_a_retake_after_a_reject_shows_the_row_and_closes_only_with_a_match(cfg):
    other = _answer([_check(element="other")])
    ports = _ports(ScriptedRunner(**{ASK: [_ask(session="q4")], ANSWER: [other] * 3}))
    transcript, patch = await interview(_rejected_state(), INTERVIEW_DEFINITION, cfg, ports)
    assert (
        transcript is None
        and "Drawing #1 was rejected and must now match" in (patch["failures"][0]["error"])
    )

    runner = ScriptedRunner(
        **{
            ASK: [_ask(session="q4"), _ask(done=True, visible=(), session="q4")],
            ANSWER: [_answer([_check()], session="a3")],
        }
    )
    transcript, patch = await interview(
        _rejected_state(), INTERVIEW_DEFINITION, cfg, _ports(runner)
    )
    assert transcript is not None and "escalated" not in patch
    assert "## Drawing departures" in runner.calls[1]["prompt"]
    (row,) = patch["drawing_departures"]
    assert row["fix_passes"] == 1
    # A later retake does not demand it again.
    later = {**_rejected_state(), "drawing_departures": patch["drawing_departures"]}
    assert drawing_answer.must_match(later, ANSWER) == {}


async def test_a_retake_whose_interviewer_closes_at_once_keeps_the_row_in_the_delta(cfg):
    runner = ScriptedRunner(**{ASK: [_ask(done=True, visible=(), session="q4")]})
    transcript, patch = await interview(
        _rejected_state(), INTERVIEW_DEFINITION, cfg, _ports(runner)
    )
    assert transcript is not None
    assert "## Drawing departures" in runner.calls[0]["prompt"]
    assert "drawing_departures" not in patch


async def test_a_failure_keeps_itself_and_the_pending_departure_waits_for_the_next_exit(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(2, visible=(2,))],
            ANSWER: [
                _answer([_check(verdict="departs")], [_departure()]),
                AttemptFailed("a", 0.02),
                AttemptFailed("b", 0.02),
            ],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is None and "PM failed 2 times" in patch["escalated"]
    assert "drawing" not in patch["escalated"].lower().replace("drawing departure", "")
    (row,) = patch["drawing_departures"]
    assert row["answer"] is None and row["raised_by"] == ANSWER
    assert "drawing_escalation" not in patch

    # The next normal exit escalates it, with no new departure.
    state = {**TASK, "drawing_departures": patch["drawing_departures"]}
    again = ScriptedRunner(**{ASK: [_ask(done=True, visible=())]})
    transcript, second = await interview(state, INTERVIEW_DEFINITION, cfg, _ports(again))
    assert transcript is None and PATH in second["escalated"]


async def test_the_same_key_in_two_rounds_is_one_row(cfg):
    departs = _answer([_check(verdict="departs")], [_departure()])
    second = _answer([_check("Q2", "departs")], [_departure()], n=2)
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(2, visible=(2,)), _ask(done=True, visible=())],
            ANSWER: [departs, second],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is None and len(patch["drawing_departures"]) == 1


async def test_with_a_spec_a_departure_escalates_with_the_drawing_cause(cfg):
    state = SPEC_TASK
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(state, INTERVIEW_DEFINITION, cfg, _ports(runner))
    assert transcript is None and patch.get("spec_cause") == "drawing"


def test_without_a_design_no_seat_has_a_check():
    for spec in (INTERVIEW_DEFINITION, INTERVIEW_SOLUTION):
        for role in (spec.interviewer, spec.interviewee):
            assert interview_drawing.check_for(role, None, {}, (1,)) is None


def test_only_the_definition_seats_get_a_check_with_a_design():
    got = {}
    for spec in (
        INTERVIEW_DEFINITION,
        INTERVIEW_SOLUTION,
        INTERVIEW_SOLUTION_PLATFORM,
        INTERVIEW_SOLUTION_STACK,
    ):
        for role in (
            spec.interviewer,
            spec.interviewee,
            *([spec.consultant] if spec.consultant else []),
        ):
            got[role.slug] = interview_drawing.check_for(role, DESIGN, {}, (1,)) is not None
    assert {slug for slug, has in got.items() if has} == {ASK, ANSWER}


async def test_without_a_design_a_departing_answer_changes_nothing(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner, None))
    assert transcript is not None and "drawing_departures" not in patch


def _conflicting(checks=(), departures=(), n=1):
    base = _answer(checks, departures, n=n)
    data = {**base.structured_output, "assumptions": [], "spec_conflict": "conflict"}
    return RawResult(data, 0.1, 1, "a")


class _SpecAnswers(Answers):
    """`Answers` as a spec delivery would ask for it. Today's `SPEC_SCHEMAS` has no such
    class for the PM interviewee, so the tests below put it there: they pin the rule of
    `interview()` for the day a seat of `DRAWING_SEATS` can raise a conflict."""

    assumptions: list[str] = []
    spec_conflict: SpecConflictCause | None = None


@pytest.fixture
def spec_answers(monkeypatch):
    monkeypatch.setitem(spec_assumptions.SPEC_SCHEMAS, Answers, _SpecAnswers)


async def test_a_conflict_and_a_departure_in_one_round_escalate_the_drawing_first(
    cfg, spec_answers
):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(2, visible=()), _ask(done=True, visible=())],
            ANSWER: [_conflicting([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(SPEC_TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))

    assert transcript is None and runner.slugs == [ASK, ANSWER]  # no more rounds
    assert patch["spec_cause"] == "drawing" and PATH in patch["escalated"]
    assert [e["kind"] for e in patch["pause_log"]] == ["escalation"]
    (row,) = patch["drawing_departures"]

    # The owner accepts it; on the retake the conflict escalates at the step it would have had.
    accepted = {**row, "answer": {"verdict": "accept", "by": "comment", "at": "T1"}}
    state = {**SPEC_TASK, **{k: v for k, v in patch.items() if k in ("runs", "sections")}}
    state = {**state, "branch": "agentic/T-1", "drawing_departures": [accepted]}
    retake = ScriptedRunner(
        **{
            ASK: [_ask(2, visible=())],
            ANSWER: [_conflicting(n=2)],
        }
    )
    transcript, second = await interview(state, INTERVIEW_DEFINITION, cfg, _ports(retake))
    assert transcript is None and second["spec_cause"] == "conflict"
    assert second["escalated"].startswith("Spec conflict raised in interview round")


async def test_a_departure_of_this_round_wins_over_a_conflict_the_owner_decided(cfg, spec_answers):
    decided = {
        "pause_log": [{"kind": "escalation", "value": "questions", "spec_cause": "conflict"}]
    }
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_conflicting([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(
        {**SPEC_TASK, **decided}, INTERVIEW_DEFINITION, cfg, _ports(runner)
    )
    assert transcript is None and patch["spec_cause"] == "drawing"
    assert runner.slugs == [ASK, ANSWER]  # stopped in this round, not at the closing exit


async def test_an_open_drawing_row_does_not_escalate_a_solution_interview(cfg):
    from tests.core.process.application.test_interview import TL_ANSWER_P, TL_ASK_P
    from tests.fakes import answers as plain_answers

    row = task.departure_records(
        None, [drawing.Departure(PATH, "date format", "a", "b", "c", "d", ANSWER)], "T0"
    )
    runner = ScriptedRunner(
        **{TL_ASK_P: [questions(1), questions(done=True)], TL_ANSWER_P: [plain_answers(1)]}
    )
    transcript, patch = await interview(
        {**TASK, "drawing_departures": row}, INTERVIEW_SOLUTION_PLATFORM, cfg, _ports(runner)
    )
    assert transcript is not None and "escalated" not in patch


def test_a_departure_already_held_as_a_row_is_not_raised_by_this_round():
    role = INTERVIEW_DEFINITION.interviewee
    collector = interview_drawing.Collector(DESIGN)
    data = {"drawing_checks": [], "departures": [_departure()]}

    collector.collect(role, data, {})
    assert collector.raised_now is True

    held = {"drawing_departures": task.departure_records(None, collector.pending({}), "T0")}
    again = interview_drawing.Collector(DESIGN)
    again.collect(role, data, held)
    assert again.raised_now is False and again.pending(held) == []


LISTED = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None, files=(PATH,))


def test_a_departure_cited_short_is_stored_with_its_full_path():
    collector = interview_drawing.Collector(LISTED)
    short = {**_departure(), "drawing": "Home.png"}
    collector.collect(INTERVIEW_DEFINITION.interviewee, {"departures": [short]}, {})
    (held,) = collector.pending({})
    assert held.drawing == PATH
    assert list(collector.new) == [KEY]
    (row,) = task.departure_records(None, [held], "T0")
    assert row["drawing"] == PATH and row["key"] == KEY


def test_a_short_matches_check_clears_the_owed_full_path_row():
    state = _rejected_state()
    collector = interview_drawing.Collector(LISTED)
    short = {**_check(), "drawing": "Home.png"}
    patch = collector.collect(INTERVIEW_DEFINITION.interviewee, {"drawing_checks": [short]}, state)
    assert [r["fix_passes"] for r in patch["drawing_departures"]] == [1]


def test_a_citation_the_check_already_accepted_but_resolve_refuses_keeps_the_raw_path():
    collector = interview_drawing.Collector(LISTED)
    odd = {**_departure(), "drawing": "pantallas/Elsewhere.png"}
    collector.collect(INTERVIEW_DEFINITION.interviewee, {"departures": [odd]}, {})
    (held,) = collector.pending({})
    assert held.drawing == "pantallas/Elsewhere.png"


class _CountingStack(MemoryStack):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.listed = 0

    def prototype_files(self, revision, design):
        self.listed += 1
        return super().prototype_files(revision, design)


async def test_a_short_citation_resolves_in_the_interview_and_the_listing_is_read_once(cfg):
    stack = _CountingStack(design=DESIGN, prototype=PrototypeListing((PATH,), "listed"))
    short = {**_check(verdict="departs"), "drawing": "Home.png"}
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer([short], [{**_departure(), "drawing": "Home.png"}])],
        }
    )
    transcript, patch = await interview(
        TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner, stack=stack)
    )
    assert transcript is None and patch["status"] == "waiting_on_human"
    (row,) = patch["drawing_departures"]
    assert row["drawing"] == PATH and row["key"] == KEY
    assert stack.listed == 1


async def test_an_unreadable_listing_refuses_a_short_path_and_says_so_once(cfg, capsys):
    stack = _CountingStack(design=DESIGN)  # unreadable by default
    short = {**_check(), "drawing": "Home.png"}
    runner = ScriptedRunner(**{ASK: [_ask()], ANSWER: [_answer([short])] * 3})
    transcript, patch = await interview(
        TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner, stack=stack)
    )
    assert (
        transcript is None
        and "cite the full path, pantallas/<name>" in patch["failures"][0]["error"]
    )
    err = capsys.readouterr().err
    assert err.count("agentic: drawing: prototype not listed (unreadable)") == 1
    assert stack.listed == 1


async def test_the_listing_is_not_read_for_an_interview_without_a_drawing_seat(cfg):
    from tests.core.process.application.test_interview import TL_ANSWER_P, TL_ASK_P
    from tests.fakes import answers as plain_answers

    stack = _CountingStack(design=DESIGN)
    runner = ScriptedRunner(
        **{TL_ASK_P: [questions(1), questions(done=True)], TL_ANSWER_P: [plain_answers(1)]}
    )
    await interview(TASK, INTERVIEW_SOLUTION_PLATFORM, cfg, memory_ports(runner, stack=stack))
    assert stack.listed == 0


async def test_the_new_rows_and_the_pause_log_share_one_clock_read(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [_ask(), _ask(done=True, visible=())],
            ANSWER: [_answer([_check(verdict="departs")], [_departure()])],
        }
    )
    transcript, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, _ports(runner))

    assert transcript is None
    (row,) = patch["drawing_departures"]
    (entry,) = [e for e in patch["pause_log"] if e["kind"] == "escalation"]
    assert row["raised_at"] == entry["at"]


def _unverified_check(ref="Q1"):
    return DrawingCheck(subject="question", ref=ref, verdict="unverified", drawing=PATH)


def _escalate_with_asks(monkeypatch, patch, asks):
    real = drawing_answer.escalation_patch

    def with_asks(state, departures, at, **kwargs):
        return real(state, departures, at, **{**kwargs, "unverified": asks})

    monkeypatch.setattr(drawing_answer, "escalation_patch", with_asks)
    drawn = interview_drawing.Collector(DESIGN)
    drawn.collect(INTERVIEW_DEFINITION.interviewee, {"departures": [_departure()]}, {})
    return _escalate_drawing({}, patch, drawn, _ports(ScriptedRunner()))


def test_the_ask_lines_follow_the_questions_the_patch_already_carried(monkeypatch):
    asks = drawing.unverified_lines([_unverified_check("Q1"), _unverified_check("Q2")])
    out = _escalate_with_asks(monkeypatch, {"questions_for_human": ["Own?", asks[0].line]}, asks)
    assert out["questions_for_human"] == ["Own?", asks[0].line, asks[1].line]
    (entry,) = [e for e in out["pause_log"] if e["kind"] == "escalation"]
    assert out["drawing_escalation"]["unverified_at"] == entry["at"]
    assert out["drawing_departures"][0]["raised_at"] == entry["at"]


def test_a_pause_without_asks_leaves_the_patch_questions_as_they_were(monkeypatch):
    held = _escalate_with_asks(monkeypatch, {"questions_for_human": ["Own?"]}, ())
    assert held["questions_for_human"] == ["Own?"]
    bare = _escalate_with_asks(monkeypatch, {}, ())
    assert "questions_for_human" not in bare


def test_a_pause_without_asks_keeps_a_question_the_patch_carried_twice(monkeypatch):
    held = _escalate_with_asks(monkeypatch, {"questions_for_human": ["Own?", "Own?"]}, ())
    assert held["questions_for_human"] == ["Own?", "Own?"]
