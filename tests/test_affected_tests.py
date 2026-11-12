"""`scripts/affected_tests.py`: the selection (fixtures, through its pure functions and its
exit contract) and the guard that keeps its input table honest on the real tree."""

from __future__ import annotations

import ast
import fnmatch
import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

import pytest
import yaml

from agentic.core.knowledge.domain import stack

REPO = Path(__file__).resolve().parent.parent
STACK_YAML = REPO / ".agentic" / "stack.yaml"
SCRIPT = REPO / "scripts" / "affected_tests.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("affected_tests_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


def _graph(**sources: str):
    return script.graph({path.replace("__", "/") + ".py": text for path, text in sources.items()})


# The layout under test: a domain module, an application module over it, an adapter over
# that, and the tests of each. `tests/test_adapter.py` never imports the domain directly.
LAYERED = {
    "agentic/x/domain.py": "VALUE = 1\n",
    "agentic/x/application.py": "from agentic.x import domain\n",
    "agentic/x/adapter.py": "from agentic.x.application import run\n",
    "tests/test_adapter.py": "from agentic.x import adapter\n",
    "tests/test_domain.py": "from agentic.x.domain import VALUE\n",
    "tests/test_other.py": "import json\n",
}


def _files(result) -> tuple[str, ...]:
    assert isinstance(result, script.Selection), result
    return result.files


def _select(changes, sources=None, table=(), ignore=()):
    return script.select(changes, script.graph(sources or LAYERED), table, ignore)


def test_a_domain_change_reaches_a_test_that_only_imports_the_adapter_two_hops_away():
    assert _files(_select(["agentic/x/domain.py"])) == (
        "tests/test_adapter.py",
        "tests/test_domain.py",
    )


def test_a_leaf_change_selects_only_what_reaches_it():
    assert _files(_select(["agentic/x/adapter.py"])) == ("tests/test_adapter.py",)


def test_module_name_of_a_module_a_package_and_a_test():
    assert script.module_name("agentic/x/domain.py") == "agentic.x.domain"
    assert script.module_name("agentic/x/__init__.py") == "agentic.x"
    assert script.module_name("tests/test_adapter.py") == "tests.test_adapter"


def test_a_relative_import_is_an_edge_to_the_module_it_resolves_to():
    sources = {
        "agentic/x/a.py": "",
        "agentic/x/b.py": "from . import a\n",
        "agentic/x/c.py": "from .a import thing\n",
        "agentic/y/d.py": "from ..x import a\n",
        "tests/test_b.py": "import agentic.x.b\n",
        "tests/test_c.py": "import agentic.x.c\n",
        "tests/test_d.py": "import agentic.y.d\n",
    }
    assert _files(_select(["agentic/x/a.py"], sources)) == (
        "tests/test_b.py",
        "tests/test_c.py",
        "tests/test_d.py",
    )


def test_a_patch_string_is_an_edge_to_the_module_it_names():
    sources = {
        "agentic/x/y.py": "",
        "tests/test_patch.py": "def test():\n    patch('agentic.x.y.helper')\n",
        "tests/test_other.py": "x = 'agentic.presubmit.checks'\n",
    }
    found = script.graph(sources)
    assert "agentic.x.y" in found.imports["tests.test_patch"]
    assert _files(_select(["agentic/x/y.py"], sources)) == ("tests/test_patch.py",)


def test_a_deleted_module_still_selects_the_test_that_imports_it():
    sources = {k: v for k, v in LAYERED.items() if k != "agentic/x/domain.py"}
    assert "tests/test_domain.py" in _files(_select(["agentic/x/domain.py"], sources))


