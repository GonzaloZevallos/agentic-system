"""The important pass before the pull request, through the compiled task graph (GH-354,
ADR-0053 amendment): a review that approves with an `important` row open sends the task to
`fix_important`, the owning unit is built and reviewed again, and only then do `full_checks`
and `deliver` run. A task with no such row takes the route it always took."""

import pytest

from agentic.core.process.domain import returns
from tests.core.process.adapters.langgraph.test_graph import TASK, script_for, technical
from tests.core.process.adapters.langgraph.test_graph_full_checks import (
    CHECKS,
    FAST,
    build,
    commands_run,
    config,  # noqa: F401  (the fixture)
)
from tests.fakes import MemoryCommands, MemoryStack, delivered, finding, reviewed

pytestmark = pytest.mark.anyio


def _important(scope="subtask"):
    return reviewed("approved", findings=[finding("important", claim="late find", scope=scope)])


async def test_approve_with_an_important_row_fixes_it_and_reviews_again_before_the_checks(config):  # noqa: F811
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = script_for(
        **technical(
            developer=[delivered(), delivered()],
            tech_lead_review=[_important(), reviewed("approved")],
        )
    )
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(
        TASK,
        config=config,
    )

    slugs = [c["slug"] for c in script.calls]
    assert slugs.count("developer") == 2 and slugs.count("tech_lead_review") == 2
    # The suite ran once, on the head that was delivered, after the second review.
    assert commands_run(commands).count("playwright") == 1
    assert final["status"] == "ready_for_review"
    [entry] = final["landing"]
    assert entry["step"] == "fix_important" and entry["before_pr"] is True
    assert entry["resolved_at"]
    # Not a return: the history entry is the landing's own and nothing counts against the pair.
    assert [returns.is_return(h) for h in final["change_history"]] == [False]
    assert not any(final.get("changes_requested", {}).values())
    assert final["full_checks"]["passed"] is True


async def test_the_pass_runs_once_even_when_the_second_review_raises_the_row_again(config):  # noqa: F811
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = script_for(
        **technical(
            developer=[delivered(), delivered()],
            tech_lead_review=[_important(), _important()],
        )
    )
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(
        TASK,
        config=config,
    )

    slugs = [c["slug"] for c in script.calls]
    assert slugs.count("developer") == 2 and slugs.count("tech_lead_review") == 2
    assert final["status"] == "ready_for_review"
    assert len(final["landing"]) == 1


async def test_a_task_with_no_important_row_goes_the_route_it_went_before(config):  # noqa: F811
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    graph, fakes, script = build(script_for(**technical()), stack, commands)
    final = await graph.ainvoke(
        TASK,
        config=config,
    )

    assert [c["slug"] for c in script.calls].count("developer") == 1
    assert "landing" not in final
    assert final["status"] == "ready_for_review"


async def test_an_outside_row_alone_does_not_start_the_pass(config):  # noqa: F811
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = script_for(**technical(tech_lead_review=[_important("outside")]))
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(
        TASK,
        config=config,
    )

    assert [c["slug"] for c in script.calls].count("developer") == 1
    assert "landing" not in final
