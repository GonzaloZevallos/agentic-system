"""The paths of README §2. A feature: PjM -> Product Analyst (interviewing PM) -> PM
-> Tech Lead (interviewed by the ephemeral one) -> Developers, one per subtask, in
parallel, each followed straight away by its review -> integration -> delivery. A
platform enhancement skips the definition stage (issue #46). A bug replaces it with
the diagnosis: Product Analyst reproduces and rules, PM validates, and a ruling of
expected behaviour ends the task in `discard` instead of the plan (issue #65).
Returns loop PM->Product Analyst and Tech Lead->Developer; every escalation goes
through `escalate`, which tells the human, and then `pause`, which waits for the
human's answer.

Each node reads the configuration from its `config`, builds the ports for it and
hands both to a use case (README.md, 14: every node reads the configuration at the
start). The rules live in the use cases; this module only wires them, opens and
finishes the role's span and maps the domain's routing to graph edges.

The technical stage is a wave of `pipeline` branches around the `integrate` node
(ADR-0004, fifth addendum). `pipeline` is `develop -> review` for one subtask, a
graph of its own, fanned out with `Send` (one branch per dispatched subtask) so a
review starts as soon as its own Developer delivers instead of waiting for the
slowest of the wave. `integrate` is the fan-in: every branch comes back to it, and
it dispatches the next wave, so a returned subtask is re-reviewed knowing what the
wave as a whole found. It is also where the workspace is touched alone and where a
single node saves the document with every branch's work in it.

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
from dataclasses import replace

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from agentic.shared.configuration import Configuration

from ...application.classify import classify
from ...application.define import define
from ...application.deliver import deliver
from ...application.develop import develop
from ...application.diagnose import diagnose
from ...application.discard import discard
from ...application.escalate import escalate
from ...application.integrate import integrate
from ...application.limits import RunLimits
from ...application.plan import plan
from ...application.ports import Ports
from ...application.preflight import product_name
from ...application.review import review
from ...application.validate_prd import validate_prd
from ...domain import rules
from ...domain.roles import DEVELOPER, PJM, PM, PRODUCT_ANALYST, TECH_LEAD, TECH_LEAD_REVIEWER, Role
from ...domain.task import TaskInput, TaskState, delta
from ..telemetry import NoTelemetry, Telemetry

UseCase = Callable[[TaskState, Configuration, Ports], Awaitable[TaskState]]
PortsFor = Callable[[Configuration], Ports]
Rule = Callable[[TaskState], str | None]

ESCALATE = "escalate"
PAUSE = "pause"
PIPELINE = "pipeline"
INTEGRATE = "integrate"


def configuration_from(config: RunnableConfig | None) -> Configuration:
    configurable = (config or {}).get("configurable") or {}
    return Configuration.from_sources(configurable, os.environ)


class _PortsPerRun:
    """The ports of a node, with the run's `RunLimits` in them.

    `RunLimits` has to be the same object for every branch of a fan-out and a different
    one for every task: the graph is built once, at import (`agentic.composition`), and
    a `langgraph dev` server runs every task through it, so one per graph would hand the
    second task the first one's spend. One per thread id, which is the task id
    (ADR-0003). The CLI is one run per process and never sees the difference."""

    def __init__(self, ports_for: PortsFor):
        self.ports_for = ports_for
        self.limits: dict[str, RunLimits] = {}

    def __call__(self, cfg: Configuration, config: RunnableConfig | None) -> Ports:
        thread = str(((config or {}).get("configurable") or {}).get("thread_id", ""))
        limits = self.limits.setdefault(thread, RunLimits())
        return replace(self.ports_for(cfg), limits=limits.sized(cfg.max_concurrent_roles))


PortsOf = _PortsPerRun


def _node(use_case: UseCase, role: Role, ports_of: PortsOf, telemetry: Telemetry):
    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        ports = ports_of(cfg, config)
        # The patch is what the use case decided; the span and the metrics read it
        # here, so the use case never sees telemetry (ADR-0007). Which product the run is
        # over is read per node and not cached: `langgraph dev` serves many tasks through
        # one graph and they need not share a product (issue #47).
        with telemetry.role_span(role.name, role.slug, product_name(ports)) as run:
            try:
                patch = await use_case(state, cfg, ports)
            except Exception:
                # Nothing was delivered; the span still closes with an outcome so the
                # role's metrics do not silently miss the run.
                run.finish({})
                raise
            run.finish(patch)
            return patch

    return node


def _system_node(use_case: UseCase, ports_of: PortsOf):
    """A node where no role runs: the fan-in, the delivery, the escalation. No role
    span, because no role ran."""

    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        return await use_case(state, cfg, ports_of(cfg, config))

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
    """Like `_route`, but `pipeline` becomes one `Send` per dispatched subtask."""
    route = _route(rule)

    def fanout(state: TaskState) -> str | list[Send]:
        target = route(state)
        if target != PIPELINE:
            return target
        return [
            Send(PIPELINE, {**state, "subtask_id": sid}) for sid in rules.dispatched_subtasks(state)
        ]

    return fanout


def _in_branch(rule: Rule) -> Callable[[TaskState], str]:
    """Routing inside a `pipeline` branch. A branch never goes to `escalate`: it ends,
    and the fan-in tells the human once, after every branch came back."""

    def route(state: TaskState) -> str:
        return END if state.get("escalated") else (rule(state) or END)

    return route


def _pipeline(ports_of: PortsOf, telemetry: Telemetry):
    """`develop -> review` for one subtask, as a graph of its own. It enters at the
    review when the Developer already delivered, which is where a run the task cap
    stopped between the two resumes: a delivery is never built twice."""
    branch = StateGraph(TaskState)
    branch.add_node("develop", _node(develop, DEVELOPER, ports_of, telemetry))
    # The node's own role names the role span: `reviewer_for` builds the actual role
    # the use case runs (allowed commands per product), but its slug never changes, so
    # the span and `agentic.role.*` can tell a review from a plan without reading the
    # patch (the 58% of spend the request opens with, all labelled `tech_lead`).
    branch.add_node("review", _node(review, TECH_LEAD_REVIEWER, ports_of, telemetry))
    branch.add_conditional_edges(START, rules.pipeline_entry, ["develop", "review"])
    branch.add_conditional_edges("develop", _in_branch(rules.after_develop), ["review", END])
    branch.add_edge("review", END)
    return branch.compile()


def _branch_node(ports_of: PortsOf, telemetry: Telemetry):
    """The pipeline as one node of the parent graph. It hands back what the branch
    added, not the state it ended holding: the parent folds a patch in with the same
    reducers, and the whole state would count `cost_usd` twice and duplicate every
    `runs`, `failures` and `change_history` entry (`task.delta`)."""
    pipeline = _pipeline(ports_of, telemetry)

    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        return delta(state, await pipeline.ainvoke(state, config))

    return node


def build_graph(ports_for: PortsFor, telemetry: Telemetry = NoTelemetry()) -> StateGraph:
    # One `RunLimits` per run: every branch of a fan-out shares it, and sharing is the
    # whole point (ADR-0004, fifth addendum). It is not state and not a port, so it
    # rides in `Ports` without the branches ever seeing each other.
    shared = _PortsPerRun(ports_for)

    graph = StateGraph(TaskState, input_schema=TaskInput)
    graph.add_node("classify", _node(classify, PJM, shared, telemetry))
    graph.add_node("define", _node(define, PRODUCT_ANALYST, shared, telemetry))
    graph.add_node("diagnose", _node(diagnose, PRODUCT_ANALYST, shared, telemetry))
    graph.add_node("validate", _node(validate_prd, PM, shared, telemetry))
    graph.add_node("plan", _node(plan, TECH_LEAD, shared, telemetry))
    graph.add_node(PIPELINE, _branch_node(shared, telemetry))
    graph.add_node(INTEGRATE, _system_node(integrate, shared))
    graph.add_node("deliver", _system_node(deliver, shared))
    graph.add_node("discard", _system_node(discard, shared))
    graph.add_node(ESCALATE, _system_node(escalate, shared))
    graph.add_node(PAUSE, pause)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify", _route(rules.after_classify), ["define", "plan", "diagnose", ESCALATE]
    )
    graph.add_conditional_edges("define", _route(rules.after_define), ["validate", ESCALATE])
    graph.add_conditional_edges("diagnose", _route(rules.after_diagnose), ["validate", ESCALATE])
    graph.add_conditional_edges(
        "validate",
        _route(rules.after_validate),
        ["define", "diagnose", "plan", "discard", ESCALATE],
    )
    graph.add_conditional_edges("plan", _route(rules.after_plan), [INTEGRATE, ESCALATE])
    # Every branch of the wave comes back here, once, however many were sent.
    graph.add_edge(PIPELINE, INTEGRATE)
    graph.add_conditional_edges(
        INTEGRATE, _fanout(rules.after_integrate), [PIPELINE, "deliver", ESCALATE, END]
    )
    graph.add_conditional_edges("deliver", _route(rules.after_deliver), [END, ESCALATE])
    graph.add_conditional_edges("discard", _route(rules.after_discard), [END, ESCALATE])
    graph.add_edge(ESCALATE, PAUSE)
    graph.add_conditional_edges(
        PAUSE,
        _route(rules.resume_target),
        [
            "classify",
            "define",
            "diagnose",
            "validate",
            "plan",
            INTEGRATE,
            "deliver",
            "discard",
            ESCALATE,
            END,
        ],
    )
    return graph
