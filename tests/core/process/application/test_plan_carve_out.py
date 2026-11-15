"""`plan_carve_out.carved` against fake `PlanSteps` and memory ports: what `plan()` cannot reach
alone (the pending record at node entry, the pause a tracker failure leaves) and the carve-out's
own seam, one scripted role call at most, never a tracker call before the remainder is judged."""

import asyncio
import functools
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application.plan_carve_out import (
    Judged,
    PlanSteps,
    _closing_block,
    _left,
    carved,
)
from agentic.core.process.application.ports import IssueState
from agentic.core.process.application.run_role import Output
from agentic.core.process.domain.schemas import FitDecision
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryInbox, MemoryTracker, RawResult, ScriptedRunner, memory_ports


def _sync(test: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """The suite runs coroutines with `asyncio.run`: this makes an async test one."""

    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


NOW = "2026-11-14T10:00:00+00:00"
PART_OF = ("GH-700", 2, 2)
ORIGINAL_FIT = FitDecision(False, 90.0, 8, 120.0)
SECTION = "### Solution\n\nHex.\n\n### Subtasks\n\n#### a\n\nBuild a.\n\n#### b\n\nBuild b."
PLAN: Any = {
    "integration": "wire a and b",
    "estimated_cost_usd": 90.0,
    "estimated_files": 8,
    "required_paths": ["src/a.py", "src/b.py"],
    "ci_required": ["tests", "docs"],
    "subtasks": [
        {"id": "a", "title": "A", "brief": "Build a.", "seams": [], "depends_on": []},
        {"id": "b", "title": "B", "brief": "Build b.", "seams": [], "depends_on": []},
    ],
}
STATE: Any = {
    "id": "GH-732",
    "title": "Part two",
    "description": "Carve it.",
    "cost_usd": 1.0,
    "sections": {"Tech Lead": SECTION},
}
DELIVERED: Any = {**STATE, "subtasks": {"a": {}, "b": {}}, "status": "planned"}
PAUSE: Any = {**DELIVERED, "escalation_class": "estimate", "fit_question": True}


def _carve(**over: Any) -> dict[str, Any]:
    carve = {
        "steps": ["b"],
        "title": "Carved steps",
        "body": "Does the rest.",
        "kind": "change",
        "moved_criteria": ["it works"],
        "estimated_cost_usd": 10.0,
        "estimated_files": 3,
        "hand_files": 2,
        "hand_lines": 60,
        "test_lines": 40,
        "required_paths": ["src/a.py"],
        "ci_required": ["tests"],
        "integration": "wire a",
        "remainder_green": "the app builds",
        "guard": "nothing calls it",
    }
    return {**carve, **over}


def _refusing(data: Any) -> None:
    raise ValueError("the plan check refuses")


class Steps:
    """Fake `PlanSteps`: the remainder fits when it keeps at most `keeps` subtasks."""

    def __init__(self, tmp_path: Path, keeps: int = 1, check: Any = None) -> None:
        self.keeps, self.landed, self.judged = keeps, [], []
        self.bundle = PlanSteps(
            judge_remainder=self.judge,
            land=self.land,
            check=check,
            part_of=PART_OF,
            cfg=Configuration(product_dir=tmp_path),
            fit=ORIGINAL_FIT,
        )

    def judge(self, rest: Any, state: Any) -> Judged:
        self.judged.append(state["cost_usd"])
        fits = len(rest["subtasks"]) <= self.keeps
        fit = FitDecision(fits, rest["estimated_cost_usd"], rest["estimated_files"], None)
        return Judged(Output(text="", data=dict(rest)), fit, "the floor")

    def land(self, state: Any, base: Any, judged: Judged, written: Any) -> Any:
        self.landed.append((base, judged, written))
        return {**base, "subtasks": {"a": {}}, "sections": written["sections"]}


def _ports(runner: Any = None, tracker: Any = None, inbox: Any = None) -> Any:
    tracker = tracker if tracker is not None else MemoryTracker()
    tracker.issue_numbers_from = 800
    tracker.inbox = inbox if inbox is not None else MemoryInbox()
    tracker.states[732] = IssueState("OPEN", "", None, None, ("bug",))
    return memory_ports(runner=runner, tracker=tracker, inbox=tracker.inbox, now=lambda: NOW)


def _role(**over: Any) -> ScriptedRunner:
    return ScriptedRunner(
        tech_lead_carve_out=[RawResult({**_carve(**over), "markdown": "Moved b."}, 0.2, 2, "cv")]
    )


async def _run(steps: Steps, ports: Any) -> Any:
    output = Output(text="", data=dict(PLAN))
    return await carved(STATE, DELIVERED, PAUSE, output, steps.bundle, ports)


def _pending(**record: Any) -> Any:
    saved = {
        "data": {"carve": _carve(), "plan_output": PLAN},
        "fit": {"fits": False, "cost_usd": 90.0, "files": 8, "reference_usd": 120.0},
        "at": NOW,
        "issue_url": "https://github.com/o/r/issues/800",
        "repointed_at": NOW,
        "commented_at": NOW,
    }
    return {**STATE, "carve_out": {**saved, **record}}


@_sync
async def test_a_judged_without_a_fit_question_comes_back_unchanged_with_no_role_call(
    tmp_path: Path,
) -> None:
    runner, judged = ScriptedRunner(), {**DELIVERED, "escalation_class": "estimate"}
    steps = Steps(tmp_path)
    output = Output(text="", data=dict(PLAN))

    kept = await carved(STATE, DELIVERED, judged, output, steps.bundle, _ports(runner))
    evaluating = {**STATE, "evaluation": True}
    paused = await carved(evaluating, DELIVERED, PAUSE, output, steps.bundle, _ports(runner))

    assert kept is judged and paused is PAUSE
    assert runner.calls == [] and steps.landed == []


@_sync
async def test_a_valid_carve_out_is_applied_then_landed_from_delivered_without_its_subtasks(
    tmp_path: Path,
) -> None:
    runner, steps, tracker = _role(), Steps(tmp_path), MemoryTracker()

    patch = await _run(steps, _ports(runner, tracker))

    [(base, judged, written)] = steps.landed
    assert runner.calls[0]["resume"] is None and len(runner.calls) == 1
    assert len(tracker.opened_issues) == 1
    assert "subtasks" not in base and base["status"] == "planned"
    assert base["cost_usd"] == pytest.approx(1.2) and base["carve_out"]["outcome"] == "applied"
    assert judged.floor == "the floor" and steps.judged[0] == pytest.approx(2.2)
    text = written["sections"]["Tech Lead"]
    assert "### Carved out -> #800" in text and "`src/b.py`" in text and "`docs`" in text
    assert "#### B" not in text and "#### A" in text and "Build b." not in text
    assert patch["subtasks"] == {"a": {}} and "escalation_class" not in patch


@_sync
@pytest.mark.parametrize("case", ["refused text", "does not fit", "check", "role"])
async def test_every_failure_closes_the_record_as_failed_and_pauses_before_any_tracker_call(
    tmp_path: Path, case: str
) -> None:
    runner = {
        "refused text": _role(body="Closes #1"),
        "does not fit": _role(),
        "check": _role(),
        "role": ScriptedRunner(tech_lead_carve_out=[RawResult({"steps": 1}, 0.3, 1, "cv")] * 9),
    }[case]
    steps = Steps(tmp_path, keeps=0 if case == "does not fit" else 1)
    if case == "check":
        steps = Steps(tmp_path, check=_refusing)
    tracker = MemoryTracker()

    patch = await _run(steps, _ports(runner, tracker))

    assert patch["carve_out"] == {"outcome": "failed"} and "subtasks" not in patch
    assert patch["escalation_class"] == "estimate" and patch["fit_question"] is True
    assert tracker.opened_issues == [] and tracker.created_labels == [] and steps.landed == []
    assert ("For the remainder" in patch["escalated"]) == (case != "role")
    assert patch["cost_usd"] > 1.0 and patch["status"] == "waiting_on_human"


@_sync
async def test_a_pending_record_lands_the_remainder_without_a_role_or_a_check(
    tmp_path: Path,
) -> None:
    runner, steps = ScriptedRunner(), Steps(tmp_path, check=_refusing)

    patch = await carved(_pending(), {}, {}, None, steps.bundle, _ports(runner))

    [(base, _, written)] = steps.landed
    assert runner.calls == [] and base["carve_out"]["outcome"] == "applied"
    assert "### Carved out -> #800" in written["sections"]["Tech Lead"]
    assert patch["subtasks"] == {"a": {}}


@_sync
async def test_a_pending_remainder_that_no_longer_fits_keeps_applied_and_queues_nothing(
    tmp_path: Path,
) -> None:
    steps = Steps(tmp_path, keeps=0)

    patch = await carved(_pending(), {}, {}, None, steps.bundle, _ports(ScriptedRunner()))

    assert patch["carve_out"]["outcome"] == "applied" and "subtasks" not in patch
    assert patch["escalation_class"] == "estimate" and patch["fit_question"] is True
    assert "For the remainder" in patch["escalated"] and steps.landed == []


@_sync
async def test_a_tracker_failure_pauses_with_no_fit_question_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    class Cut(MemoryInbox):
        def open_issue_refs(self) -> Any:
            raise OSError("boom")

    steps = Steps(tmp_path)
    saved = _pending()
    del saved["carve_out"]["repointed_at"], saved["carve_out"]["commented_at"]
    ports = _ports(ScriptedRunner(), inbox=Cut())

    patch = await carved(saved, {}, {}, None, steps.bundle, ports)

    assert patch["escalation_class"] == "failure" and "fit_question" not in patch
    assert patch["carve_out"] == saved["carve_out"] and steps.landed == []


@pytest.mark.parametrize(
    ("original", "kept"),
    [
        (["a", "b", "c"], ["a", "c"]),
        (["a", "b", "c"], ["c", "a"]),
        (["a", "b"], []),
        (["x"], ["x"]),
        ([], []),
    ],
)
def test_what_left_is_disjoint_from_the_remainder_and_with_it_makes_the_original(
    original: list[str], kept: list[str]
) -> None:
    left = _left(original, kept)
    assert not set(left) & set(kept) and set(left) | set(kept) == set(original)
    assert left == [entry for entry in original if entry in left]
    record: Any = {
        "issue_url": "https://x/issues/9",
        "data": {
            "carve": {"steps": ["b"]},
            "plan_output": {"required_paths": original, "ci_required": original},
        },
    }
    block = _closing_block(record, {"required_paths": kept, "ci_required": kept})
    shown = ", ".join(f"`{entry}`" for entry in left) or "none"
    assert block.startswith("### Carved out -> #9\n\nSubtasks moved to the follow-up: `b`.")
    assert f"required_paths that left: {shown}." in block
    assert block.endswith(f"ci_required that left: {shown}.")
