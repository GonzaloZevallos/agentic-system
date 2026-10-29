"""What `scripts/docs/generate.py` writes, as pure functions of plain data (GH-400).

Nothing here touches the disk, the environment or the clock: the generator hands in the
help texts, the Mermaid text and the pyreverse files it collected, and gets back file text.
It imports neither `agentic.cli` nor `agentic.composition`; the CLI tree and the compiled
graph are built by the entry point, which is the untyped edge (ADR-0021). Every output is
byte-deterministic: sorted, with no path, timestamp or environment in it (ADR-0076).
"""

from __future__ import annotations

import argparse
import inspect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import groups

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
LAND_FLOW = "../flujos/aterrizaje.md"
SEPARATOR = ":"
TERMINALS = ("__start__", "__end__")
GHOST_PREFIX = "phase_"
BRANCH_PREFIX = "branch_"
ESCALATION = "escalation"
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
    class_diagrams: Iterable[str],
    role_graph: Iterable[str],
    modules: Iterable[str] = MODULES,
) -> tuple[str, ...]:
    """The relative paths the generator writes, sorted. `class_diagrams` are the names of the
    class diagrams it wrote (a layer with no class has none, one over the limit has two),
    which only the run can know; `modules` are the modules drawn; `role_graph` are the keys
    `role_graph_pages` returned, the only list of role-graph pages there is."""
    paths = {CLI_REFERENCE, STACK_REFERENCE}
    paths.update(role_graph)
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
    path, overage), sorted. `.mmd` files are not measured."""
    found: list[tuple[str, Overage]] = []
    base = root / _ARCHITECTURE
    for page in sorted(base.rglob("*.md")) if base.is_dir() else []:
        relative = page.relative_to(root).as_posix()
        found.extend((relative, over) for over in diagram_budget(page.read_text(encoding="utf-8")))
    return found


def has_subcommand(parser: argparse.ArgumentParser, name: str) -> bool:
    """Whether `parser` has a subcommand called `name` among its direct subparsers."""
    return any(
        name in action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def role_graph_path(name: str) -> str:
    """The page of one phase (or of `escalation`, or `land`) of the role graph."""
    return f"{GENERATED}/role-graph-{name}.md"


def _group(name: str, nodes: Iterable[str]) -> str:
    """The phase of `name` (`escalation` for the escalation group); a name with the subgraph
    separator is looked up by its outermost ancestor. Raises `ValueError` when it has none."""
    outermost = name.split(SEPARATOR)[0]
    for phase, members in groups.PHASES.items():
        if outermost in members:
            return phase
    if outermost in groups.ESCALATION:
        return ESCALATION
    raise ValueError(
        f'role-graph node {name!r} is in no phase: add the line `        "{outermost}",` '
        f"to the tuple of its phase in PHASES, sorted, in scripts/docs/groups.py "
        f"(the nodes are: {', '.join(sorted(nodes))})"
    )


def _identifier(name: str) -> str:
    return re.sub(r"\W", "_", name)


def _ghost(group: str) -> tuple[str, str]:
    return f"{GHOST_PREFIX}{group}", f"{group} (fase)"


def _end(name: str, group: str, here: str | None) -> tuple[str, str]:
    """(id, label) of `name` on a page of phase `here` (None: the overview). It is drawn as
    itself when it is in `here` or is `__start__` or `__end__`, which no ghost ever replaces;
    otherwise it is the ghost of its phase."""
    if group == here or name in TERMINALS:
        return _identifier(name), name
    return _ghost(group)


def _mermaid(labels: Mapping[str, str], edges: Mapping[tuple[str, str], bool]) -> str:
    """A `graph TD`: every id declared once with its label, then the edges, both sorted;
    `-.->` is a conditional edge and `-->` a fixed one."""
    lines = ["graph TD"]
    lines.extend(f'    {identifier}["{labels[identifier]}"]' for identifier in sorted(labels))
    for (source, target), conditional in sorted(edges.items()):
        lines.append(f"    {source} {'-.->' if conditional else '-->'} {target}")
    return "\n".join(lines)


def _fence(labels: Mapping[str, str], edges: Mapping[tuple[str, str], bool]) -> str:
    return f"```mermaid\n{_mermaid(labels, edges)}\n```\n"


class _Block:
    """The nodes and collapsed edges of one Mermaid block: one edge per ordered pair, no
    self-loop, fixed only if every original edge of the pair is fixed."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.edges: dict[tuple[str, str], bool] = {}

    def node(self, end: tuple[str, str]) -> None:
        self.labels[end[0]] = end[1]

    def edge(self, source: tuple[str, str], target: tuple[str, str], conditional: bool) -> None:
        if source[0] == target[0]:
            return
        self.node(source)
        self.node(target)
        pair = (source[0], target[0])
        self.edges[pair] = self.edges.get(pair, False) or conditional

    def fence(self) -> str:
        return _fence(self.labels, self.edges)


