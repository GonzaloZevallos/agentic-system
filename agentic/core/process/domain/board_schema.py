"""The board schema's pure core (GH-1166): what has to change in the GitHub Project's fields
for it to carry the five Etapa options and the `Motivo` text field, as data.

`schema_steps` reads the Project's fields and answers the steps that put the schema right, or
a `SchemaRefusal` naming what it found when the Project is not one it knows how to fix. It
never creates or deletes a field. Applying a step is the `BoardSchema` port's job; no clock
and no I/O here.

This module is the one home of the previous option names (`PREVIOUS_ETAPAS`,
`DROPPED_ETAPAS`), the field names and the default colour and description of an option.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

from .board import Etapa

ETAPA_FIELD = "Etapa"
STATUS_FIELD = "Status"
MOTIVO_FIELD = "Motivo"
PREVIOUS_MOTIVO_FIELD = "Bloqueo"
SINGLE_SELECT = "SINGLE_SELECT"
TEXT = "TEXT"

PREVIOUS_ETAPAS: Mapping[str, Etapa] = {
    "Hecha": "Hecho",
    "Corriendo": "En curso",
    "Necesita humano": "Te necesita",
    "En cola": "Próximo",
    "Fuera del sistema": "En pausa",
}
"""The options the Project had before the five, and the one each became."""
DROPPED_ETAPAS: tuple[str, ...] = ("Trabada", "En PR")
"""Old options with no successor: accepted, and they never lend anything to a new option."""
KNOWN_ETAPAS: frozenset[str] = frozenset({*PREVIOUS_ETAPAS, *DROPPED_ETAPAS, *get_args(Etapa)})
"""The only option names `schema_steps` accepts on the Etapa field."""

DEFAULT_OPTION_COLOR = "GRAY"
DEFAULT_OPTION_DESCRIPTION = ""

_PREDECESSOR: Mapping[str, str] = {new: old for old, new in PREVIOUS_ETAPAS.items()}


@dataclass(frozen=True)
class OptionNode:
    id: str | None
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class FieldNode:
    id: str
    name: str
    data_type: str
    options: tuple[OptionNode, ...]


@dataclass(frozen=True)
class ReplaceOptions:
    field_id: str
    options: tuple[OptionNode, ...]


@dataclass(frozen=True)
class RenameField:
    field_id: str
    name: str


Step = ReplaceOptions | RenameField


@dataclass(frozen=True)
class SchemaRefusal:
    """The Project is not one `schema_steps` fixes; `found` names what it saw."""

    found: str


def _single_select(nodes: Sequence[FieldNode], name: str) -> FieldNode | None:
    return next((n for n in nodes if n.name == name and n.data_type == SINGLE_SELECT), None)


def etapa_field(nodes: Sequence[FieldNode]) -> FieldNode | None:
    """The field that carries the Etapa options: the SINGLE_SELECT `Etapa`, even when it is
    incomplete, else the SINGLE_SELECT `Status`, else `None`. It never judges completeness."""
    return _single_select(nodes, ETAPA_FIELD) or _single_select(nodes, STATUS_FIELD)


def _options_step(field: FieldNode) -> ReplaceOptions | SchemaRefusal | None:
    names = {o.name for o in field.options}
    unknown = sorted(names - KNOWN_ETAPAS)
    if unknown:
        return SchemaRefusal(f"{field.name} has options outside the known ones: {unknown}")
    if names == set(get_args(Etapa)):
        return None
    existing = {o.name: o for o in field.options}
    options = []
    for name in get_args(Etapa):
        source = existing.get(name) or existing.get(_PREDECESSOR[name])
        if source is None:
            options.append(OptionNode(None, name, DEFAULT_OPTION_COLOR, DEFAULT_OPTION_DESCRIPTION))
        else:
            options.append(OptionNode(source.id, name, source.color, source.description))
    return ReplaceOptions(field.id, tuple(options))


def _text_step(nodes: Sequence[FieldNode]) -> RenameField | SchemaRefusal | None:
    found = [n for n in nodes if n.name in (PREVIOUS_MOTIVO_FIELD, MOTIVO_FIELD)]
    listing = [f"{n.name} ({n.data_type})" for n in found]
    if len(found) == 2:
        return SchemaRefusal(f"both {PREVIOUS_MOTIVO_FIELD} and {MOTIVO_FIELD} exist: {listing}")
    if not found:
        return SchemaRefusal(f"neither {PREVIOUS_MOTIVO_FIELD} nor {MOTIVO_FIELD} exists")
    (only,) = found
    if only.data_type != TEXT:
        return SchemaRefusal(f"{only.name} is not a {TEXT} field: {listing}")
    if only.name == PREVIOUS_MOTIVO_FIELD:
        return RenameField(only.id, MOTIVO_FIELD)
    return None


def schema_steps(nodes: Sequence[FieldNode]) -> tuple[Step, ...] | SchemaRefusal:
    """The steps that put the Project's schema right, options first and the rename second,
    or a refusal. An empty tuple: nothing to do."""
    etapa = etapa_field(nodes)
    if etapa is None:
        found = [f"{n.name} ({n.data_type})" for n in nodes]
        return SchemaRefusal(
            f"no SINGLE_SELECT field {ETAPA_FIELD} nor {STATUS_FIELD}; fields found: {found}"
        )
    options = _options_step(etapa)
    if isinstance(options, SchemaRefusal):
        return options
    rename = _text_step(nodes)
    if isinstance(rename, SchemaRefusal):
        return rename
    return tuple(step for step in (options, rename) if step is not None)
