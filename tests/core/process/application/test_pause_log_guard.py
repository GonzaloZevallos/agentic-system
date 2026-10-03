"""Issue #137's own invariant: every escalation composes through `rules.logged`, and
nothing merges a dict that already carries a `pause_log` with the `|` operator --
`dict.__or__` picks one side's value for a shared key, never appends, which is exactly
wrong for a list `pause_log`'s own reducer (`operator.add`) expects grown one entry at a
time. Checked structurally (AST), never by grepping the word `pause_log`: a rename or a
reformat of the source must not make this guard blind.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agentic

APPLICATION = Path(agentic.__file__).parent / "core" / "process" / "application"

# The one function this invariant lets write a `"pause_log"` key at all
# (`rules.logged`'s own docstring): called either as `rules.logged(...)` or bare
# `logged(...)` after a `from ... import logged`, so the match is on the last
# attribute segment, not on the whole dotted path.
_LOGGED = "logged"


def _callee_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _carries_pause_log(node: ast.expr) -> bool:
    """Whether `node`, read structurally alone, is already known to carry a
    `"pause_log"` key: a dict literal with that literal key, or a call to `logged`
    (`rules.logged`'s one producer)."""
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "pause_log" for k in node.keys)
    return _callee_name(node) == _LOGGED


def pause_log_bitor_violations(tree: ast.AST) -> list[ast.AST]:
    """Every `|` or `|=` with an operand `_carries_pause_log` already names: the one
    composition the invariant forbids. `rules.logged` being the *outermost* call of an
    expression is never flagged -- only an operand of `|`/`|=` is, since that is the
    one shape that silently drops a `pause_log` entry instead of appending it."""
    violations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            if _carries_pause_log(node.left) or _carries_pause_log(node.right):
                violations.append(node)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
            if _carries_pause_log(node.value):
                violations.append(node)
    return violations


def test_rejects_a_dict_literal_with_pause_log_on_the_right_of_bitor() -> None:
    tree = ast.parse("patch = patch | {'pause_log': [], 'status': 'open'}\n")
    assert len(pause_log_bitor_violations(tree)) == 1


def test_rejects_a_call_to_logged_on_the_right_of_bitor() -> None:
    tree = ast.parse("return None, patch | escalation(exc)\n", filename="<synthetic>")
    # A bare name with no call to `logged` inside it is not itself a producer this
    # guard can see without executing it -- the point of this case is the next one,
    # `rules.logged` directly, which it must catch.
    assert pause_log_bitor_violations(tree) == []
    tree = ast.parse("return None, patch | rules.logged(patch, exc.failures)\n")
    assert len(pause_log_bitor_violations(tree)) == 1


def test_rejects_an_augmented_bitor_onto_logged() -> None:
    tree = ast.parse("patch |= rules.logged(patch)\n")
    assert len(pause_log_bitor_violations(tree)) == 1


def test_accepts_bitor_with_rules_escalation_which_carries_no_pause_log() -> None:
    # `rules.escalation`/`rules.unmergeable` never write `pause_log` themselves
    # (the invariant keeps them undiffed): composing with `|=` before `rules.logged`
    # runs, as `classify.py` and the other four sites do, is not this violation.
    tree = ast.parse("patch |= rules.escalation('cap', 'why')\n")
    assert pause_log_bitor_violations(tree) == []


def test_accepts_dict_merge_syntax_instead_of_bitor() -> None:
    # `{**patch, **escalation(exc)}` is the fix `run_role.py` actually uses: no `|`
    # at all, so there is nothing for this guard to flag, whatever either side carries.
    tree = ast.parse("return None, {**patch, **escalation(exc)}\n")
    assert pause_log_bitor_violations(tree) == []


def test_every_application_module_composes_pause_log_only_through_logged() -> None:
    offenders = {
        path.name: pause_log_bitor_violations(ast.parse(path.read_text(), filename=str(path)))
        for path in sorted(APPLICATION.glob("*.py"))
    }
    assert {name: v for name, v in offenders.items() if v} == {}