def _navigation(here: str, pages: Mapping[str, str]) -> str:
    """The Markdown list of the parent (the overview) and the siblings of the page `here`;
    the overview lists its children."""

    def link(path: str) -> str:
        return f"[{pages[path]}]({Path(path).name})"

    if here == ROLE_GRAPH:
        children = [f"  - {link(path)}" for path in sorted(pages) if path != ROLE_GRAPH]
        return "\n".join(["- Fases:", *children]) + "\n"
    siblings = [f"  - {link(path)}" for path in sorted(pages) if path not in (here, ROLE_GRAPH)]
    lines = [f"- Padre: {link(ROLE_GRAPH)}"]
    if siblings:
        lines.extend(["- Hermanas:", *siblings])
    return "\n".join(lines) + "\n"


_LEGEND = "Una flecha punteada es una arista condicional y una llena es fija."


def _page(sentence: str, here: str, pages: Mapping[str, str], body: str) -> str:
    return f"{sentence}\n\n{_navigation(here, pages)}\n{body}".rstrip("\n") + "\n"


def role_graph_pages(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str, bool]],
    branch_nodes: Iterable[str],
    branch_edges: Iterable[tuple[str, str, bool]],
    has_land: bool,
) -> dict[str, str]:
    """The role graph as pages: relative path -> text. It is the only thing that knows which
    pages exist. `edges` are `(source, target, conditional)`; `branch_*` are the nodes and
    edges of the graph the `pipeline` node runs for each subtask; `has_land` says whether the
    CLI has the `land` subcommand (`has_subcommand`).

    Validation. A node that is in no phase of `groups.PHASES` and not in `groups.ESCALATION`
    raises `ValueError` naming it and printing the line to add. A name with the subgraph
    separator `:` is looked up by its outermost ancestor. A node, of the graph or of the
    branch, that starts with `phase_` or `branch_` raises: those prefixes are the ids of the
    ghosts and of the branch. The branch is never checked against the phases.

    Mermaid. Each block is its own `graph TD`, sorted, with no config header, no classDef and
    no `click`; every id is declared once. A node of another phase is a ghost
    `phase_<name>["<name> (fase)"]`. `__start__` and `__end__` are never replaced by a ghost:
    wherever one is an edge endpoint it is drawn as itself, in the overview too.

    Collapse. Between two drawn nodes there is one edge per ordered pair and never a
    self-loop; it is fixed only if every original edge of the pair is fixed.

    Phase page. Its own nodes, the edges among them, the edges that come in collapsed per
    (ghost source, own target) and those that go out collapsed per (own source, ghost target).
    Edges to `escalate` are not drawn: their sources are listed under the diagram as
    `Pueden escalar: ...`. Edges from `pause` are not drawn here. The build page has a second
    block, the pipeline branch, with ids `branch_<node>` and the node names as labels.

    Overview. Each phase as one ghost-style node, and `phase_escalation` with a conditional
    edge from each phase that escalates and one back to each phase `pause` resumes.

    Escalation page. The edges out of the escalation group, collapsed to one per phase: so
    `escalate --> pause` and the resumes of `pause`.

    Land page. No diagram: a Spanish sentence and a link to the flow `aterrizaje.md`.

    Every page starts with a Spanish sentence saying what it shows, then the Markdown list of
    its parent and its siblings."""
    every = list(nodes)
    flow = list(edges)
    branch = list(branch_nodes)
    branch_flow = list(branch_edges)
    for name in (*every, *branch):
        if name.startswith((GHOST_PREFIX, BRANCH_PREFIX)):
            raise ValueError(
                f"role-graph node {name!r} starts with `{GHOST_PREFIX}` or `{BRANCH_PREFIX}`, "
                f"the prefixes of the ghost and branch ids: rename it"
            )
    names = set(every) | {end for source, target, _ in flow for end in (source, target)}
    group = {name: _group(name, every) for name in sorted(names)}
    present = sorted(set(group.values()))
    paths = {ROLE_GRAPH: "Grafo de roles"}
    paths.update(
        {
            role_graph_path(name): groups.ESCALATION_TITLE if name == ESCALATION else name
            for name in present
        }
    )
    if has_land:
        paths[role_graph_path("land")] = "land"

    result = {ROLE_GRAPH: _overview(group, flow, present, paths)}
    for phase in present:
        result[role_graph_path(phase)] = (
            _escalation_page(phase, group, flow, paths)
            if phase == ESCALATION
            else _phase_page(phase, group, flow, branch, branch_flow, paths)
        )
    if has_land:
        here = role_graph_path("land")
        sentence = (
            "Esta página enlaza el flujo del subcomando `land` de la CLI, que se describe a "
            f"mano en [Aterrizaje]({LAND_FLOW})."
        )
        result[here] = _page(sentence, here, paths, "")
    return result


