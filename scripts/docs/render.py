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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agentic.core.knowledge.domain.stack import (
    Computed,
    DependsOn,
    Disabled,
    KeyReference,
)
from agentic.core.process.domain.docs import DIAGRAM_BUDGET

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
# Pages `check_budget` does not measure, each named in full. Subtask C2 of GH-434 removes
# this one, once the role graph is split into pages that fit the budget.
_EXEMPT: frozenset[str] = frozenset({"docs/architecture/generated/role-graph.md"})

_ARCHITECTURE = "docs/architecture"
_QUOTED = re.compile(r'"[^"]*"')
_SHAPE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_PIPED_LABEL = re.compile(r"\|[^|]*\|")
_TEXT_LABEL = re.compile(
    r"(?<![-=.])(?:--|==)\s+[^<>\n]*?\s+(-->|---|==>)|-\.\s+[^<>\n]*?\s+\.-(>?)"
)
_ARROW = re.compile(r"<-->|-{2,}>|={2,}>|-\.+->|-{3,}|-\.+-|--[ox](?=\s|$)")
_IGNORED_LINE = re.compile(r"(?:subgraph|end|classDef|style|linkStyle|class|click|direction)\b")
_CLASS_LINE = re.compile(r"class\s+([^\s{:]+)")
_NODE_ID = re.compile(r"\w+")
_DECLARED = re.compile(r"(\w+)§")


@dataclass(frozen=True)
class Overage:
    """A Mermaid block over `DIAGRAM_BUDGET`: its 1-based `block` number in the page, its
    `kind`, what was counted, and which of `limits` (`nodes`, `edges`, `classes`) it passed."""

    block: int
    kind: str
    nodes: int
    edges: int
    classes: int
    limits: tuple[str, ...]


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


def _bare_arrow(match: re.Match[str]) -> str:
    """The arrow of a `-- text -->` or `-. text .->` edge, without its text."""
    return f" {match.group(1)} " if match.group(1) else f" -.-{match.group(2)} "


def _blocks(text: str) -> list[str]:
    return _FENCE.findall(text) if "```mermaid" in text else [text]


def _kind(block: str) -> str | None:
    """`flowchart`, `classDiagram`, or None for a diagram the budget ignores. Skips a leading
    `---…---` frontmatter, blank lines and `%%` comments."""
    lines = block.splitlines()
    position = 0
    if lines and lines[0].strip() == "---":
        closing = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
        position = len(lines) if closing is None else closing + 1
    for line in lines[position:]:
        word = line.strip()
        if not word or word.startswith("%%"):
            continue
        first = word.split(None, 1)[0].rstrip(";")
        if first in ("flowchart", "graph"):
            return "flowchart"
        return "classDiagram" if first == "classDiagram" else None
    return None


def _flowchart(block: str) -> tuple[int, int]:
    """(nodes, edges) of a flowchart `block` without frontmatter (see `_body`): edges per
    line, a chain or an `&` expanded; nodes the distinct ids that are an edge endpoint or are
    declared with a shape."""
    nodes: set[str] = set()
    edges = 0
    for raw in block.splitlines()[1:]:
        for statement in raw.split(";"):
            line = statement.strip()
            if not line or line.startswith("%%") or _IGNORED_LINE.match(line):
                continue
            line = _QUOTED.sub('""', line)
            previous = None
            while previous != line:
                previous, line = line, _SHAPE.sub("§", line)
            line = _TEXT_LABEL.sub(_bare_arrow, _PIPED_LABEL.sub("", line))
            parts = _ARROW.split(line)
            if len(parts) == 1:
                declared = _DECLARED.match(line)
                if declared:
                    nodes.add(declared.group(1))
                continue
            groups = [
                [found.group(0) for token in part.split("&") if (found := _NODE_ID.search(token))]
                for part in parts
            ]
            for before, after in zip(groups, groups[1:], strict=False):
                edges += len(before) * len(after)
            nodes.update(identifier for group in groups for identifier in group)
    return len(nodes), edges


def _kinds_and_counts(text: str) -> list[tuple[int, str, int, int, int]]:
    found: list[tuple[int, str, int, int, int]] = []
    for number, block in enumerate(_blocks(text), start=1):
        kind = _kind(block)
        if kind == "flowchart":
            body = _body(block)
            nodes, edges = _flowchart(body)
            found.append((number, kind, nodes, edges, 0))
        elif kind == "classDiagram":
            declared = {
                match.group(1)
                for line in block.splitlines()
                if (match := _CLASS_LINE.match(line.strip()))
            }
            found.append((number, kind, 0, 0, len(declared)))
    return found


