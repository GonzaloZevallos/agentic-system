"""`apply_carve` (GH-732, GH-737) and `finish_carve` with the memory fakes behind a spy that
records every call: what it refuses, what it writes before the tracker is touched, how it finds
or opens the follow-up, how it re-points the dependents and comments on the part, and what a
tracker failure may put in the document."""

import inspect
import json
from typing import Any

import pytest

from agentic.core.process.adapters.evaluation import EvaluationTracker
from agentic.core.process.application import plan
from agentic.core.process.application.carve_out import (
    CarveRefused,
    apply_carve,
    finish_carve,
    refusals,
)
from agentic.core.process.application.ports import IssueState, TrackerRateLimited
from agentic.core.process.domain import carve_out, rules
from agentic.core.process.domain.roles import TECH_LEAD
from agentic.core.process.domain.schemas import FitDecision
from agentic.core.process.domain.task import WAITING_ON_HUMAN, apply
from tests.fakes import MemoryDocuments, MemoryInbox, MemoryTracker, memory_ports

NOW = "2026-11-14T10:00:00+00:00"
LEAK = "<!-- boom --> Closes #1"
STATE: Any = {"id": "GH-732", "title": "Part two", "description": "Carve it."}
PART_URL = "https://github.com/o/r/issues/800"
FIT = FitDecision(False, 20.0, 4, 32.5)


def _carve(**over: Any) -> Any:
    carve = {
        "steps": ["b"],
        "title": "Carved steps",
        "body": "Does the rest.",
        "kind": "change",
        "moved_criteria": ["it works"],
        "estimated_cost_usd": 10.0,
        "estimated_files": 5,
        "hand_files": 3,
        "hand_lines": 100,
        "test_lines": 50,
        "required_paths": ["src/a.py"],
        "ci_required": ["tests"],
        "integration": "wire a",
        "remainder_green": "the app still builds",
        "guard": "nothing calls it",
    }
    return {**carve, **over}


PLAN: Any = {
    "integration": "wire a and b",
    "subtasks": [
        {"id": "a", "title": "A", "brief": "Build a.", "depends_on": []},
        {"id": "b", "title": "B", "brief": "Build b.", "invariants": ["b is pure"]},
    ],
}


def _data(**over: Any) -> Any:
    return {"carve": _carve(**over), "plan_output": PLAN}


def _fits(plan: Any) -> bool:
    return True


class Spy:
    """Forwards every call to `inner` and records its name in the list shared by the ports."""

    def __init__(self, inner: Any, calls: list[str], prefix: str) -> None:
        self._inner, self._calls, self._prefix = inner, calls, prefix

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self._calls.append(f"{self._prefix}.{name}")
            return target(*args, **kwargs)

        return call


class LeakyTracker(MemoryTracker):
    def open_issue(self, title: str, body: str, **kwargs: Any) -> str:
        raise ValueError(LEAK)


class LeakyInbox(MemoryInbox):
    def open_issues(self) -> list[tuple[str, str]]:
        raise OSError(LEAK)


def _inbox(**bodies: str) -> MemoryInbox:
    """An inbox whose open issues are `bodies` by number (`_900="..."`)."""
    return MemoryInbox(
        open_issues=[
            (f"https://github.com/o/r/issues/{n[1:]}", body) for n, body in bodies.items()
        ],
        **{n[1:]: {"id": f"GH-{n[1:]}", "title": "t", "description": b} for n, b in bodies.items()},
    )


def _setup(
    tracker: Any = None, inbox: Any = None, labels: Any = ("bug", "path:x", "needs-human")
) -> tuple[Any, list[str], Any]:
    calls: list[str] = []
    inner = tracker if tracker is not None else MemoryTracker()
    inner.issue_numbers_from = 800
    inner.inbox = inbox if inbox is not None else MemoryInbox()
    if labels is not False:
        inner.states[732] = IssueState("OPEN", "", None, None, labels)
    ports = memory_ports(
        tracker=Spy(inner, calls, "tracker"),
        inbox=Spy(inner.inbox, calls, "inbox"),
        documents=Spy(MemoryDocuments(), calls, "documents"),
        now=lambda: NOW,
    )
    return ports, calls, inner


def _run(ports: Any, state: Any = STATE, data: Any = None) -> Any:
    return apply_carve(state, _data() if data is None else data, ports, fit=FIT, fits=_fits)


