"""The ubiquitous language (ADR-0009): every glossary term the code models has exactly
one English name in `agentic.domain`, and this table is where the mapping lives.
CONTEXT.md stays free of identifiers; the test keeps both in step."""

from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints

from agentic.domain import configuration, document, knowledge, roles, task

GLOSSARY = Path(__file__).resolve().parents[2] / knowledge.GLOSSARY

# Glossary term -> the type or module that models it.
TYPES = {
    "Rol": roles.Role,
    "Par": roles.Pair,
    "Tarea": task.TaskState,
    "Fallo de rol": task.Failure,
    "Corrida de rol": task.RoleRun,
    "Documento de tarea": document,
    "Conocimiento de producto": knowledge,
}

# Glossary term -> the field of the task state that holds it.
STATE_FIELDS = {
    "Devolución": "returns",
    "Escalado": "escalated",
    "Sección": "sections",
    "Flag": "flags",
}

# Glossary term -> the value of `RoleKind` that names it.
ROLE_KINDS = {"Pensador": "thinker", "Validador": "validator"}


def glossary_terms() -> set[str]:
    return set(knowledge.terms(GLOSSARY.read_text(encoding="utf-8")))


def test_every_mapped_term_is_in_the_glossary():
    mapped = set(TYPES) | set(STATE_FIELDS) | set(ROLE_KINDS)
    assert mapped <= glossary_terms(), mapped - glossary_terms()


def test_every_type_lives_in_the_domain():
    for term, obj in TYPES.items():
        module = obj.__name__ if isinstance(obj, ModuleType) else obj.__module__
        assert module.startswith("agentic.domain."), term


def test_state_fields_exist():
    fields = get_type_hints(task.TaskState)
    for term, field in STATE_FIELDS.items():
        assert field in fields, term


def test_role_kinds_exist():
    assert set(ROLE_KINDS.values()) == set(get_args(configuration.RoleKind))
