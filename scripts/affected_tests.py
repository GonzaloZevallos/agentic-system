"""The affected-only form of this repo's `tests` check (ADR-0046, and its sibling ADR on
this selection): `uv run python scripts/affected_tests.py <base>` runs the tests a change
since `<base>` can reach, and refuses to guess otherwise.

The decision is pure (`parse_changes`, `module_name`, `graph`, `select`); `main` is the
thin shell around it. The script never runs the whole suite: the gate's retry is the only
path to full (ADR-0046, "Reintento"). Exit codes: pytest's own when a selection ran,
`EMPTY` when it selected nothing, `REFUSED` when it cannot decide. On those two it writes
exactly one stderr line and prints no selection.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import sys
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

EMPTY = 8
REFUSED = 9

ROOTS = ("agentic/", "tests/")
# Not part of the graph: the skills plugin is a pinned submodule (ADR-0013), and the
# `llm` tests are deselected by `-m "not llm"`.
EXCLUDED = ("agentic/plugins/", "tests/llm/")
LOADERS = frozenset({"import_module", "__import__", "spec_from_file_location"})
DOTTED = re.compile(r"^agentic(?:\.\w+)+")


class Row(NamedTuple):
    """A non-import input: a change to a path matching `glob` can affect `targets`.
    A target is a test file, a directory of tests (ends with `/`), or `module:<name>`,
    the tests that reach that module."""

    glob: str
    targets: tuple[str, ...]
    reason: str


# What a test reads without importing it. `tests/test_affected_tests.py` fails when a test
# reads a repo path that neither this table, `affected_fallback` nor one of its own
# allowances covers.
WALKERS = (
    "tests/test_architecture.py",
    "tests/test_language.py",
    "tests/core/measurement/adapters/otel/test_semconv.py",
    "tests/core/process/application/test_pause_log_guard.py",
    "tests/core/process/domain/test_reaping.py",
    "tests/core/process/domain/test_returns.py",
    "tests/core/process/application/test_release.py",
    "tests/core/process/application/test_prompt_placeholder_scan.py",
)
KNOWLEDGE = "agentic.core.knowledge.domain"
TABLE: tuple[Row, ...] = (
    Row("agentic/*.py", WALKERS, "these walk every source file of the package with ast"),
    Row(
        "agentic/*.py",
        ("tests/test_affected_tests.py",),
        "its guard builds the import graph of the whole package",
    ),
    Row(
        "tests/*.py",
        ("tests/test_affected_tests.py", "tests/test_tracker_doubles.py"),
        "ADR-0047's guard parses every test file for the repo paths it reads, and the "
        "Tracker-doubles guard parses every test file for doubles installed as "
        "`pull_request` or `merge`",
    ),
    Row(
        "agentic/core/process/application/landing*.py",
        ("tests/core/process/domain/test_document.py",),
        "its guard reads the keys the landing writes through `record_closure` and requires "
        "each to be a scalar or in `RECORD_KEYS`",
    ),
    Row(
        "agentic/core/knowledge/domain/*.md",
        (f"module:{KNOWLEDGE}.knowledge",),
        "the default standards and brief template are read through `knowledge`",
    ),
    Row(
        "agentic/core/knowledge/domain/stack.yaml",
        (f"module:{KNOWLEDGE}.stack",),
        "the stack template is read through `stack.TEMPLATE`",
    ),
    Row(
        "agentic/core/knowledge/domain/flags.yaml",
        (f"module:{KNOWLEDGE}.flags",),
        "the default flag vocabulary is read through `flags.DEFAULT`",
    ),
    Row(
        "CODING_STANDARDS.md",
        ("tests/test_stack.py",),
        "the pin guard reads this repo's own standards",
    ),
    Row(
        "observability/README.md",
        ("module:tests.readme_metrics",),
        "the metrics table is parsed by `readme_metrics`",
    ),
    Row(
        "observability/collector/otel-collector.yaml",
        ("tests/test_observability_config.py",),
        "that test parses the collector config",
    ),
    Row(
        "docs/*",
        ("tests/test_docs.py",),
        "its pages are checked for links, language and the generator's outputs",
    ),
    Row("docs/adr/*", ("tests/test_adr_numbers.py",), "that test lists the ADR directory"),
    Row(
        ".agentic/playbooks/*",
        (
            "tests/core/process/application/test_run_role.py",
            "tests/test_reflect_records.py",
        ),
        "those tests read the playbook files and summaries of this repo",
    ),
    Row(
        "tests/fixtures/langfuse_dashboards/*",
        (
            "tests/core/measurement/domain/test_dashboards.py",
            "tests/core/measurement/application/test_dashboards.py",
        ),
        "the dashboard definitions and the recorded columns are checked against the golden "
        "catalogue of Langfuse's schema",
    ),
    Row(
        "tests/fixtures/pre_performance_document.md",
        ("tests/core/process/domain/test_schemas.py",),
        "an old task document that must still parse with no performance scenarios",
    ),
)
# Paths no test reads (the guard asserts it); a change to them selects nothing.
IGNORE: tuple[str, ...] = (
    ".agentic/tasks/*",
    "docs/agents/*",
    "docs/product/*",
    "agentic/core/*/docs/adr/*",
)


@dataclass(frozen=True)
class Graph:
    """`imports`: module -> every module name it depends on, by name (a name need not be
    a module that exists: a deleted module is still a name an importer refers to).
    `paths`: module -> its file. `loaders`: test files that load code by a name they
    compute, so any source change can reach them."""

    imports: Mapping[str, frozenset[str]]
    paths: Mapping[str, str]
    loaders: frozenset[str]


@dataclass(frozen=True)
class Selection:
    files: tuple[str, ...]


@dataclass(frozen=True)
class Empty:
    reason: str


@dataclass(frozen=True)
class Refusal:
    reason: str


class Undecided(Exception):
    """The graph or the change set cannot be read; the script refuses."""


class GitFailure(Exception):
    pass


def module_name(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def in_graph(path: str) -> bool:
    return path.endswith(".py") and path.startswith(ROOTS) and not path.startswith(EXCLUDED)


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") and fnmatch.fnmatchcase(name, "test_*.py")


def matches(glob: str, path: str) -> bool:
    """The same match ADR-0046 gives `affected_fallback`: `*` crosses `/`."""
    return fnmatch.fnmatchcase(path, glob)


def parse_changes(diff: str, untracked: str) -> list[str]:
    """The changed set: `git diff --name-status -M -z` (both sides of a rename) plus
    `git ls-files --others --exclude-standard -z`."""
    tokens = diff.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        width = 2 if status[:1] in ("R", "C") else 1
        paths.extend(tokens[i + 1 : i + 1 + width])
        i += 1 + width
    paths.extend(p for p in untracked.split("\0") if p)
    return sorted(set(paths))


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except OSError as error:
        raise GitFailure(str(error)) from error
    if done.returncode != 0:
        raise GitFailure(done.stderr.strip())
    return done.stdout


def changed(base: str, root: Path) -> list[str]:
    """The working tree against `base`, plus untracked files. `base` is one argv element
    to git, never a shell word."""
    diff = _git(root, "diff", "--name-status", "-M", "-z", base, "--")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return parse_changes(diff, untracked)


def read_sources(root: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for top in ROOTS:
        for file in sorted((root / top).rglob("*.py")):
            rel = file.relative_to(root).as_posix()
            if in_graph(rel):
                sources[rel] = file.read_text(encoding="utf-8")
    return sources


def _dependencies(path: str, tree: ast.AST) -> tuple[set[str], bool]:
    """The names `tree` depends on and whether it loads code by a computed name."""
    own = module_name(path)
    package = own if path.endswith("__init__.py") else own.rpartition(".")[0]
    names: set[str] = set()
    computed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*up, *([node.module] if node.module else [])])
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found = DOTTED.match(node.value)
            if found:
                parts = found.group(0).split(".")
                names.update(".".join(parts[:n]) for n in range(2, len(parts) + 1))
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            arguments = [*node.args, *(k.value for k in node.keywords)]
            literal = all(
                isinstance(a, ast.Constant) and isinstance(a.value, str) for a in arguments
            )
            if called in LOADERS and not (arguments and literal):
                computed = True
    return names, computed


def graph(sources: Mapping[str, str]) -> Graph:
    """Parse `agentic/**/*.py` and `tests/**/*.py`. Every import, and every string
    constant that starts with `agentic.` (a `patch(...)` target, a `setattr` path), is an
    edge. A computed loader under `agentic/` makes the graph incomplete, so it refuses;
    in a test it joins the tests every source change reaches."""
    imports: dict[str, frozenset[str]] = {}
    paths: dict[str, str] = {}
    loaders: set[str] = set()
    for path, text in sources.items():
        if not in_graph(path):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as error:
            raise Undecided(f"{path} does not parse: {error.msg}") from error
        names, computed = _dependencies(path, tree)
        if computed and path.startswith("agentic/"):
            raise Undecided(f"{path} imports by a computed name")
        if computed:
            loaders.add(path)
        module = module_name(path)
        imports[module], paths[module] = frozenset(names), path
    return Graph(imports, paths, frozenset(loaders))


def reaching(names: Iterable[str], graph_: Graph) -> set[str]:
    """The test files whose reverse transitive closure reaches any of `names`. The match
    is by name: a test that imports a module deleted since is still selected, and fails."""
    importers: dict[str, set[str]] = defaultdict(set)
    for module, depends in graph_.imports.items():
        for name in depends:
            importers[name].add(module)
    seen = set(names)
    queue = deque(seen)
    while queue:
        for module in importers.get(queue.popleft(), ()):
            if module not in seen:
                seen.add(module)
                queue.append(module)
    return {graph_.paths[m] for m in seen if m in graph_.paths and is_test(graph_.paths[m])}


def targets_of(row: Row, graph_: Graph) -> set[str]:
    found: set[str] = set()
    tests = {p for p in graph_.paths.values() if is_test(p)}
    for target in row.targets:
        if target.startswith("module:"):
            found |= reaching([target.removeprefix("module:")], graph_)
        elif target.endswith("/"):
            found |= {t for t in tests if t.startswith(target)}
        else:
            found |= tests & {target}
    return found


def select(
    changes: Iterable[str], graph_: Graph, table: Sequence[Row], ignore: Sequence[str]
) -> Selection | Empty | Refusal:
    names: set[str] = set()
    picked: set[str] = set()
    for path in sorted(set(changes)):
        base = path.rsplit("/", 1)[-1]
        if base in ("__init__.py", "conftest.py"):
            return Refusal(f"{path} changes what every test imports or sees")
        rows = [row for row in table if matches(row.glob, path)]
        for row in rows:
            picked |= targets_of(row, graph_)
        if in_graph(path):
            names.add(module_name(path))
            if path.startswith("agentic/"):
                picked |= graph_.loaders
        elif not rows and not any(matches(glob, path) for glob in ignore):
            return Refusal(f"{path} maps to no test, input-table row or ignore entry")
    picked |= reaching(names, graph_)
    chosen = tuple(sorted(picked))
    if not chosen:
        return Empty("the change reaches no test")
    return Selection(chosen)


def _run_pytest(files: Sequence[str], root: Path) -> int:
    argv = [sys.executable, "-m", "pytest", "-m", "not llm", *files]
    return subprocess.run(argv, cwd=root, check=False).returncode


def _say(kind: str, reason: str) -> None:
    sys.stderr.write(f"affected-tests: {kind}: {' '.join(reason.split())}\n")


def main(
    argv: Sequence[str],
    *,
    root: Path | None = None,
    run_pytest: Callable[[Sequence[str], Path], int] = _run_pytest,
) -> int:
    top = root if root is not None else Path(__file__).resolve().parent.parent
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        _say("refused", "expected one base revision that does not start with '-'")
        return REFUSED
    try:
        paths = changed(argv[0], top)
        found = graph(read_sources(top))
    except GitFailure:
        _say("refused", "git")
        return REFUSED
    except (Undecided, OSError, ValueError) as error:
        _say("refused", str(error))
        return REFUSED
    result = select(paths, found, TABLE, IGNORE)
    if isinstance(result, Refusal):
        _say("refused", result.reason)
        return REFUSED
    if isinstance(result, Empty):
        _say("empty", result.reason)
        return EMPTY
    return run_pytest(result.files, top)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