def test_a_rename_selects_the_importers_of_the_old_and_the_new_name():
    sources = {
        "agentic/x/new.py": "",
        "agentic/x/user_of_old.py": "from agentic.x import old\n",
        "tests/test_old.py": "from agentic.x import old\n",
        "tests/test_new.py": "from agentic.x import new\n",
    }
    both = _files(_select(["agentic/x/old.py", "agentic/x/new.py"], sources))
    assert both == ("tests/test_new.py", "tests/test_old.py")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "agentic" / "x").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "agentic" / "x" / "old.py").write_text("VALUE = 1\nMORE = 2\nEVEN = 3\n" * 3)
    (tmp_path / "keep.txt").write_text("keep\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_changed_reads_both_sides_of_a_rename_and_the_untracked_files(repo: Path):
    _git(repo, "mv", "agentic/x/old.py", "agentic/x/new.py")
    (repo / "tests" / "test_fresh.py").write_text("")
    (repo / "keep.txt").write_text("changed\n")
    assert script.changed("HEAD", repo) == [
        "agentic/x/new.py",
        "agentic/x/old.py",
        "keep.txt",
        "tests/test_fresh.py",
    ]


def test_parse_changes_takes_both_paths_of_a_rename_and_a_copy_status():
    diff = "M\0a.py\0R100\0old.py\0new.py\0D\0gone.py\0"
    assert script.parse_changes(diff, "u.py\0") == ["a.py", "gone.py", "new.py", "old.py", "u.py"]


def test_changed_raises_on_a_base_git_cannot_resolve(repo: Path):
    with pytest.raises(script.GitFailure):
        script.changed("no-such-revision", repo)


# -- the exit contract: one fixed-token stderr line, no stdout, no full suite -----------------


def _main(repo: Path, base: str, capfd, run_pytest=None) -> tuple[int, str, str]:
    kwargs = {"run_pytest": run_pytest} if run_pytest else {}
    code = script.main([base], root=repo, **kwargs)
    out, err = capfd.readouterr()
    return code, out, err


def _never_runs(files, root):
    raise AssertionError(f"pytest must not run, got {files}")


def test_an_empty_selection_exits_8_with_one_empty_line_and_runs_nothing(repo: Path, capfd):
    (repo / ".agentic" / "tasks").mkdir(parents=True)
    (repo / ".agentic" / "tasks" / "GH-1.md").write_text("doc\n")
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (8, "")
    assert err.startswith("affected-tests: empty:") and err.count("\n") == 1


def test_no_change_at_all_selects_nothing_and_exits_8(repo: Path, capfd):
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (8, "")
    assert err.startswith("affected-tests: empty:") and err.count("\n") == 1


def test_an_unmapped_python_file_refuses_with_9(repo: Path, capfd):
    (repo / "tool.py").write_text("x = 1\n")
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (9, "")
    assert err.startswith("affected-tests: refused:") and err.count("\n") == 1


@pytest.mark.parametrize(
    "path", ["agentic/x/__init__.py", "tests/__init__.py", "tests/conftest.py"]
)
def test_a_changed_package_marker_or_conftest_refuses_with_9(repo: Path, capfd, path: str):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("")
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (9, "")
    assert err.startswith("affected-tests: refused:") and err.count("\n") == 1


def test_a_non_python_path_in_neither_the_table_nor_the_ignore_list_refuses(repo: Path, capfd):
    (repo / "langgraph.json").write_text("{}\n")
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (9, "")
    assert err.startswith("affected-tests: refused:") and "langgraph.json" in err


def test_a_git_failure_refuses_with_9_and_the_line_is_git(repo: Path, capfd):
    code, out, err = _main(repo, "no-such-revision", capfd, _never_runs)
    assert (code, out, err) == (9, "", "affected-tests: refused: git\n")


@pytest.mark.parametrize("base", ["--output=pwned", "-p", ""])
def test_a_base_starting_with_a_dash_or_empty_refuses_before_git_runs(repo: Path, capfd, base: str):
    code, out, err = _main(repo, base, capfd, _never_runs)
    assert (code, out) == (9, "")
    assert err.startswith("affected-tests: refused:") and not (repo / "pwned").exists()


def test_the_base_reaches_git_as_one_argument_never_through_a_shell(repo: Path, capfd):
    code, _, err = _main(repo, "HEAD; touch pwned", capfd, _never_runs)
    assert code == 9 and err == "affected-tests: refused: git\n"
    assert not (repo / "pwned").exists()


def test_a_computed_import_under_agentic_refuses_because_the_graph_is_incomplete(repo: Path, capfd):
    (repo / "agentic" / "x" / "load.py").write_text(
        "from importlib import import_module\n\ndef go(name):\n    return import_module(name)\n"
    )
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (9, "")
    assert "computed name" in err and err.count("\n") == 1


def test_a_selection_runs_pytest_with_the_marker_and_returns_its_exit_code_unchanged(
    repo: Path, capfd
):
    (repo / "tests" / "test_new.py").write_text("def test_a():\n    pass\n")
    seen: list[tuple[Sequence[str], Path]] = []

    def fake(files, root):
        seen.append((files, root))
        return 5

    code, out, err = _main(repo, "HEAD", capfd, fake)
    assert (code, out, err) == (5, "", "")
    assert seen == [(("tests/test_new.py",), repo)]


def test_the_real_pytest_invocation_is_an_argv_list_with_the_llm_marker_deselected(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[object] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 3)

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    assert script._run_pytest(["tests/test_a.py"], REPO) == 3
    ((argv, kwargs),) = calls
    assert argv[1:] == ["-m", "pytest", "-m", "not llm", "tests/test_a.py"]
    assert kwargs.get("shell") is None


# -- what the walkers watch ---------------------------------------------------------------


def _walker_sources() -> dict[str, str]:
    return {path: "" for path in script.WALKERS} | {"tests/test_unrelated.py": ""}


def test_adding_an_agentic_module_selects_the_walkers():
    sources = _walker_sources()
    result = _select(["agentic/x/new.py"], sources, script.TABLE, script.IGNORE)
    assert set(script.WALKERS) <= set(_files(result))


def test_deleting_an_agentic_module_selects_the_walkers():
    result = _select(["agentic/x/gone.py"], _walker_sources(), script.TABLE, script.IGNORE)
    assert set(script.WALKERS) <= set(_files(result))


def test_a_new_test_file_under_tests_selects_both_guards_that_parse_every_test_file(real):
    _, _, found = real
    result = script.select(
        ["tests/core/process/application/test_new_thing.py"], found, script.TABLE, script.IGNORE
    )
    assert {"tests/test_tracker_doubles.py", "tests/test_affected_tests.py"} <= set(_files(result))


def test_adding_a_test_selects_that_test_and_not_the_walkers():
    sources = _walker_sources() | {"tests/test_new.py": ""}
    assert _files(_select(["tests/test_new.py"], sources, script.TABLE, script.IGNORE)) == (
        "tests/test_new.py",
    )


def test_a_doc_selects_no_walker_it_is_ignored_and_selects_nothing():
    result = _select(["docs/product/x.md"], _walker_sources(), script.TABLE, script.IGNORE)
    assert isinstance(result, script.Empty)


def test_a_module_adr_is_ignored_and_selects_nothing():
    sources = _walker_sources() | {"tests/test_adr_numbers.py": ""}
    result = _select(
        ["agentic/core/knowledge/docs/adr/0001-x.md"], sources, script.TABLE, script.IGNORE
    )
    assert isinstance(result, script.Empty)


def test_an_adr_selects_the_test_that_lists_the_adr_directory_and_nothing_else():
    sources = _walker_sources() | {"tests/test_adr_numbers.py": ""}
    result = _select(["docs/adr/0049-x.md"], sources, script.TABLE, script.IGNORE)
    assert _files(result) == ("tests/test_adr_numbers.py",)


def test_a_doc_outside_the_adr_directory_and_the_ignored_ones_selects_the_docs_test():
    sources = _walker_sources() | {"tests/test_adr_numbers.py": "", "tests/test_docs.py": ""}
    result = _select(["docs/x.md"], sources, script.TABLE, script.IGNORE)
    assert _files(result) == ("tests/test_docs.py",)


def test_a_test_that_loads_code_by_a_computed_name_joins_the_agentic_trigger():
    sources = {
        "agentic/x/y.py": "",
        "tests/test_loader.py": "import importlib.util as u\nu.spec_from_file_location(a, b)\n",
        "tests/test_plain.py": "",
    }
    assert _files(_select(["agentic/x/y.py"], sources)) == ("tests/test_loader.py",)


def test_a_loader_with_constant_arguments_does_not_join_the_trigger():
    sources = {
        "agentic/x/y.py": "",
        "tests/test_loader.py": "import importlib\nimportlib.import_module('agentic.x.y')\n",
    }
    found = script.graph(sources)
    assert found.loaders == frozenset()


def test_a_source_that_is_not_utf8_refuses_with_9_not_a_traceback(repo: Path, capfd):
    (repo / "agentic" / "x" / "bin.py").write_bytes(b"x = '\xff'\n")
    code, out, err = _main(repo, "HEAD", capfd, _never_runs)
    assert (code, out) == (9, "")
    assert err.startswith("affected-tests: refused:") and err.count("\n") == 1


def test_a_syntax_error_in_the_tree_cannot_be_decided():
    with pytest.raises(script.Undecided):
        script.graph({"agentic/x/bad.py": "def (:\n"})


# -- the guard: on the real tree, not on fixtures ---------------------------------------------
#
# The selection follows imports, so it cannot see a test that reads a repo file. This
# guard finds those reads in every `tests/**/*.py` (but `tests/llm`, deselected anyway) and
# fails unless each file read is covered: by a row of the script's input table whose
# targets include the test, by an `affected_fallback` glob (the gate runs full), by the
# import closure, or by a named allowance below.

PATH_PREFIXES = ("docs/", ".agentic/", ".github/", "observability/", "scripts/", "agentic/")
PATH_SUFFIXES = (".md", ".yaml", ".yml", ".json", ".toml", ".example")
PATH_CALLS = frozenset({"Path", "open", "read_text", "read_bytes", "glob", "rglob", "iterdir"})
TEMP_NAMES = frozenset({"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"})
TEMPFILE_CALLS = frozenset(
    {"mkdtemp", "mkstemp", "gettempdir", "TemporaryDirectory", "NamedTemporaryFile"}
)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
PATH_KEEPERS = frozenset({"resolve", "absolute", "expanduser"})  # the receiver is the path
PATH_WALKS = frozenset({"glob", "rglob", "iterdir"})  # the receiver is the root of the walk
PATH_WRAPPERS = frozenset({"sorted", "list", "tuple", "set", "reversed"})
ANCHORS = frozenset({"ROOT", "REPO", "CORE", "PACKAGE"})


class Allowance(NamedTuple):
    """`test` (a glob) names a repo file in `path` (a glob) without reading the real one:
    it only mentions it in a message, or builds it under a temporary directory the guard
    cannot tell is one because the path arrives through a helper or a fixture (a path built
    from `tmp_path` or `tempfile` inside the test itself is skipped before it gets here)."""

    test: str
    path: str
    reason: str


ALLOWANCES: tuple[Allowance, ...] = (
    Allowance(
        "tests/core/process/domain/test_rules.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/domain/test_docs.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/application/test_landing_sync.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/application/test_review_arm.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/application/test_plan_skip_ephemeral.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/application/test_preflight.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/process/application/test_technical_stage.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/knowledge/adapters/git/test_stack.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/core/knowledge/domain/test_stack.py",
        "docs/architecture/generated/*",
        "names the owned glob as a string of a `docs:` block; it never reads the files",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "docs/architecture/generated/*",
        "names the owned globs in this very table; it never reads the files",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "docs/reference/*",
        "names the owned globs in this very table; it never reads the files",
    ),
    Allowance(
        "tests/core/process/adapters/git/test_workspace.py",
        "*README.md",
        "writes a README.md into the repo of the `product` fixture, which makes it under tmp_path; "
        "the path arrives through that fixture",
    ),
    Allowance(
        "tests/core/process/adapters/git/test_workspace.py",
        ".gitignore",
        "writes a .gitignore into the repo of the `product` fixture, which makes it under "
        "tmp_path; the path arrives through that fixture",
    ),
    Allowance(
        "tests/core/process/application/test_merge_for_real.py",
        "*CODING_STANDARDS.md",
        "writes CODING_STANDARDS.md into `checkout.path`, a task checkout the `world` fixture "
        "makes under tmp_path; the path arrives through that fixture",
    ),
    Allowance(
        "tests/test_cli.py",
        "BRIEF.md",
        "writes BRIEF.md into the product made by the `product(tmp_path, ...)` helper; the path "
        "arrives through that helper",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        ".agentic/playbooks/*",
        "names these paths in the allowances above; it never opens them",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "langgraph.json",
        "writes a langgraph.json into the repo of the `repo` fixture (above), which makes it under "
        "tmp_path; the path arrives through that fixture",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "agentic/core/knowledge/domain/*",
        "names these paths in the allowances and assertions above; it never opens them",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "observability/collector/otel-collector.yaml",
        "names the path in the assertion that the guard rejects an uncovered read",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "agentic/core/knowledge/docs/adr/*",
        "names the path in an allowance and in the assertion that it is ignored; it never opens it",
    ),
    Allowance(
        "tests/test_adr_numbers.py",
        "agentic/core/knowledge/docs/adr/*",
        "its `.../'docs'/'adr'` is anchored at the repo root but the guard matches an "
        "unknown anchor by suffix; it lists the root docs/adr, never a module's",
    ),
    Allowance(
        "tests/test_language.py",
        "tests/*__init__.py",
        "checks `agentic/core/*/__init__.py` exist; the same name under tests/ is not read",
    ),
    Allowance(
        "tests/core/process/domain/test_master_gain.py",
        "docs/README.md",
        "its `docs/*.md` is a glob it feeds to the matcher as a string; it reads no page",
    ),
    Allowance(
        "tests/core/process/domain/test_roles.py",
        "docs/README.md",
        "names `docs/**` as a string of a `docs:` block; it never reads the page",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "docs/README.md",
        "its `docs/*` strings are inputs to the selector under test, not reads of the page",
    ),
    *(
        Allowance(
            test,
            path,
            "names the owner file in strings and in the keys of a fake; it never reads the real "
            "one",
        )
        for test in (
            "tests/core/knowledge/adapters/filesystem/test_knowledge.py",
            "tests/core/knowledge/domain/test_knowledge_index.py",
            "tests/core/process/application/test_preflight.py",
            "tests/core/process/domain/test_head_reviewed.py",
            "tests/core/process/domain/test_master_gain.py",
            "tests/core/process/domain/test_owner_rules_rules.py",
            "tests/core/process/domain/test_roles.py",
            "tests/test_affected_tests.py",
        )
        for path in ("docs/decisiones-del-dueño.md", "docs/orquestacion.md")
        if (test, path)
        != ("tests/core/process/application/test_preflight.py", "docs/orquestacion.md")
    ),
)


class Evidence(NamedTuple):
    test: str
    parts: str  # the constant components of the path expression, joined with "/"
    scan: str | None  # the glob pattern when the expression is the root of a tree walk
    recursive: bool


def _chain(node: ast.AST, names: dict[str, list[str]]) -> list[str]:
    """The path components of an expression, `*` for a computed one, in order."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [part for part in node.value.split("/") if part]
    if isinstance(node, ast.JoinedStr):
        return ["*"]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _chain(node.left, names) + _chain(node.right, names)
    if isinstance(node, ast.Name):
        return names.get(node.id, ["*"])
    if isinstance(node, ast.Attribute):
        if node.attr == "__file__" and isinstance(node.value, ast.Name):
            return ["agentic"] if node.value.id == "agentic" else []
        return _chain(node.value, names) if node.attr in {"parent", "parents"} else ["*"]
    if isinstance(node, ast.Subscript):
        return _chain(node.value, names)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Path":
            return [part for arg in node.args for part in _chain(arg, names)]
        if isinstance(func, ast.Attribute) and func.attr in {"resolve", "absolute", "expanduser"}:
            return _chain(func.value, names)
        if isinstance(func, ast.Attribute) and func.attr == "joinpath":
            return _chain(func.value, names) + [p for a in node.args for p in _chain(a, names)]
    return ["*"]


def _anchored(chain: list[str]) -> list[str]:
    """A leading computed component is the anchor, unknown: the match is by suffix."""
    while chain and chain[0] == "*":
        chain = chain[1:]
    return chain


def _rooted(node: ast.AST, rooted: set[str]) -> bool:
    """Whether the expression starts from this file's own location or from an anchor."""
    return any(
        (isinstance(n, ast.Name) and (n.id == "__file__" or n.id in rooted))
        or (isinstance(n, ast.Attribute) and n.attr == "__file__")
        for n in ast.walk(node)
    )


def _assignments(tree: ast.Module) -> tuple[dict[str, list[str]], set[str]]:
    """Simple `NAME = <path expression>` anywhere in the file (the last one wins), and
    which names start from the file's own location."""
    names: dict[str, list[str]] = {}
    rooted: set[str] = set()
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1]
    for node in sorted(assigns, key=lambda n: n.lineno):
        target = node.targets[0]
        if isinstance(target, ast.Name):
            names[target.id] = _anchored(_chain(node.value, names))
            if _rooted(node.value, rooted):
                rooted.add(target.id)
    return names, rooted


