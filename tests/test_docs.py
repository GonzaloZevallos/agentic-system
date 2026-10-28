"""The docs generator (`scripts/docs/`, GH-400, ADR-0076): determinism, the graph against the
compiled graph, the Mermaid limits, the stale check and the CI step.

Every test writes only under `tmp_path`; none reads a file under the `docs:` globs of this
repo, which the docs check deletes and restores while pytest runs (invariants 2 and 3).
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
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


# --- The written pages (ADR-0074): Spanish prose, Mermaid diagrams, links and citations ---

WRITTEN_PAGES = (
    "docs/README.md",
    "docs/architecture/contexto.md",
    "docs/architecture/contenedores.md",
    "docs/architecture/componentes.md",
    "docs/architecture/flujos/de-issue-a-merge.md",
    "docs/architecture/flujos/aterrizaje.md",
    "docs/architecture/flujos/reanudacion.md",
)
DIAGRAM_KEYWORDS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "C4Context",
    "C4Container",
    "C4Component",
    "classDiagram",
)
SPANISH_WORDS = frozenset(
    "el la los las de del que y en un una por con para se es al lo como su más no".split()
)
_FENCE_LINE = re.compile(r"^```")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_CODE = re.compile(r"`([^`\n]+)`")
_CITATION = re.compile(r"^((?:[\w.-]+/)+[\w.-]+\.(?:py|md|yaml|yml|sh|toml|in|json))(?::([\w.]+))?")


def _owned(relative: str) -> bool:
    """Whether `relative` falls under a `docs:` glob of this repo's stack."""
    declared = yaml.safe_load((REPO / ".agentic" / "stack.yaml").read_text())["docs"]["paths"]
    return any(fnmatch.fnmatch(relative, pattern) for pattern in declared)


def _prose(text: str) -> str:
    """`text` without its fenced blocks."""
    kept, inside = [], False
    for line in text.splitlines():
        if _FENCE_LINE.match(line):
            inside = not inside
        elif not inside:
            kept.append(line)
    return "\n".join(kept)


def broken_links(page: str, text: str, generated: frozenset[str]) -> list[str]:
    """The relative links of `text`, a page at `page`, that resolve to nothing: a target
    under the owned paths must be one the generator writes, any other must exist. Anchors
    and external URLs are ignored."""
    broken = []
    for target in _LINK.findall(_prose(text)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        path = target.split("#", 1)[0]
        relative = os.path.normpath(os.path.join(os.path.dirname(page), path)).replace(os.sep, "/")
        found = relative in generated if _owned(relative) else (REPO / relative).exists()
        if not found:
            broken.append(f"{page}: {target}")
    return broken


def diagram_problems(page: str, text: str, limits: Callable[[str], None]) -> list[str]:
    """What is wrong with the Mermaid blocks of `text`: an unbalanced fence, a block that
    opens with no known diagram keyword, a block over the limits. Not a full parse."""
    problems = []
    fences = [line for line in text.splitlines() if _FENCE_LINE.match(line)]
    if len(fences) % 2:
        problems.append(f"{page}: unbalanced code fences")
    for number, block in enumerate(re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL), 1):
        first = next((line.split()[0] for line in block.splitlines() if line.strip()), "")
        if first.rstrip(";") not in DIAGRAM_KEYWORDS:
            problems.append(f"{page}: block {number} opens with {first!r}")
        try:
            limits(block)
        except ValueError as error:
            problems.append(f"{page}: block {number}: {error}")
    return problems


def citations(text: str) -> list[tuple[str, str | None]]:
    """Each `path` or `path:symbol` the prose cites in backticks."""
    found = []
    for code in _CODE.findall(text):
        if match := _CITATION.match(code):
            found.append((match.group(1), match.group(2)))
    return found


def citation_problems(page: str, text: str) -> list[str]:
    """A citation outside the owned paths must name a file that exists and, when it names
    a symbol, a symbol that file mentions; a page with no such citation is a problem."""
    checked, problems = 0, []
    for path, symbol in citations(text):
        if _owned(path):
            continue
        file = REPO / path
        if not file.is_file():
            problems.append(f"{page}: cites {path}, which does not exist")
            continue
        checked += 1
        if symbol and symbol.split(".")[-1] not in file.read_text(encoding="utf-8"):
            problems.append(f"{page}: {path} has no {symbol}")
    if not checked:
        problems.append(f"{page}: cites no repo path")
    return problems


