"""Every role prompt, rendered through the real entry point that loads it.

`roles.render` is strict: a `{{placeholder}}` with no value raises `ValueError` from
`load_prompt`, before the retry loop. The older tests render through the
`preflight.PLACEHOLDERS` superset, which supplies every name, so a call site that forgets
one (#293 added `{{cost_history}}` to the Tech Lead's prompts) passed them all. These
tests drive the application functions themselves, over the system's shipped prompt files
read by the real `FilesystemPrompts`, and assert on the text the runtime received."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.filesystem.prompts import FilesystemPrompts
from agentic.core.process.application.design import design_review, ux_walk
from agentic.core.process.application.final_validation import final_validation
from agentic.core.process.application.interview import interview
from agentic.core.process.application.plan import plan
from agentic.core.process.application.ports import Ports, Prompts, RawResult
from agentic.core.process.application.reflect_run import closed_documents
from agentic.core.process.application.review import review
from agentic.core.process.application.stack_review import stack_review
from agentic.core.process.domain import cost_history, roles
from agentic.core.process.domain.playbook import Playbook
from agentic.core.process.domain.roles import Interview, Role
from agentic.core.process.domain.task import Subtask, TaskState
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryWorkspace,
    ScriptedRunner,
    answers,
    consulted,
    design_findings,
    memory_ports,
    planned,
    questions,
    reviewed,
    stack_proposal,
    tech_answers,
)
from tests.fakes import final_validation as final_verdict

pytestmark = pytest.mark.anyio

TASK: TaskState = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
PLATFORM: TaskState = {**TASK, "type": "platform_enhancement"}

# Who runs on each solution path: the interviewer, the interviewee, the author.
FEATURE_SIDES = ("tech_lead_ephemeral", "tech_lead_interviewee", "tech_lead")
PLATFORM_SIDES = (
    "tech_lead_ephemeral_platform",
    "tech_lead_interviewee_platform",
    "tech_lead_platform",
)


class RecordingPrompts:
    """The real `FilesystemPrompts`, pointed at the shipped prompt files, that notes which
    role slugs had their prompt read. It adds nothing else: the text is the file's."""

    def __init__(self, product_dir: Path):
        self.inner: Prompts = FilesystemPrompts(product_dir)
        self.read: list[str] = []

    def raw(self, role: Role) -> tuple[str, Path]:
        self.read.append(role.slug)
        return self.inner.raw(role)

    def playbook(self, role: Role) -> Playbook:
        return self.inner.playbook(role)


def scripted(queues: dict[str, list[RawResult | Exception]]) -> ScriptedRunner:
    """`ScriptedRunner(**queues)`, for queues named by a computed slug."""
    runner = ScriptedRunner()
    runner.queues = {slug: list(results) for slug, results in queues.items()}
    return runner


def wired(runner: ScriptedRunner, tmp_path: Path) -> tuple[Ports, RecordingPrompts]:
    prompts = RecordingPrompts(tmp_path)
    return memory_ports(runner, prompts=prompts), prompts


def subtask(id_: str, **more: Any) -> Subtask:
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        **more,
    }


def closed(task_id: str, cost: float) -> TaskState:
    return {
        "id": task_id,
        "title": task_id,
        "description": "d",
        "closed_at": "2026-10-01",
        "cost_usd": cost,
        "subtasks": {"s0": subtask("s0")},
    }


def with_history(ports: Ports) -> str:
    """Five closed documents in the port, and the history text they make."""
    ports.documents.saved.extend(closed(f"OLD-{n}", 20.0) for n in range(5))
    text = cost_history.build(closed_documents(ports)).text()
    assert "n=5" in text  # the fixture really produces a history, not the empty note
    return text


def assert_rendered(
    runner: ScriptedRunner, prompts: RecordingPrompts, expected: Iterable[str]
) -> dict[str, str]:
    """Every expected slug's prompt was read and reached the runtime with no `{{` left.
    Returns the system prompt each slug ran with (the last one, for a slug run twice)."""
    texts = {call["slug"]: call["system_prompt"] for call in runner.calls}
    for slug in expected:
        assert slug in prompts.read, f"{slug} was never read through the Prompts port"
        assert slug in texts, f"{slug} never reached the runtime"
    for slug, text in texts.items():
        assert text.strip(), f"{slug} ran with an empty system prompt"
        assert "{{" not in text, f"{slug} kept a placeholder: {text[text.index('{{') :][:40]}"
    return texts


def solution_runner(sides: tuple[str, str, str], reply: Any) -> ScriptedRunner:
    ask, answer, author = sides
    return scripted(
        {
            ask: [questions(1, session="e1"), questions(done=True, session="e1")],
            answer: [reply],
            author: [planned()],
        }
    )


SOLUTIONS = [
    pytest.param(TASK, FEATURE_SIDES, tech_answers(1, session="t1"), id="with_pm"),
    pytest.param(PLATFORM, PLATFORM_SIDES, answers(1, session="t1"), id="platform"),
]