def _body(block: str) -> str:
    """The block without its frontmatter, the type line first."""
    lines = block.splitlines()
    if lines and lines[0].strip() == "---":
        closing = next(
            (n for n in range(1, len(lines)) if lines[n].strip() == "---"), len(lines) - 1
        )
        lines = lines[closing + 1 :]
    while lines and (not lines[0].strip() or lines[0].strip().startswith("%%")):
        lines = lines[1:]
    return "\n".join(lines)


def diagram_counts(text: str) -> list[tuple[int, int, int]]:
    """(nodes, edges, classes) of each block the budget measures, in order."""
    return [(nodes, edges, classes) for _, _, nodes, edges, classes in _kinds_and_counts(text)]


def diagram_budget(text: str) -> list[Overage]:
    """The blocks of `text` (a page of fenced blocks, or a bare diagram) over
    `DIAGRAM_BUDGET`, in order. Only flowchart/graph and classDiagram are measured; the type is
    decided before counting, so a sequence diagram or a C4 one is never over budget."""
    budget = DIAGRAM_BUDGET
    over: list[Overage] = []
    for number, kind, nodes, edges, classes in _kinds_and_counts(text):
        limits = tuple(
            name
            for name, count, most in (
                ("nodes", nodes, budget.nodes),
                ("edges", edges, budget.edges),
                ("classes", classes, budget.classes),
            )
            if count > most
        )
        if limits:
            over.append(Overage(number, kind, nodes, edges, classes, limits))
    return over


def check_budget(root: Path) -> list[tuple[str, Overage]]:
    """Every `.md` under `root/docs/architecture/` with a block over budget, as (relative
    path, overage), sorted. `.mmd` files are not measured and `_EXEMPT` pages are skipped."""
    found: list[tuple[str, Overage]] = []
    base = root / _ARCHITECTURE
    for page in sorted(base.rglob("*.md")) if base.is_dir() else []:
        relative = page.relative_to(root).as_posix()
        if relative in _EXEMPT:
            continue
        found.extend((relative, over) for over in diagram_budget(page.read_text(encoding="utf-8")))
    return found


def role_graph(mermaid: str, branch: str) -> str:
    """The role graph page: LangGraph's own `draw_mermaid()` text of the task graph and of
    the `develop -> review` graph inside its `pipeline` node, each in a fence."""
    return (
        "# Grafo de roles\n\n"
        "Generado por `scripts/docs/generate.py` desde `agentic.composition:graph` "
        "(`get_graph(xray=True).draw_mermaid()`). No se edita a mano.\n\n"
        f"```mermaid\n{mermaid.rstrip()}\n```\n\n"
        "## Rama `pipeline`\n\n"
        "El nodo `pipeline` corre, por cada subtarea, este grafo propio "
        "(`agentic.core.process.adapters.langgraph.graph:pipeline_branch`), que el grafo de "
        "arriba no expande.\n\n"
        f"```mermaid\n{branch.rstrip()}\n```\n"
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


def _annotation(reader: Callable[[object, object], object] | None) -> str:
    """The return annotation of a reader, as the text it is written with."""
    if reader is None:
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
        if reader is not None:
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


def merge_mermaid(parts: Sequence[str]) -> str:
    """One pyreverse diagram out of several runs over parts of the same code: the header of
    the first, then every block once (the first of a name) and every relation once, both
    sorted so the result does not depend on the order of the runs."""
    header = parts[0].splitlines()[0] if parts and parts[0] else "classDiagram"
    blocks: dict[str, str] = {}
    relations: set[str] = set()
    for part in parts:
        lines = part.splitlines()
        position = 1
        while position < len(lines):
            line = lines[position]
            if line.startswith("  class "):
                end = position
                while end < len(lines) - 1 and lines[end] != "  }" and line.endswith("{"):
                    end += 1
                blocks.setdefault(line, "\n".join(lines[position : end + 1]))
                position = end
            elif line.strip():
                relations.add(line)
            position += 1
    body = [blocks[key] for key in sorted(blocks)] + sorted(relations)
    return "\n".join([header, *body]) + "\n"