def _record(ports: Any) -> Any:
    return ports.documents.saved[-1]["carve_out"]


def test_a_refused_text_raises_before_any_call_and_persists_nothing() -> None:
    ports, calls, _ = _setup()
    data = _data(title="Plain", body=f"{LEAK}\n")

    with pytest.raises(CarveRefused) as refused:
        _run(ports, data=data)

    assert calls == [] and ports.documents.saved == []
    assert refused.value.reasons and str(refused.value) == "carve-out refused: 1 reason(s)"
    assert "<!--" not in str(refused.value) and "Closes" not in str(refused.value)


@pytest.mark.parametrize(
    ("fits", "expected"), [(_fits, []), (lambda _: False, ["the remainder still does not fit"])]
)
def test_refusals_ask_the_callers_fits_and_the_text_rules(fits: Any, expected: list[str]) -> None:
    found = refusals(_carve(), PLAN, fits)
    assert [r[: len(e)] for r, e in zip(found, expected, strict=True)] == expected
    assert refusals(_carve(body="Closes #1"), PLAN, _fits)


def test_an_evaluation_run_closes_the_record_and_calls_nothing() -> None:
    ports, calls, _ = _setup()
    patch = _run(ports, {**STATE, "evaluation": True})

    assert calls == ["documents.save"]
    assert patch["carve_out"] == {"outcome": "refused", "cause": "evaluation"}
    assert _record(ports) == {"outcome": "refused", "cause": "evaluation"}


def test_an_evaluation_tracker_is_never_asked_to_write() -> None:
    inner = MemoryTracker()
    ports = memory_ports(tracker=EvaluationTracker(inner), documents=MemoryDocuments())

    _run(ports, {**STATE, "evaluation": True})

    assert inner.opened_issues == [] and inner.created_labels == []


def test_a_task_with_no_issue_closes_the_record_and_writes_nothing() -> None:
    ports, calls, inner = _setup()
    _run(ports, {**STATE, "id": "T-9"})

    assert _record(ports)["outcome"] == "refused" and _record(ports)["cause"] == "no_issue"
    assert [c for c in calls if c.startswith("tracker.")] == []
    assert inner.opened_issues == []


def test_a_valid_carve_out_saves_its_record_then_opens_the_follow_up_with_the_copied_labels() -> (
    None
):
    ports, calls, inner = _setup()
    patch = _run(ports)

    assert calls.index("documents.save") < calls.index("inbox.issue_of")
    assert calls.index("documents.save") < calls.index("tracker.open_issue")
    assert calls.count("tracker.comment_on_issue") == 1 and "tracker.update_issue_body" not in calls
    assert inner.created_labels == ["bug"]
    assert inner.opened_label_sets == [("agentic", "bug")]
    ((title, body),) = inner.opened_issues
    assert title == "Carved steps" and "### b: B" in body
    assert "Blocked by: #732" in body.splitlines() and rules.blockers_from_body(body) == (732,)
    assert carve_out.marked_by(body) == ("GH-732", 732)
    record = _record(ports)
    assert record["issue_url"] == PART_URL and "outcome" not in record and "at" in record
    assert record["repointed_at"] == NOW and record["commented_at"] == NOW
    assert record["data"]["carve"]["title"] == "Carved steps"
    assert record["fit"]["reference_usd"] == 32.5 and record["fit"]["crossed"] == []
    assert record["at"] == NOW and "labels_unread" not in record
    assert patch["carve_out"] == record


@pytest.mark.parametrize("labels", [None, False])
def test_labels_that_cannot_be_read_leave_only_agentic_and_say_so(labels: Any) -> None:
    ports, _, inner = _setup(labels=labels)
    _run(ports)

    assert inner.opened_label_sets == [("agentic",)] and inner.created_labels == []
    assert _record(ports)["labels_unread"] is True and len(inner.opened_issues) == 1


def test_a_lost_record_finds_the_follow_up_by_its_marker_and_opens_no_second() -> None:
    other = carve_out.marker("GH-1", 732)
    mine = f"Body.\n{carve_out.marker('GH-732', 732)}"
    inbox = MemoryInbox(open_issues=[("u/1", other), ("https://github.com/o/r/issues/5", mine)])
    ports, calls, inner = _setup(inbox=inbox)
    _run(ports)

    assert inner.opened_issues == [] and "tracker.ensure_label" not in calls
    assert _record(ports)["issue_url"] == "https://github.com/o/r/issues/5"