@pytest.mark.parametrize(("task", "sides", "reply"), SOLUTIONS)
async def test_plan_interview_arm_renders_every_prompt(task, sides, reply, tmp_path):
    runner = solution_runner(sides, reply)
    ports, prompts = wired(runner, tmp_path)
    history = with_history(ports)

    patch = await plan(task, Configuration(product_dir=tmp_path), ports)

    texts = assert_rendered(runner, prompts, sides)
    assert patch["status"] == "planned"
    assert history in texts[sides[2]]


@pytest.mark.parametrize(("task", "sides", "reply"), SOLUTIONS)
async def test_plan_cold_arm_renders_author_prompt(task, sides, reply, tmp_path):
    runner = solution_runner(sides, reply)
    ports, prompts = wired(runner, tmp_path)
    history = with_history(ports)
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)

    patch = await plan(task, cfg, ports)

    texts = assert_rendered(runner, prompts, sides[2:])
    assert runner.slugs == [sides[2]]  # the cold arm runs no interview
    assert patch["status"] == "planned"
    assert history in texts[sides[2]]


def scripted_interview(spec: Interview) -> ScriptedRunner:
    """One round, and one consultation where the spec has a consultant."""
    ask, answer = spec.interviewer.slug, spec.interviewee.slug
    rounds = [questions(1, session="q"), questions(done=True, session="q")]
    if spec.consultant is not None:
        return scripted(
            {
                ask: rounds,
                answer: [tech_answers(1, for_pm=["Which date?"], session="a")],
                spec.consultant.slug: [consulted(1, session="c")],
            }
        )
    return scripted({ask: rounds, answer: [answers(1, session="a")]})


@pytest.mark.parametrize("spec", roles.INTERVIEWS, ids=lambda s: s.interviewer.slug)
async def test_interview_renders_every_side(spec, tmp_path):
    runner = scripted_interview(spec)
    ports, prompts = wired(runner, tmp_path)

    transcript, _ = await interview(TASK, spec, Configuration(product_dir=tmp_path), ports)

    assert transcript is not None
    sides = [spec.interviewer.slug, spec.interviewee.slug]
    if spec.consultant is not None:
        sides.append(spec.consultant.slug)
    assert_rendered(runner, prompts, sides)
    assert set(prompts.read) == set(sides)  # the interview reads no prompt beyond its sides


def delivered_state() -> TaskState:
    return {
        **TASK,
        "subtasks": {
            "api": subtask("api", branch="agentic/GH-12.api", base="base1"),
            "domain": subtask("domain", branch="agentic/GH-12.domain", base="base1"),
        },
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: domain": "y"},
        "subtask_id": "api",
    }


@pytest.mark.parametrize(
    ("mode", "slug"), [("full", "tech_lead_review"), ("light", "tech_lead_review_light")]
)
async def test_review_renders_prompt(mode, slug, tmp_path):
    runner = scripted({slug: [reviewed("approved")]})
    ports, prompts = wired(runner, tmp_path)
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode=mode)

    patch = await review(delivered_state(), cfg, ports)

    assert_rendered(runner, prompts, [slug])
    assert runner.slugs == [slug]
    assert patch["subtasks"]["api"]["status"] == "approved"


async def test_stack_review_renders_every_prompt(tmp_path):
    ask, answer, author = (
        "tech_lead_ephemeral_stack",
        "tech_lead_interviewee_stack",
        "tech_lead_stack",
    )
    runner = scripted(
        {
            ask: [questions(1, session="e1"), questions(done=True, session="e1")],
            answer: [answers(1, session="a1")],
            author: [stack_proposal(session="t1")],
        }
    )
    ports, prompts = wired(runner, tmp_path)

    result = await stack_review(
        "stack-2026-10-22", "signals", "deadbeef", Configuration(product_dir=tmp_path), ports
    )

    assert_rendered(runner, prompts, [ask, answer, author])
    assert result.needs_info is False and result.issue_url is not None


def design_state() -> TaskState:
    return {
        **TASK,
        "sections": {
            **TASK["sections"],
            "Product Analyst": "x",
            "UX Designer": "flow",
            "UI Designer": "screens",
        },
    }


Walk = Callable[[TaskState, Configuration, Ports], Coroutine[Any, Any, TaskState]]


@pytest.mark.parametrize(
    ("entry", "slug"), [(ux_walk, "ux_lead"), (design_review, "ui_lead")], ids=["ux", "ui"]
)
async def test_design_widened_renders_prompt(entry: Walk, slug, tmp_path):
    runner = scripted({slug: [design_findings("approved")]})
    ports, prompts = wired(runner, tmp_path)

    await entry(design_state(), Configuration(product_dir=tmp_path), ports)

    assert_rendered(runner, prompts, [slug])
    assert runner.slugs == [slug]


async def test_final_validation_widened_renders_prompt(tmp_path):
    runner = scripted(
        {
            "ux_lead_final": [design_findings("approved")],
            "ui_lead_final": [design_findings("approved")],
            "pm_final": [final_verdict("approved")],
        }
    )
    ports, prompts = wired(runner, tmp_path)

    await final_validation(design_state(), Configuration(product_dir=tmp_path), ports)

    assert_rendered(runner, prompts, ["ux_lead_final", "ui_lead_final", "pm_final"])
    assert runner.slugs == ["ux_lead_final", "ui_lead_final", "pm_final"]
