"""Feature path, first slice: PjM -> Product Analyst -> PM, with the PM→Product
Analyst return loop and escalation to the human.

Context rule (DISENO.md, 6): each role reads, in full, the sections it declares
in `Role.reads`. Not the previous role's handoff: a validator that only sees a
summary returns over gaps that were never there.

Escalation ends the run with `status: waiting_on_human` for now. `interrupt()`
and `agentic resume` come with the checkpointer (ADR-0003).

The module exports `graph` uncompiled for `langgraph dev`; the CLI compiles it.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from . import document, flags, knowledge, observe
from .agent import Output, RoleFailure, run
from .configuration import Configuration
from .roles import ORIGINAL, PAIR_PM_PA, PJM, PM, PRODUCT_ANALYST, Role
from .state import Status, TaskInput, TaskState

WAITING_ON_HUMAN: Status = "waiting_on_human"


def context_for(state: TaskState, role: Role, product_knowledge: str = "") -> str:
    """The role's input: the product knowledge if the role reads it, then the sections
    it declares, in document order, that exist."""
    sections = state.get("sections", {})
    parts = [product_knowledge] if product_knowledge else []
    for name in role.reads:
        if name == ORIGINAL:
            parts.append(f"## {ORIGINAL}\n\n{state['title']}\n\n{state['description']}")
        elif name in sections:
            parts.append(f"## {name}\n\n{sections[name]}")
    return "\n\n".join(parts)


async def _run_role(
    state: TaskState,
    role: Role,
    config: RunnableConfig,
    check: Callable[[dict], None] | None = None,
    **placeholders: object,
) -> tuple[Output | None, TaskState]:
    """Run a role and build the state patch. On `RoleFailure` the patch is an escalation."""
    cfg = Configuration.from_runnable_config(config)
    product_knowledge = knowledge.read(cfg.product_dir) if role.reads_knowledge else ""
    vocabulary = flags.load(cfg.product_dir)
    loaded = role.prompt(
        cfg.product_dir,
        max_returns=cfg.max_returns_per_pair,
        flags=flags.describe(vocabulary),
        **placeholders,
    )
    patch: TaskState = {"current_role": role.name, "prompts": {role.slug: loaded.hash}}
    try:
        output = await run(
            role,
            context_for(state, role, product_knowledge),
            system_prompt=loaded.text,
            model=cfg.model_for(role.slug, role.kind),
            retries=cfg.role_retries,
            flags=vocabulary,
            cwd=cfg.product_dir,
            check=check,
            observer=observe.Printer(role.name) if cfg.verbose else None,
        )
    except RoleFailure as exc:
        patch |= {
            "failures": exc.failures,
            "cost_usd": exc.cost_usd,
            "escalated": str(exc),
            "status": WAITING_ON_HUMAN,
        }
        _persist(state, patch, cfg)
        return None, patch

    sections = {role.name: output.text}
    version = 1 + sum(1 for r in state.get("runs", []) if r["role"] == role.name)
    if role.name in state.get("sections", {}):
        # A return: the delivery being replaced stays in the document as history.
        sections[f"{role.name} (v{version - 1})"] = state["sections"][role.name]
    patch |= {
        "sections": sections,
        "failures": output.failures,
        "cost_usd": output.cost_usd,
        "runs": [
            {
                "role": role.name,
                "version": version,
                "session_id": output.session_id,
                "model": output.model,
                "cost_usd": output.cost_usd,
                "turns": output.turns,
            }
        ],
    }
    return output, patch


def _persist(state: TaskState, patch: TaskState, cfg: Configuration) -> None:
    """Write the document with the reducers applied by hand: the patch is partial."""
    merged: dict = {**state, **patch}
    merged["cost_usd"] = state.get("cost_usd", 0.0) + patch.get("cost_usd", 0.0)
    for key in ("failures", "runs"):
        merged[key] = state.get(key, []) + patch.get(key, [])
    for key in ("sections", "prompts", "returns"):
        merged[key] = {**state.get(key, {}), **patch.get(key, {})}
    document.write(merged, cfg.product_dir)


async def classify(state: TaskState, config: RunnableConfig) -> TaskState:
    output, patch = await _run_role(state, PJM, config)
    if output is None:
        return patch
    task_type = output.data["type"]
    patch["type"] = task_type
    if output.data["confidence"] == "low":
        # Low confidence is an escalation, not a guess. PjM does not re-route
        # while a task waits on the human (DISENO.md, 3).
        patch["escalated"] = f"PjM could not classify with confidence: {output.data['reason']}"
        patch["status"] = WAITING_ON_HUMAN
    elif task_type != "feature":
        # The task left the inbox and the system cannot carry it yet: the human must see it.
        patch["escalated"] = f"{task_type} path not implemented yet"
        patch["status"] = WAITING_ON_HUMAN
    _persist(state, patch, Configuration.from_runnable_config(config))
    return patch


async def define(state: TaskState, config: RunnableConfig) -> TaskState:
    output, patch = await _run_role(state, PRODUCT_ANALYST, config)
    if output is None:
        return patch
    patch["flags"] = output.data["flags"]
    patch["status"] = "defined"
    _persist(state, patch, Configuration.from_runnable_config(config))
    return patch


async def validate(state: TaskState, config: RunnableConfig) -> TaskState:
    cfg = Configuration.from_runnable_config(config)
    used = state.get("returns", {}).get(PAIR_PM_PA, 0)
    remaining = cfg.max_returns_per_pair - used
    output, patch = await _run_role(
        state,
        PM,
        config,
        check=knowledge.approval_check(state.get("type", "")),
        remaining_returns=remaining,
    )
    if output is None:
        return patch

    patch["flags"] = output.data["flags"]
    if output.data["verdict"] == "approved":
        # The distillation (ADR-0006): PM returned the knowledge, the graph writes it.
        knowledge.write_definitions(
            cfg.product_dir, {d["capability"]: d["content"] for d in output.data["definitions"]}
        )
        knowledge.upsert_terms(
            cfg.product_dir,
            {
                t["term"]: {"definition": t["definition"], "avoid": t["avoid"]}
                for t in output.data["glossary"]
            },
        )
        patch["status"] = "definition_ready"
    elif remaining > 0:
        patch["returns"] = {PAIR_PM_PA: used + 1}
        patch["status"] = "returned"
    else:
        # The pair hit its limit: escalate, always (DISENO.md, 3). PM knew this was
        # its last return, so the reason is written for the human.
        limit = cfg.max_returns_per_pair
        patch["escalated"] = f"{PAIR_PM_PA} hit {limit} returns: {output.data['reason']}"
        patch["status"] = WAITING_ON_HUMAN
    _persist(state, patch, cfg)
    return patch


def after_classify(state: TaskState) -> str:
    return END if state.get("escalated") else "define"


def after_define(state: TaskState) -> str:
    return END if state.get("escalated") else "validate"


def after_validate(state: TaskState) -> str:
    if state.get("escalated"):
        return END
    return "define" if state.get("status") == "returned" else END


def build_graph() -> StateGraph:
    graph = StateGraph(TaskState, input_schema=TaskInput)
    graph.add_node("classify", classify)
    graph.add_node("define", define)
    graph.add_node("validate", validate)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", after_classify, {"define": "define", END: END})
    graph.add_conditional_edges("define", after_define, {"validate": "validate", END: END})
    graph.add_conditional_edges("validate", after_validate, {"define": "define", END: END})
    return graph


graph = build_graph()
