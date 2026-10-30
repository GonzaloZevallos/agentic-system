"""The class diagrams of `agentic/core/process/domain`, as pages of plain data (GH-430 part 3).

Pure: the text of pyreverse's `-o dot` output and the `CLUSTERS` table in, a mapping of page key
to `ClassPage` out. No file is read and pyreverse is never run (`generate.py` is the only
caller); `render.py` turns each `ClassPage` into a `.md` file.

Why dot: pyreverse's mmd output keeps only the last name of a class, so homonyms (`Entry`,
`Pair`, `Verdict`...) merge; the dot output keeps the qualified name. A class is identified by
`canonical` (`module.Class`, relative to the domain package) and drawn under `node_id`
(`module__Class`); its label is the short name unless another class on the same page has it,
then `module.Class`. Two homonyms are never merged.

What the real run showed (pyreverse 4.1.3 over `agentic/core/process/domain`, `-o dot -k
--filter-mode PUB_ONLY`), which the parser relies on:

- The relation kind is the edge's `arrowhead`: `empty` inheritance, `vee` association,
  `odiamond` aggregation and `diamond` composition (pylint's `dot_printer.ARROWS`). The domain
  run emits only the first two; the other layers emit aggregation; no layer emits composition.
- `PUB_ONLY` filters members, not classes: a private class (`_Shelves`) is emitted with its
  edges. A page shows public classes only, so `parse` drops every class named `_<x>` and the
  edges that touch it, by that rule and no other.
- An edge direction is the dot one: inheritance and association run from the class that
  declares it to the base or the type; aggregation runs from the part to the owner.
- Every edge of the real run has both ends among its nodes; one that does not fails `parse`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import packages

from agentic.core.process.domain.docs import DIAGRAM_BUDGET

DOMAIN = "agentic.core.process.domain"
INDEX = "index"
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
    """A class of the domain: `qname` is its canonical `module.Class`, `module` the part
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


def canonical(qname: str) -> str:
    """`qname` relative to the domain package: `<DOMAIN>.<module>.<Class>` becomes
    `<module>.<Class>`. A name outside the domain, or with no module part, raises."""
    prefix = DOMAIN + "."
    rest = qname.removeprefix(prefix)
    if rest == qname or "." not in rest or not all(rest.split(".")):
        raise ValueError(
            f"{qname!r} is not a class of {DOMAIN}: expected `{prefix}<module>.<Class>`"
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


def _node(qname: str) -> ClassNode:
    name = canonical(qname)
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


def parse(dot_text: str) -> Parsed:
    """The public classes and relations of one pyreverse dot output.

    Nodes are unique and sorted; relations are unique by `(source, target, kind)` and sorted.
    A line that is neither a node, an edge nor the dot frame, an id outside the domain, an edge
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
            source, target = canonical(edge["source"]), canonical(edge["target"])
            edges.append((source, target, _kind(edge["attrs"], text), text))
            continue
        node = _NODE.fullmatch(text)
        if node is None:
            raise ValueError(f"cannot read this line of the dot output: {line}")
        found = _node(node["id"])
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
    """The classes and relations of several runs, each class and each relation once, by
    canonical name."""
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


def _cluster_of(node: ClassNode, clusters: Mapping[str, str]) -> str:
    key = f"domain.{node.module}"
    if key not in clusters:
        known = sorted({v for k, v in clusters.items() if k.startswith("domain.")})
        raise ValueError(
            f"process module {key!r} is in no cluster: add the line "
            f'`    "{key}": "<cluster>",` to CLUSTERS in scripts/docs/groups.py, '
            f"sorted (the clusters of domain are: {', '.join(known)})"
        )
    return clusters[key]


def _sentence(cluster: str, part: tuple[int, int] | None) -> str:
    text = (
        f"Esta página muestra las clases públicas de los módulos del grupo `{cluster}` de "
        f"`{DOMAIN}` y sus relaciones (herencia, composición, agregación y asociación); las "
        "relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. "
        "Cada clase se identifica por su módulo y su nombre: si dos clases de la página se "
        "llaman igual, la etiqueta incluye el módulo."
    )
    if part is not None:
        text += (
            f" El grupo se dividió por cantidad: esta es la parte {part[0]} de {part[1]}, con "
            "las clases en orden alfabético por módulo y clase."
        )
    return text


def _index_sentence() -> str:
    return (
        f"Esta página es el índice de los diagramas de clases de `{DOMAIN}`: una página por "
        "grupo de `CLUSTERS` (en `scripts/docs/groups.py`), dividida por cantidad cuando el "
        "grupo tiene más clases de las que caben en un diagrama."
    )


def pages(
    nodes: Iterable[ClassNode], relations: Iterable[Relation], clusters: Mapping[str, str]
) -> dict[str, ClassPage]:
    """The class pages, by key: `index`, then one page per cluster of `domain`
    (`process-domain-<cluster>`), or `process-domain-<cluster>-<n>` when the cluster has more
    than `DIAGRAM_BUDGET.classes` classes: the classes, ordered by module then class, are split
    by `packages.chunks`. A class takes the cluster of its module; a module with no entry in
    `clusters` raises naming it and the line to add. A relation whose ends are on two pages is
    in `elsewhere` of both and not in `edges`."""
    found = sorted(set(nodes), key=lambda node: (node.module, node.name))
    by_cluster: dict[str, list[ClassNode]] = {}
    for node in found:
        by_cluster.setdefault(_cluster_of(node, clusters), []).append(node)
    groups: list[tuple[str, str, str, tuple[int, int] | None, list[ClassNode]]] = []
    for cluster in sorted(by_cluster):
        members = by_cluster[cluster]
        base = f"process-domain-{cluster}"
        if len(members) <= DIAGRAM_BUDGET.classes:
            groups.append((base, f"domain: {cluster}", cluster, None, members))
            continue
        by_name = {node.qname: node for node in members}
        runs = packages.chunks([node.qname for node in members])
        for number, run in enumerate(runs, start=1):
            title = f"domain: {cluster} (parte {number})"
            split = (number, len(runs))
            groups.append((f"{base}-{number}", title, cluster, split, [by_name[q] for q in run]))
    page_of = {node.qname: key for key, _, _, _, members in groups for node in members}
    ordered = sorted(set(relations), key=lambda rel: (rel.source, rel.target, rel.kind))
    for rel in ordered:
        for end in (rel.source, rel.target):
            if end not in page_of:
                raise ValueError(
                    f"relation {rel.source} -> {rel.target} ({rel.kind}) has an end, {end!r}, "
                    "that is not one of the classes given"
                )
    keys = [key for key, *_ in groups]
    result = {
        INDEX: ClassPage(
            INDEX, f"Clases de {DOMAIN.rsplit('.', 1)[-1]}", _index_sentence(), None, (), (), (), ()
        )
    }
    for key, title, cluster, part, members in groups:
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
        result[key] = ClassPage(
            key,
            title,
            _sentence(cluster, part),
            INDEX,
            tuple(other for other in keys if other != key),
            tuple(sorted((ids[node.qname], label[node.qname]) for node in members)),
            tuple(sorted(drawn)),
            elsewhere,
        )
    return result
