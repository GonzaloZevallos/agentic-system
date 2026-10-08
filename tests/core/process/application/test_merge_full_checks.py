"""ADR-0046: `agentic merge` never runs a partial suite. A stack that declares affected-only
forms and fallback globs gets, through `merge.py`'s real check path, exactly its full
commands, with no base in them and no `mode` on any row; and `merge.py` never hands a plan
to `CheckoutPresubmit`, which this reads out of the source as well."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable

import pytest

from agentic.core.process.application import merge
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, memory_ports

HEAD = "a" * 40
MASTER = "b" * 40
URL = "https://github.com/o/r/pull/7"
CHECKS = {"tests": "pytest -q", "e2e": "npx playwright test"}
AFFECTED = {"tests": "pytest -q --since {base}", "e2e": "npx playwright test --only-changed={base}"}


def _merged_with(stack: MemoryStack):
    ports = memory_ports(tracker=MemoryTracker(), stack=stack, commands=MemoryCommands())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.existing_branches["agentic/GH-7"] = ("tip", "taskbase1")
    ports.workspace.diff_paths = ("src/app.css",)
    ports.tracker.prs[URL] = PullRequest(
        state="OPEN", created_at="2026-10-05T00:00:00Z", merged_at=None, commits=(), head_oid=HEAD
    )
    ports.documents.save(
        {
            "id": "GH-7",
            "status": "ready_for_review",
            "pull_request": URL,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
        }
    )
    logged: list[str] = []
    cfg = Configuration(product_dir=ports.workspace.ROOT)
    merge.merge_task(
        "GH-7", 7, cfg, ports, Upstream("origin/master", MASTER, None), frozenset(), logged.append
    )
    return ports


def test_merge_runs_exactly_the_full_commands_whatever_the_stack_declares():
    stack = MemoryStack(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    ports = _merged_with(stack)
    ran = [command for _, command in ports.commands.ran]
    assert ran == list(CHECKS.values())
    assert not any("{base}" in c or "taskbase1" in c or "--since" in c for c in ran)
    assert ports.documents.load("GH-7")["merge_checks"]["passed"] is True


def test_merge_never_reads_the_affected_keys():
    class Watching(MemoryStack):
        def __init__(self, **declared) -> None:
            super().__init__(**declared)
            self.asked: list[str] = []

        def affected_checks(self, revision: str) -> dict[str, str]:
            self.asked.append("affected_checks")
            return super().affected_checks(revision)

        def affected_fallback(self, revision: str) -> tuple[str, ...]:
            self.asked.append("affected_fallback")
            return super().affected_fallback(revision)

    stack = Watching(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    _merged_with(stack)
    assert stack.asked == []


def test_a_merge_row_carries_no_mode(monkeypatch):
    stack = MemoryStack(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    seen: list[dict] = []
    original = merge.CheckoutPresubmit.__call__

    def spy(self):
        outcome = original(self)
        seen.extend(self.results or [])
        return outcome

    monkeypatch.setattr(merge.CheckoutPresubmit, "__call__", spy)
    _merged_with(stack)
    assert [row["name"] for row in seen] == list(CHECKS)
    assert all("mode" not in row for row in seen)


GATE = "CheckoutPresubmit"
DYNAMIC_BUILTINS = frozenset(
    {"getattr", "setattr", "vars", "globals", "locals", "__import__", "eval", "exec", "compile"}
)
PLAN_NAMES = frozenset({"AffectedPlan", "affected_checks", "affected_fallback"})


def _type_expression_names(node: ast.expr) -> list[ast.Name]:
    """The `Name`s reached from an annotation by descending only through the shapes of a
    type expression; a `Call` (or anything else) stops the walk and exempts nothing below."""
    if isinstance(node, ast.Name):
        return [node]
    if isinstance(node, ast.Attribute):
        return _type_expression_names(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _type_expression_names(node.left) + _type_expression_names(node.right)
    if isinstance(node, ast.Subscript):
        return _type_expression_names(node.value) + _type_expression_names(node.slice)
    if isinstance(node, ast.Tuple):
        return [name for element in node.elts for name in _type_expression_names(element)]
    return []


def _annotations(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    if isinstance(node, ast.arg) and node.annotation is not None:
        return [node.annotation]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
        return [node.returns]
    return []


def _conforming_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == GATE
        and isinstance(node.func.ctx, ast.Load)
        and not any(isinstance(a, ast.Starred) for a in node.args)
        and all(k.arg is not None and k.arg != "affected" for k in node.keywords)
    )


def _presubmit_offences(source: str) -> list[tuple[str, int]]:
    """Every offence in `source` against ADR-0046's rule that the merge never hands
    `CheckoutPresubmit` a plan, as `(rule, line)` pairs. It fails closed: the gate class may
    appear only as `from .presubmit import CheckoutPresubmit`, as a Load name inside an
    annotation's type expression, or as the callee of a direct bare-name call without `*`,
    `**` or `affected=`; any `affected` attribute, string or keyword, the plan names, and the
    dynamic lookups are offences too.

    Its limit: it reads one module. A plan or a gate subclass built in another module and
    passed into this one is out of reach; the runtime tests in this file cover that case."""
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        for annotation in _annotations(node):
            allowed.update(
                id(n)
                for n in _type_expression_names(annotation)
                if n.id == GATE and isinstance(n.ctx, ast.Load)
            )
        if isinstance(node, ast.Call) and _conforming_call(node):
            allowed.add(id(node.func))

    found: list[tuple[str, int]] = []

    def flag(rule: str, node: ast.AST) -> None:
        found.append((rule, getattr(node, "lineno", 0)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id == GATE and id(node) not in allowed:
                flag("name", node)
            if node.id in DYNAMIC_BUILTINS:
                flag("dynamic", node)
            if node.id in PLAN_NAMES:
                flag("plan-name", node)
        elif isinstance(node, ast.Attribute):
            if node.attr == GATE:
                flag("attribute", node)
            if node.attr == "affected":
                flag("affected-attribute", node)
            if node.attr in PLAN_NAMES:
                flag("plan-name", node)
        elif isinstance(node, ast.Constant):
            if node.value == GATE:
                flag("constant", node)
            if node.value == "affected":
                flag("affected-constant", node)
            if isinstance(node.value, str) and node.value in PLAN_NAMES:
                flag("plan-name", node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == GATE:
                flag("name", node)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == GATE:
                if any(isinstance(a, ast.Starred) for a in node.args) or any(
                    k.arg is None for k in node.keywords
                ):
                    flag("unpack", node)
            for keyword in node.keywords:
                if keyword.arg == "affected":
                    flag("affected-keyword", keyword)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                _flag_alias(alias, node, flag)
                if alias.name == "importlib" or alias.name.startswith("importlib."):
                    flag("dynamic", node)
                if alias.name == GATE:
                    flag("import-origin", node)
        elif isinstance(node, ast.ImportFrom):
            from_importlib = node.module == "importlib" or (node.module or "").startswith(
                "importlib."
            )
            if from_importlib:
                flag("dynamic", node)
            for alias in node.names:
                _flag_alias(alias, node, flag)
                pinned = node.module == "presubmit" and node.level == 1
                if alias.name == GATE and not pinned:
                    flag("import-origin", node)
    return sorted(found, key=lambda pair: pair[1])


def _flag_alias(alias: ast.alias, node: ast.stmt, flag: Callable[[str, ast.AST], None]) -> None:
    if alias.name == GATE and alias.asname is not None:
        flag("alias", node)
    if alias.name in PLAN_NAMES or alias.asname in PLAN_NAMES:
        flag("plan-name", node)


def _rules(source: str) -> frozenset[str]:
    return frozenset(rule for rule, _ in _presubmit_offences(source))


MERGE_SHAPE = """
from .presubmit import CheckoutPresubmit

