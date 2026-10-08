"""`scripts/affected_tests.py`: the selection (fixtures, through its pure functions and its
exit contract) and the guard that keeps its input table honest on the real tree."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
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


def test_adding_a_test_selects_that_test_and_not_the_walkers():
    sources = _walker_sources() | {"tests/test_new.py": ""}
    assert _files(_select(["tests/test_new.py"], sources, script.TABLE, script.IGNORE)) == (
        "tests/test_new.py",
    )


def test_a_doc_selects_no_walker_it_is_refused_as_unmapped():
    result = _select(["docs/x.md"], _walker_sources(), script.TABLE, script.IGNORE)
    assert isinstance(result, script.Refusal) and "docs/x.md" in result.reason


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


def test_a_syntax_error_in_the_tree_cannot_be_decided():
    with pytest.raises(script.Undecided):
        script.graph({"agentic/x/bad.py": "def (:\n"})