class _Origins:
    """Which expressions of a module are derived from a temporary directory: `tmp_path` and
    its siblings, and `tempfile`. A name is temporary only if every binding of it in its own
    function scope is a temporary expression; one other binding unmarks it. A name the scope
    does not bind is looked up in the enclosing scopes, then the module. A name a function
    receives is temporary only when it is called as one of the sources: what a test passes
    to a helper is not seen (the allowances name those helpers)."""

    def __init__(self, tree: ast.Module) -> None:
        self.nodes = list(ast.walk(tree))
        self.parents = {id(c): n for n in self.nodes for c in ast.iter_child_nodes(n)}
        self.tempfile_modules = {"tempfile"}
        self.tempfile_calls: set[str] = set()
        self.bindings: dict[tuple[int, str], list[ast.expr | bool]] = {}
        self.deciding: set[tuple[int, str]] = set()
        self.decided: dict[tuple[int, str], bool] = {}
        self.tree = tree
        taken: set[int] = set()
        for node in self.nodes:
            self._import(node)
            self._bind(node, taken)
        for node in self.nodes:
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store)
                and id(node) not in taken
            ):
                self._add(node, node.id, False)

    def _import(self, node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tempfile":
                    self.tempfile_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "tempfile":
            for alias in node.names:
                if alias.name in TEMPFILE_CALLS:
                    self.tempfile_calls.add(alias.asname or alias.name)

    def scope(self, node: ast.AST) -> ast.AST:
        """The innermost function or lambda around the node, else the module. A class body
        and a comprehension bind in the function that holds them."""
        parent = self.parents.get(id(node))
        while parent is not None and not isinstance(parent, SCOPES):
            parent = self.parents.get(id(parent))
        return parent if parent is not None else self.tree

    def _add(self, node: ast.AST, name: str, value: ast.expr | bool) -> None:
        self.bindings.setdefault((id(self.scope(node)), name), []).append(value)

    def _target(self, target: ast.expr, value: ast.expr | bool, taken: set[int]) -> None:
        if isinstance(target, ast.Name):
            taken.add(id(target))
            self._add(target, target.id, value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            paired = (
                isinstance(value, (ast.Tuple, ast.List))
                and len(value.elts) == len(target.elts)
                and not any(isinstance(e, ast.Starred) for e in (*value.elts, *target.elts))
            )
            for index, element in enumerate(target.elts):
                item = (
                    value.elts[index]
                    if paired and isinstance(value, (ast.Tuple, ast.List))
                    else False
                )
                self._target(element, item, taken)

    def _bind(self, node: ast.AST, taken: set[int]) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._target(target, node.value, taken)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            self._target(node.target, node.value, taken)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            self._target(node.target, node.iter, taken)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._target(node.optional_vars, node.context_expr, taken)
        elif isinstance(node, ast.arg):
            self._add(node, node.arg, node.arg in TEMP_NAMES)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._add(node, node.name, False)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self._add(node, node.name, False)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self._add(node, (alias.asname or alias.name).split(".")[0], False)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            self._add(node, node.name, False)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self._add(node, node.rest, False)

    def _name(self, node: ast.Name) -> bool:
        scope = self.scope(node)
        while True:
            key = (id(scope), node.id)
            if key in self.bindings:
                return self._decide(key)
            if scope is self.tree:
                return node.id in TEMP_NAMES
            scope = self.scope(scope)

    def _decide(self, key: tuple[int, str]) -> bool:
        if key in self.decided:
            return self.decided[key]
        if key in self.deciding:
            return False  # a name built on itself is not proven temporary
        self.deciding.add(key)
        verdict = all(
            value if isinstance(value, bool) else self.temporary(value)
            for value in self.bindings[key]
        )
        self.deciding.discard(key)
        self.decided[key] = verdict
        return verdict

    def _tempfile_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in self.tempfile_calls
        return (
            isinstance(func, ast.Attribute)
            and func.attr in TEMPFILE_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id in self.tempfile_modules
        )

    def temporary(self, node: ast.AST) -> bool:
        """Whether the expression is a path (or the paths of a walk) under a temporary
        directory: the leftmost operand of the path spine is one."""
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.BinOp):
            return isinstance(node.op, ast.Div) and self.temporary(node.left)
        if isinstance(node, ast.Attribute):
            return node.attr in {"parent", "parents", "name"} and self.temporary(node.value)
        if isinstance(node, ast.Subscript):
            return self.temporary(node.value)
        if isinstance(node, (ast.Tuple, ast.List)):
            return bool(node.elts) and all(self.temporary(e) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return self.temporary(node.body) and self.temporary(node.orelse)
        if isinstance(node, ast.Call):
            func = node.func
            if self._tempfile_call(node):
                return True
            if isinstance(func, ast.Name) and func.id == "Path":
                return bool(node.args) and self.temporary(node.args[0])
            if isinstance(func, ast.Name) and func.id in PATH_WRAPPERS:
                return bool(node.args) and self.temporary(node.args[0])
            if isinstance(func, ast.Attribute):
                spine = PATH_KEEPERS | PATH_WALKS | {"joinpath"}
                return func.attr in spine and self.temporary(func.value)
        return False


def _spine(node: ast.AST, skip: set[int]) -> None:
    """Add the nodes of a path expression's spine to `skip`: the same cases `_chain`
    follows, and no other (a call argument or an attribute like `.name` ends it)."""
    skip.add(id(node))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        _spine(node.left, skip)
        _spine(node.right, skip)
    elif isinstance(node, ast.JoinedStr):
        skip.update(id(v) for v in node.values if isinstance(v, ast.Constant))
    elif isinstance(node, ast.Attribute) and node.attr in {"parent", "parents"}:
        _spine(node.value, skip)
    elif isinstance(node, ast.Subscript):
        _spine(node.value, skip)
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Path":
            for arg in node.args:
                _spine(arg, skip)
        elif isinstance(func, ast.Attribute) and func.attr in PATH_KEEPERS | PATH_WALKS:
            _spine(func.value, skip)
        elif isinstance(func, ast.Attribute) and func.attr == "joinpath":
            _spine(func.value, skip)
            for arg in node.args:
                _spine(arg, skip)


def _temporary_spines(tree: ast.Module) -> set[int]:
    """The ids of every node that lies on the path spine of a temporary expression: it
    names a place under a temporary directory, never a repo file."""
    origins = _Origins(tree)
    skip: set[int] = set()
    for node in origins.nodes:
        if isinstance(node, ast.expr) and id(node) not in skip and origins.temporary(node):
            _spine(node, skip)
    return skip


def _evidence(test: str, tree: ast.Module) -> set[Evidence]:
    names, rooted = _assignments(tree)
    found: set[Evidence] = set()
    skip = _temporary_spines(tree)
    inner_divs = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        for child in (node.left, node.right)
        if isinstance(child, ast.BinOp)
    }

    def add(chain: list[str], scan: str | None = None, recursive: bool = False) -> None:
        chain = _anchored(chain)
        if scan is not None or (chain and chain[-1] != "*"):
            found.add(Evidence(test, "/".join(chain), scan, recursive))

    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            plain = not any(c in text for c in " \n{}")
            if plain and (
                text.startswith(PATH_PREFIXES) or ("/" in text and text.endswith(PATH_SUFFIXES))
            ):
                add([text])
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if id(node) not in inner_divs:
                add(_chain(node, names))
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called not in PATH_CALLS:
                continue
            if called in {"glob", "rglob", "iterdir"} and isinstance(func, ast.Attribute):
                pattern = "*"
                if node.args and isinstance(node.args[0], ast.Constant):
                    pattern = str(node.args[0].value)
                receiver = _anchored(_chain(func.value, names))
                if receiver or _rooted(func.value, rooted):
                    add(receiver, pattern, called == "rglob")
            elif called == "Path":
                add(_chain(node, names))
    return found


def _remainder(parts: str, tracked: str) -> str | None:
    """What of `tracked` lies under `parts`, wherever `parts` sits in the path; `*` in
    `parts` is a computed component. None when it does not."""
    hay = "/" + tracked + "/"
    needle = "/" + parts + "/" if parts else "/"
    for start in range(len(hay)):
        if hay[start] != "/":
            continue
        pattern_len = needle.count("/")
        pieces = hay[start:].split("/")
        head = "/".join(pieces[:pattern_len]) + "/"
        if fnmatch.fnmatchcase(head, needle):
            return "/".join(pieces[pattern_len:]).strip("/")
    return None


def _files_read(item: Evidence, tracked: list[str]) -> list[str]:
    found: list[str] = []
    for path in tracked:
        rest = _remainder(item.parts, path)
        if rest is None:
            continue
        if item.scan is None:
            if rest == "":
                found.append(path)
        elif rest:
            base = rest if item.recursive is False else rest.rsplit("/", 1)[-1]
            deep = "/" in rest and not item.recursive and "/" not in item.scan
            if not deep and fnmatch.fnmatchcase(base, item.scan):
                found.append(path)
    return found


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p and (REPO / p).exists())


