"""The class diagrams of one layer of a core module, as pages of plain data (GH-430 parts 3 and 4).

Pure: the text of pyreverse's `-o dot` output of a layer (`agentic/core/<context>/<layer>`) and
the grouping tables of `groups.py` in, a mapping of page key to `ClassPage` out. No file is
read and pyreverse is never run (`generate.py` is the only caller); `render.py` turns each
`ClassPage` into a `.md` file.

Why dot: pyreverse's mmd output keeps only the last name of a class, so homonyms (`Entry`,
`Pair`, `Verdict`...) merge; the dot output keeps the qualified name. A class is identified by
`canonical` (`module.Class`, relative to the layer package) and drawn under `node_id`
(`module__Class`); its label is the short name unless another class on the same page has it,
then `module.Class`. Two homonyms are never merged.

What the real run showed (pyreverse 4.1.3 over `agentic/core/process/domain`, `-o dot -k
--filter-mode PUB_ONLY`), which the parser relies on:

- The relation kind is the edge's `arrowhead`: `empty` inheritance, `vee` association,
  `odiamond` aggregation and `diamond` composition (pylint's `dot_printer.ARROWS`). The runs
  emit the first three; no layer emits composition.
- `PUB_ONLY` filters members, not classes: a private class (`_Shelves`) is emitted with its
  edges. A page shows public classes only, so `parse` drops every class named `_<x>` and the
  edges that touch it, by that rule and no other.
- An edge direction is the dot one: inheritance and association run from the class that
  declares it to the base or the type; aggregation runs from the part to the owner.
- Every edge of the real run has both ends among its nodes; one that does not fails `parse`.
- A layer run lists the classes of the layer only; a class of another layer or context in it
  fails `parse` naming it, so two homonyms of two layers are two nodes on two pages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import packages

from agentic.core.process.domain.docs import DIAGRAM_BUDGET

CORE = "agentic.core"
TOP = "toplevel"
ALL = "all"
GROUPINGS = ("clusters", "kind", "layer")
KINDS = ("inherits", "composes", "aggregates", "associates")

_ARROWHEADS = {
    "empty": "inherits",
    "diamond": "composes",
    "odiamond": "aggregates",
    "vee": "associates",
}
_NODE = re.compile(r'"(?P<id>[^"]+)" \[(?P<attrs>.*)\];')
_EDGE = re.compile(r'"(?P<source>[^"]+)" -> "(?P<target>[^"]+)" \[(?P<attrs>.*)\];')
_HEADER = re.compile(r'digraph "[^"]*" \{|rankdir=\w+|charset="[^"]*"|\}')
_ARROWHEAD = re.compile(r'arrowhead="(?P<head>[a-z]+)"')


@dataclass(frozen=True)
class ClassNode:
    """A class of a layer: `qname` is its canonical `module.Class`, `module` the part
    before the last dot and `name` the short name."""

    qname: str
    module: str
    name: str


@dataclass(frozen=True)
class Relation:
    """`source` and `target` are canonical names; `kind` is one of `KINDS`."""

    source: str
    target: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"relation kind {self.kind!r} is not one of {', '.join(KINDS)}")


Parsed = tuple[tuple[ClassNode, ...], tuple[Relation, ...]]


@dataclass(frozen=True)
class ClassPage:
    """One page of the class diagrams. `key` names it; `title` is how links to it read and
    `sentence` its Spanish first sentence; `parent` is the key of the page above it (None for
    the index) and `siblings` the keys of the pages beside it; `nodes` are `(id, label)` and
    `edges` `(source id, target id, kind)`, sorted, each once; `elsewhere` are the relations
    `(source, target, kind)`, in canonical names, between a class of this page and a class of
    another page, which the diagram does not draw."""

    key: str
    title: str
    sentence: str
    parent: str | None
    siblings: tuple[str, ...]
    nodes: tuple[tuple[str, str], ...]
    edges: tuple[tuple[str, str, str], ...]
    elsewhere: tuple[tuple[str, str, str], ...]


def package_of(context: str, layer: str) -> str:
    """The Python package a layer's dot run is over: `agentic.core.<context>.<layer>`."""
    return f"{CORE}.{context}.{layer}"


