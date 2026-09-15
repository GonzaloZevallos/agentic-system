"""Feature path (README.md, 2): PjM -> Product Analyst (interviewing PM) -> PM ->
Tech Lead (interviewed by the ephemeral one) -> Developers, one per subtask, in
parallel -> Tech Lead's review -> integration -> delivery. Returns loop PM->Product
Analyst and Tech Lead->Developer; every escalation goes through `escalate`, which
tells the human, and then `pause`, which waits for the human's answer.

Each node reads the configuration from its `config`, builds the ports for it and
hands both to a use case (README.md, 14: every node reads the configuration at the
start). The rules live in the use cases; this module only wires them, opens and
finishes the role's span and maps the domain's routing to graph edges. `develop` is
fanned out with `Send`, one node run per ready subtask (ADR-0004), so the trace
shows which Developer built what.

`pause` holds the `interrupt()` (ADR-0003, addendum). A resumed node runs again from
its start, so the interrupt cannot sit in a use case, which would pay the role again,
nor in `escalate`, which would tell the human again: `pause` has no effects, and on
the human's answer it returns the patch that resumes the task, and the routing goes
where the state says the task stopped (`rules.resume_target`). A task seeded from its
document enters the same way, as if `escalate` had just run.

`build_graph` takes the ports factory; the wired, uncompiled graph is exported by
`agentic.composition` for `langgraph dev`, and the CLI compiles it with the
checkpointer, without which `interrupt()` cannot pause.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from agentic.shared.configuration import Configuration

from ...application.classify import classify
from ...application.define import define
from ...application.deliver import deliver
from ...application.develop import develop
from ...application.escalate import escalate
from ...application.plan import plan
from ...application.ports import Ports
from ...application.review import review
from ...application.validate_definition import validate_definition
from ...domain import rules
from ...domain.roles import DEVELOPER, PJM, PM, PRODUCT_ANALYST, TECH_LEAD, Role
from ...domain.task import TaskInput, TaskState
from ..telemetry import NoTelemetry, Telemetry

UseCase = Callable[[TaskState, Configuration, Ports], Awaitable[TaskState]]
PortsFor = Callable[[Configuration], Ports]
Rule = Callable[[TaskState], str | None]

ESCALATE = "escalate"
PAUSE = "pause"


def configuration_from(config: RunnableConfig | None) -> Configuration:
    configurable = (config or {}).get("configurable") or {}
    return Configuration.from_sources(configurable, os.environ)


def _node(use_case: UseCase, role: Role, ports_for: PortsFor, telemetry: Telemetry):
    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        # The patch is what the use case decided; the span and the metrics read it
        # here, so the use case never sees telemetry (ADR-0007).
        with telemetry.role_span(role.name) as run:
            try:
                patch = await use_case(state, cfg, ports_for(cfg))
            except Exception:
                # Nothing was delivered; the span still closes with an outcome so the
                # role's metrics do not silently miss the run.
                run.finish({})
                raise
            run.finish(patch)
            return patch

    return node


def _system_node(use_case: UseCase, ports_for: PortsFor):
    """A node where no role runs (delivery, escalation): no role span."""

    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        return await use_case(state, cfg, ports_for(cfg))

    return node


async def pause(state: TaskState) -> TaskState:
    """Wait for the human; resumed with `Command(resume={"decision": text or None})`."""
    answer = interrupt(
        {
            "reason": state.get("escalated"),
            "spent_usd": state.get("cost_usd", 0.0),
            "questions": state.get("questions_for_human", []),
        }
    )
    return rules.resumed(state, (answer or {}).get("decision"))


def _route(rule: Rule) -> Callable[[TaskState], str]:
    def route(state: TaskState) -> str:
        target = rule(state)
        if target is not None:
            return target
        return ESCALATE if state.get("escalated") else END

    return route


def _fanout(rule: Rule) -> Callable[[TaskState], str | list[Send]]:
    """Like `_route`, but `develop` becomes one `Send` per ready subtask."""
    route = _route(rule)

    def fanout(state: TaskState) -> str | list[Send]:
        target = route(state)
        if target != "develop":
            return target
        return [
            Send("develop", {**state, "subtask_id": sid}) for sid in rules.ready_subtasks(state)
        ]

    return fanout


def build_graph(ports_for: PortsFor, telemetry: Telemetry = NoTelemetry()) -> StateGraph:
    graph = StateGraph(TaskState, input_schema=TaskInput)
    graph.add_node("classify", _node(classify, PJM, ports_for, telemetry))
    graph.add_node("define", _node(define, PRODUCT_ANALYST, ports_for, telemetry))
    graph.add_node("validate", _node(validate_definition, PM, ports_for, telemetry))
    graph.add_node("plan", _node(plan, TECH_LEAD, ports_for, telemetry))
    graph.add_node("develop", _node(develop, DEVELOPER, ports_for, telemetry))
    graph.add_node("review", _node(review, TECH_LEAD, ports_for, telemetry))
    graph.add_node("deliver", _system_node(deliver, ports_for))
    graph.add_node(ESCALATE, _system_node(escalate, ports_for))
    graph.add_node(PAUSE, pause)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", _route(rules.after_classify), ["define", ESCALATE])
    graph.add_conditional_edges("define", _route(rules.after_define), ["validate", ESCALATE])
    graph.add_conditional_edges(
        "validate", _route(rules.after_validate), ["define", "plan", ESCALATE]
    )
    graph.add_conditional_edges("plan", _fanout(rules.after_plan), ["develop", ESCALATE])
    graph.add_conditional_edges("develop", _route(rules.after_develop), ["review", ESCALATE])
    graph.add_conditional_edges(
        "review", _fanout(rules.after_review), ["develop", "deliver", ESCALATE]
    )
    graph.add_conditional_edges("deliver", _route(rules.after_deliver), [END, ESCALATE])
    graph.add_edge(ESCALATE, PAUSE)
    graph.add_conditional_edges(
        PAUSE,
        _fanout(rules.resume_target),
        ["classify", "define", "validate", "plan", "develop", "review", "deliver", ESCALATE, END],
    )
    return graph