def _test_files() -> list[str]:
    return sorted(
        p.relative_to(REPO).as_posix()
        for p in (REPO / "tests").rglob("*.py")
        if not p.relative_to(REPO).as_posix().startswith("tests/llm/")
    )


def _real_graph():
    return script.graph(script.read_sources(REPO))


def _readers(test: str, found) -> set[str]:
    """The tests a file's reads belong to: itself, or the tests that import a helper."""
    if script.is_test(test):
        return {test}
    return script.reaching([script.module_name(test)], found)


def _reads(tracked: list[str]) -> list[tuple[str, str, str]]:
    """(file that reads, repo file read, expression) over the real tests."""
    found: list[tuple[str, str, str]] = []
    for test in _test_files():
        tree = ast.parse((REPO / test).read_text(encoding="utf-8"))
        for item in sorted(_evidence(test, tree), key=str):
            found.extend((test, path, item.parts) for path in _files_read(item, tracked))
    return found


def _uncovered(
    reads: list[tuple[str, str, str]],
    fallback: tuple[str, ...],
    table: tuple,
    allowances: tuple,
    found,
    memo: dict[str, dict] | None = None,
) -> list[tuple[str, str]]:
    """(test, file read) for every repo file a test reads that nothing covers: not a
    fallback glob, not a table row listing the test, not the import closure, not a named
    allowance. `memo` keeps what does not depend on the allowances (the closures of the
    graph) between calls that share `found` and `table`."""
    missing: set[tuple[str, str]] = set()
    cache = {} if memo is None else memo
    targets: dict[int, set[str]] = cache.setdefault("targets", {})
    closure: dict[str, set[str]] = cache.setdefault("closure", {})
    readers: dict[str, set[str]] = cache.setdefault("readers", {})
    for source, path in sorted({(s, p) for s, p, _ in reads}):
        if source not in readers:
            readers[source] = _readers(source, found)
        for test in readers[source]:
            rows = [row for row in table if script.matches(row.glob, path)]
            module = script.module_name(path)
            for row in rows:
                if id(row) not in targets:
                    targets[id(row)] = script.targets_of(row, found)
            if module not in closure and script.in_graph(path):
                closure[module] = script.reaching([module], found)
            covered = (
                any(script.matches(glob, path) for glob in fallback)
                or any(test in targets[id(row)] for row in rows)
                or (script.in_graph(path) and test in closure[module])
                or any(
                    script.matches(a.test, source) and script.matches(a.path, path)
                    for a in allowances
                )
            )
            if not covered:
                missing.add((test, path))
    return sorted(missing)


