"""The optional `docs` block of the product stack: the command that regenerates every
generated doc artifact and the paths that command owns.

Declared under `docs` in `.agentic/stack.yaml` (`core.knowledge.domain.stack`) and validated
there, at load -- a broken declaration never reaches this module, which is pure: no
filesystem, no YAML, nothing of a stack the domain does not already hand it as a `Docs`.
Read at the task's base, like `checks` and `design`, never at the branch's HEAD."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Docs:
    """The product's docs block, once `core.knowledge.domain.stack.docs` has validated it.
    `generate` is the one-line command that rewrites every generated doc artifact; `paths`
    are the globs it owns, each validated at load. `mode` says what the system does with the
    owned files: `commit` (the default) has a branch commit the regenerated output, `validate`
    has it only prove that `generate` still works while the system keeps those files out of
    the branch (master regenerates them after each merge). Every text built from a `Docs`
    branches on `mode` and on nothing else."""

    generate: str
    paths: tuple[str, ...]
    mode: Literal["commit", "validate"] = "commit"


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


def _segment_regex(segment: str) -> re.Pattern[str]:
    """One path segment of a glob as a regex: `*` any run of characters, `?` one, `[...]` a
    class (`!` or `^` negates); a segment never holds a `/`, so none of them crosses one."""
    out: list[str] = []
    position = 0
    while position < len(segment):
        character = segment[position]
        position += 1
        if character == "*":
            out.append(".*")
        elif character == "?":
            out.append(".")
        elif character == "[" and (close := segment.find("]", position + 1)) != -1:
            body = segment[position:close]
            negated = body[:1] in ("!", "^")
            body = body[1:] if negated else body
            out.append(f"[{'^' if negated else ''}{re.escape(body).replace(chr(92) + '-', '-')}]")
            position = close + 1
        else:
            out.append(re.escape(character))
    return re.compile("".join(out), re.DOTALL)


def _segments_match(pattern: list[str], path: list[str]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        if not rest:
            return bool(path)
        return any(_segments_match(rest, path[skipped:]) for skipped in range(len(path) + 1))
    if not path:
        return False
    return _segment_regex(head).fullmatch(path[0]) is not None and _segments_match(rest, path[1:])


def owned(docs: Docs, path: str) -> bool:
    """Whether `path` (repository-relative, `/`-separated) is under one of `docs.paths`: the
    one matcher every decision of the system uses, with git's `:(glob)` pathspec semantics.

    `*`, `?` and `[...]` never cross a `/`; a whole segment `**` spans any number of
    directories (`**/x` also matches `x`, `a/**/b` also `a/b`, a trailing `a/**` everything
    inside `a`); a path equal to an owned entry, or under it as a directory, is owned. An
    entry is matched as git matches it, so the agreement is checked against
    `git ls-files -- ':(glob)<entry>'`; backslash escapes are not supported."""
    segments = path.split("/")
    for entry in docs.paths:
        if path == entry or path.startswith(entry.rstrip("/") + "/"):
            return True
        if _segments_match(entry.split("/"), segments):
            return True
    return False


Ancestry = Literal["same", "ours-older", "theirs-older", "diverged"]


def split_conflicts(docs: Docs, paths: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`paths` (conflicted ones, in the order given) split into `(owned, others)` by the one
    matcher `owned`. A conflict confined to the owned paths has no `others`."""
    mine = tuple(path for path in paths if owned(docs, path))
    return mine, tuple(path for path in paths if path not in mine)


def settle_source(ours_base: str, theirs_base: str, ancestry: Ancestry) -> str | None:
    """The one commit a conflict confined to the owned paths is restored from: the newer of
    the two merge-bases with master, `ours_base` and `theirs_base`. `ancestry` says how the
    two compare: `same`, one `ours-older`/`theirs-older` than the other, or `diverged`, where
    neither is newer and the answer is `None` (the system then does not act)."""
    if ancestry in ("same", "theirs-older"):
        return ours_base
    if ancestry == "ours-older":
        return theirs_base
    return None


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
    generated = (
        f"Generated files under {_owned(docs)} are never edited nor committed in a feature "
        "branch: master regenerates them after each merge and the system keeps them out of "
        f"the delivery, so no brief asks the Developer to run `{docs.generate}` to commit its "
        "output. "
        if docs.mode == "validate"
        else f"Generated files under {_owned(docs)} are never edited by hand: the brief has "
        f"the Developer regenerate them by running `{docs.generate}`. "
    )
    return (
        "\nThis product declares technical docs (`docs:` in `.agentic/stack.yaml`). When a "
        "subtask changes architecture (a container, a component, a contract between modules, "
        "the data model or a key flow), that same subtask updates the written docs: the C4 "
        "context, container and component views and the sequence diagrams of key flows, in "
        "Mermaid, under `docs/architecture/`, and the technical sections of `docs/README.md` "
        "(architecture, API or CLI reference, ADR index). If `docs/README.md` does not exist "
        f"at the base, the first architecture subtask creates it. {generated}The prose is in "
        "Spanish (Rioplatense); code, identifiers and diagram names stay in English; every "
        "claim cites the file or symbol it rests on. You never write the files; the Developer "
        "of the subtask does."
        f"{_budget_plan_sentence()}"
    )


def review_text(docs: Docs | None) -> str:
    """What the reviewing Tech Lead is told when the product declares `docs`, empty when it
    declares none. It starts with its own blank line, like `plan_text` with its newline."""
    if docs is None:
        return ""
    generated = (
        f"- Generated files under {_owned(docs)} belong to the system: never ask for a hand "
        "edit or for a regeneration to commit; the delivery gate restores them and master "
        "regenerates them after each merge."
        if docs.mode == "validate"
        else f"- Generated files under {_owned(docs)} belong to the `docs` check: never ask "
        f"for a hand edit, the fix is to run `{docs.generate}`."
    )
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
        f"{generated}"
        f"{_budget_review_bullet()}"
    )


def conflict_line(docs: Docs | None) -> str:
    """The sentence a merge-conflict text gains when the product declares `docs`, empty when
    it declares none: generated files are regenerated, never merged by hand."""
    if docs is None:
        return ""
    if docs.mode == "validate":
        return (
            f" The system resolves a conflict confined to {_owned(docs)} by itself, from "
            "master's side: you never touch those files, so leave them out of any resolution."
        )
    return (
        f" For conflicted files under {_owned(docs)}, do not merge them by hand: take either "
        f"side, run `{docs.generate}` and commit its output."
    )