def test_a_resume_uses_the_record_and_ignores_the_data_it_is_given() -> None:
    first, _, cut = _setup(tracker=MemoryTracker())
    cut.open_issue_fails_on = {0}
    paused = _run(first)
    assert paused["status"] == WAITING_ON_HUMAN
    saved = first.documents.saved[-1]

    second, _, inner = _setup()
    _run(second, saved, _data(title="Other", body="Other body.", steps=["a"]))

    ((title, body),) = inner.opened_issues
    assert title == "Carved steps" and "Does the rest." in body and "Other" not in body
    assert "### b: B" in body and "### a" not in body


def test_a_complete_record_makes_no_call_at_all() -> None:
    ports, calls, inner = _setup()
    record = {"data": _data(), "at": NOW, "issue_url": PART_URL, "repointed_at": NOW}
    state = {**STATE, "carve_out": {**record, "commented_at": NOW}}

    patch = _run(ports, state, _data(body=LEAK))

    assert calls == ["inbox.issue_of"] and inner.opened_issues == []
    assert patch["carve_out"] == state["carve_out"] and "outcome" not in patch["carve_out"]
    closed = {**STATE, "carve_out": {"outcome": "refused", "cause": "no_issue"}}
    assert _run(ports, closed)["carve_out"] == closed["carve_out"] and calls == ["inbox.issue_of"]


def test_a_record_with_data_of_the_wrong_shape_pauses_instead_of_raising() -> None:
    ports, calls, _ = _setup()
    state = {**STATE, "carve_out": {"data": {"carve": {}}, "at": NOW}}

    patch = _run(ports, state)

    assert patch["status"] == WAITING_ON_HUMAN and "reading the saved record" in patch["escalated"]
    assert [c for c in calls if c.startswith("tracker.")] == []


@pytest.mark.parametrize("where", ["reading the open issues", "creating a label", "opening"])
def test_a_port_error_pauses_with_its_class_and_never_its_text(where: str) -> None:
    inbox = LeakyInbox() if where.startswith("reading") else None
    tracker = LeakyTracker() if where == "opening" else MemoryTracker()
    if where == "creating a label":
        tracker.ensure_label_error = LEAK
    ports, _, _ = _setup(tracker=tracker, inbox=inbox)

    patch = _run(ports)

    step = "opening the follow-up" if where == "opening" else where
    cls = "OSError" if where.startswith("reading") else "ValueError"
    assert patch["id"] == "GH-732" and patch["title"] == "Part two"
    assert patch["description"] == "Carve it."
    assert patch["current_role"] == TECH_LEAD.name and patch["status"] == WAITING_ON_HUMAN
    assert patch["escalation_class"] == "failure" and patch["escalation_cause"] is None
    assert patch["escalated"] == (
        f"carve-out: {step} failed ({cls}); the follow-up, its re-point and the comment on the "
        "part are left, resume to finish"
    )
    assert patch["pause_log"] == [{"kind": "escalation", "value": "failure", "at": NOW}]
    assert set(patch) == {
        "id",
        "title",
        "description",
        "current_role",
        "status",
        "escalated",
        "escalation_class",
        "escalation_cause",
        "pause_log",
    }
    for text in (json.dumps(patch, default=str), json.dumps(ports.documents.saved, default=str)):
        assert "<!--" not in text and "Closes #1" not in text


def test_plan_py_does_not_name_apply_carve() -> None:
    assert "apply_carve" not in inspect.getsource(plan)


DEPENDENTS = {"_900": "Blocked by: #732", "_901": "Intro\n\nBlocked by: #5, #732\n"}
PHRASE = (
    "#732 now also waits on the carved-out follow-up #800: this issue is unblocked when both "
    "are closed."
)
REPOINT_LEFT = "its re-point and the comment on the part are left"
COMMENT_LEFT = "the comment on the part is left"
KEYS = {
    "id",
    "title",
    "description",
    "current_role",
    "status",
    "escalated",
    "escalation_class",
    "escalation_cause",
    "pause_log",
}


def _carve_comments(inner: Any) -> list[str]:
    return [text for ref, text in inner.commented_issues if ref == "732"]


