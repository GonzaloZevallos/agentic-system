"""Write the generated documentation of agentic-system, deterministically (GH-400, ADR-0076).

`uv run python scripts/docs/generate.py [--root DIR]` writes, under `DIR`, the role graph, the
packages and classes of the three modules, the CLI reference and the stack reference. The
packages of `process` come from the import reader (`imports.py`), everything else from
pyreverse. `DIR` defaults to the repository this file lives in, never to the working
directory. What is
written is decided by `render.py`, next to this file (Python puts the script's directory on
`sys.path`); this file is the edge that builds the inputs: the argparse tree, the compiled
graph (without a checkpointer: no SQLite file is opened) and the pyreverse runs. It writes
nothing outside `DIR`.
"""

from __future__ import annotations

import argparse
import functools
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import classes
import groups
import imports
import packages
import render

REPO = Path(__file__).resolve().parents[2]
HELP_WIDTH = 100
# pyreverse 4.1.3 crashes inside astroid (`brain_typing.infer_typing_alias`, a `str > int`
# comparison) when `adapters/github/board.py`, whose function body annotates a local with
# `Literal["open", "closed"]`, is analysed together with any other module. Each file alone is
# fine. So the layer runs skip it and its classes come from a run over that file alone,
# merged in (ADR-0076). The package diagrams lack its node: a single-file run draws none.
PYREVERSE_IGNORE = "board.py"
PYREVERSE_FLAGS = ("-k", "--filter-mode", "PUB_ONLY")
# The class pages of every layer come from the dot output (it keeps qualified class names, so
# homonyms are never merged). Until the pages are written (GH-430 part 4, second subtask) these
# layers keep their `.mmd` and their pages are only built and checked in memory.
LEGACY_MMD = frozenset(
    {
        ("knowledge", "adapters"),
        ("knowledge", "domain"),
        ("measurement", "adapters"),
        ("measurement", "application"),
        ("measurement", "domain"),
        ("process", "adapters"),
        ("process", "application"),
    }
)


class GenerateError(Exception):
    """A reason the docs cannot be generated; `main` prints it and exits 1."""


def cli_helps() -> list[tuple[tuple[str, ...], str]]:
    """Every parser of a freshly built tree with its `format_help()`, at a fixed width and
    without color, in the tree's creation order, the root first."""
    from agentic import cli

    tree = cli.build_parser()
    parsers = [((), tree.root), *tree.commands.items()]
    helps: list[tuple[tuple[str, ...], str]] = []
    for path, parser in parsers:
        parser.formatter_class = functools.partial(argparse.HelpFormatter, width=HELP_WIDTH)
        if hasattr(parser, "color"):
            parser.color = False
        helps.append((path, parser.format_help()))
    return helps


Edges = list[tuple[str, str, bool]]


def _plain(graph: Any) -> tuple[list[str], Edges]:
    """The nodes and the `(source, target, conditional)` edges of a LangGraph drawable graph,
    read from its `.nodes` and `.edges`; never from the text of `draw_mermaid()` (ADR-0076)."""
    return list(graph.nodes), [(e.source, e.target, bool(e.conditional)) for e in graph.edges]


def role_graph_inputs() -> tuple[list[str], Edges, list[str], Edges, bool]:
    """What `render.role_graph_pages` takes: the nodes and edges of the compiled task graph
    (subgraphs expanded, `xray=True`; no checkpointer, so no database is opened), those of the
    `develop -> review` branch, and whether the CLI has the `land` subcommand."""
    from agentic import cli, composition

    nodes, edges = _plain(composition.graph.compile().get_graph(xray=True))
    branch_nodes, branch_edges = _plain(branch_graph().get_graph())
    has_land = render.has_subcommand(cli.build_parser().root, "land")
    return nodes, edges, branch_nodes, branch_edges, has_land


def branch_graph() -> Any:
    """The compiled `develop -> review` graph one subtask runs inside the `pipeline` node,
    from the adapter's public `pipeline_branch`. Nothing runs, so no port is ever asked for."""
    from agentic.core.process.adapters.langgraph.graph import pipeline_branch

    def no_ports(cfg: object, config: object) -> Any:
        raise RuntimeError("drawing the graph asks for no ports")

    return pipeline_branch(no_ports, arm_for=lambda cfg: "")


def package_page_inputs() -> tuple[dict[str, bool], dict[str, set[str]]]:
    """What `packages.build_pages` takes besides `CLUSTERS`: every module under `agentic/` on
    disk, mapped to whether it is a package `__init__` (named by its package), and the names
    `imports_of` reads from each file of `process`, `knowledge` and `measurement`."""
    on_disk: dict[str, bool] = {}
    imported: dict[str, set[str]] = {}
    for file in sorted((REPO / "agentic").rglob("*.py")):
        parts = file.relative_to(REPO).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        name = ".".join(parts[:-1] if is_package else parts)
        on_disk[name] = is_package
        if not is_package and any(name.startswith(f"agentic.core.{m}.") for m in render.MODULES):
            imported[name] = imports.imports_of(file, REPO)
    return on_disk, imported