def _dead_allowances(
    reads: list[tuple[str, str, str]],
    fallback: tuple[str, ...],
    table: tuple,
    allowances: tuple[Allowance, ...],
    found,
) -> list[Allowance]:
    """The allowances that cover nothing: walking them in order, one is dead when taking it
    out of those still standing leaves `_uncovered` as it is, and a dead one is out for the
    rest of the walk. Of two that cover the same pair, the first is the one reported."""
    # Only a read nothing else covers can need an allowance, so the walk replays just those
    # through `_uncovered`: the answer is the one it gives for every read, in a fraction of
    # the time.
    memo: dict[str, dict] = {}
    open_pairs = set(_uncovered(reads, fallback, table, (), found, memo))
    reads = [r for r in reads if any((t, r[1]) in open_pairs for t in memo["readers"][r[0]])]
    alive = list(range(len(allowances)))
    baseline = _uncovered(reads, fallback, table, allowances, found, memo)
    dead: list[Allowance] = []
    for index, allowance in enumerate(allowances):
        rest = tuple(allowances[i] for i in alive if i != index)
        if _uncovered(reads, fallback, table, rest, found, memo) == baseline:
            alive.remove(index)
            dead.append(allowance)
    return dead


def _declared_fallback() -> tuple[str, ...]:
    data = yaml.safe_load(STACK_YAML.read_text(encoding="utf-8"))
    return stack.affected_fallback(data, STACK_YAML)