def run(ports, path, checks, cfg, base):
    gate: CheckoutPresubmit | None = None
    gate = CheckoutPresubmit(ports, path, "", checks, cfg.check_timeout_s{extra})
"""
WITNESS = ",\n        witness=ports.stack.witness(base)"
PARALLEL = ",\n        parallel=ports.stack.parallel_checks(base)"
GATE_IMPORT = "from .presubmit import CheckoutPresubmit\n"

ACCEPTED = {
    "the shape of merge.py": MERGE_SHAPE.format(extra=""),
    "with witness": MERGE_SHAPE.format(extra=WITNESS),
    "with parallel": MERGE_SHAPE.format(extra=PARALLEL),
    "with witness and parallel": MERGE_SHAPE.format(extra=WITNESS + PARALLEL),
    "a method call named compile on a module is not the builtin": "import re\nre.compile('x')\n",
}


def test_the_real_merge_source_has_no_offence_and_builds_its_gate_by_a_bare_name_call():
    source = inspect.getsource(merge)
    offences = _presubmit_offences(source)
    assert offences == [], f"merge.py breaks the ADR-0046 guard: {offences}"
    conforming = [
        n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _conforming_call(n)
    ]
    assert conforming, "merge.py is expected to build its gate through CheckoutPresubmit"


@pytest.mark.parametrize("source", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_the_guard_accepts_the_shapes_merge_may_use(source: str):
    assert _presubmit_offences(source) == []


REJECTED: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "bare call with affected=": (
        GATE_IMPORT + "CheckoutPresubmit(ports, path, 'x', checks, 1.0, affected=plan)",
        frozenset({"name", "affected-keyword"}),
        frozenset({"unpack"}),
    ),
    "attribute call with affected=": (
        "presubmit.CheckoutPresubmit(ports, path, 'x', checks, 1.0, affected=plan)",
        frozenset({"attribute", "affected-keyword"}),
        frozenset(),
    ),
    "attribute call with no keywords": (
        "presubmit.CheckoutPresubmit(ports, path, 'x', checks, 1.0)",
        frozenset({"attribute"}),
        frozenset({"affected-keyword", "affected-attribute", "unpack"}),
    ),
    "**opts": (
        GATE_IMPORT + "CheckoutPresubmit(ports, path, 'x', checks, 1.0, **opts)",
        frozenset({"unpack", "name"}),
        frozenset({"affected-keyword"}),
    ),
    "*args": (
        GATE_IMPORT + "CheckoutPresubmit(*args)",
        frozenset({"unpack", "name"}),
        frozenset({"affected-keyword"}),
    ),
    "aliased import": (
        "from .presubmit import CheckoutPresubmit as Gate",
        frozenset({"alias"}),
        frozenset({"import-origin"}),
    ),
    "import from another module": (
        "from .develop import CheckoutPresubmit",
        frozenset({"import-origin"}),
        frozenset({"alias"}),
    ),
    "import of the gate as a module": (
        "import CheckoutPresubmit",
        frozenset({"import-origin"}),
        frozenset(),
    ),
    "partial of the class": (
        "partial(CheckoutPresubmit, affected=plan)",
        frozenset({"name", "affected-keyword"}),
        frozenset(),
    ),
    "assigning the class": ("g = CheckoutPresubmit", frozenset({"name"}), frozenset()),
    "rebinding the name": ("CheckoutPresubmit = Other", frozenset({"name"}), frozenset()),
    "deleting the name": ("del CheckoutPresubmit", frozenset({"name"}), frozenset()),
    "defining a class of that name": (
        "class CheckoutPresubmit: ...",
        frozenset({"name"}),
        frozenset(),
    ),
    "a call inside an annotation": (
        "x: CheckoutPresubmit(ports, p, '', c, 1.0, affected=plan)",
        frozenset({"name", "affected-keyword"}),
        frozenset(),
    ),
    "the class named in a string": (
        "name = 'CheckoutPresubmit'",
        frozenset({"constant"}),
        frozenset(),
    ),
    "getattr of the class": (
        "getattr(presubmit, 'CheckoutPresubmit')(ports, p, '', c, 1.0)",
        frozenset({"dynamic", "constant"}),
        frozenset(),
    ),
    "assigning an affected attribute": (
        "gate.affected = plan",
        frozenset({"affected-attribute"}),
        frozenset({"affected-constant", "affected-keyword"}),
    ),
    "setattr of affected": (
        "setattr(gate, 'affected', plan)",
        frozenset({"dynamic", "affected-constant"}),
        frozenset({"affected-attribute"}),
    ),
    "vars of the gate": (
        "vars(gate)['affected'] = plan",
        frozenset({"dynamic", "affected-constant"}),
        frozenset(),
    ),
    "affected= on any call": (
        "something(affected=plan)",
        frozenset({"affected-keyword"}),
        frozenset({"name", "unpack"}),
    ),
    "a getattr call": ("getattr(gate, name)", frozenset({"dynamic"}), frozenset()),
    "a setattr call": ("setattr(gate, name, 1)", frozenset({"dynamic"}), frozenset()),
    "vars": ("vars(gate)", frozenset({"dynamic"}), frozenset()),
    "globals": ("globals()", frozenset({"dynamic"}), frozenset()),
    "locals": ("locals()", frozenset({"dynamic"}), frozenset()),
    "import importlib": ("import importlib", frozenset({"dynamic"}), frozenset()),
    "from importlib import": (
        "from importlib import import_module",
        frozenset({"dynamic"}),
        frozenset(),
    ),
    "__import__": ("__import__('x')", frozenset({"dynamic"}), frozenset()),
    "eval": ("eval('x')", frozenset({"dynamic"}), frozenset()),
    "exec": ("exec('x')", frozenset({"dynamic"}), frozenset()),
    "compile": ("compile('x', 'f', 'exec')", frozenset({"dynamic"}), frozenset()),
    "affected_checks": (
        "ports.stack.affected_checks(base)",
        frozenset({"plan-name"}),
        frozenset({"affected-attribute"}),
    ),
    "affected_fallback": (
        "ports.stack.affected_fallback(base)",
        frozenset({"plan-name"}),
        frozenset({"affected-attribute"}),
    ),
    "importing AffectedPlan": (
        "from .presubmit import AffectedPlan",
        frozenset({"plan-name"}),
        frozenset(),
    ),
    "importing a plan name under an alias": (
        "from .x import y as AffectedPlan",
        frozenset({"plan-name"}),
        frozenset(),
    ),
    "AffectedPlan call": ("AffectedPlan(checks, ())", frozenset({"plan-name"}), frozenset()),
    "a plan name in a string": ("n = 'affected_fallback'", frozenset({"plan-name"}), frozenset()),
}


@pytest.mark.parametrize(("source", "expected", "absent"), REJECTED.values(), ids=REJECTED.keys())
def test_the_guard_rejects_by_the_rules_each_row_targets(
    source: str, expected: frozenset[str], absent: frozenset[str]
):
    found = _rules(source)
    # `unpack` is deliberate: `affected` is keyword-only, so only `**` could smuggle a plan
    # in, and the guard does not trace dicts (GH-223); any `*` or `**` in a gate call is
    # refused rather than followed.
    assert expected <= found, f"expected {sorted(expected)} to fire, found {sorted(found)}"
    assert not absent & found, f"{sorted(absent & found)} must not fire, found {sorted(found)}"


def test_every_rule_key_has_a_rejecting_row():
    keys = {
        "name", "attribute", "alias", "import-origin", "constant", "affected-constant",
        "unpack", "affected-keyword", "affected-attribute", "dynamic", "plan-name",
    }  # fmt: skip
    targeted = frozenset().union(*(expected for _, expected, _ in REJECTED.values()))
    assert keys == targeted


def _insert_after_parallel(line: str) -> tuple[str, str, int]:
    source = inspect.getsource(merge)
    anchor = "parallel=ports.stack.parallel_checks(base),"
    lines = source.splitlines()
    at = [i for i, text in enumerate(lines) if text.strip() == anchor]
    assert len(at) == 1 and source.count(anchor) == 1, f"anchor {anchor!r} must appear once"
    indent = lines[at[0]][: len(lines[at[0]]) - len(lines[at[0]].lstrip())]
    lines.insert(at[0] + 1, indent + line)
    mutated = "\n".join(lines) + "\n"
    assert mutated != source
    ast.parse(mutated)
    return source, mutated, at[0] + 2


def test_a_plan_inserted_into_the_real_gate_call_is_an_affected_keyword_offence():
    _, mutated, line = _insert_after_parallel("affected=plan,")
    assert ("affected-keyword", line) in _presubmit_offences(mutated)


def test_a_double_star_inserted_into_the_real_gate_call_is_an_unpack_offence():
    _, mutated, _ = _insert_after_parallel("**opts,")
    assert "unpack" in _rules(mutated)


def test_affected_is_keyword_only_so_double_star_is_the_only_route_for_a_plan():
    kind = inspect.signature(merge.CheckoutPresubmit).parameters["affected"].kind
    assert kind is inspect.Parameter.KEYWORD_ONLY, (
        "`affected` must stay keyword-only: that is what makes `**` the only way to pass a "
        "plan without writing `affected=`, which the guard refuses"
    )