def spanish_share(text: str) -> float:
    """The share of the words of the prose of `text` that are common Spanish words."""
    words = re.findall(r"[a-záéíóúñü]+", _prose(text).lower())
    return sum(word in SPANISH_WORDS for word in words) / max(len(words), 1)


def _pages() -> dict[str, str]:
    return {page: (REPO / page).read_text(encoding="utf-8") for page in WRITTEN_PAGES}


@pytest.fixture(scope="module")
def generated_files(scripts: tuple[ModuleType, ModuleType], full_run: Path) -> frozenset[str]:
    render, _ = scripts
    classes = [
        Path(name).stem.removeprefix("classes-") for name in _tree(full_run) if "/classes-" in name
    ]
    return frozenset(render.outputs(classes))


def test_every_written_page_exists_and_is_not_under_the_owned_paths() -> None:
    for page in WRITTEN_PAGES:
        assert (REPO / page).is_file(), page
        assert not _owned(page), page


def test_every_relative_link_of_the_written_pages_resolves(generated_files: frozenset[str]) -> None:
    problems = [
        problem
        for page, text in _pages().items()
        for problem in broken_links(page, text, generated_files)
    ]
    assert problems == []
    linked = {t for text in _pages().values() for t in _LINK.findall(text)}
    assert any("generated/role-graph.md" in target for target in linked)


def test_a_link_to_nothing_is_refused(generated_files: frozenset[str]) -> None:
    text = (
        "[gone](missing.md) [kept](../README.md#a) [web](https://example.com) [self](#x) "
        "[stale](generated/role-graf.md) [fresh](generated/role-graph.md)"
    )
    assert broken_links("docs/architecture/x.md", text, generated_files) == [
        "docs/architecture/x.md: missing.md",
        "docs/architecture/x.md: generated/role-graf.md",
    ]


def test_every_mermaid_block_of_the_written_pages_is_a_diagram_within_the_limits(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = _pages()
    problems = [
        problem
        for page, text in pages.items()
        for problem in diagram_problems(page, text, render.mermaid_limits)
    ]
    assert problems == []
    assert sum(text.count("```mermaid") for text in pages.values()) >= 4


def test_a_bad_fence_is_refused(scripts: tuple[ModuleType, ModuleType]) -> None:
    render, _ = scripts
    page = "docs/architecture/x.md"
    assert (
        diagram_problems(
            page, "```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n", render.mermaid_limits
        )
        == []
    )
    assert diagram_problems(
        page, "```mermaid\nsequenceDiagram\n  A->>B: hi\n", render.mermaid_limits
    ) == [f"{page}: unbalanced code fences"]
    assert diagram_problems(page, "```mermaid\nnonsense\n```\n", render.mermaid_limits) == [
        f"{page}: block 1 opens with 'nonsense'"
    ]
    huge = "graph TD;\n" + "\n".join(f"a{n} --> b{n};" for n in range(render.MAX_EDGES + 1))
    (problem,) = diagram_problems(page, f"```mermaid\n{huge}\n```\n", render.mermaid_limits)
    assert "edges" in problem


def test_every_written_page_cites_files_and_symbols_that_exist() -> None:
    problems = [
        problem for page, text in _pages().items() for problem in citation_problems(page, text)
    ]
    assert problems == []


def test_a_citation_of_nothing_is_refused() -> None:
    page = "docs/architecture/x.md"
    good = "`agentic/composition.py:graph` and `agentic/cli.py:build_parser`"
    assert citation_problems(page, good) == []
    assert citation_problems(page, "`agentic/nada.py:graph`") == [
        f"{page}: cites agentic/nada.py, which does not exist",
        f"{page}: cites no repo path",
    ]
    absent = citation_problems(page, "`agentic/cli.py:no_such_symbol_anywhere_x`")
    assert absent == [f"{page}: agentic/cli.py has no no_such_symbol_anywhere_x"]
    assert citation_problems(page, "sin citas") == [f"{page}: cites no repo path"]


def test_every_written_page_is_in_spanish() -> None:
    low = {page: share for page, text in _pages().items() if (share := spanish_share(text)) < 0.15}
    assert low == {}
    assert spanish_share("The system reads the task from the issue and then runs the roles.") < 0.05