def test_a_valid_carve_out_re_points_the_dependents_comments_once_and_finishes() -> None:
    ports, _, inner = _setup(inbox=_inbox(**DEPENDENTS))
    patch = _run(ports)

    assert dict(inner.updated_bodies) == {
        900: "Blocked by: #732, #800",
        901: "Intro\n\nBlocked by: #5, #732, #800\n",
    }
    told = [(ref, text) for ref, text in inner.commented_issues if ref != "732"]
    assert told == [(f"https://github.com/o/r/issues/{n}", PHRASE) for n in (900, 901)]
    (posted,) = _carve_comments(inner)
    first, *_ = posted.splitlines()
    assert first == "Carved out: b -> #800"
    assert "- b: B" in posted and "- it works" in posted and "labels" not in posted
    assert "3 hand files, 100 hand lines, 50 test lines, estimated cost 10.0 USD" in posted
    record = patch["carve_out"]
    assert {"data", "issue_url", "repointed_at", "commented_at"} <= set(record)
    assert "outcome" not in record

    closing, rest = finish_carve({**STATE, "carve_out": record})
    assert closing["carve_out"] == {**record, "outcome": "applied"}
    assert set(closing) == {"id", "title", "description", "carve_out"}
    assert rest == carve_out.remainder(PLAN, _carve())
    assert [s["id"] for s in rest["subtasks"]] == ["a"] and rest["integration"] == "wire a"


def test_a_run_cut_after_the_follow_up_was_opened_opens_no_second_and_finishes_the_rest() -> None:
    first, _, cut = _setup(inbox=_inbox(**DEPENDENTS))
    cut.comment_on_issue_fails_on = {2}
    paused = _run(first)
    assert paused["status"] == WAITING_ON_HUMAN and len(cut.opened_issues) == 1
    assert "commenting on the part failed (ValueError)" in paused["escalated"]
    saved = first.documents.saved[-1]
    assert "repointed_at" in saved["carve_out"] and "commented_at" not in saved["carve_out"]

    only_url = {**saved, "carve_out": {**saved["carve_out"]}}
    del only_url["carve_out"]["repointed_at"]
    second, calls, inner = _setup(inbox=_inbox(**DEPENDENTS))
    patch = _run(second, only_url)

    assert inner.opened_issues == [] and "inbox.open_issues" not in calls
    assert len(inner.updated_bodies) == 2 and len(_carve_comments(inner)) == 1
    assert patch["carve_out"]["issue_url"] == PART_URL and "commented_at" in patch["carve_out"]


@pytest.mark.parametrize("failure", ["failed", "stopped", "refs", "comments", "posting"])
def test_an_incomplete_step_pauses_key_by_key_and_the_resume_finishes(failure: str) -> None:
    tracker = {"stopped": RateLimitedTracker, "comments": UnreadableTracker}.get(
        failure, MemoryTracker
    )()
    inbox = _inbox(**DEPENDENTS)
    if failure == "failed":
        tracker.body_errors = {900, 901}
    if failure == "refs":
        inbox.refs_error = OSError(LEAK)
    if failure == "posting":
        tracker.comment_on_issue_fails_on = {2}
    ports, _, inner = _setup(tracker=tracker, inbox=inbox)

    patch = _run(ports)

    detail, left = {
        "failed": ("re-pointing the dependents failed (2 failed)", REPOINT_LEFT),
        "stopped": (
            "re-pointing the dependents failed (stopped by TrackerRateLimited)",
            REPOINT_LEFT,
        ),
        "refs": ("reading the open issues failed (OSError)", REPOINT_LEFT),
        "comments": ("reading the comments of the part failed (ValueError)", COMMENT_LEFT),
        "posting": ("commenting on the part failed (ValueError)", COMMENT_LEFT),
    }[failure]
    assert patch["id"] == "GH-732" and patch["title"] == "Part two"
    assert patch["description"] == "Carve it."
    assert patch["current_role"] == TECH_LEAD.name and patch["status"] == WAITING_ON_HUMAN
    assert patch["escalation_class"] == "failure" and patch["escalation_cause"] is None
    assert patch["escalated"] == f"carve-out: {detail}; {left}, resume to finish"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "failure", "at": NOW}]
    assert set(patch) == KEYS
    record = _record(ports)
    assert ("repointed_at" in record) == (failure in {"comments", "posting"})
    assert "commented_at" not in record and "outcome" not in record
    if failure != "posting":
        assert _carve_comments(inner) == []
    for text in (json.dumps(patch, default=str), json.dumps(ports.documents.saved, default=str)):
        assert "<!--" not in text and "Closes #1" not in text

    healthy, _, again = _setup(inbox=_inbox(**DEPENDENTS))
    resumed = _run(healthy, apply({**STATE, "carve_out": record}, patch))
    assert again.opened_issues == [] and len(_carve_comments(again)) == 1
    assert {"repointed_at", "commented_at"} <= set(resumed["carve_out"])
    assert (
        finish_carve({**STATE, "carve_out": resumed["carve_out"]})[0]["carve_out"]["outcome"]
        == "applied"
    )


