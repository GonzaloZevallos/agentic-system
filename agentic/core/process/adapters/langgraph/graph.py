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

The technical stage is the `pipeline` node around the `integrate` node (ADR-0004, fifth
addendum, whose waves GH-364 replaced). `pipeline` runs the scheduling loop
(`application/schedule.py`): each subtask is `develop -> review`, a graph of its own, started
the moment `rules.next_to_start` allows it and reviewed the moment its Developer delivers,
with the system's turn (`integrate_turn`) after every termination. `integrate` still enters
the stage and leaves it: it is where the workspace is touched alone before the first
subtask starts, and where the loop's result comes back to be routed.

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
from langgraph.types import interrupt

from agentic.shared.configuration import Configuration

from ...application.classify import classify
from ...application.copy import copy_review, write_copy
from ...application.define import define
from ...application.deliver import deliver
from ...application.design import consolidate, design_review, ui_screens, ux_flow, ux_walk
from ...application.develop import develop
from ...application.diagnose import diagnose
from ...application.discard import discard
from ...application.escalate import escalate
from ...application.final_validation import final_validation
from ...application.full_checks import full_checks
from ...application.integrate import integrate, integrate_turn
from ...application.limits import RunLimits
from ...application.plan import plan
from ...application.ports import Ports
from ...application.preflight import product_name
from ...application.qa_performance import qa_performance
from ...application.review import review
from ...application.schedule import run_subtasks
from ...application.split import split
from ...application.tracker_only import tracker_only
from ...application.validate_prd import validate_prd
from ...domain import rules
from ...domain.roles import (
    DEVELOPER,
    PJM,
    PM,
    PM_CONSOLIDATOR,
    PM_FINAL,
    PRODUCT_ANALYST,
    QA_PERFORMANCE,
    TECH_LEAD,
    TECH_LEAD_REVIEWER,
    TECH_WRITER,
    TECH_WRITER_REVIEW,
    UI_DESIGNER,
    UI_LEAD,
    UX_DESIGNER,
    UX_LEAD,
    Role,
)
from ...domain.task import TaskInput, TaskState
from ..telemetry import NoTelemetry, Telemetry

UseCase = Callable[[TaskState, Configuration, Ports], Awaitable[TaskState]]
PortsFor = Callable[[Configuration], Ports]
Rule = Callable[[TaskState], str | None]
# The live arm string for a node's own `Configuration` (`metrics.arm_label`, ADR-0030):
# injected rather than computed here, the same reason `ports_of` is. `core.process` may
# import only `shared` (this module's own layer rule, `tests/test_architecture.py`), and
# `metrics.arm_label` lives in `core.measurement`; the composition root, which sees both,
# builds the one callable that closes over it and hands it down, so this module never
# imports measurement and the arm string still has exactly one producer.
ArmFor = Callable[[Configuration], str]

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


PortsOf = Callable[[Configuration, RunnableConfig | None], Ports]


def _node(use_case: UseCase, role: Role, ports_of: PortsOf, telemetry: Telemetry, arm_for: ArmFor):
    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)
        ports = ports_of(cfg, config)
        # The patch is what the use case decided; the span and the metrics read it
        # here, so the use case never sees telemetry (ADR-0007). Which product the run is
        # over is read per node and not cached: `langgraph dev` serves many tasks through
        # one graph and they need not share a product (issue #47). `arm` is read the same
        # way, off this node's own `cfg` (`Configuration`), never the patch or the state
        # (ADR-0030): a Developer of a non-default arm must read as that arm, not `default`.
        with telemetry.role_span(
            role.name, role.slug, product_name(ports), arm=arm_for(cfg)
        ) as run:
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
    """Wait for the human; resumed with `Command(resume={"decision": text or None})`, plus
    `"estimate": USD` when the human gave `--estimate` (the estimate to put in force when the
    task paused at the cap of its estimate)."""
    answer = interrupt(
        {
            "reason": state.get("escalated"),
            "spent_usd": state.get("cost_usd", 0.0),
            "questions": state.get("questions_for_human", []),
        }
    )
    answer = answer or {}
    return rules.resumed(state, answer.get("decision"), answer.get("estimate"))


def _route(rule: Rule) -> Callable[[TaskState], str]:
    def route(state: TaskState) -> str:
        target = rule(state)
        if target is not None:
            return target
        return ESCALATE if state.get("escalated") else END

    return route


def _in_branch(rule: Rule) -> Callable[[TaskState], str]:
    """Routing inside a `pipeline` branch. A branch never goes to `escalate`: it ends,
    and the fan-in tells the human once, after every branch came back."""

    def route(state: TaskState) -> str:
        return END if state.get("escalated") else (rule(state) or END)

    return route


