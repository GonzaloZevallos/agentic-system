"""The ubiquitous language (ADR-0009, ADR-0010): every glossary term the code models
has exactly one English name, in the domain of the module whose `CONTEXT.md` defines
the term. The glossaries stay free of identifiers; this table keeps both in step."""

from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints

from agentic.core.knowledge.domain import knowledge, stack
from agentic.core.measurement.domain import metrics
from agentic.core.process.domain import document, presubmit, roles, rules, schemas, task
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
    "Estándares de ingeniería": knowledge.assemble_standards,
    "Job": knowledge.approval_check,  # the check that a capability names its jobs
    "Retrabajo": metrics.is_rework,
    "Hallazgo": schemas.Finding,
    "Dictamen": schemas.Diagnosis,
    "Categoría de devolución": rules.change_category,
    "Tope de tarea": rules.task_cap,
    "Reanudación": rules.resumed,
    "Decisión del humano": rules.human_section,
    "Puerta de entrega": presubmit,
    "Stack del producto": stack,
}

# Glossary term -> the field of the task state that holds it.
STATE_FIELDS = {
    "Devolución": "changes_requested",
    "Escalado": "escalated",
    "Sección": "sections",
    "Flag": "flags",
    "Rama de tarea": "branch",
    "Pull request de tarea": "pull_request",
    "Pregunta para el humano": "questions_for_human",
}

# Glossary term -> the value of `RoleKind` that names it.
ROLE_KINDS = {"Pensador": "actor", "Validador": "verifier"}


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


def test_state_fields_exist_and_are_process_terms():
    fields = get_type_hints(task.TaskState)
    for term, field in STATE_FIELDS.items():
        assert field in fields, term
        assert term in glossary_terms("process"), term


def test_role_kinds_exist_and_are_process_terms():
    assert set(ROLE_KINDS.values()) == set(get_args(configuration.RoleKind))
    assert set(ROLE_KINDS) <= glossary_terms("process")