class RateLimitedTracker(MemoryTracker):
    def update_issue_body(self, number: int, body: str) -> None:
        raise TrackerRateLimited(LEAK)


class UnreadableTracker(MemoryTracker):
    def issue_comments(self, task_id: str) -> list[str]:
        raise ValueError(LEAK)


def test_the_comment_on_the_part_says_when_the_labels_could_not_be_read() -> None:
    ports, _, inner = _setup(labels=None)
    _run(ports)

    (posted,) = _carve_comments(inner)
    assert "The labels of this issue could not be read" in posted.splitlines()[-1]


def test_a_comment_holding_the_exact_carved_out_line_suppresses_a_second() -> None:
    tracker = MemoryTracker()
    tracker.earlier = ["hello", "text\n  Carved out: b -> #800  \nmore"]
    ports, _, inner = _setup(tracker=tracker)
    patch = _run(ports)

    assert _carve_comments(inner) == [] and patch["carve_out"]["commented_at"] == NOW


@pytest.mark.parametrize("earlier", ["Carved out: b -> #8000", "See Carved out: b -> #800 here"])
def test_a_comment_that_only_contains_the_line_does_not_suppress_the_post(earlier: str) -> None:
    tracker = MemoryTracker()
    tracker.earlier = [earlier]
    ports, _, inner = _setup(tracker=tracker)
    _run(ports)

    assert len(_carve_comments(inner)) == 1


def _complete() -> Any:
    record = {
        "data": _data(),
        "at": NOW,
        "issue_url": PART_URL,
        "repointed_at": NOW,
        "commented_at": NOW,
    }
    return {**STATE, "carve_out": record}


@pytest.mark.parametrize("key", ["data", "issue_url", "repointed_at", "commented_at"])
@pytest.mark.parametrize("broken", ["missing", "empty", "number"])
def test_finish_carve_refuses_a_record_missing_a_required_key(key: str, broken: str) -> None:
    state = _complete()
    if broken == "missing":
        del state["carve_out"][key]
    else:
        state["carve_out"][key] = "" if broken == "empty" else 7
    if key == "data" and broken != "missing":
        state["carve_out"][key] = {} if broken == "empty" else [1]

    with pytest.raises(ValueError, match="carve-out"):
        finish_carve(state)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(outcome="applied"),
        lambda r: r.update(data={"carve": {}, "plan_output": {}}),
        lambda r: r.update(data={"carve": _carve(steps=None), "plan_output": PLAN}),
        lambda r: r.update(data={"carve": _carve(steps=7), "plan_output": PLAN}),
        lambda r: r["data"]["carve"].pop("hand_files"),
    ],
)
def test_finish_carve_raises_value_error_for_what_it_cannot_close(mutation: Any) -> None:
    state = _complete()
    mutation(state["carve_out"])
    with pytest.raises(ValueError) as refused:
        finish_carve(state)
    assert not isinstance(refused.value, KeyError | TypeError)
    assert refused.value.__cause__ is None


def test_finish_carve_answers_the_remainder_of_the_saved_plan_and_saves_nothing() -> None:
    state = _complete()
    state["plan_output"] = {"subtasks": [{"id": "z"}], "integration": "other"}
    ports, calls, _ = _setup()

    patch, rest = finish_carve(state)

    assert [s["id"] for s in rest["subtasks"]] == ["a"] and rest["integration"] == "wire a"
    assert patch["carve_out"]["outcome"] == "applied" and "outcome" not in state["carve_out"]
    assert calls == [] and ports.documents.saved == []
