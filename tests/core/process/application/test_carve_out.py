"""`apply_carve` steps 1-3 (GH-732) with the memory fakes behind a spy that records every call:
what it refuses, what it writes before the tracker is touched, how it finds or opens the
follow-up, and what a tracker failure may put in the document."""

import json
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.evaluation import EvaluationTracker
from agentic.core.process.application.carve_out import CarveRefused, apply_carve, refusals
from agentic.core.process.application.ports import IssueState
from agentic.core.process.domain import carve_out, rules
from agentic.core.process.domain.roles import TECH_LEAD
from agentic.core.process.domain.schemas import FitDecision
from agentic.core.process.domain.task import WAITING_ON_HUMAN
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


def _setup(
    tracker: Any = None, inbox: Any = None, labels: Any = ("bug", "path:x", "needs-human")
) -> tuple[Any, list[str], Any]:
    calls: list[str] = []
    inner = tracker if tracker is not None else MemoryTracker()
    inner.issue_numbers_from = 800
    if labels is not False:
        inner.states[732] = IssueState("OPEN", "", None, None, labels)
    ports = memory_ports(
        tracker=Spy(inner, calls, "tracker"),
        inbox=Spy(inbox if inbox is not None else MemoryInbox(), calls, "inbox"),
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
    assert not {"tracker.comment_on_issue", "tracker.update_issue_body"} & set(calls)
    assert inner.created_labels == ["bug"]
    assert inner.opened_label_sets == [("agentic", "bug")]
    ((title, body),) = inner.opened_issues
    assert title == "Carved steps" and "### b: B" in body
    assert "Blocked by: #732" in body.splitlines() and rules.blockers_from_body(body) == (732,)
    assert carve_out.marked_by(body) == ("GH-732", 732)
    record = _record(ports)
    assert record["issue_url"] == PART_URL and "outcome" not in record and "at" in record
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


def test_a_record_with_its_issue_url_resumes_with_no_call_at_all() -> None:
    ports, calls, inner = _setup()
    state = {**STATE, "carve_out": {"data": {}, "fit": {}, "at": NOW, "issue_url": PART_URL}}

    _run(ports, state, _data(body=LEAK))

    assert calls == [] and inner.opened_issues == [] and inner.created_labels == []


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
    source = Path("agentic/core/process/application/plan.py").read_text()
    assert "apply_carve" not in source
