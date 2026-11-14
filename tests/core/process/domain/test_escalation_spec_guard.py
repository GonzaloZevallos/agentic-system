"""Every call that can raise a `questions` escalation passes the task's spec (GH-594, plan
invariant 3): `rules.escalation(..., spec=state.get("spec"))`, so a spec delivery's pause
about the spec's behaviour cannot be built without a `spec_cause`.

The guard reads `agentic/` as syntax trees. A call is checked when its function is the name
`escalation`, a `from .rules import escalation as <alias>` alias, or any `.escalation`
attribute; the exact name, so `ci_escalation` and `escalation_entry` are not. A call whose
first argument is a string literal other than "questions" is no spec question and passes;
every other one passes `spec=` with a value that is not the literal `None`, or its enclosing
function is named in `ALLOWED` with the reason it is exempt."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / "agentic"
APPLICATION = "agentic/core/process/application"

ALLOWED: dict[tuple[str, str], str] = {
    (f"{APPLICATION}/run_role.py", "escalation"): (
        "builds the pause of an Escalating exception, which is only failure, cap or estimate"
    ),
    (f"{APPLICATION}/run_role.py", "run_role"): (
        "calls the module's own `escalation(exc)` wrapper above, not rules.escalation"
    ),
    (f"{APPLICATION}/landing_fix.py", "_escalate"): (
        "delivery-mechanics questions (landing, CI, ADR collisions), not doubts about the "
        "spec's behaviour"
    ),
}
REASON_MAX = 240

Call = tuple[str, int]  # the enclosing function and the line


def _aliases(tree: ast.AST) -> set[str]:
    return {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "rules"
        for alias in node.names
        if alias.name == "escalation" and alias.asname
    }


def _is_escalation(call: ast.Call, names: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in names
    return isinstance(func, ast.Attribute) and func.attr == "escalation"


def _passes_spec(call: ast.Call) -> bool:
    first = call.args[0] if call.args else None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        if first.value != "questions":
            return True
    for keyword in call.keywords:
        if keyword.arg == "spec":
            value = keyword.value
            return not (isinstance(value, ast.Constant) and value.value is None)
    return False


class _Walker(ast.NodeVisitor):
    def __init__(self, names: set[str]) -> None:
        self.names = names
        self.scope: list[str] = []
        self.checked: list[Call] = []
        self.unplugged: list[Call] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        if _is_escalation(node, self.names):
            where = (self.scope[-1] if self.scope else "<module>", node.lineno)
            self.checked.append(where)
            if not _passes_spec(node):
                self.unplugged.append(where)
        self.generic_visit(node)


def unplugged_calls(source: str) -> tuple[list[Call], list[Call]]:
    """The escalation calls of `source` and, among them, the ones that need `spec=` and lack it."""
    tree = ast.parse(source)
    walker = _Walker({"escalation"} | _aliases(tree))
    walker.visit(tree)
    return walker.checked, walker.unplugged


def stale_entries(used: set[tuple[str, str]]) -> list[str]:
    """The `ALLOWED` entries that exempt no call, or carry an empty or over-long reason."""
    found: list[str] = []
    for entry, reason in ALLOWED.items():
        if entry not in used:
            found.append(f"{entry}: allowed but nothing in it needs the exemption")
        if not reason.strip() or len(reason) > REASON_MAX:
            found.append(f"{entry}: the reason is empty or over {REASON_MAX} characters")
    return found


def problems(sources: dict[str, str]) -> list[str]:
    """One line per call missing `spec=` outside `ALLOWED`, then the stale entries."""
    found: list[str] = []
    used: set[tuple[str, str]] = set()
    for path, source in sorted(sources.items()):
        _, missing = unplugged_calls(source)
        for function, line in missing:
            if (path, function) in ALLOWED:
                used.add((path, function))
            else:
                found.append(f"{path}:{line} ({function}): escalation call without spec=")
    return found + stale_entries(used)


def repo_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
        if "tests" not in path.relative_to(PACKAGE).parts
    }


def test_every_questions_escalation_of_the_repo_passes_spec_or_is_named():
    assert problems(repo_sources()) == []


def test_a_wrapper_forwarding_the_class_without_spec_is_flagged():
    source = "def wrap(cls, reason):\n    return rules.escalation(cls, reason)\n"
    assert unplugged_calls(source)[1] == [("wrap", 2)]


def test_an_alias_of_the_rule_calling_questions_is_flagged():
    source = (
        "from .domain.rules import escalation as esc\n"
        "def ask():\n    return esc('questions', 'why')\n"
    )
    assert unplugged_calls(source)[1] == [("ask", 3)]


def test_kwargs_do_not_stand_for_spec():
    source = "def ask(**kw):\n    return rules.escalation(cls, 'r', **kw)\n"
    assert unplugged_calls(source)[1] == [("ask", 2)]


def test_a_literal_none_spec_is_flagged():
    source = "def ask():\n    return rules.escalation('questions', 'r', spec=None)\n"
    assert unplugged_calls(source)[1] == [("ask", 2)]


def test_calls_that_are_no_spec_question_pass():
    source = (
        "def ok(state):\n"
        "    rules.escalation('failure', 'r')\n"
        "    rules.escalation('questions', 'r', spec=state.get('spec'))\n"
        "    ci_escalation('questions', 'r')\n"
        "    escalation_entry('questions')\n"
    )
    checked, missing = unplugged_calls(source)
    assert len(checked) == 2
    assert missing == []


def test_an_entry_nothing_needs_is_reported_as_stale():
    assert any("allowed but nothing" in line for line in problems({}))


def test_a_call_outside_the_allow_list_is_reported_with_its_place():
    source = "def wrap(cls):\n    return rules.escalation(cls, 'r')\n"
    assert problems({"agentic/x.py": source})[0] == (
        "agentic/x.py:2 (wrap): escalation call without spec="
    )
