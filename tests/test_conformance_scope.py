"""The doubles' conformance to the ports is only as good as its inventory and its bite
(GH-227, ADR-0021's 2026-10-14 addendum).

The inventory: every protocol `ports.py` defines is in `COVERED` (assigned in
`tests/conformance.py`, where mypy compares it) or in `UNCOVERED` (named with its stand-ins).
The bite: a copy of `tests/fakes.py` and `tests/conformance.py` with one signature altered is
refused by the same mypy this repo runs, and the unaltered copy is not (ADR-0023)."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
import os
import re
import subprocess
import sys
from collections.abc import Callable, Collection, Iterable
from pathlib import Path
from types import ModuleType

from agentic.core.process.application.ports import Ports
from tests.conformance import COVERED, UNCOVERED, Uncovered
from tests.fakes import memory_ports

REPO = Path(__file__).resolve().parent.parent
PORTS = REPO / "agentic" / "core" / "process" / "application" / "ports.py"
CONFORMANCE = REPO / "tests" / "conformance.py"
PROBE_PACKAGE = "_conformance_probe"
TESTS_IMPORT = r"(?<![\w.])tests(?=[.\s,]|$)"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _is_protocol(cls: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id == "Protocol")
        or (isinstance(b, ast.Attribute) and b.attr == "Protocol")
        for b in cls.bases
    )


def protocols_of(tree: ast.Module) -> set[str]:
    return {c.name for c in _classes(tree) if _is_protocol(c) and not c.name.startswith("_")}


def _named_members(cls: ast.ClassDef) -> set[str]:
    """What `cls` declares: methods, class attributes (annotated or not), and the
    `self.<name>` any method assigns."""
    names: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        targets: list[ast.expr] = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                names.add(target.attr)
    return names


def _members_with_bases(cls: ast.ClassDef, in_file: list[ast.ClassDef]) -> set[str]:
    names = _named_members(cls)
    for base in cls.bases:
        if isinstance(base, ast.Name):
            for parent in in_file:
                if parent.name == base.id and parent is not cls:
                    names |= _members_with_bases(parent, in_file)
    return names


def protocol_members(tree: ast.Module, name: str) -> set[str]:
    """The non-underscore members a protocol declares, as the names a stand-in must have."""
    (cls,) = (c for c in _classes(tree) if c.name == name and _is_protocol(c))
    members: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            members.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)
    return {m for m in members if not m.startswith("_")}


def inventory_violations(
    protocols: Collection[str], covered: Collection[str], uncovered: Collection[str]
) -> list[str]:
    found = [f"{n} is in both COVERED and UNCOVERED" for n in sorted(set(covered) & set(uncovered))]
    found += [
        f"{n} is a protocol of ports.py and is accounted for nowhere"
        for n in sorted(set(protocols) - set(covered) - set(uncovered))
    ]
    found += [
        f"{n} is not a protocol of ports.py"
        for n in sorted((set(covered) | set(uncovered)) - set(protocols))
    ]
    return found


def stand_in_violations(stand_in: tuple[str, str], members: Iterable[str], root: Path) -> list[str]:
    """Why the named class does not stand in for a protocol that declares `members`."""
    path, name = stand_in
    classes = _classes(_tree(root / path))
    named = [c for c in classes if c.name == name]
    if not named:
        return [f"{path} has no class {name}"]
    return [
        f"{path}::{name} lacks {missing}"
        for cls in named
        for missing in sorted(set(members) - _members_with_bases(cls, classes))
    ]


def signature_violations(function: Callable[..., object], fields: Collection[str]) -> list[str]:
    parameters = list(inspect.signature(function).parameters.values())
    found: list[str] = []
    if {p.name for p in parameters} != set(fields):
        found.append("the parameters are not the fields")
    if not parameters or parameters[0].name != "runner":
        found.append("runner is not first")
    elif parameters[0].kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
        found.append("runner is not positional")
    found += [
        f"{p.name} is not keyword-only"
        for p in parameters[1:]
        if p.kind is not inspect.Parameter.KEYWORD_ONLY
    ]
    return found


def test_every_protocol_of_ports_is_covered_or_uncovered():
    protocols = protocols_of(_tree(PORTS))
    assert {"RoleRunner", "RunLock", "Launcher"} <= protocols
    assert inventory_violations(protocols, COVERED, UNCOVERED) == []


def test_the_inventory_refuses_a_protocol_nobody_accounts_for():
    assert inventory_violations({"A", "B"}, {"A"}, set()) == [
        "B is a protocol of ports.py and is accounted for nowhere"
    ]


def test_the_inventory_refuses_overlap_and_names_that_are_not_protocols():
    assert inventory_violations({"A"}, {"A"}, {"A", "Ghost"}) == [
        "A is in both COVERED and UNCOVERED",
        "Ghost is not a protocol of ports.py",
    ]


def test_every_stand_in_declares_the_members_of_its_protocol():
    ports_tree = _tree(PORTS)
    for name, entry in UNCOVERED.items():
        assert isinstance(entry, Uncovered) and entry.reason.strip(), name
        assert entry.stand_ins, name
        members = protocol_members(ports_tree, name)
        assert members, name
        for stand_in in entry.stand_ins:
            assert stand_in_violations(stand_in, members, REPO) == []


def test_a_stand_in_that_lacks_a_member_or_does_not_exist_is_refused(tmp_path):
    (tmp_path / "t.py").write_text(
        "class Base:\n    def read(self): ...\n\nclass Lock(Base):\n    held = 1\n"
        "    def __init__(self):\n        self.refusals = 0\n",
        encoding="utf-8",
    )
    assert stand_in_violations(("t.py", "Lock"), {"read", "held", "refusals"}, tmp_path) == []
    assert stand_in_violations(("t.py", "Lock"), {"read", "release"}, tmp_path) == [
        "t.py::Lock lacks release"
    ]
    assert stand_in_violations(("t.py", "Gone"), {"read"}, tmp_path) == ["t.py has no class Gone"]


def test_memory_ports_has_the_runner_then_one_keyword_per_other_field_of_ports():
    fields = [f.name for f in dataclasses.fields(Ports)]
    parameters = inspect.signature(memory_ports).parameters
    assert set(parameters) == set(fields)
    assert signature_violations(memory_ports, fields) == []
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in parameters.values())


def test_the_signature_check_refuses_kwargs_a_missing_field_and_a_positional_field():
    def kwargs_form(runner=None, **overrides): ...

    def positional_form(runner=None, documents=None): ...

    assert signature_violations(kwargs_form, ["runner", "documents"]) != []
    assert signature_violations(positional_form, ["runner", "documents"]) == [
        "documents is not keyword-only"
    ]
    assert signature_violations(positional_form, ["runner", "documents", "stack"]) != []


def _imports_of_tests(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """Every import that reaches `tests`, `tests.*` or is relative."""
    found: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level or module == "tests" or module.startswith("tests."):
                found.append(node)
        elif isinstance(node, ast.Import) and any(
            a.name == "tests" or a.name.startswith("tests.") for a in node.names
        ):
            found.append(node)
    return found


def test_conformance_imports_the_doubles_through_exactly_one_statement():
    (node,) = _imports_of_tests(_tree(CONFORMANCE))
    assert isinstance(node, ast.ImportFrom) and node.module == "tests.fakes" and node.level == 0


def test_a_second_or_a_relative_import_of_tests_is_seen():
    tree = ast.parse("from tests.fakes import A\nimport tests.x\nfrom . import y\n")
    assert len(_imports_of_tests(tree)) == 3


def _line_starts(source: bytes) -> list[int]:
    return [0] + [i + 1 for i, byte in enumerate(source) if byte == ord("\n")]


def _splice(source: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]
    return source


def _retarget_tests_imports(source: str) -> tuple[str, int]:
    """`source` with every import of `tests` or `tests.*` pointed at the probe package,
    found by `ast` and spliced by byte offsets, and how many nodes were rewritten."""
    raw = source.encode("utf-8")
    starts = _line_starts(raw)
    edits: list[tuple[int, int, bytes]] = []
    nodes = [n for n in _imports_of_tests(ast.parse(source)) if not getattr(n, "level", 0)]
    for node in nodes:
        assert node.end_lineno is not None and node.end_col_offset is not None
        begin = starts[node.lineno - 1] + node.col_offset
        end = starts[node.end_lineno - 1] + node.end_col_offset
        segment = raw[begin:end].decode("utf-8")
        count = 1 if isinstance(node, ast.ImportFrom) else 0
        edits.append(
            (begin, end, re.sub(TESTS_IMPORT, PROBE_PACKAGE, segment, count=count).encode())
        )
    return _splice(raw, edits).decode("utf-8"), len(nodes)


def _insert_drift(source: str) -> str:
    """Add a required `_drift: int` parameter after `self` of `MemoryStack.checks`."""
    (stack,) = (c for c in _classes(ast.parse(source)) if c.name == "MemoryStack")
    (method,) = (n for n in stack.body if isinstance(n, ast.FunctionDef) and n.name == "checks")
    first = method.args.args[0]
    assert first.arg == "self" and first.end_col_offset is not None
    raw = source.encode("utf-8")
    at = _line_starts(raw)[first.lineno - 1] + first.end_col_offset
    return _splice(raw, [(at, at, b", _drift: int")]).decode("utf-8")


def _mypy_on(tmp_path: Path, *, drift: bool) -> subprocess.CompletedProcess[str]:
    """Run this repo's mypy over a copy of the doubles and their conformance module, in a
    package not named `tests`, with a private cache. Nothing is written in the repo tree."""
    package = tmp_path / PROBE_PACKAGE
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("fakes.py", "conformance.py"):
        source = (REPO / "tests" / name).read_text(encoding="utf-8")
        text, rewritten = _retarget_tests_imports(source)
        assert rewritten == (1 if name == "conformance.py" else 0), name
        assert _imports_of_tests(ast.parse(text)) == [], f"{name} still reaches tests"
        (package / name).write_text(_insert_drift(text) if drift and name == "fakes.py" else text)
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(REPO / "pyproject.toml"),
        "--cache-dir",
        str(tmp_path / "cache"),
        str(package),
    ]
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    if "agentic" in run.stdout and "missing library stubs or py.typed" in run.stdout:
        # The editable install is not a typed package for mypy: point it at the source.
        env = {**os.environ, "MYPYPATH": str(REPO)}
        run = subprocess.run(
            command, cwd=tmp_path, capture_output=True, text=True, check=False, env=env
        )
    return run


def test_mypy_accepts_the_copied_doubles_unaltered(tmp_path):
    run = _mypy_on(tmp_path, drift=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Success: no issues found" in run.stdout


def test_mypy_refuses_a_double_whose_method_signature_drifted(tmp_path):
    run = _mypy_on(tmp_path, drift=True)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    errors = [
        line
        for line in run.stdout.splitlines()
        if line.startswith(f"{PROBE_PACKAGE}/conformance.py:")
        and ": error:" in line
        and ("[assignment]" in line or "[arg-type]" in line)
    ]
    assert errors, out
    assert "ProductStack" in out
    assert "_drift" in out


def test_the_copy_rewrites_a_tests_import_by_ast_and_leaves_no_reference():
    source = "from tests.fakes import (\n    A,\n)\nimport tests.x as t\n"
    text, count = _retarget_tests_imports(source)
    assert count == 2
    assert f"from {PROBE_PACKAGE}.fakes import (" in text
    assert f"import {PROBE_PACKAGE}.x as t" in text
    assert _imports_of_tests(ast.parse(text)) == []
    assert _retarget_tests_imports("import os\n") == ("import os\n", 0)


def _load_selector() -> ModuleType:
    path = REPO / "scripts" / "affected_tests.py"
    spec = importlib.util.spec_from_file_location("affected_tests_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_change_to_the_ports_the_doubles_or_the_assignments_selects_this_file():
    selector = _load_selector()
    graph = selector.graph(selector.read_sources(REPO))
    for path in (
        "agentic/core/process/application/ports.py",
        "tests/fakes.py",
        "tests/conformance.py",
    ):
        result = selector.select([path], graph, (), ())
        assert isinstance(result, selector.Selection), path
        assert "tests/test_conformance_scope.py" in result.files, path