def _fallback() -> tuple[str, ...]:
    """What the gate treats as fallback: the declared globs and, always, the stack file
    itself, which the domain adds (ADR-0046)."""
    return (*_declared_fallback(), ".agentic/stack.yaml")


@pytest.fixture(scope="module")
def real():
    tracked = _tracked()
    return tracked, _reads(tracked), _real_graph()


def test_the_stack_declares_the_affected_form_and_keeps_the_other_checks_full():
    data = yaml.safe_load(STACK_YAML.read_text(encoding="utf-8"))
    forms = stack.affected_checks(data, STACK_YAML)
    assert list(forms) == ["tests"]
    assert forms["tests"].startswith("uv run python scripts/affected_tests.py {base}")
    # `docs` is the reserved check the `docs:` block adds (ADR-0074); CI runs it as check.py.
    assert set(stack.checks(data, STACK_YAML)) == {"tests", "lint", "format", "types", "docs"}
    assert _declared_fallback() == (
        "pyproject.toml",
        "uv.lock",
        "*conftest.py",
        "agentic/shared/*",
        "agentic/composition.py",
        "agentic/cli.py",
        "agentic/*/prompts/*",
        "agentic/plugins/*",
        "*CONTEXT*.md",
        ".github/*",
        "scripts/*",
        ".env.example",
    )


def test_every_repo_file_a_test_reads_is_covered_on_the_real_tree(real):
    tracked, reads, found = real
    assert reads, "the guard found no read at all: its evidence collector is broken"
    missing = _uncovered(reads, _fallback(), script.TABLE, ALLOWANCES, found)
    assert missing == [], (
        "a test reads these repo files and neither `TABLE` in scripts/affected_tests.py, "
        "`affected_fallback` in .agentic/stack.yaml nor ALLOWANCES covers them"
    )


def test_no_allowance_is_dead_on_the_real_tree(real):
    _, reads, found = real
    dead = _dead_allowances(reads, _fallback(), script.TABLE, ALLOWANCES, found)
    assert dead == [], "these allowances cover nothing any more, remove them:\n" + "\n".join(
        f"{a.test} {a.path} -- {a.reason}" for a in dead
    )


def test_the_guard_rejects_a_read_nothing_covers(real):
    _, reads, found = real
    uncovered = _uncovered(reads, _fallback(), (), (), found)
    assert ("tests/test_stack.py", "CODING_STANDARDS.md") in uncovered
    assert (
        "tests/test_observability_config.py",
        "observability/collector/otel-collector.yaml",
    ) in (uncovered)


def test_the_guard_rejects_a_fallback_that_lost_a_glob_a_read_relied_on(real):
    _, reads, found = real
    narrowed = tuple(g for g in _fallback() if g != "pyproject.toml")
    missing = _uncovered(reads, narrowed, script.TABLE, ALLOWANCES, found)
    assert ("tests/test_stack.py", "pyproject.toml") in missing


def test_the_evidence_finds_a_read_through_an_anchor_a_literal_and_a_walk():
    source = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "A = ROOT / 'docs' / 'x.md'\n"
        "B = 'observability/y.yaml'\n"
        "def f():\n    return list((ROOT / 'agentic').rglob('*.py'))\n"
    )
    found = {(e.parts, e.scan) for e in _evidence("tests/test_x.py", ast.parse(source))}
    assert ("docs/x.md", None) in found
    assert ("observability/y.yaml", None) in found
    assert ("agentic", "*.py") in found


