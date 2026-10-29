"""The optional `docs` block of the product stack: the command that regenerates every
generated doc artifact and the paths that command owns.

Declared under `docs` in `.agentic/stack.yaml` (`core.knowledge.domain.stack`) and validated
there, at load -- a broken declaration never reaches this module, which is pure: no
filesystem, no YAML, nothing of a stack the domain does not already hand it as a `Docs`.
Read at the task's base, like `checks` and `design`, never at the branch's HEAD."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Docs:
    """The product's docs block, once `core.knowledge.domain.stack.docs` has validated it.
    `generate` is the one-line command that rewrites every generated doc artifact; `paths`
    are the globs it owns, each validated at load."""

    generate: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class DiagramBudget:
    """The most a Mermaid diagram under `docs/architecture/` may hold before GitHub renders
    it as an unreadable wall: flowchart/graph `nodes` and `edges`, classDiagram `classes`.
    Going over means strictly more than the number."""

    nodes: int
    edges: int
    classes: int


DIAGRAM_BUDGET = DiagramBudget(nodes=20, edges=30, classes=15)


def _owned(docs: Docs) -> str:
    return ", ".join(f"`{path}`" for path in docs.paths)


def _budget_plan_sentence() -> str:
    budget = DIAGRAM_BUDGET
    return (
        f" A Mermaid diagram over budget ({budget.nodes} nodes or {budget.edges} edges for a "
        f"flowchart or graph, {budget.classes} classes for a classDiagram) is split by "
        "meaning, never by count alone; each split page is a `.md` that starts by saying in "
        "Spanish what it shows and lists its parent and siblings."
    )


def _budget_review_bullet() -> str:
    budget = DIAGRAM_BUDGET
    return (
        f"\n- A diagram under `docs/architecture/` over budget (more than {budget.nodes} nodes "
        f"or {budget.edges} edges in a flowchart or graph, more than {budget.classes} classes "
        "in a classDiagram) is an `important` finding. Its evidence is the counted nodes, "
        "edges and classes against the budget. The fix is to split it by meaning into `.md` "
        "pages that say what they show and list their parent and siblings."
    )


def plan_text(docs: Docs | None) -> str:
    """What the planning Tech Lead is told when the product declares `docs`, empty when it
    declares none. It starts with its own newline: the prompt places the placeholder at the
    end of a line, so the empty text leaves the prompt exactly as it was, no blank line."""
    if docs is None:
        return ""
    return (
        "\nThis product declares technical docs (`docs:` in `.agentic/stack.yaml`). When a "
        "subtask changes architecture (a container, a component, a contract between modules, "
        "the data model or a key flow), that same subtask updates the written docs: the C4 "
        "context, container and component views and the sequence diagrams of key flows, in "
        "Mermaid, under `docs/architecture/`, and the technical sections of `docs/README.md` "
        "(architecture, API or CLI reference, ADR index). If `docs/README.md` does not exist "
        "at the base, the first architecture subtask creates it. Generated files under "
        f"{_owned(docs)} are never edited by hand: the brief has the Developer regenerate "
        f"them by running `{docs.generate}`. The prose is in Spanish (Rioplatense); code, "
        "identifiers and diagram names stay in English; every claim cites the file or symbol "
        "it rests on. You never write the files; the Developer of the subtask does."
        f"{_budget_plan_sentence()}"
    )


def review_text(docs: Docs | None) -> str:
    """What the reviewing Tech Lead is told when the product declares `docs`, empty when it
    declares none. It starts with its own blank line, like `plan_text` with its newline."""
    if docs is None:
        return ""
    return (
        "\n\nThis product declares technical docs (`docs:`). Review `docs/architecture/` and "
        "`docs/README.md` against the code, on the diff:\n\n"
        "- A boundary changed in the diff (a container, a component, a contract between "
        "modules, the data model or a key flow) with no change under `docs/architecture/` or "
        "`docs/README.md` in the same subtask is a `blocking` finding. Its evidence is the "
        "cited import, signature or model line plus an empty `git diff --stat <base> -- "
        "docs/architecture docs/README.md`.\n"
        "- A claim with no file or symbol reference, and prose not in Spanish, are "
        "`important` findings, judged only on the lines the diff adds or changes under those "
        "targets. Anything else in a doc the diff touches is scope `outside`.\n"
        "- A stale `docs/README.md` is raised only when the file exists at the base. A "
        "missing one is never `blocking` by itself; it is `important` only when the plan said "
        "to create it and the subtask did not.\n"
        f"- Generated files under {_owned(docs)} belong to the `docs` check: never ask for a "
        f"hand edit, the fix is to run `{docs.generate}`."
        f"{_budget_review_bullet()}"
    )


def conflict_line(docs: Docs | None) -> str:
    """The sentence a merge-conflict text gains when the product declares `docs`, empty when
    it declares none: generated files are regenerated, never merged by hand."""
    if docs is None:
        return ""
    return (
        f" For conflicted files under {_owned(docs)}, do not merge them by hand: take either "
        f"side, run `{docs.generate}` and commit its output."
    )