def _pipeline(ports_of: PortsOf, telemetry: Telemetry, arm_for: ArmFor):
    """`develop -> review` for one subtask, as a graph of its own. It enters at the
    review when the Developer already delivered, which is where a run the task cap
    stopped between the two resumes: a delivery is never built twice."""
    branch = StateGraph(TaskState)
    branch.add_node("develop", _node(develop, DEVELOPER, ports_of, telemetry, arm_for))
    # The node's own role names the role span: `reviewer_for` builds the actual role
    # the use case runs (allowed commands per product), but its slug never changes, so
    # the span and `agentic.role.*` can tell a review from a plan without reading the
    # patch (the 58% of spend the request opens with, all labelled `tech_lead`).
    branch.add_node("review", _node(review, TECH_LEAD_REVIEWER, ports_of, telemetry, arm_for))
    branch.add_conditional_edges(START, rules.pipeline_entry, ["develop", "review"])
    branch.add_conditional_edges("develop", _in_branch(rules.after_develop), ["review", END])
    branch.add_edge("review", END)
    return branch.compile(checkpointer=False)


BRANCH_DOCUMENTS = "branch_documents"


def _branch_ports(ports_of: PortsOf) -> PortsOf:
    """`ports_of` for the nodes inside a branch: the same ports, with the documents the
    loop gave the branch (`FoldingDocuments.branch`) in place of the real ones, so that a
    branch's save is folded with its sisters' instead of replacing them. They travel in
    the branch's `configurable`, the only thing a node of the branch graph is handed."""

    def ports(cfg: Configuration, config: RunnableConfig | None) -> Ports:
        configurable = (config or {}).get("configurable") or {}
        if BRANCH_DOCUMENTS not in configurable:
            # A branch node run outside the loop would save through the real documents
            # and erase its sisters' work: refuse instead of falling back.
            raise RuntimeError(f"a pipeline branch needs the loop's {BRANCH_DOCUMENTS!r}")
        return replace(ports_of(cfg, config), documents=configurable[BRANCH_DOCUMENTS])

    return ports


def _branch_node(ports_of: PortsOf, telemetry: Telemetry, arm_for: ArmFor):
    """The technical stage as one node of the parent graph (GH-364): the scheduling loop
    (`run_subtasks`) runs each subtask's `develop -> review` as soon as
    `rules.next_to_start` allows it, with the system's turn after every termination. The
    node hands back what the loop added, not the state it ended holding: the parent folds a
    patch in with the same reducers, and the whole state would count `cost_usd` twice and
    duplicate every `runs`, `failures` and `change_history` entry (`task.delta`)."""
    pipeline = _pipeline(_branch_ports(ports_of), telemetry, arm_for)

    async def node(state: TaskState, config: RunnableConfig) -> TaskState:
        cfg = configuration_from(config)

        async def run_branch(branch_state: TaskState, branch_ports: Ports) -> TaskState:
            configurable = {
                **(config.get("configurable") or {}),
                BRANCH_DOCUMENTS: branch_ports.documents,
            }
            return await pipeline.ainvoke(branch_state, {**config, "configurable": configurable})

        return await run_subtasks(
            state,
            cfg,
            ports_of(cfg, config),
            run_branch,
            integrate_turn,
            cfg.parallel_subtasks,
            rules.next_to_start,
        )

    return node