def _overview(
    group: Mapping[str, str],
    flow: Sequence[tuple[str, str, bool]],
    present: Sequence[str],
    paths: Mapping[str, str],
) -> str:
    block = _Block()
    for phase in present:
        block.node(_ghost(phase))
    for source, target, conditional in flow:
        touches = ESCALATION in (group[source], group[target])
        block.edge(
            _end(source, group[source], None),
            _end(target, group[target], None),
            conditional or touches,
        )
    sentence = (
        "Esta página muestra el grafo de roles de `agentic.composition:graph` reducido a sus "
        "fases: cada fase es un nodo y cada flecha resume las aristas entre dos fases. "
        f"{_LEGEND} Generada por `scripts/docs/generate.py`; no se edita a mano."
    )
    return _page(sentence, ROLE_GRAPH, paths, block.fence())


def _phase_page(
    phase: str,
    group: Mapping[str, str],
    flow: Sequence[tuple[str, str, bool]],
    branch: Sequence[str],
    branch_flow: Sequence[tuple[str, str, bool]],
    paths: Mapping[str, str],
) -> str:
    block = _Block()
    for name in group:
        if group[name] == phase:
            block.node((_identifier(name), name))
    escalating: set[str] = set()
    for source, target, conditional in flow:
        if ESCALATION in (group[source], group[target]):
            if group[target] == ESCALATION and group[source] == phase:
                escalating.add(source)
            continue
        if phase in (group[source], group[target]):
            block.edge(
                _end(source, group[source], phase), _end(target, group[target], phase), conditional
            )
    sentence = (
        f"Esta página muestra los nodos de la fase `{phase}` del grafo de roles y las aristas "
        "que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en "
        f"su página. {_LEGEND}"
    )
    body = block.fence()
    if escalating:
        listed = ", ".join(f"`{name}`" for name in sorted(escalating))
        body += f"\nPueden escalar: {listed}\n"
    if phase == "build" and branch:
        inner = _Block()
        for name in branch:
            inner.node((f"{BRANCH_PREFIX}{_identifier(name)}", name))
        for source, target, conditional in branch_flow:
            inner.edge(
                (f"{BRANCH_PREFIX}{_identifier(source)}", source),
                (f"{BRANCH_PREFIX}{_identifier(target)}", target),
                conditional,
            )
        body += (
            "\n## Rama `pipeline`\n\n"
            "El nodo `pipeline` corre, por cada subtarea, este grafo propio "
            "(`agentic.core.process.adapters.langgraph.graph:pipeline_branch`), que el grafo de "
            f"arriba no expande.\n\n{inner.fence()}"
        )
    return _page(sentence, role_graph_path(phase), paths, body)


def _escalation_page(
    phase: str,
    group: Mapping[str, str],
    flow: Sequence[tuple[str, str, bool]],
    paths: Mapping[str, str],
) -> str:
    block = _Block()
    for name in group:
        if group[name] == ESCALATION:
            block.node((_identifier(name), name))
    for source, target, conditional in flow:
        if group[source] == ESCALATION:
            block.edge(
                _end(source, group[source], ESCALATION),
                _end(target, group[target], ESCALATION),
                conditional,
            )
    sentence = (
        f"Esta página muestra el escalado y la reanudación: los nodos `escalate` y `pause` "
        "del grafo de roles y a qué fase vuelve el grafo cuando `pause` reanuda, una flecha por "
        f"fase. {_LEGEND}"
    )
    return _page(sentence, role_graph_path(phase), paths, block.fence())


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
