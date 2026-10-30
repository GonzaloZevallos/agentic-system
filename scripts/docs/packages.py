"""The package graph of `agentic/core/process`, as pages of plain data (GH-430 part 2, ADR-0076).

Pure: the modules on disk, what each process module imports and the `CLUSTERS` table in, a
mapping of page key to `Page` out. No file is read and no Mermaid text is written; `render.py`
turns each `Page` into a `.md` file and `generate.py` collects the inputs. A module is
identified by its qualified name, relative to the process package (`domain.task`,
`adapters.git.workspace`), so two homonyms of different layers are never merged.

The pages: an index of the process subpackages; one page per subpackage (`domain`,
`application`, `adapters` for its loose modules, and each adapter kind); one page per cluster
of `domain` and of `application`. A page draws only the outgoing edges of its own modules;
every target it does not draw collapses to one ghost (a cluster, a subpackage or a top-level
package).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from agentic.core.process.domain.docs import DIAGRAM_BUDGET

PROCESS = "agentic.core.process"
LAYERS = ("domain", "application")
ADAPTERS = "adapters"
INDEX = "index"
MAX_PART = DIAGRAM_BUDGET.classes

_CLUSTER_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
Ends = tuple[tuple[str, str], ...]
End = tuple[str, str]


@dataclass(frozen=True)
class Page:
    """One page of the process package diagrams. `key` names it (`index`, `domain`,
    `domain-state`, `domain-state-2`, `adapters-git`); `title` is how links to it read and
    `sentence` its Spanish first sentence; `parent` is the key of the page above it (None for
    the index), `siblings` the keys of the pages beside it and `children` those below; `nodes`
    are `(id, label)` and `edges` `(source id, target id)`, each pair once."""

    key: str
    title: str
    sentence: str
    parent: str | None
    siblings: tuple[str, ...]
    children: tuple[str, ...]
    nodes: Ends
    edges: Ends


def node_id(kind: str, name: str, part: int | None = None) -> str:
    """The Mermaid id of everything a page draws; no other function builds one. `kind` is
    `module` (`m_<layer>__<module>`), `cluster` (`g_<layer>__<cluster>`, `name` being
    `<layer>.<cluster>`, and `part` the number of a count-split page) or `package`
    (`g_pkg__<name>`). A dot becomes `__`. A name segment with `__` or an edge underscore would
    make two names share an id, so it raises."""
    for segment in name.split("."):
        if not segment or "__" in segment or segment.startswith("_") or segment.endswith("_"):
            raise ValueError(
                f"{name!r} cannot be an id: segment {segment!r} has `__` or an edge `_`"
            )
    body = name.replace(".", "__")
    if kind == "module" and part is None:
        return f"m_{body}"
    if kind == "cluster":
        return f"g_{body}" if part is None else f"g_{body}__{part}"
    if kind == "package" and part is None:
        return f"g_pkg__{body}"
    raise ValueError(f"no id for kind {kind!r} with part {part!r}")


def _relative(name: str) -> str | None:
    """`name` relative to the process package, or None when it is outside it."""
    return name.removeprefix(PROCESS + ".") if name.startswith(PROCESS + ".") else None


def _outside(name: str) -> str:
    """The top-level package of a module outside `agentic/core/process`: `agentic.core.<m>` for
    a core module, `agentic.<x>` otherwise."""
    parts = name.split(".")
    return ".".join(parts[:3] if parts[:2] == ["agentic", "core"] else parts[:2])


def _subpackage(relative: str) -> str:
    """`domain`, `application`, `adapters` (a loose module) or `adapters.<kind>`."""
    parts = relative.split(".")
    return ".".join(parts[:2]) if parts[0] == ADAPTERS and len(parts) > 2 else parts[0]


def _key(subpackage: str) -> str:
    return subpackage.replace(".", "-")


def _package_end(name: str) -> End:
    return node_id("package", name), f"{name} (paquete)"


class _Draw:
    """The nodes and edges of one page: each id once, each pair once, no self-loop."""

    def __init__(self) -> None:
        self.nodes: dict[str, str] = {}
        self.edges: set[tuple[str, str]] = set()

    def node(self, end: End) -> None:
        self.nodes[end[0]] = end[1]

    def edge(self, source: End, target: End) -> None:
        self.node(source)
        self.node(target)
        if source[0] != target[0]:
            self.edges.add((source[0], target[0]))

    def over(self) -> bool:
        return len(self.nodes) > DIAGRAM_BUDGET.nodes or len(self.edges) > DIAGRAM_BUDGET.edges

    def page(
        self,
        key: str,
        title: str,
        sentence: str,
        parent: str | None,
        siblings: Iterable[str],
        children: Iterable[str] = (),
    ) -> Page:
        return Page(
            key,
            title,
            sentence,
            parent,
            tuple(siblings),
            tuple(children),
            tuple(sorted(self.nodes.items())),
            tuple(sorted(self.edges)),
        )


class _Graph:
    """The process modules, the cluster of each layer module and the targets each one names
    that are modules on disk."""

    def __init__(
        self,
        on_disk: Mapping[str, bool],
        imports: Mapping[str, Iterable[str]],
        clusters: Mapping[str, str],
    ) -> None:
        self.cluster: dict[str, str] = {}
        self.targets: dict[str, set[str]] = {}
        for name, names in imports.items():
            relative = _relative(name)
            if relative is None:
                continue
            self.targets[relative] = {
                target
                for target in names
                if target != name and target in on_disk and not on_disk[target]
            }
        self.modules = sorted(self.targets)
        for name in set(clusters.values()):
            if not _CLUSTER_NAME.fullmatch(name):
                raise ValueError(f"cluster name {name!r} must be lowercase words joined by `_`")
        for relative in self.modules:
            layer = relative.split(".")[0]
            if layer not in LAYERS:
                continue
            if relative not in clusters:
                known = sorted({v for k, v in clusters.items() if k.startswith(f"{layer}.")})
                raise ValueError(
                    f"process module {relative!r} is in no cluster: add the line "
                    f'`    "{relative}": "<cluster>",` to CLUSTERS in scripts/docs/groups.py, '
                    f"sorted (the clusters of {layer} are: {', '.join(known)})"
                )
            self.cluster[relative] = clusters[relative]
        self.layer_clusters = {
            layer: sorted({c for m, c in self.cluster.items() if m.startswith(f"{layer}.")})
            for layer in LAYERS
        }
        kinds = {_subpackage(m) for m in self.modules if m.startswith(f"{ADAPTERS}.")}
        self.subpackages = [*LAYERS, ADAPTERS, *sorted(kinds - {ADAPTERS})]

    def modules_of(self, subpackage: str) -> list[str]:
        return [m for m in self.modules if _subpackage(m) == subpackage]

    def cluster_modules(self, layer: str, cluster: str) -> list[str]:
        return [m for m in self.modules_of(layer) if self.cluster[m] == cluster]

    def sub_end(self, target: str) -> End:
        """The ghost of the subpackage of a process module, or of the top-level package of a
        module outside process; the one end every page may draw for a target it does not
        detail."""
        relative = _relative(target)
        return _package_end(_outside(target) if relative is None else _subpackage(relative))


def _own_end(relative: str) -> End:
    return node_id("module", relative), relative


def _cluster_end(layer: str, cluster: str, of: str) -> End:
    """The ghost of a cluster; `of` is the layer the page is in (a domain cluster seen from
    `application` says so)."""
    suffix = "(grupo)" if of == layer else f"(grupo de {layer})"
    return node_id("cluster", f"{layer}.{cluster}"), f"{cluster} {suffix}"


def build_pages(
    on_disk: Mapping[str, bool],
    imports: Mapping[str, Iterable[str]],
    clusters: Mapping[str, str],
) -> dict[str, Page]:
    """Every page of the process package diagrams, by key.

    `on_disk` maps each qualified module name under `agentic/` to whether it is a package
    `__init__` (a package maps to its package name); `imports` maps each process module, by
    qualified name, to the names `imports_of` returned for its file; `clusters` is
    `groups.CLUSTERS`.

    A node is a qualified module that exists on disk. A name that is only a package, a name
    that is not a module and a self-import are dropped, with their edge. A module of `domain`
    or `application` with no `clusters` entry raises `ValueError` naming it and the line to
    paste. A cluster of more than 15 modules, or whose page is over `DIAGRAM_BUDGET`, is split
    once into `ceil(n / 15)` pages (at least two) of consecutive modules in alphabetical order,
    each saying so; a page still over budget raises, naming the cluster."""
    graph = _Graph(on_disk, imports, clusters)
    pages = {INDEX: _index(graph)}
    for sub in graph.subpackages:
        if sub in LAYERS:
            pages[_key(sub)] = _layer_page(graph, sub)
            for cluster in graph.layer_clusters[sub]:
                pages.update(_cluster_pages(graph, sub, cluster))
        else:
            pages[_key(sub)] = _adapter_page(graph, sub)
    return pages


def _index(graph: _Graph) -> Page:
    draw = _Draw()
    for sub in graph.subpackages:
        draw.node(_package_end(sub))
    for source in graph.modules:
        for target in graph.targets[source]:
            relative = _relative(target)
            if relative is not None:
                draw.edge(_package_end(_subpackage(source)), _package_end(_subpackage(relative)))
    sentence = (
        "Esta página muestra los paquetes de `agentic.core.process`: `domain`, `application`, "
        "`adapters` y cada tipo de adaptador, con una flecha por cada paquete que importa a "
        "otro. Cada página dibuja los imports que hacen sus módulos, no quién los importa: las "
        "flechas que llegan a un paquete se leen en la página del paquete que importa."
    )
    return draw.page(
        INDEX, "Paquetes de process", sentence, None, (), [_key(s) for s in graph.subpackages]
    )


def _others(graph: _Graph, sub: str) -> list[str]:
    return [_key(other) for other in graph.subpackages if other != sub]


def _layer_page(graph: _Graph, layer: str) -> Page:
    """The clusters of `layer` as nodes and one edge per ordered pair of clusters where one
    imports a module of the other. Imports out of the layer are read on the cluster pages."""
    draw = _Draw()
    for cluster in graph.layer_clusters[layer]:
        draw.node(_cluster_end(layer, cluster, layer))
    for source in graph.modules_of(layer):
        here = _cluster_end(layer, graph.cluster[source], layer)
        for target in graph.targets[source]:
            relative = _relative(target)
            if relative is not None and _subpackage(relative) == layer:
                draw.edge(here, _cluster_end(layer, graph.cluster[relative], layer))
    sentence = (
        f"Esta página muestra los grupos de módulos de `{layer}` y una flecha por cada grupo "
        "que importa módulos de otro; cada nodo `<nombre> (grupo)` tiene su página. Los "
        "imports hacia otros paquetes se leen en la página de cada grupo, y los grupos se "
        "definen en `CLUSTERS`, en `scripts/docs/groups.py`."
    )
    children = [key for c in graph.layer_clusters[layer] for key in _part_keys(graph, layer, c)]
    return _checked(draw, layer, layer, sentence, INDEX, _others(graph, layer), children)


def _cluster_key(layer: str, cluster: str, part: int | None) -> str:
    base = f"{layer}-{cluster}"
    return base if part is None else f"{base}-{part}"


def chunks(modules: list[str]) -> list[list[str]]:
    """`modules` in `ceil(n / DIAGRAM_BUDGET.classes)` (at least two) runs of consecutive names, as
    even as can be; the one rule that splits a list by count, for package pages and class pages."""
    count = max(2, math.ceil(len(modules) / MAX_PART))
    size = math.ceil(len(modules) / count)
    return [modules[start : start + size] for start in range(0, len(modules), size)]


def _splits(graph: _Graph, layer: str, cluster: str) -> bool:
    modules = graph.cluster_modules(layer, cluster)
    return len(modules) > MAX_PART or _draw_cluster(graph, layer, cluster, modules, None)[1] is None


def _part_keys(graph: _Graph, layer: str, cluster: str) -> list[str]:
    if not _splits(graph, layer, cluster):
        return [_cluster_key(layer, cluster, None)]
    count = len(chunks(graph.cluster_modules(layer, cluster)))
    return [_cluster_key(layer, cluster, number) for number in range(1, count + 1)]


def _checked(
    draw: _Draw,
    key: str,
    title: str,
    sentence: str,
    parent: str | None,
    siblings: Iterable[str],
    children: Iterable[str] = (),
) -> Page:
    if draw.over():
        budget = DIAGRAM_BUDGET
        raise ValueError(
            f"process page {key!r} has {len(draw.nodes)} nodes and {len(draw.edges)} edges; "
            f"the budget is {budget.nodes} nodes and {budget.edges} edges: split it by meaning "
            "in CLUSTERS (scripts/docs/groups.py)"
        )
    return draw.page(key, title, sentence, parent, siblings, children)


def _draw_cluster(
    graph: _Graph,
    layer: str,
    cluster: str,
    modules: list[str],
    parts: tuple[int, list[list[str]]] | None,
) -> tuple[_Draw, str | None]:
    """The drawing of `modules` of a cluster, and how it was drawn: `fine` (every domain
    target one ghost per domain cluster), `domain` (an application page that fell back to one
    `domain` ghost) or None when neither fits `DIAGRAM_BUDGET`."""
    options = ("fine", "domain") if layer == "application" else ("fine",)
    fallback = _Draw()
    for how in options:
        draw = _Draw()
        for source in modules:
            here = _own_end(source)
            draw.node(here)
            for target in graph.targets[source]:
                draw.edge(here, _target_end(graph, layer, cluster, source, target, how, parts))
        if not draw.over():
            return draw, how
        fallback = draw
    return fallback, None


def _target_end(
    graph: _Graph,
    layer: str,
    cluster: str,
    source: str,
    target: str,
    how: str,
    parts: tuple[int, list[list[str]]] | None,
) -> End:
    relative = _relative(target)
    if relative is None:
        return graph.sub_end(target)
    sub = _subpackage(relative)
    if sub == layer:
        other = graph.cluster[relative]
        if other != cluster:
            return _cluster_end(layer, other, layer)
        if parts is not None and relative not in parts[1][parts[0] - 1]:
            number = next(n for n, chunk in enumerate(parts[1], 1) if relative in chunk)
            return node_id("cluster", f"{layer}.{cluster}", number), (
                f"{cluster} parte {number} (grupo)"
            )
        return _own_end(relative)
    if layer == "application" and sub == "domain" and how == "fine":
        return _cluster_end("domain", graph.cluster[relative], "application")
    return _package_end(sub)


_PACKAGE_NODES = "con su página si es un paquete de `process`, sin ella si es de fuera."


def _cluster_sentence(layer: str, cluster: str, how: str, parts: tuple[int, int] | None) -> str:
    nodes = f"un nodo `<nombre> (grupo)` es otro grupo de `{layer}`"
    if layer == "application":
        nodes += ", `<nombre> (grupo de domain)` un grupo de `domain`"
    text = (
        f"Esta página muestra los módulos del grupo `{cluster}` de `{layer}` y los imports "
        f"que hacen; {nodes} (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: "
        f"{_PACKAGE_NODES}"
    )
    if how == "domain":
        text += (
            " Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque "
            "dibujar un nodo por grupo no cabe en el presupuesto de diagramas."
        )
    if parts is not None:
        text += (
            f" El grupo se dividió por cantidad: esta es la parte {parts[0]} de {parts[1]}, con "
            "módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos "
            "`<grupo> parte <n> (grupo)`."
        )
    return text


def _cluster_pages(graph: _Graph, layer: str, cluster: str) -> dict[str, Page]:
    modules = graph.cluster_modules(layer, cluster)
    keys = _part_keys(graph, layer, cluster)
    siblings_of = [
        key for other in graph.layer_clusters[layer] for key in _part_keys(graph, layer, other)
    ]
    title = f"{layer}: {cluster}"
    if len(keys) == 1:
        draw, how = _draw_cluster(graph, layer, cluster, modules, None)
        if how is None:
            raise ValueError("unreachable: an unsplit cluster fits")
        sentence = _cluster_sentence(layer, cluster, how, None)
        page = draw.page(
            keys[0], title, sentence, layer, [key for key in siblings_of if key != keys[0]]
        )
        return {keys[0]: page}
    runs = chunks(modules)
    found: dict[str, Page] = {}
    for number, (key, chunk) in enumerate(zip(keys, runs, strict=True), start=1):
        draw, how = _draw_cluster(graph, layer, cluster, chunk, (number, runs))
        if how is None:
            raise ValueError(
                f"cluster {layer}.{cluster} has {len(modules)} modules and part {number} of "
                f"{len(runs)} has {len(draw.nodes)} nodes and {len(draw.edges)} edges, over "
                f"the budget of {DIAGRAM_BUDGET.nodes} nodes and {DIAGRAM_BUDGET.edges} edges: "
                f"split `{layer}.{cluster}` by meaning in CLUSTERS (scripts/docs/groups.py)"
            )
        sentence = _cluster_sentence(layer, cluster, how, (number, len(runs)))
        found[key] = draw.page(
            key,
            f"{title} (parte {number})",
            sentence,
            layer,
            [other for other in siblings_of if other != key],
        )
    return found


def _adapter_page(graph: _Graph, sub: str) -> Page:
    draw = _Draw()
    for source in graph.modules_of(sub):
        here = _own_end(source)
        draw.node(here)
        for target in graph.targets[source]:
            relative = _relative(target)
            if relative is not None and _subpackage(relative) == sub:
                draw.edge(here, _own_end(relative))
            else:
                draw.edge(here, graph.sub_end(target))
    what = (
        "los módulos sueltos de `adapters`, los que no están en ningún tipo de adaptador,"
        if sub == ADAPTERS
        else f"los módulos del adaptador `{sub}`"
    )
    sentence = (
        f"Esta página muestra {what} y los imports que hacen; un nodo `<nombre> (paquete)` es "
        f"otro paquete: {_PACKAGE_NODES}"
    )
    return _checked(draw, _key(sub), sub, sentence, INDEX, _others(graph, sub))
