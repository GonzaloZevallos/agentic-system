"""LangGraph wiring for the feature path, first slice: PjM -> Product Analyst -> PM.

Context rule from DISENO.md: a role receives only what the previous role wrote
in its "For the next role" block, with the full document kept on disk as backup.
"""
from __future__ import annotations

import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from . import document
from .agent import Output, Role, run
from .roles import PJM, PM, PRODUCT_ANALYST

_HANDOFF_SECTION = re.compile(r"##\s*For the next role\s*\n(.+)", re.S | re.I)


def _accumulate(current: float, new: float) -> float:
    return current + new


class TaskState(TypedDict, total=False):
    id: str
    title: str
    description: str
    type: str
    status: str
    current_role: str
    flags: dict
    escalated: str | None
    sections: dict[str, str]
    handoff: str
    cost_usd: Annotated[float, _accumulate]


def _handoff_of(output: Output) -> str:
    """The block the next role reads. Falls back to the whole text."""
    match = _HANDOFF_SECTION.search(output.text)
    return match.group(1).strip() if match else output.text


def _record(state: TaskState, role: Role, output: Output, **extra) -> TaskState:
    """Build the state patch for a finished role and persist the document.

    `cost_usd` is a reducer field, so the patch carries only this role's cost
    while the document needs the running total.
    """
    sections = dict(state.get("sections", {}))
    sections[role.name] = output.text
    patch: TaskState = {
        "sections": sections,
        "handoff": _handoff_of(output),
        "current_role": role.name,
        "cost_usd": output.cost_usd,
        **extra,
    }
    total = state.get("cost_usd", 0.0) + output.cost_usd
    document.write({**state, **patch, "cost_usd": total})
    return patch


async def classify(state: TaskState) -> TaskState:
    output = await run(PJM, f"Task: {state['title']}\n\n{state['description']}")
    extra: dict = {"type": output.data.get("type", "unknown")}
    # Low confidence is an escalation, not a guess. DISENO.md: PjM never re-routes
    # on its own while a task waits on the human.
    if output.data.get("confidence") == "low":
        extra["escalated"] = "PjM could not classify with confidence"
        extra["status"] = "waiting_on_human"
    return _record(state, PJM, output, **extra)


async def define(state: TaskState) -> TaskState:
    prompt = f"Feature: {state['title']}\n\n{state['description']}\n\nFrom PjM:\n{state['handoff']}"
    return _record(state, PRODUCT_ANALYST, await run(PRODUCT_ANALYST, prompt))


async def validate(state: TaskState) -> TaskState:
    prompt = f"Feature: {state['title']}\n\nFrom the Product Analyst:\n{state['handoff']}"
    output = await run(PM, prompt)
    return _record(state, PM, output, status="definition_ready")


def _after_classify(state: TaskState) -> str:
    if state.get("escalated"):
        return END
    # Only the feature path exists so far; bug and platform_enhancement stop here.
    return "define" if state.get("type") == "feature" else END


def build():
    graph = StateGraph(TaskState)
    graph.add_node("classify", classify)
    graph.add_node("define", define)
    graph.add_node("validate", validate)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", _after_classify, {"define": "define", END: END})
    graph.add_edge("define", "validate")
    graph.add_edge("validate", END)
    return graph.compile()
