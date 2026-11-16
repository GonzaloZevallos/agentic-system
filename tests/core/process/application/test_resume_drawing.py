"""`agentic resume` over a pause on drawing departures (GH-766 part 2b): the answers by number, from
comments or `--drawing`, with the resume fake ports. Nothing is written when a resume is refused."""

from typing import Any, cast

import pytest

from agentic.core.process.application import resume
from agentic.core.process.application.escalate import ESCALATED
from agentic.core.process.application.poll import AUTO_RESUMED
from agentic.core.process.domain import drawing, drawing_answer, rules, task
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_resume import paused
from tests.fakes import memory_ports

AT = "2026-11-15T10:00:00Z"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _departure(element: str) -> drawing.Departure:
    return drawing.Departure(
        drawing="design/home.png",
        element=element,
        shows="a blue button",
        would_ship="a green button",
        recommendation="keep the drawing",
        reason="the owner approved it",
        raised_by="developer",
    )


def _asked(*elements: str, answered: dict[int, str] | None = None) -> dict[str, Any]:
    """A task paused on departures `elements`, numbered from 1; `answered` rows carry a verdict."""
    base = cast(task.TaskState, paused(cost_usd=1.0, subtasks={}, runs=[]))
    patch = drawing_answer.escalation_patch(base, [_departure(e) for e in elements], AT)
    state = dict(task.apply(base, cast(Any, patch)))
    for row in state["drawing_departures"]:
        if (verdict := (answered or {}).get(row["number"])) is not None:
            row["answer"] = {"verdict": verdict, "by": "comment", "at": AT}
    return state


def _ports(*comments: str):
    ports = memory_ports()
    ports.tracker.earlier = [f"{ESCALATED}the owner decides the departures", *comments]
    return ports


def _verdicts(r: resume.Resumption) -> dict[int, tuple[str, str]]:
    rows = r.resumed["drawing_departures"]
    return {x["number"]: (x["answer"]["verdict"], x["answer"]["by"]) for x in rows if x["answer"]}


def test_a_comment_line_answers_the_departure_and_resumes(cfg):
    state = _asked("hero")
    r = resume.prepare("GH-12", state, "checkpoint", cfg, _ports("Drawing 1: accept"))
    assert _verdicts(r) == {1: ("accept", "comment")}
    assert drawing_answer.unanswered(r.resumed) == []
    assert r.target == "define"
    assert r.decision == "Drawing 1: accept"
    assert "Drawing 1: accept" in r.resumed["sections"]["Human"]
    assert r.answer()["drawing_departures"] == list(r.drawing_answers)


def test_reject_is_recorded_as_reject(cfg):
    r = resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, _ports("Drawing 1: reject"))
    assert _verdicts(r) == {1: ("reject", "comment")}


def test_a_missing_number_keeps_the_task_waiting_and_names_it(cfg):
    state = _asked("hero", "footer")
    with pytest.raises(resume.WaitingOnHuman) as waiting:
        resume.prepare("GH-12", state, "checkpoint", cfg, _ports("Drawing 1: accept"))
    message = str(waiting.value)
    assert "2" in message and "1" not in message.replace("GH-12", "").split("missing")[-1]
    assert "--drawing" in message and "not recorded" in message


def test_a_bare_decision_with_open_numbers_waits(cfg):
    with pytest.raises(resume.WaitingOnHuman):
        resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, _ports("Decision: keep it"))


def test_no_comment_at_all_waits(cfg):
    with pytest.raises(resume.WaitingOnHuman):
        resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, _ports())


def test_a_flag_beats_the_comment_and_the_text_lists_it(cfg):
    r = resume.prepare(
        "GH-12",
        _asked("hero"),
        "checkpoint",
        cfg,
        _ports("Drawing 1: accept"),
        drawing_flags=["1=reject"],
    )
    assert _verdicts(r) == {1: ("reject", "flag")}
    assert r.decision == "Drawing 1: accept\n\nDrawing 1: reject (flag)"
    assert "Drawing 1: reject (flag)" in r.resumed["sections"]["Human"]
    assert "drawing:   1=reject (flag)" in resume.summary(r, cfg, _ports())


def test_comments_across_a_poller_comment_resume_with_both_and_human_holds_both(cfg):
    ports = _ports("Drawing 1: accept", f"{AUTO_RESUMED}waited", "Drawing 2: reject")
    r = resume.prepare("GH-12", _asked("hero", "footer"), "checkpoint", cfg, ports)
    assert _verdicts(r) == {1: ("accept", "comment"), 2: ("reject", "comment")}
    human = r.resumed["sections"]["Human"]
    assert "Drawing 1: accept" in human and "Drawing 2: reject" in human
    assert AUTO_RESUMED not in human


def test_flags_alone_answer_every_number(cfg):
    r = resume.prepare(
        "GH-12",
        _asked("hero", "footer"),
        "checkpoint",
        cfg,
        _ports(),
        drawing_flags=["1=accept", "2=reject"],
    )
    assert _verdicts(r) == {1: ("accept", "flag"), 2: ("reject", "flag")}


def test_a_flag_for_one_number_of_two_waits_and_records_nothing(cfg):
    state = _asked("hero", "footer")
    with pytest.raises(resume.WaitingOnHuman, match="not recorded"):
        resume.prepare("GH-12", state, "checkpoint", cfg, _ports(), drawing_flags=["1=accept"])
    assert drawing_answer.unanswered(cast(task.TaskState, state)) == [1, 2]


