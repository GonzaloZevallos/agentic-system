"""The docs generator (`scripts/docs/`, GH-400, ADR-0076): determinism, the graph against the
compiled graph, the Mermaid limits, the stale check and the CI step.

Every test writes only under `tmp_path`; none reads a file under the `docs:` globs of this
repo, which the docs check deletes and restores while pytest runs (invariants 2 and 3).
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.docs import Docs

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts" / "docs"


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
        for name in ("render", "generate", "groups", "imports", "packages", "classes"):
            sys.modules.pop(name, None)


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
    layers = {"process": ("domain",), "knowledge": ("domain", "adapters")}
    first, second = tmp_path / "first", tmp_path / "second"
    written, role_pages, package_paths = generate.generate(first, layers)
    again, _, _ = generate.generate(second, layers)
    assert written == again == render.outputs(role_pages, package_paths)
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


def _role_keys(scripts: tuple[ModuleType, ModuleType]) -> list[str]:
    """The role-graph page paths the pure renderer returns for the real graph, in this process:
    the independent side of the comparison with the files a subprocess run wrote."""
    render, generate = scripts
    return sorted(render.role_graph_pages(*generate.role_graph_inputs()))


def _package_pages(
    scripts: tuple[ModuleType, ModuleType], module: str = "process"
) -> dict[str, str]:
    """The package pages of `module` the pure builder returns for the real tree, in this
    process: no pyreverse and nothing written."""
    render, generate = scripts
    groups, packages = sys.modules["groups"], sys.modules["packages"]
    on_disk, imported = generate.package_page_inputs()
    if module == "process":
        return render.package_pages(packages.build_pages(on_disk, imported, groups.CLUSTERS))
    model = packages.build_pages(
        on_disk, imported, {}, f"agentic.core.{module}", render.FLAT_PACKAGES[module]
    )
    return render.package_pages(model, module)


def _package_keys(scripts: tuple[ModuleType, ModuleType]) -> list[str]:
    """The paths of the package pages of every module and the class pages of process/domain,
    each from its own pure builder; the class pages need one pyreverse run, made once."""
    _, generate = scripts
    paths = {
        path
        for module in ("process", "knowledge", "measurement")
        for path in _package_pages(scripts, module)
    }
    return sorted(paths | set(_class_page_texts(generate)))


_CLASS_PAGES: dict[str, str] = {}


def _class_page_texts(generate: ModuleType) -> dict[str, str]:
    if not _CLASS_PAGES:
        for module, module_layers in sys.modules["render"].LAYERS.items():
            for layer in module_layers:
                _CLASS_PAGES.update(generate.class_pages(module, layer))
    return _CLASS_PAGES


def test_minimal_environment_run_writes_every_output_under_root(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    files = sorted(_tree(full_run))
    expected = render.outputs(_role_keys(scripts), _package_keys(scripts))
    assert files == list(expected)


def _declared(mermaid: str) -> list[str]:
    """The ids a `graph TD` block declares with a label, in order."""
    return re.findall(r'^    (\w+)\["', mermaid, re.MULTILINE)


def test_role_graph_nodes_are_the_compiled_graphs_and_the_pipeline_branch(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    from agentic import composition

    _, generate = scripts
    pages = {
        relative: content.decode()
        for relative, content in _tree(full_run).items()
        if Path(relative).name.startswith("role-graph")
    }
    real: set[str] = set()
    branch: list[str] = []
    for relative, text in pages.items():
        if relative.endswith("role-graph.md"):
            continue  # the overview draws phases, not nodes
        blocks = re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)
        for number, block in enumerate(blocks):
            for identifier in _declared(block):
                if identifier.startswith("branch_"):
                    branch.append(identifier)
                elif not identifier.startswith("phase_"):
                    assert number == 0, (relative, identifier)
                    real.add(identifier)
    task_nodes = composition.graph.compile().get_graph(xray=True).nodes
    assert real == {re.sub(r"\W", "_", name) for name in task_nodes}
    assert {"classify", "pipeline", "deliver", "escalate", "pause"} <= real
    # `develop` and `review` live in the graph the `pipeline` node runs for each subtask,
    # which the task graph does not expand: they are the `branch_` block of the build page.
    branch_nodes = generate.branch_graph().get_graph().nodes
    assert sorted(branch) == sorted(f"branch_{name}" for name in branch_nodes)
    assert {"branch_develop", "branch_review"} <= set(branch)
    shared = set(branch_nodes) - {"__start__", "__end__"}
    assert not shared & set(task_nodes)


def test_every_generated_mermaid_block_is_within_the_limits(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    checked = 0
    for relative, content in _tree(full_run).items():
        if relative.endswith(".md"):
            text = content.decode()
            if "```mermaid" in text:
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
    return frozenset(render.outputs(_role_keys(scripts), _package_keys(scripts)))


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


def test_every_relative_link_of_every_generated_page_resolves(
    generated_files: frozenset[str], full_run: Path
) -> None:
    problems = [
        problem
        for relative, content in _tree(full_run).items()
        if relative.endswith(".md")
        for problem in broken_links(relative, content.decode(), generated_files)
    ]
    assert problems == []
    assert any(name.endswith("role-graph-build.md") for name in generated_files)


def _graph_with(
    monkeypatch: pytest.MonkeyPatch, generate: ModuleType, extra: str, branch: str = "develop"
) -> None:
    nodes = ["__start__", "classify", extra, "__end__"]
    inputs = (nodes, [("__start__", "classify", False)], [branch], [], False)
    monkeypatch.setattr(generate, "role_graph_inputs", lambda: inputs)


def test_generate_refuses_a_node_in_no_phase_naming_it_and_the_line_to_add(
    scripts: tuple[ModuleType, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, generate = scripts
    _graph_with(monkeypatch, generate, "mystery")
    with pytest.raises(generate.GenerateError) as raised:
        generate.generate(tmp_path, {})
    message = str(raised.value)
    assert "'mystery'" in message and '"mystery",' in message and "groups.py" in message
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["phase_build", "branch_x"])
def test_generate_refuses_a_node_with_a_reserved_prefix(
    scripts: tuple[ModuleType, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    _, generate = scripts
    _graph_with(monkeypatch, generate, name)
    with pytest.raises(generate.GenerateError, match=name):
        generate.generate(tmp_path, {})
    _graph_with(monkeypatch, generate, "plan", branch=name)
    with pytest.raises(generate.GenerateError, match=name):
        generate.generate(tmp_path, {})


def test_main_exits_one_naming_the_node_in_no_phase(
    scripts: tuple[ModuleType, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, generate = scripts
    _graph_with(monkeypatch, generate, "mystery")
    assert generate.main(["--root", str(tmp_path)]) == 1
    assert "mystery" in capsys.readouterr().err


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


def _flow(*lines: str) -> str:
    return "flowchart TD\n" + "\n".join(f"    {line}" for line in lines) + "\n"


def _counts(render: ModuleType, text: str) -> tuple[int, int, int]:
    """(nodes, edges, classes) of the first overage of `text` measured against a zero budget."""
    found = render.diagram_counts(text)
    assert len(found) == 1
    return found[0]


def test_a_chain_counts_one_edge_per_arrow(scripts: tuple[ModuleType, ModuleType]) -> None:
    render, _ = scripts
    assert _counts(render, _flow("a --> b --> c")) == (3, 2, 0)


def test_an_ampersand_expands_to_one_edge_per_pair(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    assert _counts(render, _flow("a --> b & c")) == (3, 2, 0)
    assert _counts(render, _flow("a & b --> c")) == (3, 2, 0)


def test_edge_labels_are_not_nodes_or_arrows(scripts: tuple[ModuleType, ModuleType]) -> None:
    render, _ = scripts
    assert _counts(render, _flow("a -->|x --> y| b", "b -. x .-> c")) == (3, 2, 0)
    assert _counts(render, _flow("a -- text --> b")) == (2, 1, 0)


def test_langgraph_frontmatter_and_comments_are_skipped(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    text = (
        "---\nconfig:\n  flowchart:\n    curve: linear\n---\n"
        "%% a comment --> b\ngraph TD;\n\ta([a]) --> b;\n"
    )
    assert _counts(render, text) == (2, 1, 0)


def test_a_ghost_declared_with_a_shape_counts_once(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    text = _flow('phase_x["x (fase) --> [y]"]', "phase_x --> a", "a{c}", "d(e)", "f([g])")
    assert _counts(render, text) == (4, 1, 0)


def test_subgraph_styles_and_classes_are_not_counted(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    text = _flow(
        "subgraph inner [Inner]",
        "a --> b",
        "end",
        "classDef hot fill:#f00",
        "style a fill:#fff",
        "linkStyle 0 stroke:#f00",
        "class a,b hot",
        'click a "https://x" "t"',
        "inner --> c",
    )
    assert _counts(render, text) == (4, 2, 0)


def test_a_sequence_diagram_and_c4_are_ignored(scripts: tuple[ModuleType, ModuleType]) -> None:
    render, _ = scripts
    sequence = "sequenceDiagram\n" + "".join(
        f"    A->>B: m{n}\n    B-->>A: r{n}\n" for n in range(40)
    )
    assert render.diagram_budget(f"```mermaid\n{sequence}```\n") == []
    assert render.diagram_counts(sequence) == []
    c4 = "C4Context\n" + "".join(
        f'    Person(p{n}, "p")\n    Rel(p{n}, s, "uses")\n' for n in range(40)
    )
    assert render.diagram_budget(f"```mermaid\n{c4}```\n") == []


def _classes(count: int) -> str:
    return "classDiagram\n" + "".join(f"  class C{n} {{\n  }}\n" for n in range(count))


def test_a_class_diagram_is_over_budget_at_sixteen_classes(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    assert render.diagram_budget(_classes(15)) == []
    [over] = render.diagram_budget(_classes(16))
    assert (over.kind, over.classes, over.limits) == ("classDiagram", 16, ("classes",))


def test_a_flowchart_is_over_budget_past_twenty_nodes_or_thirty_edges(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    budget = render.DIAGRAM_BUDGET
    assert (budget.nodes, budget.edges, budget.classes) == (20, 30, 15)
    # 20 nodes and 30 edges: a ring of 20 plus 10 chords.
    ring = [f"n{k} --> n{(k + 1) % 20}" for k in range(20)]
    chords = [f"n{k} --> n{(k + 5) % 20}" for k in range(10)]
    assert render.diagram_budget(_flow(*ring, *chords)) == []
    [by_nodes] = render.diagram_budget(_flow(*ring, "n20 --> n0"))
    assert (by_nodes.nodes, by_nodes.limits) == (21, ("nodes",))
    [by_edges] = render.diagram_budget(_flow(*ring, *chords, "n0 --> n10"))
    assert (by_edges.edges, by_edges.limits) == (31, ("edges",))
    [second] = render.diagram_budget(
        f"```mermaid\n{_flow('a --> b')}```\n\n```mermaid\n{_flow(*ring, 'n20 --> n0')}```\n"
    )
    assert second.block == 2 and second.kind == "flowchart"


def _write_page(root: Path, relative: str, nodes: int) -> None:
    page = root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    body = _flow(*(f"n{k} --> n{k + 1}" for k in range(nodes - 1)))
    page.write_text(f"# T\n\n```mermaid\n{body}```\n")


def test_check_budget_names_the_written_pages_over_budget(
    scripts: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    render, _ = scripts
    _write_page(tmp_path, "docs/architecture/written.md", 21)
    _write_page(tmp_path, "docs/architecture/generated/page.md", 21)
    _write_page(tmp_path, "docs/architecture/generated/role-graph.md", 21)
    _write_page(tmp_path, "docs/architecture/ok.md", 20)
    (tmp_path / "docs/architecture/generated/big.mmd").write_text(
        _flow(*(f"a{k} --> a{k + 1}" for k in range(40)))
    )
    found = render.check_budget(tmp_path)
    assert [name for name, _ in found] == [
        "docs/architecture/generated/big.mmd",
        "docs/architecture/generated/page.md",
        "docs/architecture/generated/role-graph.md",
        "docs/architecture/written.md",
    ]
    assert found[0][1].nodes == 41 and all(over.nodes == 21 for _, over in found[1:])
    assert not hasattr(render, "_EXEMPT")


def test_no_mmd_file_is_left_under_the_generated_docs(
    scripts: tuple[ModuleType, ModuleType], full_run: Path, tmp_path: Path
) -> None:
    """Every class diagram is a `.md` page now: a `.mmd` in the repository, or one the
    generator would write, fails; `generate()` deletes a stale one it finds under `root`."""
    render, generate = scripts
    assert sorted((REPO / render.GENERATED).rglob("*.mmd")) == []
    assert [name for name in _tree(full_run) if name.endswith(".mmd")] == []
    stale = tmp_path / render.GENERATED / "classes-old.mmd"
    stale.parent.mkdir(parents=True)
    stale.write_text("classDiagram\n")
    generate.generate(tmp_path, {})
    assert not stale.exists()


def test_the_written_pages_are_within_the_budget(scripts: tuple[ModuleType, ModuleType]) -> None:
    render, _ = scripts
    assert render.check_budget(REPO) == []
    counts = _counts(render, (REPO / "docs/architecture/componentes.md").read_text())
    assert counts == (5, 9, 0)


def test_every_generated_page_is_within_the_budget(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    checked = 0
    for relative, content in _tree(full_run).items():
        if relative.endswith(".md") and "```mermaid" in content.decode():
            assert render.diagram_budget(content.decode()) == [], relative
            checked += 1
    assert checked >= 6


def test_the_generator_reports_a_page_over_budget_and_says_to_split(
    scripts: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    render, generate = scripts
    _write_page(tmp_path, "docs/architecture/big.md", 21)
    message = generate.budget_message(render.check_budget(tmp_path))
    assert "docs/architecture/big.md" in message
    assert "block 1" in message and "21 nodes" in message and "20" in message
    assert "split the page by meaning" in message and "regenerating" in message
    assert generate.budget_message([]) == ""


def test_the_docs_check_fails_when_the_generate_command_finds_a_page_over_budget(
    tmp_path: Path,
) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "import sys; sys.path.insert(0, 'scripts/docs'); import render, pathlib; "
        "sys.exit(1 if render.check_budget(pathlib.Path('.')) else 0)"
    )
    shutil.copytree(
        SCRIPTS, tmp_path / "scripts" / "docs", ignore=shutil.ignore_patterns("__pycache__")
    )
    _write_page(tmp_path, "docs/architecture/big.md", 21)
    _write_page(tmp_path, "docs/architecture/ok.md", 3)
    block = Docs(generate=command, paths=("docs/architecture/generated/**",))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    failed = subprocess.run(
        ["sh", "-c", stack.docs_command(block)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert "docs generate exited 1" in failed.stdout
    (tmp_path / "docs/architecture/big.md").unlink()
    ok = subprocess.run(
        ["sh", "-c", stack.docs_command(block)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stdout


Edge = tuple[str, str, bool]
TOY_NODES = ["__start__", "classify", "define", "validate", "__end__"]


def _toy_edges(conditional: bool) -> list[Edge]:
    return [
        ("__start__", "classify", False),
        ("__start__", "define", False),
        ("classify", "define", conditional),
        ("classify", "validate", False),
        ("define", "validate", False),
        ("validate", "classify", True),
        ("validate", "__end__", False),
    ]


def _toy_pages(
    render: ModuleType, conditional: bool = True, has_land: bool = False
) -> dict[str, str]:
    result: dict[str, str] = render.role_graph_pages(
        TOY_NODES, _toy_edges(conditional), [], [], has_land
    )
    return result


DEFINE_PAGE = """\
Esta página muestra los nodos de la fase `define` del grafo de roles y las aristas que la unen con \
otras fases; \
un nodo `<fase> (fase)` es otra fase, que se detalla en su página. \
Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [classify](role-graph-classify.md)
  - [deliver](role-graph-deliver.md)