def _parts(source: str) -> set[str]:
    return {e.parts for e in _evidence("tests/test_x.py", ast.parse(source))}


def test_a_tmp_path_expression_leaves_no_evidence_not_even_its_literal():
    # the shape of tests/core/process/adapters/filesystem/test_prompts.py:84
    source = (
        "def test_a(tmp_path):\n"
        "    (tmp_path / '.agentic/playbooks/summaries/10.md').write_text('x')\n"
        "    assert (tmp_path / 'docs/x.md').exists()\n"
    )
    assert _evidence("tests/test_x.py", ast.parse(source)) == set()


def test_a_tmp_path_read_is_not_matched_to_the_repo_file_of_the_same_name():
    tracked = [".agentic/playbooks/summaries/10.md"]
    found = script.graph({"tests/test_x.py": ""})
    source = (
        "def test_a(tmp_path):\n    (tmp_path / '.agentic/playbooks/summaries/10.md').read_text()\n"
    )
    reads = [
        ("tests/test_x.py", path, item.parts)
        for item in _evidence("tests/test_x.py", ast.parse(source))
        for path in _files_read(item, tracked)
    ]
    assert _uncovered(reads, (), (), (), found) == []


def test_a_read_anchored_at_the_repo_is_still_reported_when_nothing_covers_it():
    source = "def test_a():\n    (ROOT / 'docs/x.md').read_text()\n"
    found = script.graph({"tests/test_x.py": ""})
    reads = [
        ("tests/test_x.py", path, item.parts)
        for item in _evidence("tests/test_x.py", ast.parse(source))
        for path in _files_read(item, ["docs/x.md"])
    ]
    assert _uncovered(reads, (), (), (), found) == [("tests/test_x.py", "docs/x.md")]


def test_a_name_bound_from_tmp_path_carries_the_mark_to_what_is_built_on_it():
    source = (
        "def test_a(tmp_path):\n    root = tmp_path / 'x'\n    (root / 'docs/x.md').read_text()\n"
    )
    assert _parts(source) == set()


def test_a_name_the_function_receives_is_not_temporary_even_when_a_test_passes_a_tmp_path():
    # named residual: `helper(tmp_path)` is a false positive a reasoned allowance covers
    source = "def helper(root):\n    (root / 'docs/x.md').read_text()\n"
    assert "docs/x.md" in _parts(source)


def test_one_unmarked_binding_of_a_name_in_its_scope_unmarks_it():
    source = (
        "def test_a(tmp_path):\n"
        "    root = tmp_path / 'x'\n"
        "    root = ROOT\n"
        "    (root / 'docs/x.md').read_text()\n"
    )
    assert "docs/x.md" in _parts(source)


def test_a_nested_function_binding_does_not_mark_the_outer_scope_name():
    source = (
        "def test_a(tmp_path):\n"
        "    root = ROOT\n"
        "    def inner():\n"
        "        root = tmp_path / 'x'\n"
        "        return (root / 'docs/inner.md').read_text()\n"
        "    (root / 'docs/outer.md').read_text()\n"
    )
    parts = _parts(source)
    assert "docs/outer.md" in parts and "docs/inner.md" not in parts


def test_a_loop_over_a_repo_root_and_a_tmp_path_is_not_temporary():
    source = (
        "def test_a(tmp_path):\n"
        "    for root in (REPO, tmp_path):\n"
        "        (root / 'docs/x.md').read_text()\n"
    )
    assert "docs/x.md" in _parts(source)


def test_a_copy_from_the_repo_to_tmp_path_leaves_only_the_repo_side():
    source = "def test_a(tmp_path):\n    shutil.copy(REPO / 'docs/x.md', tmp_path / 'docs/x.md')\n"
    found = _evidence("tests/test_x.py", ast.parse(source))
    assert [e.parts for e in found] == ["docs/x.md"]
    assert len(found) == 1


def test_a_repo_read_nested_in_a_tmp_path_expression_still_leaves_evidence():
    source = "def test_a(tmp_path):\n    tmp_path / (ROOT / 'docs/x.md').name\n"
    assert "docs/x.md" in _parts(source)


def test_a_path_under_a_tempfile_directory_leaves_no_evidence():
    source = (
        "import tempfile\n"
        "from pathlib import Path\n"
        "from tempfile import mkdtemp\n"
        "def test_a():\n"
        "    with tempfile.TemporaryDirectory() as d:\n"
        "        (Path(d) / 'docs/x.md').read_text()\n"
        "    (Path(mkdtemp()) / 'docs/y.md').read_text()\n"
    )
    assert _parts(source) == set()


def test_a_glob_over_a_tmp_path_directory_leaves_no_evidence():
    source = "def test_a(tmp_path):\n    list((tmp_path / 'docs').glob('*.md'))\n"
    assert _evidence("tests/test_x.py", ast.parse(source)) == set()


def test_the_real_tree_keeps_a_read_anchored_at_this_file_s_location(real):
    _, reads, _ = real
    assert any(t == "tests/test_adr_numbers.py" and p.startswith("docs/adr/") for t, p, _ in reads)


def _table(glob: str, *targets: str) -> tuple:
    return (script.Row(glob, targets, "covers it"),)


def _dead(allowances: tuple[Allowance, ...], table: tuple = ()) -> list[Allowance]:
    reads = [("tests/test_x.py", "docs/x.md", "docs/x.md")]
    found = script.graph({"tests/test_x.py": ""})
    return _dead_allowances(reads, (), table, allowances, found)


def test_an_allowance_over_a_read_a_table_row_covers_is_dead():
    named = Allowance("tests/test_x.py", "docs/x.md", "names it")
    assert _dead((named,), _table("docs/x.md", "tests/test_x.py")) == [named]


def test_the_only_cover_of_a_read_is_not_dead():
    named = Allowance("tests/test_x.py", "docs/x.md", "names it")
    assert _dead((named,)) == []


def test_of_two_allowances_over_the_same_pair_only_the_first_is_dead():
    first = Allowance("tests/test_x.py", "docs/x.md", "names it")
    second = Allowance("tests/test_x.py", "docs/*", "names the glob")
    assert _dead((first, second)) == [first]


