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
ANCHORS = frozenset({"ROOT", "REPO", "CORE", "PACKAGE"})


class Allowance(NamedTuple):
    """`test` (a glob) names a repo file in `path` (a glob) without reading the real one:
    it builds that name under `tmp_path`, or only mentions it in a message."""

    test: str
    path: str
    reason: str


ALLOWANCES: tuple[Allowance, ...] = (
    Allowance(
        "tests/*",
        "docs/architecture/generated/*",
        "tests name these as the string globs of a `docs:` block in a throwaway repo; none "
        "reads the files, which the docs check deletes and restores (GH-400, ADR-0076)",
    ),
    Allowance(
        "tests/*",
        "docs/reference/*",
        "same: the second owned glob of this repo's `docs:` block, named and never read",
    ),
    Allowance(
        "tests/core/knowledge/adapters/filesystem/test_knowledge.py",
        "BRIEF.md",
        "builds BRIEF.md under tmp_path, the same name as the repo's",
    ),
    Allowance(
        "tests/core/knowledge/adapters/filesystem/test_knowledge.py",
        "*CODING_STANDARDS.md",
        "builds CODING_STANDARDS.md under tmp_path, the same name as the repo's",
    ),
    Allowance(
        "tests/core/knowledge/adapters/git/test_stack.py",
        "*README.md",
        "commits a README.md into a throwaway repo",
    ),
    Allowance(
        "tests/core/process/adapters/git/test_workspace.py",
        "*README.md",
        "makes its own repos under tmp_path with a README.md",
    ),
    Allowance(
        "tests/core/process/adapters/git/test_workspace.py",
        ".gitignore",
        "makes its own repos under tmp_path with a .gitignore",
    ),
    Allowance(
        "tests/core/process/application/test_merge_for_real.py",
        "*CODING_STANDARDS.md",
        "writes CODING_STANDARDS.md into a throwaway product",
    ),
    Allowance("tests/test_cli.py", "BRIEF.md", "writes BRIEF.md into a tmp_path product"),
    Allowance(
        "tests/test_affected_tests.py",
        ".agentic/playbooks/*",
        "names these paths in the allowances above; it never opens them",
    ),
    Allowance(
        "tests/test_reflect_records.py",
        ".agentic/playbooks/*",
        "writes the playbook files under a tmp_path root, the same names as the repo's",
    ),
    Allowance(
        "tests/core/process/application/test_run_role.py",
        ".agentic/playbooks/*",
        "writes playbook summaries under a tmp_path checkout, the same names as the repo's",
    ),
    Allowance(
        "tests/test_affected_tests.py",
        "langgraph.json",
        "writes a langgraph.json into a throwaway repo to provoke a refusal",
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
        "tests/test_stack.py",
        "agentic/core/knowledge/domain/*",
        "reads the repo-root CODING_STANDARDS.md, not the package default of the same name",
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


def _evidence(test: str, tree: ast.Module) -> set[Evidence]:
    names, rooted = _assignments(tree)
    found: set[Evidence] = set()
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
) -> list[tuple[str, str]]:
    """(test, file read) for every repo file a test reads that nothing covers: not a
    fallback glob, not a table row listing the test, not the import closure, not a named
    allowance."""
    missing: set[tuple[str, str]] = set()
    for source, path, _ in reads:
        for test in _readers(source, found):
            rows = [row for row in table if script.matches(row.glob, path)]
            module = script.module_name(path)
            covered = (
                any(script.matches(glob, path) for glob in fallback)
                or any(test in script.targets_of(row, found) for row in rows)
                or (script.in_graph(path) and test in script.reaching([module], found))
                or any(
                    script.matches(a.test, source) and script.matches(a.path, path)
                    for a in allowances
                )
            )
            if not covered:
                missing.add((test, path))
    return sorted(missing)


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