```mermaid
graph TD
    __end__["__end__"]
    __start__["__start__"]
    define["define"]
    phase_classify["classify (fase)"]
    validate["validate"]
    __start__ --> define
    define --> validate
    phase_classify -.-> define
    phase_classify --> validate
    validate --> __end__
    validate -.-> phase_classify
```
"""


def test_a_two_phase_toy_graph_renders_the_expected_pages(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = _toy_pages(render)
    base = "docs/architecture/generated/"
    assert sorted(pages) == [
        base + name
        for name in (
            "role-graph-classify.md",
            "role-graph-define.md",
            "role-graph-deliver.md",
            "role-graph.md",
        )
    ]
    assert pages[base + "role-graph-define.md"] == DEFINE_PAGE
    assert all(not render.diagram_budget(text) for text in pages.values())
    assert all("click" not in text for text in pages.values())


def test_a_conditional_edge_changes_the_page_when_it_is_fixed(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    fixed = _toy_pages(render, conditional=False)[
        "docs/architecture/generated/role-graph-define.md"
    ]
    assert fixed == DEFINE_PAGE.replace("phase_classify -.-> define", "phase_classify --> define")


def test_terminals_are_drawn_as_themselves_and_never_as_a_ghost(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = _toy_pages(render)
    define = pages["docs/architecture/generated/role-graph-define.md"]
    assert "__start__ --> define" in define and "validate --> __end__" in define
    assert "phase_deliver" not in define and "phase_classify[" in define
    overview = pages["docs/architecture/generated/role-graph.md"]
    assert "__start__ --> phase_classify" in overview
    assert "phase_define --> phase_deliver" not in overview
    assert "phase_define --> __end__" in overview
    assert "phase___" not in overview


def test_the_overview_collapses_the_edges_of_two_phases_to_one(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    overview = _toy_pages(render, conditional=False)["docs/architecture/generated/role-graph.md"]
    # classify -> define fixed and classify -> validate fixed: one fixed edge; the conditional
    # validate -> classify is the only edge back.
    assert overview.count("phase_classify --> phase_define") == 1
    assert overview.count("phase_define -.-> phase_classify") == 1
    assert "phase_classify -.-> phase_define" not in overview
    mixed = _toy_pages(render)["docs/architecture/generated/role-graph.md"]
    assert mixed.count("phase_classify -.-> phase_define") == 1


def test_an_edge_to_escalate_is_listed_under_the_diagram_and_not_drawn(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = render.role_graph_pages(
        ["classify", "define", "escalate", "pause"],
        [
            ("classify", "define", False),
            ("define", "escalate", True),
            ("classify", "escalate", True),
            ("escalate", "pause", False),
            ("pause", "define", True),
            ("pause", "classify", True),
        ],
        [],
        [],
        False,
    )
    define = pages["docs/architecture/generated/role-graph-define.md"]
    assert "Pueden escalar: `define`" in define
    assert "escalate" not in define.split("```")[1] and "pause" not in define.split("```")[1]
    assert (
        "Pueden escalar: `classify`" in pages["docs/architecture/generated/role-graph-classify.md"]
    )
    page = pages["docs/architecture/generated/role-graph-escalation.md"]
    diagram = page.split("```")[1]
    assert "escalate --> pause" in diagram
    assert diagram.count("pause -.->") == 2
    assert "phase_define" in diagram and "phase_classify" in diagram
    overview = pages["docs/architecture/generated/role-graph.md"]
    assert "phase_define -.-> phase_escalation" in overview
    assert "phase_escalation -.-> phase_classify" in overview


def test_the_build_page_carries_the_pipeline_branch_with_prefixed_ids(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = render.role_graph_pages(
        ["pipeline", "integrate"],
        [("pipeline", "integrate", False)],
        ["__start__", "develop", "review"],
        [("__start__", "develop", False), ("develop", "review", True)],
        False,
    )
    build = pages["docs/architecture/generated/role-graph-build.md"]
    blocks = re.findall(r"```mermaid\n(.*?)```", build, re.DOTALL)
    assert len(blocks) == 2
    assert 'branch_develop["develop"]' in blocks[1]
    assert "branch_develop -.-> branch_review" in blocks[1]
    assert "branch___start__" in blocks[1]
    assert "branch_" not in blocks[0]


def test_a_node_in_no_phase_is_refused_with_the_line_to_add(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    with pytest.raises(ValueError, match=r"'mystery' is in no phase.*\"mystery\",.*groups\.py"):
        render.role_graph_pages(["classify", "mystery"], [], [], [], False)
    with pytest.raises(ValueError, match="'stray'"):
        render.role_graph_pages(["classify"], [("classify", "stray", False)], [], [], False)


def test_a_node_with_a_reserved_prefix_is_refused(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    for name in ("phase_define", "branch_develop"):
        with pytest.raises(ValueError, match=name):
            render.role_graph_pages(["classify", name], [], [], [], False)
    with pytest.raises(ValueError, match="phase_x"):
        render.role_graph_pages(["classify"], [], ["develop", "phase_x"], [], False)


def test_a_node_inside_a_subgraph_is_grouped_by_its_outermost_ancestor(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = render.role_graph_pages(
        ["pipeline:develop", "pipeline:inner:review", "classify"],
        [("classify", "pipeline:develop", False)],
        [],
        [],
        False,
    )
    build = pages["docs/architecture/generated/role-graph-build.md"]
    assert 'pipeline_develop["pipeline:develop"]' in build
    assert 'pipeline_inner_review["pipeline:inner:review"]' in build
    assert "phase_classify -.->" not in build and "phase_classify --> pipeline_develop" in build


def _toy_cli(*commands: str) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="toy")
    sub = root.add_subparsers(dest="command")
    for name in commands:
        sub.add_parser(name)
    return root


def test_the_land_page_exists_only_when_the_cli_has_the_land_subcommand(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    with_land, without = _toy_cli("run", "land"), _toy_cli("run", "resume")
    assert render.has_subcommand(with_land, "land") is True
    assert render.has_subcommand(without, "land") is False
    assert render.has_subcommand(argparse.ArgumentParser(prog="bare"), "land") is False
    land = "docs/architecture/generated/role-graph-land.md"
    pages = _toy_pages(render, has_land=render.has_subcommand(with_land, "land"))
    assert land in pages and "(../flujos/aterrizaje.md)" in pages[land]
    assert "```" not in pages[land]
    assert "[land](role-graph-land.md)" in pages["docs/architecture/generated/role-graph.md"]
    absent = _toy_pages(render, has_land=render.has_subcommand(without, "land"))
    assert land not in absent
    assert all("role-graph-land" not in text for text in absent.values())


def test_outputs_lists_exactly_the_role_graph_keys_it_is_given(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    keys = sorted(_toy_pages(render, has_land=True))
    written = render.outputs(role_graph=keys, package_and_class_pages=[])
    assert set(keys) <= set(written)
    assert set(written) - set(keys) == {render.CLI_REFERENCE, render.STACK_REFERENCE}
    assert render.outputs(role_graph=[], package_and_class_pages=[]) == (
        render.CLI_REFERENCE,
        render.STACK_REFERENCE,
    )


def test_every_toy_page_starts_with_a_sentence_and_links_resolve_among_the_pages(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    pages = _toy_pages(render, has_land=True)
    names = {Path(path).name for path in pages}
    for path, text in pages.items():
        assert text.startswith("Esta página "), path
        for target in re.findall(r"\]\(([^)#]+)\)", text):
            assert target in names or target == "../flujos/aterrizaje.md", (path, target)


# --- The process package pages (GH-430 part 2): `packages.py` over synthetic graphs ---

PROCESS = "agentic.core.process"


@pytest.fixture(scope="module")
def packages(scripts: tuple[ModuleType, ModuleType]) -> ModuleType:
    return sys.modules["packages"]


def _world(modules: Iterable[str], extra: Iterable[str] = ()) -> dict[str, bool]:
    """`on_disk` for `packages.build_pages`: each process module, the packages and `extra`."""
    disk = {"agentic": True, "agentic.core": True, PROCESS: True}
    for layer in ("domain", "application", "adapters"):
        disk[f"{PROCESS}.{layer}"] = True
    disk.update({f"{PROCESS}.{name}": False for name in modules})
    disk.update({name: False for name in extra})
    return disk


def _imports(modules: dict[str, tuple[str, ...]]) -> dict[str, set[str]]:
    return {
        f"{PROCESS}.{name}": {f"{PROCESS}.{t}" for t in targets}
        for name, targets in modules.items()
    }


def _labels(page: Any) -> list[str]:
    return [label for _, label in page.nodes]


def test_an_edge_to_a_package_init_and_a_self_edge_are_dropped(packages: ModuleType) -> None:
    pages = packages.build_pages(
        _world(["domain.a", "domain.b"]),
        _imports({"domain.a": ("domain", "domain.a", "domain.b"), "domain.b": ()}),
        {"domain.a": "x", "domain.b": "x"},
    )
    page = pages["domain-x"]
    assert _labels(page) == ["domain.a", "domain.b"]
    assert len(page.edges) == 1
    assert "domain (paquete)" not in _labels(page)


def test_a_page_draws_only_the_outgoing_edges_of_its_own_modules(packages: ModuleType) -> None:
    pages = packages.build_pages(
        _world(["domain.a", "domain.b", "application.c", "application.d"]),
        _imports(
            {
                "domain.a": ("domain.b",),
                "domain.b": (),
                "application.c": ("domain.a", "domain.b"),
                "application.d": ("application.c",),
            }
        ),
        {"domain.a": "x", "domain.b": "y", "application.c": "z", "application.d": "w"},
    )
    assert _labels(pages["domain-y"]) == ["domain.b"] and pages["domain-y"].edges == ()
    assert sorted(_labels(pages["domain-x"])) == ["domain.a", "y (grupo)"]
    assert sorted(_labels(pages["application-z"])) == [
        "application.c",
        "x (grupo de domain)",
        "y (grupo de domain)",
    ]
    assert sorted(_labels(pages["application-w"])) == ["application.d", "z (grupo)"]
    assert [label for _, label in pages["application"].nodes] == ["w (grupo)", "z (grupo)"]
    assert len(pages["application"].edges) == 1


def test_an_application_page_over_budget_falls_back_to_one_domain_ghost(
    packages: ModuleType,
) -> None:
    domain = [f"domain.m{n:02d}" for n in range(12)]
    uses = {f"application.u{n}": tuple(domain) for n in range(3)}
    pages = packages.build_pages(
        _world([*domain, *uses]),
        _imports({**{name: () for name in domain}, **uses}),
        {**{name: f"c{n:02d}" for n, name in enumerate(domain)}, **{u: "z" for u in uses}},
    )
    page = pages["application-z"]
    assert "domain (paquete)" in _labels(page)
    assert not any("grupo de domain" in label for label in _labels(page))
    assert "un solo nodo `domain (paquete)`" in page.sentence
    assert "un solo nodo" not in pages["domain-c00"].sentence


def test_a_cluster_of_more_than_fifteen_modules_is_split_by_count(packages: ModuleType) -> None:
    names = [f"domain.m{n:02d}" for n in range(20)]
    pages = packages.build_pages(
        _world(names),
        _imports({**{name: () for name in names}, "domain.m00": ("domain.m19",)}),
        {name: "big" for name in names},
    )
    assert sorted(key for key in pages if key.startswith("domain-big")) == [
        "domain-big-1",
        "domain-big-2",
    ]
    first, second = pages["domain-big-1"], pages["domain-big-2"]
    assert len(first.nodes) == 11 and "big parte 2 (grupo)" in _labels(first)
    assert sorted(_labels(second)) == [f"domain.m{n:02d}" for n in range(10, 20)]
    assert "por cantidad" in first.sentence and "parte 1 de 2" in first.sentence
    assert pages["domain"].children == ("domain-big-1", "domain-big-2")


def test_a_page_still_over_budget_after_the_split_is_refused_naming_the_cluster(
    packages: ModuleType,
) -> None:
    outside = [f"agentic.p{n:02d}.x" for n in range(25)]
    names = ["domain.a", "domain.b"]
    imports = {f"{PROCESS}.{name}": set(outside) for name in names}
    with pytest.raises(
        ValueError, match=r"domain\.fat.*split `domain\.fat` by meaning in CLUSTERS"
    ):
        packages.build_pages(_world(names, outside), imports, {name: "fat" for name in names})


def test_a_module_with_no_cluster_is_refused_with_the_line_to_paste(packages: ModuleType) -> None:
    with pytest.raises(ValueError) as caught:
        packages.build_pages(
            _world(["application.foo", "application.bar"]),
            _imports({"application.foo": (), "application.bar": ()}),
            {"application.bar": "build"},
        )
    assert '"application.foo": "<cluster>",' in str(caught.value)
    assert "application are: build" in str(caught.value)


def test_node_id_is_injective_over_the_real_tree_and_refuses_ambiguous_names(
    scripts: tuple[ModuleType, ModuleType], packages: ModuleType
) -> None:
    _, generate = scripts
    groups = sys.modules["groups"]
    on_disk, _ = generate.package_page_inputs()
    ids: dict[str, tuple[str, str, int | None]] = {}
    process = [
        name.removeprefix(PROCESS + ".") for name in on_disk if name.startswith(PROCESS + ".")
    ]
    sources = [("module", name, None) for name in process]
    sources += [("package", name, None) for name in on_disk]
    for qualified, cluster in groups.CLUSTERS.items():
        layer = qualified.split(".")[0]
        sources += [("cluster", f"{layer}.{cluster}", None), ("cluster", f"{layer}.{cluster}", 2)]
        sources.append(("cluster", f"domain.{cluster}", None))
    for source in sources:
        identifier = packages.node_id(*source)
        assert ids.setdefault(identifier, source) == source, (identifier, source, ids[identifier])
    assert len(ids) > 100
    with pytest.raises(ValueError):
        packages.node_id("module", "domain.a__b")
    with pytest.raises(ValueError):
        packages.node_id("module", "domain._a")


# --- The real tree: the pages the generator writes ---


def test_every_process_module_has_a_cluster_and_the_pages_fit_the_caps_and_the_budget(
    scripts: tuple[ModuleType, ModuleType], packages: ModuleType
) -> None:
    render, generate = scripts
    groups = sys.modules["groups"]
    on_disk, imported = generate.package_page_inputs()
    for name in imported:
        relative = name.removeprefix(PROCESS + ".")
        if relative.split(".")[0] in ("domain", "application"):
            assert relative in groups.CLUSTERS, f'add `    "{relative}": "<cluster>",`'
    assert set(groups.CLUSTERS) <= {name.removeprefix(PROCESS + ".") for name in imported}
    assert list(groups.CLUSTERS) == sorted(groups.CLUSTERS)
    for layer, most in (("domain", 6), ("application", 8)):
        found = {c for q, c in groups.CLUSTERS.items() if q.startswith(layer + ".")}
        assert 0 < len(found) <= most, (layer, found)
    pages = packages.build_pages(on_disk, imported, groups.CLUSTERS)
    assert len(pages) <= 27
    texts = render.package_pages(pages)
    for path, text in texts.items():
        assert render.diagram_budget(text) == [], path
        assert text.startswith("Esta página muestra "), path
        assert "click " not in text and "href" not in text, path


def test_the_index_links_every_process_page_and_each_page_its_parent_and_siblings(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    texts = _package_pages(scripts)
    names = {Path(path).name for path in texts}
    index = texts[render.package_page_path("index")]
    linked = set(re.findall(r"\]\(([^)]+)\)", index))
    assert linked == names - {"packages-process.md"}
    for path, text in texts.items():
        if path != render.package_page_path("index"):
            assert "- Padre: [" in text, path


def test_the_package_pages_of_every_module_are_the_committed_ones(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    """Parameterising build_pages by root and layers must not move a byte of the process
    pages, and the knowledge and measurement pages must be the ones in the tree."""
    for module in ("process", "knowledge", "measurement"):
        texts = _package_pages(scripts, module)
        assert texts, module
        for path, text in texts.items():
            assert (REPO / path).read_text(encoding="utf-8") == text, path


def test_packages_process_mmd_is_gone_from_outputs_and_from_the_tree(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    old = "docs/architecture/generated/packages-process.mmd"
    assert old not in render.outputs(_role_keys(scripts), _package_keys(scripts))
    assert not hasattr(render, "PACKAGE_MODULES") and not hasattr(render, "packages_path")
    assert old not in _tree(full_run)
    assert not (REPO / old).exists()
    assert render.package_page_path("index") in _tree(full_run)


def test_a_cluster_name_that_is_not_lowercase_words_is_refused_naming_it(
    packages: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="'Bad-Name' must be lowercase words"):
        packages.build_pages(
            _world(["domain.a"]), _imports({"domain.a": ()}), {"domain.a": "Bad-Name"}
        )


def test_an_adapter_page_over_budget_is_refused_naming_the_page_and_its_counts(
    packages: ModuleType,
) -> None:
    outside = [f"agentic.p{n:02d}.x" for n in range(35)]
    modules = ["adapters.git.a"]
    imports = {f"{PROCESS}.adapters.git.a": set(outside)}
    with pytest.raises(ValueError) as caught:
        packages.build_pages(_world(modules, outside), imports, {})
    message = str(caught.value)
    assert "'adapters-git' has 36 nodes and 35 edges" in message
    assert "split it by meaning in CLUSTERS" in message


def test_a_layer_page_over_budget_is_refused_naming_the_page_and_its_counts(
    packages: ModuleType,
) -> None:
    names = [f"domain.m{n:02d}" for n in range(21)]
    with pytest.raises(ValueError) as caught:
        packages.build_pages(
            _world(names),
            _imports({name: () for name in names}),
            {name: f"c{n:02d}" for n, name in enumerate(names)},
        )
    assert "'domain' has 21 nodes and 0 edges" in str(caught.value)


# --- The package pages of knowledge and measurement (GH-430 part 3): one page per layer ---

KNOWLEDGE = "agentic.core.knowledge"


def _flat_world(
    modules: Iterable[str], outside: Iterable[str] = (), root: str = KNOWLEDGE
) -> dict[str, bool]:
    disk = {"agentic": True, "agentic.core": True, root: True}
    for layer in ("domain", "application", "adapters"):
        disk[f"{root}.{layer}"] = True
    disk.update({f"{root}.{name}": False for name in modules})
    disk.update({name: False for name in outside})
    return disk


def _flat_imports(
    modules: dict[str, tuple[str, ...]], root: str = KNOWLEDGE
) -> dict[str, set[str]]:
    return {f"{root}.{name}": set(targets) for name, targets in modules.items()}


def test_a_package_without_groups_gets_an_index_and_one_page_per_layer(
    packages: ModuleType,
) -> None:
    pages = packages.build_pages(
        _flat_world(["domain.a", "domain.b", "adapters.git.c"], ["agentic.core.process.domain.x"]),
        _flat_imports(
            {
                "domain.a": (f"{KNOWLEDGE}.domain.b", "agentic.core.process.domain.x"),
                "domain.b": (),
                "adapters.git.c": (f"{KNOWLEDGE}.domain.a",),
            }
        ),
        {},
        KNOWLEDGE,
        ("domain", "adapters"),
    )
    assert sorted(pages) == ["adapters", "domain", "index"]
    assert pages["index"].children == ("domain", "adapters")
    assert _labels(pages["index"]) == ["adapters (paquete)", "domain (paquete)"]
    assert len(pages["index"].edges) == 1
    domain, adapters = pages["domain"], pages["adapters"]
    assert domain.parent == "index" and domain.siblings == ("adapters",)
    assert _labels(domain) == ["agentic.core.process (paquete)", "domain.a", "domain.b"]
    assert _labels(adapters) == ["domain (paquete)", "adapters.git.c"]  # sorted by id
    assert "agentic.core.knowledge" in domain.sentence and "domain" in domain.sentence


def test_a_package_layer_that_is_missing_or_unknown_is_refused_naming_it(
    packages: ModuleType,
) -> None:
    with pytest.raises(ValueError, match=r"'application\.x' is in no layer"):
        packages.build_pages(
            _flat_world(["domain.a", "application.x"]),
            _flat_imports({"domain.a": (), "application.x": ()}),
            {},
            KNOWLEDGE,
            ("domain", "adapters"),
        )


def test_a_package_layer_over_the_budget_is_refused_naming_the_layer_and_the_fix(
    packages: ModuleType,
) -> None:
    budget = packages.DIAGRAM_BUDGET
    names = [f"domain.m{n:02d}" for n in range(budget.classes + 1)]
    with pytest.raises(ValueError) as caught:
        packages.build_pages(
            _flat_world(names),
            _flat_imports({name: () for name in names}),
            {},
            KNOWLEDGE,
            ("domain", "adapters"),
        )
    message = str(caught.value)
    assert "'domain'" in message and f"{budget.classes + 1} modules" in message
    assert "add groups" in message
    outside = [f"agentic.p{n:02d}.x" for n in range(budget.nodes)]
    counts = f"{budget.nodes + 1} nodes and {budget.nodes} edges"
    with pytest.raises(ValueError, match=rf"layer 'domain' of .* has {counts}.*add groups"):
        packages.build_pages(
            _flat_world(["domain.a"], outside),
            _flat_imports({"domain.a": tuple(outside)}),
            {},
            KNOWLEDGE,
            ("domain",),
        )


# --- The class pages and the removed `.mmd` (GH-430 part 3): `render.class_pages` ---

DOMAIN = "agentic.core.process.domain"


def _dot(nodes: Iterable[str], edges: Iterable[tuple[str, str, str]] = ()) -> str:
    lines = ['digraph "classes_x" {', "rankdir=BT", 'charset="utf-8"']
    lines += [f'"{DOMAIN}.{n}" [color="black", label=<x>, shape="record"];' for n in nodes]
    lines += [
        f'"{DOMAIN}.{s}" -> "{DOMAIN}.{t}" [arrowhead="{head}", arrowtail="none"];'
        for s, t, head in edges
    ]
    return "\n".join([*lines, "}"])


def _class_texts(
    scripts: tuple[ModuleType, ModuleType], dot: str, clusters: dict[str, str]
) -> dict[str, str]:
    render, _ = scripts
    classes = sys.modules["classes"]
    parsed = classes.parse(dot, "process", "domain")
    return render.class_pages(
        classes.pages(*parsed, "process", "domain", sys.modules["groups"].CLASS_GROUPING, clusters)
    )


def test_class_pages_draw_qualified_ids_labels_the_four_arrows_and_the_other_pages(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    dot = _dot(
        ["a.Pair", "b.Pair", "a.Base", "a.Child", "c.Far", "a.Owner", "a.Part"],
        [
            ("a.Child", "a.Base", "empty"),
            ("a.Pair", "b.Pair", "vee"),
            ("a.Part", "a.Owner", "odiamond"),
            ("a.Owner", "a.Part", "diamond"),
            ("a.Child", "c.Far", "vee"),
        ],
    )
    texts = _class_texts(scripts, dot, {"domain.a": "one", "domain.b": "one", "domain.c": "two"})
    one = texts["docs/architecture/generated/classes-process-domain-one.md"]
    assert one.startswith("Esta página muestra las clases públicas")
    assert '    class a__Pair["a.Pair"]' in one and '    class b__Pair["b.Pair"]' in one
    assert '    class a__Base["Base"]' in one
    for arrow in (
        "a__Child --|> a__Base",
        "a__Pair --> b__Pair",
        "a__Owner o-- a__Part",
        "a__Part *-- a__Owner",
    ):
        assert f"    {arrow}\n" in one, arrow
    assert "c__Far" not in one.split("## Relaciones con otras páginas")[0]
    tail = one.split("## Relaciones con otras páginas")[1]
    assert "`a.Child` se asocia con `c.Far`" in tail
    assert "- [domain: two](classes-process-domain-two.md)\n  - `a.Child` se asocia" in tail
    index = texts["docs/architecture/generated/classes-process-domain.md"]
    assert "[domain: one](classes-process-domain-one.md)" in index and "```" not in index
    assert "- Padre: [Clases de domain](classes-process-domain.md)" in one
    for text in texts.values():
        assert "click " not in text and "href" not in text


def test_a_class_cluster_over_the_budget_is_written_as_numbered_pages_each_within_it(
    scripts: tuple[ModuleType, ModuleType],
) -> None:
    render, _ = scripts
    names = [f"m.C{n:02d}" for n in range(render.DIAGRAM_BUDGET.classes + 1)]
    texts = _class_texts(scripts, _dot(names), {"domain.m": "big"})
    pages = {Path(path).name for path in texts}
    assert pages == {
        "classes-process-domain.md",
        "classes-process-domain-big-1.md",
        "classes-process-domain-big-2.md",
    }
    for path, text in texts.items():
        assert render.diagram_budget(text) == [], path
    assert "por cantidad" in texts["docs/architecture/generated/classes-process-domain-big-1.md"]


def test_the_three_replaced_mmd_files_are_gone_from_outputs_and_from_the_tree(
    scripts: tuple[ModuleType, ModuleType], full_run: Path
) -> None:
    render, _ = scripts
    gone = [
        f"docs/architecture/generated/{name}.mmd"
        for name in ("classes-process-domain", "packages-knowledge", "packages-measurement")
    ]
    written = render.outputs(_role_keys(scripts), _package_keys(scripts))
    tree = _tree(full_run)
    for old in gone:
        assert old not in written and old not in tree and not (REPO / old).exists(), old
    for new in (
        "classes-process-domain.md",
        "packages-knowledge.md",
        "packages-knowledge-domain.md",
        "packages-measurement.md",
        "packages-measurement-adapters.md",
    ):
        assert f"docs/architecture/generated/{new}" in tree, new


def test_generate_refuses_dot_it_cannot_read_and_a_module_in_no_cluster(
    scripts: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, generate = scripts
    groups = sys.modules["groups"]
    bad = 'digraph "x" {\nsomething unexpected\n}'
    monkeypatch.setattr(generate, "_pyreverse", lambda *a, **k: {"classes_x.dot": bad})
    with pytest.raises(generate.GenerateError, match="something unexpected"):
        generate.class_pages("process", "domain")
    good = _dot(["ghost_module.Thing"])
    monkeypatch.setattr(generate, "_pyreverse", lambda *a, **k: {"classes_x.dot": good})
    monkeypatch.setattr(groups, "CLUSTERS", {})
    with pytest.raises(generate.GenerateError, match=r'"domain.ghost_module": "<cluster>",'):
        generate.class_pages("process", "domain")


def test_generate_refuses_a_class_page_over_budget_naming_its_path(
    scripts: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every class page is measured, whatever its layer, and a page over `DIAGRAM_BUDGET`
    fails naming its path and the counts."""
    render, generate = scripts
    path = f"{render.GENERATED}/classes-knowledge-domain-all.md"
    names = [f"m__C{n:02d}" for n in range(render.DIAGRAM_BUDGET.classes + 1)]
    fat = "```mermaid\nclassDiagram\n" + "".join(f"    class {n}\n" for n in names) + "```\n"
    monkeypatch.setattr(generate, "class_pages", lambda context, layer: {path: fat})
    with pytest.raises(generate.GenerateError, match=re.escape(path)) as raised:
        generate.generate(tmp_path, {"knowledge": ("domain",)})
    assert "classes" in str(raised.value)