def test_an_allowance_over_a_pair_no_test_reads_is_dead():
    other = Allowance("tests/test_x.py", "docs/y.md", "names another page")
    assert _dead((other,)) == [other]


def test_removing_every_dead_allowance_leaves_the_uncovered_reads_unchanged():
    reads = [
        ("tests/test_x.py", "docs/x.md", "docs/x.md"),
        ("tests/test_x.py", "docs/y.md", "docs/y.md"),
    ]
    found = script.graph({"tests/test_x.py": ""})
    table = _table("docs/x.md", "tests/test_x.py")
    allowances = (
        Allowance("tests/test_x.py", "docs/x.md", "table covers it"),
        Allowance("tests/test_x.py", "docs/y.md", "the glob after it covers y as well"),
        Allowance("tests/test_x.py", "docs/*", "the last cover of y"),
        Allowance("tests/test_x.py", "docs/z.md", "nothing reads it"),
    )
    dead = _dead_allowances(reads, (), table, allowances, found)
    kept = tuple(a for a in allowances if a not in dead)
    assert _uncovered(reads, (), table, kept, found) == _uncovered(
        reads, (), table, allowances, found
    )
    assert dead == [allowances[0], allowances[1], allowances[3]]


# Rows whose glob legitimately reaches paths the fallback also matches: a `.py` under the
# fallback still has to select the walkers, and the closure decides it in a standalone run.
OVERLAPS = {
    "agentic/*.py": "walkers and the guard read every source file, fallback ones included",
    "tests/*.py": "the guard parses every test file, `conftest.py` included",
}


def _overlaps(
    globs: dict[str, str], fallback: tuple[str, ...], tracked: list[str], exempt: dict[str, str]
) -> list[tuple[str, str]]:
    """(glob, path) for every tracked path that a glob and a fallback glob both match, and
    every glob that repeats a fallback glob, unless the glob is a named exemption."""
    found = []
    for glob in globs:
        if glob in exempt:
            continue
        if glob in fallback:
            found.append((glob, "<same glob>"))
        found.extend(
            (glob, path)
            for path in tracked
            if script.matches(glob, path) and any(script.matches(f, path) for f in fallback)
        )
    return found


def _ignored_but_read(
    ignore: tuple[str, ...],
    reads: list[tuple[str, str, str]],
    allowances: tuple[Allowance, ...] = (),
):
    """(ignore glob, test, path) for every read an ignore entry covers, unless a named
    allowance says the test only names that path."""
    return [
        (g, t, p)
        for g in ignore
        for t, p, _ in reads
        if script.matches(g, p)
        and not any(script.matches(a.test, t) and script.matches(a.path, p) for a in allowances)
    ]


def _dead_globs(fallback: tuple[str, ...], tracked: list[str]) -> list[str]:
    return [g for g in fallback if not any(script.matches(g, p) for p in tracked)]


def _sharing() -> dict[str, str]:
    return (
        {row.glob: row.reason for row in script.TABLE}
        | {glob: "ignored" for glob in script.IGNORE}
        | {a.path: a.reason for a in ALLOWANCES}
    )


def test_table_ignore_and_allowances_match_nothing_the_fallback_matches(real):
    tracked, _, _ = real
    assert _overlaps(_sharing(), _declared_fallback(), tracked, OVERLAPS) == []


def test_the_overlap_check_rejects_a_row_inside_a_fallback_glob_and_a_repeated_glob():
    tracked = ["CONTEXT-MAP.md", "README.md", "agentic/core/process/CONTEXT.md"]
    fallback = ("*CONTEXT*.md",)
    found = _overlaps({"CONTEXT-MAP.md": "x", "README.md": "y"}, fallback, tracked, {})
    assert found == [("CONTEXT-MAP.md", "CONTEXT-MAP.md")]
    assert _overlaps({"*CONTEXT*.md": "x"}, fallback, tracked, {}) != []
    assert _overlaps({"*.md": "x"}, fallback, tracked, {"*.md": "named"}) == []


def test_no_ignore_entry_is_read_by_a_test(real):
    _, reads, _ = real
    assert _ignored_but_read(script.IGNORE, reads, ALLOWANCES) == []


def test_the_ignore_check_rejects_an_entry_a_test_reads():
    reads = [("tests/test_x.py", "docs/x.md", "docs/x.md")]
    assert _ignored_but_read(("docs/*",), reads) == [("docs/*", "tests/test_x.py", "docs/x.md")]
    assert _ignored_but_read((".agentic/tasks/*",), reads) == []
    named = (Allowance("tests/test_x.py", "docs/x.md", "only names it"),)
    assert _ignored_but_read(("docs/*",), reads, named) == []
    other_test = (Allowance("tests/test_y.py", "docs/x.md", "another test names it"),)
    other_path = (Allowance("tests/test_x.py", "docs/y.md", "names another path"),)
    expected = [("docs/*", "tests/test_x.py", "docs/x.md")]
    assert _ignored_but_read(("docs/*",), reads, other_test) == expected
    assert _ignored_but_read(("docs/*",), reads, other_path) == expected


def test_every_fallback_glob_matches_a_real_path(real):
    tracked, _, _ = real
    assert _dead_globs(_declared_fallback(), tracked) == []


def test_the_fallback_check_rejects_a_glob_that_matches_no_path():
    assert _dead_globs(("pyproject.toml", "nothing/*"), ["pyproject.toml"]) == ["nothing/*"]


def test_every_table_row_names_targets_that_exist_and_a_reason(real):
    _, _, found = real
    for row in script.TABLE:
        assert row.reason.strip() and "\n" not in row.reason
        assert script.targets_of(row, found), f"{row.glob} -> {row.targets} reaches no test"


def test_the_real_tree_selects_a_subset_for_a_leaf_and_nearly_all_for_the_core(real):
    _, _, found = real
    every = {p for p in found.paths.values() if script.is_test(p)}
    leaf = script.select(
        ["agentic/core/process/adapters/shell/inhibitor.py"], found, script.TABLE, script.IGNORE
    )
    assert isinstance(leaf, script.Selection) and set(leaf.files) < every
    assert len(leaf.files) < len(every) / 2
