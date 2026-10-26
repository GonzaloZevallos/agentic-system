"""The ubiquitous language (ADR-0009, ADR-0010): every glossary term the code models
has exactly one English name, in the domain of the module whose `CONTEXT.md` defines
the term. The glossaries stay free of identifiers; this table keeps both in step."""

from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints

from agentic.core.knowledge.domain import knowledge, knowledge_index, stack
from agentic.core.measurement.adapters.otel import telemetry
from agentic.core.measurement.domain import metrics, scorecard
from agentic.core.process.application import landing_fix
from agentic.core.process.domain import (
    boot,
    document,
    landing,
    playbook,
    presubmit,
    roles,
    rules,
    schema_guard,
    schemas,
    task,
)
from agentic.shared import configuration
from tests.test_architecture import ALLOWED

CORE = Path(__file__).resolve().parents[1] / "agentic" / "core"

# Glossary term -> the type or module that models it.
TYPES = {
    "Rol": roles.Role,
    "Par": roles.Pair,
    "Tarea": task.TaskState,
    "Fallo de rol": task.Failure,
    "Corrida de rol": task.RoleRun,
    "Documento de tarea": document,
    "Entrevista": roles.Interview,
    "Subtarea": task.Subtask,
    "Conocimiento de producto": knowledge,
    "Estándares de ingeniería": knowledge_index.build_index,
    "Job": knowledge.approval_check,  # the check that a capability names its jobs
    "Retrabajo": metrics.rework_cost_usd,
    "Muestra residual": metrics.residual_cost_usd,
    "Scorecard": scorecard.Scorecard,
    "Hallazgo": schemas.Finding,
    "Dictamen": schemas.Diagnosis,
    "Categoría de devolución": rules.change_category,
    "Tope de tarea": rules.task_cap,
    "Reanudación": rules.resumed,
    "Decisión del humano": rules.human_section,
    "Puerta de entrega": presubmit,
    "Testigo del checkout": presubmit.witness_refusal,
    "Guarda de esquema": schema_guard,
    "Stack del producto": stack,
    "Brief": knowledge.brief_refusal,
    "Reflector": roles.REFLECTOR,
    "Playbook": playbook.Playbook,
    "Lección": playbook.Item,
    "Arranque": boot,
    "Aterrizaje": landing,
    "Cercanía": rules.closeness,
}

# Glossary term -> (the domain module, the name of the constant) that models it, for a term a
# plain value names: `module_of` cannot place an int by itself.
CONSTANTS = {"Cupo reservado para aterrizaje": (rules, "LAND_HEADROOM")}

# Glossary term -> the field of the task state that holds it.
STATE_FIELDS = {
    "Devolución": "changes_requested",
    "Escalado": "escalated",
    "Clase de escalado": "escalation_class",
    "Sección": "sections",
    "Flag": "flags",
    "Rama de tarea": "branch",
    "Pull request de tarea": "pull_request",
    "Pregunta para el humano": "questions_for_human",
}

# Glossary term -> the value of `RoleKind` that names it.
ROLE_KINDS = {"Pensador": "actor", "Validador": "verifier"}

# Glossary term -> a closed-set type that names it but lives in an adapter, not a
# domain module (the concept crosses no port: `cli._invoke` is the only writer and
# `core/measurement`'s adapter the only reader), so `context_of` cannot place it.
ADAPTER_TERMS = {
    "Invocación": telemetry.InvocationKind,
    # The landing step lives in the application layer (`landing_fix`), not in a domain module.
    "Pase final de importantes": landing_fix.fix_important,
}


def module_of(obj) -> str:
    return obj.__name__ if isinstance(obj, ModuleType) else obj.__module__


def context_of(module: str) -> str:
    """The core module a domain identifier belongs to: `agentic.core.<name>.domain...`."""
    parts = module.split(".")
    assert parts[:2] == ["agentic", "core"] and parts[3] == "domain", module
    return parts[2]


def glossary_terms(context: str) -> set[str]:
    return set(knowledge.terms((CORE / context / "CONTEXT.md").read_text(encoding="utf-8")))


def test_every_context_has_a_glossary_and_the_map_lists_it():
    contexts = {p.name for p in CORE.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert contexts == set(ALLOWED) - {"shared"}  # one registry: the dependency rule's
    context_map = (CORE.parents[1] / "CONTEXT-MAP.md").read_text(encoding="utf-8")
    for context in contexts:
        assert (CORE / context / "CONTEXT.md").is_file(), context
        assert f"agentic/core/{context}/CONTEXT.md" in context_map, context


def test_every_type_is_defined_in_its_own_contexts_glossary():
    for term, obj in TYPES.items():
        assert term in glossary_terms(context_of(module_of(obj))), term


def undefined_constants(
    table: Mapping[str, tuple[ModuleType, str]], terms_of: Callable[[str], set[str]]
) -> list[str]:
    """The terms of `table`, in order, whose constant is missing, is not exactly an `int`
    (a `bool` is not one) or is absent from the glossary of its own context."""
    return [
        term
        for term, (module, name) in table.items()
        if type(getattr(module, name, None)) is not int
        or term not in terms_of(context_of(module_of(module)))
    ]


def test_every_constant_is_defined_in_its_own_contexts_glossary():
    assert undefined_constants(CONSTANTS, glossary_terms) == []


def test_undefined_constants_reports_a_term_absent_from_the_glossary():
    table = {"Cupo reservado para aterrizaje": (rules, "LAND_HEADROOM")}
    assert undefined_constants(table, lambda _: set()) == ["Cupo reservado para aterrizaje"]


def test_undefined_constants_reports_a_value_that_is_not_an_int():
    table = {"Tope de tarea": (rules, "task_cap")}
    assert undefined_constants(table, lambda _: {"Tope de tarea"}) == ["Tope de tarea"]


def test_undefined_constants_reports_a_bool():
    stub = ModuleType("agentic.core.process.domain.stub")
    stub.__dict__["FLAG"] = True  # no domain module holds a bool constant to point at
    table = {"Bandera": (stub, "FLAG")}
    assert undefined_constants(table, lambda _: {"Bandera"}) == ["Bandera"]


def test_undefined_constants_reports_a_missing_name():
    table = {"Cupo reservado para aterrizaje": (rules, "NO_SUCH_CONSTANT")}
    assert undefined_constants(table, lambda _: {"Cupo reservado para aterrizaje"}) == [
        "Cupo reservado para aterrizaje"
    ]


def test_undefined_constants_accepts_a_defined_int_constant():
    table = {"Cupo reservado para aterrizaje": (rules, "LAND_HEADROOM")}
    assert undefined_constants(table, lambda _: {"Cupo reservado para aterrizaje"}) == []


def test_state_fields_exist_and_are_process_terms():
    fields = get_type_hints(task.TaskState)
    for term, field in STATE_FIELDS.items():
        assert field in fields, term
        assert term in glossary_terms("process"), term


def test_role_kinds_exist_and_are_process_terms():
    assert set(ROLE_KINDS.values()) == set(get_args(configuration.RoleKind))
    assert set(ROLE_KINDS) <= glossary_terms("process")


def test_adapter_terms_are_defined_before_they_are_typed():
    """§2: a new concept goes into the glossary before it goes into code. `Invocación`
    names one `agentic run`/`resume` call (distinct from `Corrida`, the whole run over
    a task); it has no domain type of its own — `TYPES` cannot place it — but the term
    still has to exist in the process glossary before `InvocationKind` and the
    `agentic.invocation.*`/`agentic.task.invocations` series it labels."""
    for term in ADAPTER_TERMS:
        assert term in glossary_terms("process"), term
