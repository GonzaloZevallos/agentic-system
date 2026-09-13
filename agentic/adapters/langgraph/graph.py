"""Feature path, first slice: PjM -> Product Analyst -> PM, with the PM→Product
Analyst return loop and escalation to the human.

Each node reads the configuration from its `config`, builds the ports for it and
hands both to a use case (README.md, 14: every node reads the configuration at the
start). The rules live in the use cases; this module only wires them, opens the
role's span and maps the domain's routing to graph edges.

Escalation ends the run with `status: waiting_on_human` for now. `interrupt()`
and `agentic resume` come with the checkpointer (ADR-0003) and will live here.

`build_graph` takes the ports factory; the wired, uncompiled graph is exported by
`agentic.composition` for `langgraph dev`, and the CLI compiles it.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ...application.classify import classify
from ...application.define import define
from ...application.ports import Ports
from ...application.validate_definition import validate_definition
from ...domain import rules
from ...domain.configuration import Configuration
from ...domain.roles import PJM, PM, PRODUCT_ANALYST, Role
from ...domain.task import TaskInput, TaskState
from ..otel import tracing

UseCase = Callable[[TaskState, Configuration, Ports], Awaitable[TaskState]]
PortsFor = Callable[[Configuration], Ports]


def configuration_from(config: RunnableConfig | None) -> Configuration:
    configurable = (config or {}).get("configurable") or {}
    return Configuration.from_sources(configurable, os.environ)


def _node(use_case: UseCase, role: Role, ports_for: PortsFor):
    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        with tracing.role_span(role.name):
            return await use_case(state, cfg, ports_for(cfg))

    return node


def _route(rule: Callable[[TaskState], str | None]) -> Callable[[TaskState], str]:
    return lambda state: rule(state) or END


def build_graph(ports_for: PortsFor) -> StateGraph:
    graph = StateGraph(TaskState, input_schema=TaskInput)
    graph.add_node("classify", _node(classify, PJM, ports_for))
    graph.add_node("define", _node(define, PRODUCT_ANALYST, ports_for))
    graph.add_node("validate", _node(validate_definition, PM, ports_for))

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", _route(rules.after_classify), ["define", END])
    graph.add_conditional_edges("define", _route(rules.after_define), ["validate", END])
    graph.add_conditional_edges("validate", _route(rules.after_validate), ["define", END])
    return graph
