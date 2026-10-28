"""What `scripts/docs/generate.py` writes, as pure functions of plain data (GH-400).

Nothing here touches the disk, the environment or the clock: the generator hands in the
help texts, the Mermaid text and the pyreverse files it collected, and gets back file text.
It imports neither `agentic.cli` nor `agentic.composition`; the CLI tree and the compiled
graph are built by the entry point, which is the untyped edge (ADR-0021). Every output is
byte-deterministic: sorted, with no path, timestamp or environment in it (ADR-0076).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterable, Mapping, Sequence

from agentic.core.knowledge.domain.stack import (
    Computed,
    DependsOn,
    Disabled,
    KeyReference,
)

GENERATED = "docs/architecture/generated"
REFERENCE = "docs/reference"
ROLE_GRAPH = f"{GENERATED}/role-graph.md"
CLI_REFERENCE = f"{REFERENCE}/cli.md"
STACK_REFERENCE = f"{REFERENCE}/stack-yaml.md"
MODULES = ("process", "knowledge", "measurement")
# The layer directories each module may have (ADR-0010); a layer that is not there, or has
# no class, writes nothing.
LAYERS: Mapping[str, tuple[str, ...]] = {
    "process": ("domain", "application", "adapters"),
    "knowledge": ("domain", "adapters"),
    "measurement": ("domain", "application", "adapters"),
}
MAX_CHARACTERS = 40000
MAX_EDGES = 400

_FENCE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_EDGE = re.compile(r"-\.(?:[^\n]*?\.)?->|-->|==>|--\|>|\.\.\|>|\.\.>|--\*|--o|\*--|o--|<\|--|<--")


def packages_path(module: str) -> str:
    return f"{GENERATED}/packages-{module}.mmd"


def classes_path(name: str) -> str:
    """`name` is `<module>-<layer>` or `<module>-<layer>-<n>`."""
    return f"{GENERATED}/classes-{name}.mmd"


def outputs(
    class_diagrams: Iterable[str] = (), modules: Iterable[str] = MODULES
) -> tuple[str, ...]:
    """The relative paths the generator writes, sorted. `class_diagrams` are the names of the
    class diagrams it wrote (a layer with no class has none, one over the limit has two),
    which only the run can know; `modules` are the modules drawn."""
    paths = {ROLE_GRAPH, CLI_REFERENCE, STACK_REFERENCE}
    paths.update(packages_path(module) for module in modules)
    paths.update(classes_path(name) for name in class_diagrams)
    return tuple(sorted(paths))


def mermaid_limits(text: str) -> None:
    """Raise `ValueError` when a Mermaid block of `text` is over 40000 characters or 400
    edges. `text` is a file of fenced blocks, or a bare diagram (a `.mmd` file)."""
    blocks = _FENCE.findall(text) if "```mermaid" in text else [text]
    for number, block in enumerate(blocks, start=1):
        if len(block) > MAX_CHARACTERS:
            raise ValueError(
                f"mermaid block {number} has {len(block)} characters; the limit is {MAX_CHARACTERS}"
            )
        edges = len(_EDGE.findall(block))
        if edges > MAX_EDGES:
            raise ValueError(f"mermaid block {number} has {edges} edges; the limit is {MAX_EDGES}")


def role_graph(mermaid: str) -> str:
    """The role graph page: LangGraph's own `draw_mermaid()` text in a fence."""
    return (
        "# Grafo de roles\n\n"
        "Generado por `scripts/docs/generate.py` desde `agentic.composition:graph` "
        "(`get_graph(xray=True).draw_mermaid()`). No se edita a mano.\n\n"
        f"```mermaid\n{mermaid.rstrip()}\n```\n"
    )


def cli_reference(helps: Sequence[tuple[tuple[str, ...], str]]) -> str:
    """The CLI reference: each command path with its `format_help()`, in tree order."""
    parts = [
        "# Referencia de la CLI\n\n"
        "Generada por `scripts/docs/generate.py` recorriendo `agentic.cli.build_parser()`. "
        "Cada sección es la ayuda de un comando, tal como la imprime `agentic`. "
        "No se edita a mano.\n"
    ]
    for path, text in helps:
        title = " ".join(("agentic", *path))
        parts.append(f"## `{title}`\n\n```text\n{text.rstrip()}\n```\n")
    return "\n".join(parts)


def _annotation(reader: object) -> str:
    """The return annotation of a reader, as the text it is written with."""
    if not callable(reader):
        return "?"
    annotation = inspect.signature(reader).return_annotation
    if annotation is inspect.Signature.empty:
        return "?"
    return annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "?")


def _absence(reference: KeyReference, reads_as: str) -> str:
    """What a missing key means, in words."""
    default = reference.default
    if default is None:
        return "no tiene valor por defecto: falta la clave y el stack no carga"
    if isinstance(default, DependsOn):
        return f"depende de `{default.key}`"
    if isinstance(default, Disabled):
        return "ausente = desactivado (el lector devuelve `None`)"
    assert isinstance(default, Computed)
    if reads_as.startswith("tuple"):
        return "lista vacía (`()`)"
    if reads_as.startswith("dict"):
        return "mapa vacío (`{}`)"
    if reads_as == "bool":
        return "`false`"
    return "el valor que calcula el lector"


def stack_reference(references: Sequence[KeyReference]) -> str:
    """The product contract: every key `.agentic/stack.yaml` may carry, from `KEY_REFERENCE`."""
    parts = [
        "# Referencia de `.agentic/stack.yaml`\n\n"
        "Generada por `scripts/docs/generate.py` desde `KEY_REFERENCE` en "
        "`agentic/core/knowledge/domain/stack.py`. Lista cada clave que el sistema lee, "
        "con el tipo con que la lee la función lectora y qué significa que falte. "
        "No se edita a mano.\n"
    ]
    for reference in references:
        reader = reference.reader
        reads_as = _annotation(reader)
        required = "obligatoria" if reference.default is None else "opcional"
        lines = [
            f"## `{reference.name}`\n",
            f"- **Presencia**: {required}",
            f"- **Se lee como**: `{reads_as}`",
            f"- **Si falta**: {_absence(reference, reads_as)}",
        ]
        if reference.sub_keys:
            lines.append("- **Subclaves**: " + ", ".join(f"`{key}`" for key in reference.sub_keys))
        lines.append(f"- **Qué es**: {reference.description}")
        if callable(reader):
            lines.append(f"- **Lector**: `{reader.__module__}.{reader.__qualname__}`")
        parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)


def select_mermaid(files: Mapping[str, str], kind: str) -> str:
    """The one pyreverse output of `kind` (`packages` or `classes`) among `files`, a mapping
    of file name to text; raises when there is not exactly one."""
    found = sorted(name for name in files if name.startswith(f"{kind}_") and name.endswith(".mmd"))
    if len(found) != 1:
        raise ValueError(f"pyreverse wrote {len(found)} `{kind}_*.mmd` files, expected one")
    return files[found[0]]


def has_classes(diagram: str) -> bool:
    """Whether a pyreverse class diagram declares any class."""
    return any(line.startswith("  class ") for line in diagram.splitlines())