def test_a_contradicted_number_waits_and_is_named(cfg):
    ports = _ports("Drawing 1: accept\nDrawing 1: reject")
    with pytest.raises(resume.WaitingOnHuman, match="contradict"):
        resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, ports)


def test_the_last_line_wins_across_comments(cfg):
    ports = _ports("Drawing 1: accept", "Drawing 1: reject")
    r = resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, ports)
    assert _verdicts(r) == {1: ("reject", "comment")}


def test_a_flag_settles_a_contradicted_comment(cfg):
    ports = _ports("Drawing 1: accept\nDrawing 1: reject")
    r = resume.prepare(
        "GH-12", _asked("hero"), "checkpoint", cfg, ports, drawing_flags=["1=accept"]
    )
    assert _verdicts(r) == {1: ("accept", "flag")}


def test_every_open_row_needs_a_verdict_not_only_the_escalated_ones(cfg):
    # Row 1 was asked in an earlier round and is still open; row 2 is the new round's.
    state = _asked("hero", "footer")
    state["drawing_escalation"] = {"round": 2, "numbers": [2], "cause": "drawing_departure"}
    with pytest.raises(resume.WaitingOnHuman):
        resume.prepare("GH-12", state, "checkpoint", cfg, _ports("Drawing 2: accept"))


@pytest.mark.parametrize(
    ("flags", "comments", "state", "match"),
    [
        (["1=accept"], [], "plain", "--drawing"),
        (["9=accept"], [], "open", "9"),
        (["1=reject"], [], "answered", "already answered"),
        (["x"], [], "open", "expected <n>=accept"),
        (["1=accept", "1=reject"], [], "open", "two different"),
        ([], ["Drawing 7: accept"], "open", "edit or delete that comment"),
    ],
)
def test_a_misuse_is_refused_before_anything_is_written(cfg, flags, comments, state, match):
    asked = {
        "plain": paused(cost_usd=1.0, subtasks={}, runs=[]),
        "open": _asked("hero"),
        "answered": _asked("hero", "footer", answered={1: "accept"}),
    }[state]
    ports = _ports(*comments)
    with pytest.raises(ValueError, match=match):
        resume.prepare("GH-12", asked, "checkpoint", cfg, ports, drawing_flags=flags)
    assert ports.documents.saved == [] and ports.tracker.comments == []


def test_flags_with_a_run_that_crashed_are_refused(cfg):
    state = {**_asked("hero"), "escalated": None, "status": "developing"}
    with pytest.raises(ValueError, match="--drawing"):
        resume.prepare(
            "GH-12", state, "checkpoint", cfg, _ports(), ("integrate",), drawing_flags=["1=accept"]
        )


def test_an_unknown_number_in_a_comment_names_the_comment(cfg):
    ports = _ports("thanks", "Drawing 7: accept")
    with pytest.raises(ValueError) as refused:
        resume.prepare("GH-12", _asked("hero"), "checkpoint", cfg, ports)
    assert "7" in str(refused.value) and "Drawing 7: accept" in str(refused.value)


def test_answering_an_answered_number_with_the_same_verdict_is_a_no_op(cfg):
    state = _asked("hero", "footer", answered={1: "accept"})
    r = resume.prepare(
        "GH-12",
        state,
        "checkpoint",
        cfg,
        _ports("Drawing 1: accept", "Drawing 2: reject"),
    )
    assert _verdicts(r) == {1: ("accept", "comment"), 2: ("reject", "comment")}
    assert [row["number"] for row in r.drawing_answers] == [2]


def test_a_mixed_pause_with_tracker_items_keeps_its_rows_open(cfg):
    state = _asked("hero")
    state.update(rules.escalation("questions", "typed items", tracker_human=["h1"]))
    state["tracker_items"] = []
    assert drawing_answer.prompt_cause(cast(task.TaskState, state)) is None
    r = resume.prepare("GH-12", state, "checkpoint", cfg, _ports("Decision: ok"))
    assert drawing_answer.unanswered(r.resumed) == [1]
    assert "drawing_departures" not in r.answer()


def test_a_pause_without_departures_resumes_as_before(cfg):
    state = paused(cost_usd=1.0, subtasks={}, runs=[])
    r = resume.prepare("GH-12", state, "checkpoint", cfg, _ports("USD."))
    assert r.drawing_answers == ()
    assert r.answer() == {"decision": "USD."}
    assert "drawing:" not in resume.summary(r, cfg, _ports())


def test_dry_run_writes_nothing(cfg):
    ports = _ports()
    r = resume.prepare(
        "GH-12", _asked("hero"), "checkpoint", cfg, ports, drawing_flags=["1=accept"]
    )
    resume.summary(r, cfg, ports)
    assert ports.documents.saved == [] and ports.tracker.comments == []
    assert ports.tracker.labels == []


def test_comments_since_reads_past_other_system_comments_without_resetting():
    comments = [
        "before",
        f"{ESCALATED}x",
        "one",
        f"{AUTO_RESUMED}y",
        "  two  ",
        "",
    ]
    assert resume.comments_since(comments, ESCALATED) == ["one", "two"]
    assert resume.comments_since(comments, "Nothing: ") == []
    assert resume.decision_since(comments, ESCALATED) == "one\n\ntwo"
    assert resume.decision_since(["x"], ESCALATED) is None