def build_graph(
    ports_for: PortsFor, telemetry: Telemetry = NoTelemetry(), *, arm_for: ArmFor
) -> StateGraph:
    # One `RunLimits` per run: every branch of a fan-out shares it, and sharing is the
    # whole point (ADR-0004, fifth addendum). It is not state and not a port, so it
    # rides in `Ports` without the branches ever seeing each other.
    shared = _PortsPerRun(ports_for)

    graph = StateGraph(TaskState, input_schema=TaskInput)
    graph.add_node("classify", _node(classify, PJM, shared, telemetry, arm_for))
    graph.add_node("define", _node(define, PRODUCT_ANALYST, shared, telemetry, arm_for))
    graph.add_node("diagnose", _node(diagnose, PRODUCT_ANALYST, shared, telemetry, arm_for))
    graph.add_node("validate", _node(validate_prd, PM, shared, telemetry, arm_for))
    # The definition-side design branch (ADR-0040): only reached on a `touches_ui`
    # feature, through `rules.design_step` (`after_validate`/`resume_target`/each
    # node's own `after_*`), never wired to any other node directly.
    graph.add_node("ux_flow", _node(ux_flow, UX_DESIGNER, shared, telemetry, arm_for))
    graph.add_node("ux_walk", _node(ux_walk, UX_LEAD, shared, telemetry, arm_for))
    graph.add_node("ui_screens", _node(ui_screens, UI_DESIGNER, shared, telemetry, arm_for))
    graph.add_node("design_review", _node(design_review, UI_LEAD, shared, telemetry, arm_for))
    graph.add_node("consolidate", _node(consolidate, PM_CONSOLIDATOR, shared, telemetry, arm_for))
    # The texts (README.md, 4): `write_copy` is the last rung of `rules.design_step`, so it is
    # reached by the same routing as the design nodes and goes on to the plan.
    graph.add_node("write_copy", _node(write_copy, TECH_WRITER, shared, telemetry, arm_for))
    graph.add_node("plan", _node(plan, TECH_LEAD, shared, telemetry, arm_for))
    graph.add_node(PIPELINE, _branch_node(shared, telemetry, arm_for))
    graph.add_node(INTEGRATE, _system_node(integrate, shared))
    # PM's early, UI-only final validation (ADR-0040): only reached through
    # `rules.final_validation_due`, from `after_integrate`. One node, one span, labelled
    # by its own final decision-maker (`PM_FINAL`) -- the same shape `define`/`plan`
    # already use for a node that runs more than one role in its own session
    # (an interview's interviewer and interviewee, here UX Lead's and UI Lead's own
    # final counterparts): the role spans in this graph are one per node, not one per
    # `run_role` call, and `PM_FINAL` is the one that decides this node's own verdict.
    graph.add_node(
        "final_validation", _node(final_validation, PM_FINAL, shared, telemetry, arm_for)
    )
    # Tech Writer's review of the texts in the integrated piece: reached from
    # `after_integrate` through `rules.copy_review_due`, back to the fan-in like the final
    # validation, with no return of its own.
    graph.add_node(
        "copy_review", _node(copy_review, TECH_WRITER_REVIEW, shared, telemetry, arm_for)
    )
    # QA Performance (GH-318, ADR-0060): reached through `rules.qa_performance_due`, from
    # `after_integrate`, back to the fan-in like the final validation. Never visited by a
    # plan that convened no performance.
    graph.add_node(
        "qa_performance", _node(qa_performance, QA_PERFORMANCE, shared, telemetry, arm_for)
    )
    # The whole suite, once, on the integrated head (GH-358): no role and no model cost, a
    # system node like `deliver`. Reached through `rules.full_checks_due` from
    # `after_integrate`, only for a product that declares `delivery_checks`, and always back to
    # the fan-in.
    graph.add_node("full_checks", _system_node(full_checks, shared))
    graph.add_node("deliver", _system_node(deliver, shared))
    graph.add_node("discard", _system_node(discard, shared))
    graph.add_node("split", _system_node(split, shared))
    graph.add_node("tracker_only", _system_node(tracker_only, shared))
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
        ["define", "diagnose", "ux_flow", "write_copy", "plan", "discard", ESCALATE],
    )
    graph.add_conditional_edges(
        "ux_flow", _route(rules.after_ux_flow), ["ux_walk", "write_copy", "plan", ESCALATE]
    )
    graph.add_conditional_edges(
        "ux_walk",
        _route(rules.after_ux_walk),
        ["ux_flow", "ui_screens", "write_copy", "plan", ESCALATE],
    )
    graph.add_conditional_edges(
        "ui_screens",
        _route(rules.after_ui_screens),
        ["design_review", "write_copy", "plan", ESCALATE],
    )
    graph.add_conditional_edges(
        "design_review",
        _route(rules.after_design_review),
        ["ui_screens", "consolidate", "write_copy", "plan", ESCALATE],
    )
    graph.add_conditional_edges(
        "consolidate",
        _route(rules.after_consolidate),
        ["define", "ux_flow", "write_copy", "plan", ESCALATE],
    )
    graph.add_conditional_edges("write_copy", _route(rules.after_write_copy), ["plan", ESCALATE])
    graph.add_conditional_edges(
        "plan", _route(rules.after_plan), [INTEGRATE, "split", "tracker_only", ESCALATE]
    )
    graph.add_conditional_edges("split", _route(rules.after_split), [END, ESCALATE])
    graph.add_conditional_edges("tracker_only", _route(rules.after_tracker_only), [END, ESCALATE])
    # The loop comes back here once, when no subtask is left to start or running.
    graph.add_edge(PIPELINE, INTEGRATE)
    graph.add_conditional_edges(
        INTEGRATE,
        _route(rules.after_integrate),
        [
            PIPELINE,
            "full_checks",
            "copy_review",
            "final_validation",
            "qa_performance",
            "deliver",
            ESCALATE,
            END,
        ],
    )
    graph.add_conditional_edges(
        "full_checks", _route(rules.after_full_checks), [INTEGRATE, ESCALATE]
    )
    graph.add_conditional_edges(
        "copy_review", _route(rules.after_copy_review), [INTEGRATE, ESCALATE]
    )
    graph.add_conditional_edges(
        "final_validation", _route(rules.after_final_validation), [INTEGRATE, ESCALATE]
    )
    graph.add_conditional_edges(
        "qa_performance", _route(rules.after_qa_performance), [INTEGRATE, ESCALATE]
    )
    graph.add_conditional_edges("deliver", _route(rules.after_deliver), [INTEGRATE, END, ESCALATE])
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
            "ux_flow",
            "ux_walk",
            "ui_screens",
            "design_review",
            "consolidate",
            "write_copy",
            "plan",
            INTEGRATE,
            "deliver",
            "discard",
            "split",
            "tracker_only",
            ESCALATE,
            END,
        ],
    )
    return graph