def layer_key(context: str, layer: str) -> str:
    """The key of the index page of a layer, `<context>-<layer>`; its pages are
    `<context>-<layer>-<group>[-<n>]`."""
    return f"{context}-{layer}"


def canonical(qname: str, context: str, layer: str) -> str:
    """`qname` relative to the layer package: `agentic.core.<context>.<layer>.<module>.<Class>`
    becomes `<module>.<Class>` (`module` may have dots: `claude_sdk.hooks`). A name outside the
    layer, or with no module part, raises."""
    prefix = package_of(context, layer) + "."
    rest = qname.removeprefix(prefix)
    if rest == qname or "." not in rest or not all(rest.split(".")):
        raise ValueError(
            f"{qname!r} is not a class of {package_of(context, layer)}: "
            f"expected `{prefix}<module>.<Class>`"
        )
    return rest


def node_id(module: str, name: str) -> str:
    """The Mermaid id of a class, `module__Class`; no other function builds one. A segment
    with `__` or an edge underscore would make two classes share an id, so it raises."""
    qualified = f"{module}.{name}"
    for segment in qualified.split("."):
        if not segment or "__" in segment or segment.startswith("_") or segment.endswith("_"):
            raise ValueError(
                f"{qualified!r} cannot be an id: segment {segment!r} has `__` or an edge `_`"
            )
    return qualified.replace(".", "__")


def _node(qname: str, context: str, layer: str) -> ClassNode:
    name = canonical(qname, context, layer)
    module, _, short = name.rpartition(".")
    return ClassNode(name, module, short)


def _private(node: ClassNode) -> bool:
    return node.name.startswith("_")


def _kind(attrs: str, line: str) -> str:
    found = _ARROWHEAD.search(attrs)
    kind = _ARROWHEADS.get(found["head"]) if found else None
    if kind is None:
        raise ValueError(
            f"cannot tell the kind of this dot edge (arrowhead {found['head'] if found else None!r}"
            f", known: {', '.join(sorted(_ARROWHEADS))}): {line}"
        )
    return kind


def parse(dot_text: str, context: str, layer: str) -> Parsed:
    """The public classes and relations of one pyreverse dot output over
    `agentic/core/<context>/<layer>`.

    Nodes are unique and sorted; relations are unique by `(source, target, kind)` and sorted.
    A line that is neither a node, an edge nor the dot frame, an id outside the layer, an edge
    of an unknown kind and an edge whose end is not a node of the text raise `ValueError`
    naming it. Private classes and the edges that touch them are dropped (see the module)."""
    nodes: dict[str, ClassNode] = {}
    edges: list[tuple[str, str, str, str]] = []
    for line in dot_text.splitlines():
        text = line.strip()
        if not text or _HEADER.fullmatch(text):
            continue
        edge = _EDGE.fullmatch(text)
        if edge:
            source = canonical(edge["source"], context, layer)
            target = canonical(edge["target"], context, layer)
            edges.append((source, target, _kind(edge["attrs"], text), text))
            continue
        node = _NODE.fullmatch(text)
        if node is None:
            raise ValueError(f"cannot read this line of the dot output: {line}")
        found = _node(node["id"], context, layer)
        nodes[found.qname] = found
    for source, target, _, text in edges:
        if source not in nodes or target not in nodes:
            raise ValueError(f"this dot edge has an end that is not a class of the output: {text}")
    public = {qname: node for qname, node in nodes.items() if not _private(node)}
    relations = {
        Relation(source, target, kind)
        for source, target, kind, _ in edges
        if source in public and target in public
    }
    return _ordered(public.values(), relations)


def _ordered(nodes: Iterable[ClassNode], relations: Iterable[Relation]) -> Parsed:
    return (
        tuple(sorted(set(nodes), key=lambda node: (node.module, node.name))),
        tuple(sorted(set(relations), key=lambda rel: (rel.source, rel.target, rel.kind))),
    )


