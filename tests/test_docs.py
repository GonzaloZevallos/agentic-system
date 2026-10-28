"""The docs generator (`scripts/docs/`, GH-400, ADR-0076): determinism, the graph against the
compiled graph, the Mermaid limits, the stale check and the CI step.

Every test writes only under `tmp_path`; none reads a file under the `docs:` globs of this
repo, which the docs check deletes and restores while pytest runs (invariants 2 and 3).
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from agentic.core.knowledge.domain import stack

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts" / "docs"
SEPARATOR = ":"


@pytest.fixture(scope="module")
def scripts() -> Iterator[tuple[ModuleType, ModuleType]]:
    """`render` and `generate`, loaded by path; `generate` imports `render` by name."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        loaded = []
        for name in ("render", "generate"):
            spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
        yield loaded[0], loaded[1]
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.modules.pop("render", None)
        sys.modules.pop("generate", None)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        file.relative_to(root).as_posix(): file.read_bytes()
        for file in sorted(root.rglob("*"))
        if file.is_file()
    }


def test_generation_is_byte_deterministic_and_equals_outputs(
    scripts: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    render, generate = scripts
    layers = {"knowledge": ("domain", "adapters")}
    first, second = tmp_path / "first", tmp_path / "second"
    written, names = generate.generate(first, layers)
    again, _ = generate.generate(second, layers)
    assert written == again == render.outputs(names, modules=("knowledge",))
    assert sorted(_tree(first)) == list(written)
    assert _tree(first) == _tree(second)
    for relative, content in _tree(first).items():
        text = content.decode()
        assert str(REPO) not in text and str(tmp_path) not in text, relative


@pytest.fixture(scope="module")
def full_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One run of the script as a subprocess with the smallest environment, from a
    directory that is not the repo, writing under `--root`."""
    root = tmp_path_factory.mktemp("full")
    work = tmp_path_factory.mktemp("work")
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(work))}
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "generate.py"), "--root", str(root)],
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert not any(work.iterdir()), "generate.py wrote outside --root"
    return root


def test_minimal_environment_run_writes_every_output_under_root(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    files = sorted(_tree(full_run))
    classes = [Path(name).stem.removeprefix("classes-") for name in files if "/classes-" in name]
    assert files == list(render.outputs(classes))


def _declarations(mermaid: str) -> list[tuple[str, str]]:
    """(enclosing subgraph or root, label) of each node a flowchart declares."""
    stack: list[str] = []
    found: list[tuple[str, str]] = []
    for line in mermaid.splitlines():
        text = line.strip()
        if text.startswith("subgraph "):
            stack.append(text.split()[1])
        elif text == "end":
            stack.pop()
        elif m := re.match(r"^(\w+)[(\[]", text):
            found.append((stack[-1] if stack else "", m.group(1)))
    return found


def _expected(nodes: Iterable[str]) -> list[tuple[str, str]]:
    """(subgraph or root, label) of each node name, split on LangGraph's separator."""
    expected = []
    for name in nodes:
        parent, _, leaf = name.rpartition(SEPARATOR)
        expected.append((parent.replace(SEPARATOR, "_"), leaf))
    return sorted(expected)


def test_role_graph_nodes_are_the_compiled_graphs_and_the_pipeline_branch(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    from agentic import composition

    _, generate = scripts
    text = (full_run / "docs/architecture/generated/role-graph.md").read_text()
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)
    assert len(blocks) == 2
    task_nodes = composition.graph.compile().get_graph(xray=True).nodes
    declared = _declarations(blocks[0])
    assert len(declared) == len(task_nodes)
    assert sorted(declared) == _expected(task_nodes)
    assert {"classify", "pipeline", "deliver"} <= {label for _, label in declared}
    # `develop` and `review` live in the graph the `pipeline` node runs for each subtask,
    # which the task graph does not expand: they are the second block.
    branch_nodes = generate.branch_graph().get_graph().nodes
    branch = _declarations(blocks[1])
    assert len(branch) == len(branch_nodes)
    assert sorted(branch) == _expected(branch_nodes)
    assert {"develop", "review"} <= {label for _, label in branch}


def test_merging_pyreverse_runs_keeps_each_block_once_and_sorts(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    first = "classDiagram\n  class B {\n  }\n  class A {\n  }\n  B --|> A\n"
    second = "classDiagram\n  class C {\n  }\n  class A {\n  }\n  C --|> A\n  B --|> A\n"
    merged = render.merge_mermaid([first, second])
    assert merged == (
        "classDiagram\n  class A {\n  }\n  class B {\n  }\n  class C {\n  }\n"
        "  B --|> A\n  C --|> A\n"
    )
    assert render.merge_mermaid([second, first]) == merged


def test_every_generated_mermaid_block_is_within_the_limits(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    checked = 0
    for relative, content in _tree(full_run).items():
        if relative.endswith((".md", ".mmd")):
            text = content.decode()
            if "```mermaid" in text or relative.endswith(".mmd"):
                render.mermaid_limits(text)
                checked += 1
    assert checked >= 5


def test_mermaid_limits_refuse_a_block_over_either_limit(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    too_long = "graph TD;\n" + "x" * (render.MAX_CHARACTERS + 1)
    too_many = "graph TD;\n" + "\n".join(f"a{n} --> b{n};" for n in range(render.MAX_EDGES + 1))
    with pytest.raises(ValueError, match="characters"):
        render.mermaid_limits(f"```mermaid\n{too_long}\n```\n")
    with pytest.raises(ValueError, match="edges"):
        render.mermaid_limits(f"```mermaid\n{too_many}\n```\n")
    render.mermaid_limits("```mermaid\ngraph TD;\na --> b;\n```\n")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_check_fails_on_a_stale_generated_file_and_restores_the_tree(tmp_path: Path) -> None:
    (tmp_path / ".agentic").mkdir()
    (tmp_path / ".agentic" / "stack.yaml").write_text(
        "product: demo\n"
        "checks:\n  tests: 'true'\n"
        "docs:\n"
        "  generate: printf 'fresh\\n' > out/page.txt\n"
        '  paths: ["out/**"]\n'
    )
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "page.txt").write_text("stale\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "check.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert stack.DOCS_STALE in done.stdout
    assert "out/page.txt" in done.stdout
    assert "scripts/docs/generate.py" in done.stdout
    assert (tmp_path / "out" / "page.txt").read_text() == "stale\n"


def test_ci_check_job_runs_the_docs_check_through_check_py() -> None:
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["check"]["steps"]
    assert "uv run python scripts/docs/check.py" in [step.get("run") for step in steps]
