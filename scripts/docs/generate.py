"""Write the generated documentation of agentic-system, deterministically (GH-400, ADR-0076).

`uv run python scripts/docs/generate.py [--root DIR]` writes, under `DIR`, the role graph, the
packages and classes of the three modules, the CLI reference and the stack reference. `DIR`
defaults to the repository this file lives in, never to the working directory. What is
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

import render

REPO = Path(__file__).resolve().parents[2]
HELP_WIDTH = 100
# pyreverse 4.1.3 crashes inside astroid (`brain_typing.infer_typing_alias`, a `str > int`
# comparison) when `adapters/github/board.py`, whose function body annotates a local with
# `Literal["open", "closed"]`, is analysed together with any other module. Each file alone is
# fine. So the layer runs skip it and its classes come from a run over that file alone,
# merged in (ADR-0076). The package diagrams lack its node: a single-file run draws none.
PYREVERSE_IGNORE = "board.py"
PYREVERSE_FLAGS = ("-o", "mmd", "-k", "--filter-mode", "PUB_ONLY")


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


def role_graph_mermaid() -> str:
    """LangGraph's drawing of the compiled graph, subgraphs expanded (`xray=True`). The graph
    is compiled with no checkpointer: nothing here opens a database."""
    from agentic import composition

    return composition.graph.compile().get_graph(xray=True).draw_mermaid()


def branch_graph() -> Any:
    """The compiled `develop -> review` graph one subtask runs inside the `pipeline` node,
    from the adapter's public `pipeline_branch`. Nothing runs, so no port is ever asked for."""
    from agentic.core.process.adapters.langgraph.graph import pipeline_branch

    def no_ports(cfg: object, config: object) -> Any:
        raise RuntimeError("drawing the graph asks for no ports")

    return pipeline_branch(no_ports, arm_for=lambda cfg: "")


def pipeline_branch_mermaid() -> str:
    return str(branch_graph().get_graph().draw_mermaid())


def _pyreverse(name: str, targets: Sequence[Path], ignore: bool = True) -> dict[str, str]:
    """Run pyreverse over `targets` with the fixed project name `name`; the files it wrote."""
    environment = {"PYTHONHASHSEED": "0", "PATH": os.environ.get("PATH", "")}
    if "HOME" in os.environ:
        environment["HOME"] = os.environ["HOME"]
    with tempfile.TemporaryDirectory(prefix="agentic-docs-") as scratch:
        command = [
            sys.executable,
            "-m",
            "pylint.pyreverse.main",
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
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Write every generated file under `root`. `layers` maps each module to draw (its
    packages and its classes) to its layers. Returns the relative paths written, sorted, and
    the names of the class diagrams among them."""
    from agentic.core.knowledge.domain import stack

    texts: dict[str, str] = {
        render.ROLE_GRAPH: render.role_graph(role_graph_mermaid(), pipeline_branch_mermaid()),
        render.CLI_REFERENCE: render.cli_reference(cli_helps()),
        render.STACK_REFERENCE: render.stack_reference(stack.KEY_REFERENCE),
    }
    names: list[str] = []
    for module, module_layers in layers.items():
        texts[render.packages_path(module)] = _diagram(module, [_module_dir(module)], "packages")
        for name, diagram in class_diagrams(module, module_layers).items():
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
    return written, tuple(sorted(names))


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