def merge(*parsed: Parsed) -> Parsed:
    """The classes and relations of several runs of one layer, each class and each relation
    once, by canonical name."""
    nodes: dict[str, ClassNode] = {}
    relations: set[Relation] = set()
    for run_nodes, run_relations in parsed:
        for node in run_nodes:
            nodes[node.qname] = node
        relations.update(run_relations)
    return _ordered(nodes.values(), relations)


def labels(nodes: Iterable[ClassNode]) -> dict[str, str]:
    """The label of each class of one page, by canonical name: the short name, or
    `module.Class` when another class of the page has the same short name."""
    found = list(nodes)
    count = {
        name: sum(1 for node in found if node.name == name) for name in {n.name for n in found}
    }
    return {node.qname: node.name if count[node.name] == 1 else node.qname for node in found}


def _group_of(
    node: ClassNode,
    context: str,
    layer: str,
    grouping: str,
    clusters: Mapping[str, str],
) -> str:
    """The group a class belongs to: its cluster (`clusters`), its adapter kind (`kind`) or the
    whole layer (`layer`)."""
    key = f"{layer}.{node.module}"
    if grouping == "layer":
        return ALL
    if grouping == "kind":
        if layer != packages.ADAPTERS:
            raise ValueError(
                f"{context}/{layer} is grouped by `kind`, which only `{packages.ADAPTERS}` has"
            )
        return packages.subpackage(key).partition(".")[2] or TOP
    if key not in clusters:
        known = sorted({v for k, v in clusters.items() if k.startswith(f"{layer}.")})
        raise ValueError(
            f"{context} module {key!r} is in no cluster: add the line "
            f'`    "{key}": "<cluster>",` to the cluster table of {context} in '
            f"scripts/docs/groups.py, sorted (the clusters of {layer} are: {', '.join(known)})"
        )
    return clusters[key]


def _grouping_of(grouping: Mapping[str, str], context: str, layer: str) -> str:
    key = f"{context}/{layer}"
    if key not in grouping:
        raise ValueError(
            f"{key!r} has no grouping: add the line "
            f'`    "{key}": "<{"|".join(GROUPINGS)}>",` to CLASS_GROUPING in scripts/docs/groups.py, '
            "sorted"
        )
    if grouping[key] not in GROUPINGS:
        raise ValueError(
            f"{key!r} is grouped by {grouping[key]!r} in CLASS_GROUPING; the groupings are "
            f"{', '.join(GROUPINGS)}"
        )
    return grouping[key]


def _sentence(
    group: str,
    package: str,
    part: tuple[int, int] | None,
    separate: tuple[str, ...],
) -> str:
    scope = (
        f"de todos los módulos de `{package}`"
        if group == ALL
        else f"de los módulos del grupo `{group}` de `{package}`"
    )
    text = (
        f"Esta página muestra las clases públicas {scope} y sus relaciones (herencia, "
        "composición, agregación y asociación); las relaciones con clases de otras páginas no "
        "se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su "
        "nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo."
    )
    if part is not None:
        text += (
            f" El grupo se dividió por cantidad: esta es la parte {part[0]} de {part[1]}, con "
            "las clases en orden alfabético por módulo y clase."
        )
    if separate:
        names = ", ".join(f"`{module}`" for module in separate)
        text += (
            f" Las relaciones entre las clases de {names} y las demás clases de la capa no se "
            "dibujan: pyreverse analizó ese archivo en una corrida aparte."
        )
    return text


def _index_sentence(package: str, grouping: str) -> str:
    how = {
        "clusters": "una página por grupo de módulos, definido en `scripts/docs/groups.py`",
        "kind": "una página por tipo de adaptador y una para los módulos sueltos",
        "layer": "una página con toda la capa",
    }[grouping]
    return (
        f"Esta página es el índice de los diagramas de clases de `{package}`: {how}, dividida "
        "por cantidad cuando el grupo tiene más clases de las que caben en un diagrama."
    )


_Group = tuple[str, str, str, tuple[int, int] | None, list[ClassNode]]