def _pyreverse(
    name: str, targets: Sequence[Path], ignore: bool = True, output: str = "mmd"
) -> dict[str, str]:
    """Run pyreverse over `targets` with the fixed project name `name`, writing `output` (`mmd`
    or `dot`); the files it wrote."""
    environment = {"PYTHONHASHSEED": "0", "PATH": os.environ.get("PATH", "")}
    if "HOME" in os.environ:
        environment["HOME"] = os.environ["HOME"]
    with tempfile.TemporaryDirectory(prefix="agentic-docs-") as scratch:
        command = [
            sys.executable,
            "-m",
            "pylint.pyreverse.main",
            "-o",
            output,
            *PYREVERSE_FLAGS,
            *(("--ignore", PYREVERSE_IGNORE) if ignore else ()),
            "-p",
            name,
            "-d",
            scratch,
            *map(str, targets),
        ]
        try:
            done = subprocess.run(
                command,
                cwd=scratch,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except OSError as error:
            raise GenerateError(f"pyreverse could not start: {error}") from error
        if done.returncode != 0:
            raise GenerateError(
                f"pyreverse exited {done.returncode} for {name}: {done.stderr.strip()}"
            )
        return {
            file.name: file.read_text(encoding="utf-8") for file in sorted(Path(scratch).iterdir())
        }


def _module_dir(module: str) -> Path:
    return REPO / "agentic" / "core" / module


def _diagram(name: str, targets: Sequence[Path], kind: str, ignore: bool = True) -> str:
    files = _pyreverse(name, targets, ignore)
    try:
        return render.select_mermaid(files, kind)
    except ValueError as error:
        raise GenerateError(f"pyreverse emitted no mmd for {name}: {error}") from error


def _with_ignored(diagram: str, name: str, files: Sequence[Path]) -> str:
    """`diagram` plus the classes of the files pyreverse crashes on in company."""
    extras = [
        _diagram(name, [file], "classes", ignore=False)
        for file in files
        if file.name == PYREVERSE_IGNORE
    ]
    return render.merge_mermaid([diagram, *extras]) if extras else diagram


def cluster_table(context: str) -> Mapping[str, str]:
    """The table of clusters of `context` in `groups.py`; empty for a context with none."""
    tables = {"process": groups.CLUSTERS, "measurement": groups.MEASUREMENT_CLUSTERS}
    return tables.get(context, {})


def class_pages(context: str, layer: str) -> dict[str, str]:
    """The class pages of `agentic/core/<context>/<layer>`: pyreverse's dot output over the
    layer, plus a run over each file named `PYREVERSE_IGNORE` alone (the layer run skips it, see
    there), parsed by `classes.parse` and merged by qualified name. The relations between the
    classes of such a file and the rest of the layer are lost, as they are with mmd (ADR-0076),
    and the pages that hold them say so. Relative path -> text; empty when the layer is not
    there or has no public class."""
    name = f"{context}-{layer}"
    directory = _module_dir(context) / layer
    if not directory.is_dir():
        return {}
    runs = [(_pyreverse(name, [directory], output="dot"), name, False)]
    runs += [
        (_pyreverse(name, [file], ignore=False, output="dot"), f"{name} ({file.name})", True)
        for file in sorted(directory.rglob("*.py"), key=lambda file: file.as_posix())
        if file.name == PYREVERSE_IGNORE
    ]
    parsed = []
    separate: set[str] = set()
    for files, label, alone in runs:
        found = sorted(file for file in files if file.startswith("classes_"))
        if len(found) != 1:
            raise GenerateError(f"pyreverse wrote {len(found)} `classes_*.dot` files for {label}")
        try:
            run = classes.parse(files[found[0]], context, layer)
        except ValueError as error:
            raise GenerateError(f"{label}: {error}") from error
        parsed.append(run)
        if alone:
            separate.update(node.module for node in run[0])
    try:
        model = classes.pages(
            *classes.merge(*parsed),
            context,
            layer,
            groups.CLASS_GROUPING,
            cluster_table(context),
            separate,
        )
    except ValueError as error:
        raise GenerateError(str(error)) from error
    return render.class_pages(model)


def class_diagrams(module: str, layers: Sequence[str]) -> dict[str, str]:
    """`<module>-<layer>[-<n>]` -> class diagram, for each layer that exists and has a class.
    A layer over the Mermaid limit is split in at most two chunks of consecutive source files
    in alphabetical order; if two are not enough this refuses."""
    found: dict[str, str] = {}
    for layer in layers:
        directory = _module_dir(module) / layer
        if not directory.is_dir():
            continue
        name = f"{module}-{layer}"
        every = sorted(directory.rglob("*.py"), key=lambda file: file.as_posix())
        whole = _with_ignored(_diagram(name, [directory], "classes"), name, every)
        if not render.has_classes(whole):
            continue
        try:
            render.mermaid_limits(whole)
        except ValueError:
            middle = (len(every) + 1) // 2
            for number, chunk in enumerate((every[:middle], every[middle:]), start=1):
                diagram = _with_ignored(_diagram(name, chunk, "classes"), name, chunk)
                try:
                    render.mermaid_limits(diagram)
                except ValueError as error:
                    raise GenerateError(
                        f"{name} is over the Mermaid limit even in two chunks: {error}"
                    ) from error
                if render.has_classes(diagram):
                    found[f"{name}-{number}"] = diagram
        else:
            found[name] = whole
    return found


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_stale(root: Path, kept: Sequence[str]) -> None:
    """Files under the two owned directories that this run does not write."""
    wanted = set(kept)
    for owned in (render.GENERATED, render.REFERENCE):
        directory = root / owned
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*")):
            relative = file.relative_to(root).as_posix()
            if file.is_file() and relative not in wanted:
                file.unlink()


def generate(
    root: Path, layers: Mapping[str, tuple[str, ...]] = render.LAYERS
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Write every generated file under `root`. `layers` maps each module to draw (its
    packages and its classes) to its layers; every package diagram is a page built from the
    import reader, and the class pages are built from pyreverse's dot output (those of the
    layers in `LEGACY_MMD` are only checked, not written). Returns the relative paths written,
    sorted, the names of the `.mmd` class diagrams among them and the paths of the role-graph
    pages and of the package and class pages among them: what `render.outputs` takes."""
    from agentic.core.knowledge.domain import stack

    try:
        role_pages = render.role_graph_pages(*role_graph_inputs())
    except ValueError as error:
        raise GenerateError(str(error)) from error
    package_texts: dict[str, str] = {}
    try:
        on_disk, imported = package_page_inputs()
        for module in layers:
            if module == "process":
                model = packages.build_pages(on_disk, imported, groups.CLUSTERS)
            elif module in render.FLAT_PACKAGES:
                model = packages.build_pages(
                    on_disk, imported, {}, f"agentic.core.{module}", render.FLAT_PACKAGES[module]
                )
            else:
                continue
            package_texts.update(render.package_pages(model, module))
    except ValueError as error:
        raise GenerateError(str(error)) from error
    class_texts: dict[str, str] = {}
    unwritten: dict[str, str] = {}
    for module, module_layers in layers.items():
        for layer in module_layers:
            target = unwritten if (module, layer) in LEGACY_MMD else class_texts
            target.update(class_pages(module, layer))
    waiting = [
        (relative, over)
        for relative, text in sorted(unwritten.items())
        for over in render.diagram_budget(text)
    ]
    if message := budget_message(waiting):
        raise GenerateError(message)
    texts: dict[str, str] = {
        **role_pages,
        **package_texts,
        **class_texts,
        render.CLI_REFERENCE: render.cli_reference(cli_helps()),
        render.STACK_REFERENCE: render.stack_reference(stack.KEY_REFERENCE),
    }
    names: list[str] = []
    for module, module_layers in layers.items():
        mmd_layers = tuple(layer for layer in module_layers if (module, layer) in LEGACY_MMD)
        for name, diagram in class_diagrams(module, mmd_layers).items():
            texts[render.classes_path(name)] = diagram
            names.append(name)
    for relative, text in texts.items():
        try:
            render.mermaid_limits(text)
        except ValueError as error:
            raise GenerateError(f"{relative}: {error}") from error
    written = tuple(sorted(texts))
    for relative in written:
        _write(root, relative, texts[relative])
    _remove_stale(root, written)
    if message := budget_message(render.check_budget(root)):
        raise GenerateError(message)
    return (
        written,
        tuple(sorted(names)),
        tuple(sorted(role_pages)),
        tuple(sorted({*package_texts, *class_texts})),
    )


def budget_message(overages: Sequence[tuple[str, render.Overage]]) -> str:
    """One line per page over `DIAGRAM_BUDGET` (file, block, kind, counts against the limits)
    and the fix, or an empty text when there is none."""
    if not overages:
        return ""
    budget = render.DIAGRAM_BUDGET
    lines = [
        f"{relative}: block {over.block} ({over.kind}) has {over.nodes} nodes, {over.edges} "
        f"edges, {over.classes} classes; the budget is {budget.nodes} nodes, {budget.edges} "
        f"edges, {budget.classes} classes (over: {', '.join(over.limits)})"
        for relative, over in overages
    ]
    lines.append(
        "The fix is to split the page by meaning into smaller `.md` pages; regenerating does "
        "not fix it."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate.py", description="Write the generated docs of agentic-system."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO,
        help="the directory to write under (default: the repository this script is in)",
    )
    arguments = parser.parse_args(argv)
    try:
        generate(arguments.root.resolve())
    except GenerateError as error:
        print(f"generate.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