def _groups(
    found: list[ClassNode],
    key: str,
    layer: str,
    grouping: str,
    clusters: Mapping[str, str],
    context: str,
) -> list[_Group]:
    """`(page key, title, group, part, classes)` for each page, in key order."""
    by_group: dict[str, list[ClassNode]] = {}
    for node in found:
        by_group.setdefault(_group_of(node, context, layer, grouping, clusters), []).append(node)
    groups: list[_Group] = []
    for group in sorted(by_group):
        members = by_group[group]
        base = f"{key}-{group}"
        title = f"{layer}: {group}"
        if len(members) <= DIAGRAM_BUDGET.classes:
            groups.append((base, title, group, None, members))
            continue
        if grouping == "layer":
            raise ValueError(
                f"{context}/{layer} has {len(members)} public classes, over the "
                f"{DIAGRAM_BUDGET.classes} of a diagram: group it by clusters, with a table of "
                "modules in scripts/docs/groups.py and the line "
                f'`    "{context}/{layer}": "clusters",` in CLASS_GROUPING'
            )
        by_name = {node.qname: node for node in members}
        runs = packages.chunks([node.qname for node in members])
        for number, run in enumerate(runs, start=1):
            split = (number, len(runs))
            groups.append(
                (
                    f"{base}-{number}",
                    f"{layer}: {group} (parte {number})",
                    group,
                    split,
                    [by_name[q] for q in run],
                )
            )
    return groups


def pages(
    nodes: Iterable[ClassNode],
    relations: Iterable[Relation],
    context: str,
    layer: str,
    grouping: Mapping[str, str],
    clusters: Mapping[str, str],
    separate: Iterable[str] = (),
) -> dict[str, ClassPage]:
    """The class pages of one layer, by key: the index `<context>-<layer>`, then one page per
    group, `<context>-<layer>-<group>`, or `<context>-<layer>-<group>-<n>` when the group has
    more than `DIAGRAM_BUDGET.classes` classes: the classes, ordered by module then class, are
    split by `packages.chunks`.

    `grouping` is `CLASS_GROUPING`: the line of `<context>/<layer>` says whether a class takes
    the cluster of its module (`clusters`, from the table `clusters`, keyed `<layer>.<module>`),
    its adapter kind (`kind`, `packages.subpackage`) or the one page of the layer (`layer`,
    which fails if it is over the budget). A layer with no line, a module with no cluster and a
    layer over the budget raise naming what to add. A relation whose ends are on two pages is
    in `elsewhere` of both and not in `edges`. `separate` are the modules whose file had a run
    of its own: each page with one of them says its relations with the rest of the layer are
    not drawn. No class gives no page at all."""
    found = sorted(set(nodes), key=lambda node: (node.module, node.name))
    if not found:
        return {}
    how = _grouping_of(grouping, context, layer)
    key = layer_key(context, layer)
    listed = _groups(found, key, layer, how, clusters, context)
    page_of = {node.qname: page for page, _, _, _, members in listed for node in members}
    ordered = sorted(set(relations), key=lambda rel: (rel.source, rel.target, rel.kind))
    for rel in ordered:
        for end in (rel.source, rel.target):
            if end not in page_of:
                raise ValueError(
                    f"relation {rel.source} -> {rel.target} ({rel.kind}) has an end, {end!r}, "
                    "that is not one of the classes given"
                )
    keys = [page for page, *_ in listed]
    package = package_of(context, layer)
    result = {
        key: ClassPage(
            key, f"Clases de {layer}", _index_sentence(package, how), None, (), (), (), ()
        )
    }
    apart = set(separate)
    for page, title, group, part, members in listed:
        label = labels(members)
        ids = {node.qname: node_id(node.module, node.name) for node in members}
        drawn = tuple(
            (ids[rel.source], ids[rel.target], rel.kind)
            for rel in ordered
            if rel.source in ids and rel.target in ids
        )
        elsewhere = tuple(
            (rel.source, rel.target, rel.kind)
            for rel in ordered
            if (rel.source in ids) != (rel.target in ids)
        )
        modules = tuple(sorted({node.module for node in members} & apart))
        result[page] = ClassPage(
            page,
            title,
            _sentence(group, package, part, modules),
            key,
            tuple(other for other in keys if other != page),
            tuple(sorted((ids[node.qname], label[node.qname]) for node in members)),
            tuple(sorted(drawn)),
            elsewhere,
        )
    return result
