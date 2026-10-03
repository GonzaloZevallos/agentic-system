"""Issue #137's own invariant: every escalation composes through `rules.logged`, and
nothing merges a dict that already carries a `pause_log` with the `|` operator --
`dict.__or__` picks one side's value for a shared key, never appends, which is exactly
wrong for a list `pause_log`'s own reducer (`operator.add`) expects grown one entry at a
time. Checked structurally (AST), never by grepping the word `pause_log`: a rename or a
reformat of the source must not make either half of this guard blind.

Two halves, two checkers: `pause_log_bitor_violations` (nothing merges an operand that
already carries `pause_log` with `|`/`|=`) and `unlogged_escalation_violations` (every
`rules.escalation`/`rules.unmergeable` result reaches a patch only by way of `logged`,
directly or carried there by a simple name)."""

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
_PRODUCER_ATTRS = {"escalation", "unmergeable"}


def _callee_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_rules_producer_call(node: ast.AST) -> bool:
    """`rules.escalation(...)` / `rules.unmergeable(...)` exactly -- not `run_role.py`'s
    own local `escalation` wrapper, which `_local_producers` names separately."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _PRODUCER_ATTRS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "rules"
    )


def _local_producers(tree: ast.AST) -> set[str]:
    """Every function this module defines whose own body calls `logged`
    (`run_role.escalation`'s own shape): a local wrapper that already carries
    `pause_log`, the same as `rules.logged` itself, so `pause_log_bitor_violations`
    must treat a bare call to it the same way."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(_callee_name(n) == _LOGGED for n in ast.walk(node) if isinstance(n, ast.Call))
    }


def _carries_pause_log(node: ast.expr, producers: set[str]) -> bool:
    """Whether `node`, read structurally alone, is already known to carry a
    `"pause_log"` key: a dict literal with that literal key, or a call to one of
    `producers` (`logged` itself, or a local wrapper `_local_producers` found)."""
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "pause_log" for k in node.keys)
    name = _callee_name(node)
    return name is not None and name in producers


def pause_log_bitor_violations(tree: ast.AST) -> list[ast.AST]:
    """Every `|` or `|=` with an operand `_carries_pause_log` already names: the one
    composition the invariant forbids. `logged`/a local wrapper being the *outermost*
    call of an expression is never flagged -- only an operand of `|`/`|=` is, since
    that is the one shape that silently drops a `pause_log` entry instead of appending
    it."""
    producers = _local_producers(tree) | {_LOGGED}
    violations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            if _carries_pause_log(node.left, producers) or _carries_pause_log(
                node.right, producers
            ):
                violations.append(node)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
            if _carries_pause_log(node.value, producers):
                violations.append(node)
    return violations


def _contains(outer: ast.AST, inner: ast.AST) -> bool:
    return any(n is inner for n in ast.walk(outer))


def _logged_calls(func: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(func) if isinstance(n, ast.Call) and _callee_name(n) == _LOGGED]


def _producer_calls(func: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(func) if _is_rules_producer_call(n)]


def _names_fed_to(calls: list[ast.Call]) -> set[str]:
    """Every simple name reaching one of `calls` as an argument, anywhere in its own
    subtree: `rules.logged(patch, exc.failures)` feeds it `patch` (and `exc`, harmless
    to also collect -- a producer's own carrier is never named `exc`)."""
    names: set[str] = set()
    for call in calls:
        for arg in (*call.args, *(kw.value for kw in call.keywords)):
            names.update(n.id for n in ast.walk(arg) if isinstance(n, ast.Name))
    return names


def _carrier_name(func: ast.AST, producer: ast.Call) -> str | None:
    """The simple name `producer`'s own dict is assigned or merged into, in the
    nearest `Assign`/`AnnAssign`/`AugAssign` statement that contains it --
    `patch |= rules.escalation(...)` or `patch: TaskState = {..., **rules.escalation(...)}`
    both name `"patch"`."""
    for node in ast.walk(func):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.AugAssign):
            target, value = node.target, node.value
        if value is not None and isinstance(target, ast.Name) and _contains(value, producer):
            return target.id
    return None


def unlogged_escalation_violations(tree: ast.AST) -> list[ast.AST]:
    """Every `rules.escalation(...)`/`rules.unmergeable(...)` call whose result never
    reaches a `logged(...)` call in the same function -- neither nested inside it
    directly (`rules.logged(rules.unmergeable(...))`) nor carried there by a simple
    name (`patch |= rules.escalation(...)` followed by `rules.logged(patch)`). The one
    shape this invariant forbids: an escalation the human sees that `pause_log` never
    recorded, so `rules.auto_resumable` reads a stale, emptier epoch than what actually
    happened."""
    violations: list[ast.AST] = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        logged_calls = _logged_calls(func)
        fed_names = _names_fed_to(logged_calls)
        for producer in _producer_calls(func):
            if any(_contains(call, producer) for call in logged_calls):
                continue
            carrier = _carrier_name(func, producer)
            if carrier is not None and carrier in fed_names:
                continue
            violations.append(producer)
    return violations


def test_rejects_a_dict_literal_with_pause_log_on_the_right_of_bitor() -> None:
    tree = ast.parse("patch = patch | {'pause_log': [], 'status': 'open'}\n")
    assert len(pause_log_bitor_violations(tree)) == 1


def test_rejects_a_call_to_logged_on_the_right_of_bitor() -> None:
    tree = ast.parse("return None, patch | rules.logged(patch, exc.failures)\n")
    assert len(pause_log_bitor_violations(tree)) == 1


def test_rejects_a_call_to_a_local_wrapper_of_logged_on_the_right_of_bitor() -> None:
    # `run_role.py`'s own shape: `escalation` is defined in the same module and its
    # body calls `logged`, so a bare `patch | escalation(exc)` elsewhere in the file
    # must be caught too, not just a direct `rules.logged(...)` call.
    tree = ast.parse(
        "def escalation(exc):\n"
        "    return logged({'escalated': str(exc)})\n"
        "\n"
        "def run_role():\n"
        "    return None, patch | escalation(exc)\n"
    )
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


def test_rejects_an_escalation_never_passed_to_logged_at_all() -> None:
    # The exact shape item 4 asks this guard to catch: a new call site that escalates
    # and never logs it.
    tree = ast.parse(
        "async def classify(state, cfg, ports):\n"
        "    patch = {}\n"
        "    patch |= rules.escalation('classification', 'why')\n"
        "    patch['status'] = WAITING_ON_HUMAN\n"
        "    return patch\n"
    )
    assert len(unlogged_escalation_violations(tree)) == 1


def test_rejects_unmergeable_never_passed_to_logged() -> None:
    tree = ast.parse(
        "def develop(state, cfg, ports):\n"
        "    patch = rules.unmergeable(subtask_id, dep, reason)\n"
        "    return finish(state, patch, ports)\n"
    )
    assert len(unlogged_escalation_violations(tree)) == 1


def test_accepts_escalation_carried_by_name_into_logged() -> None:
    # `classify.py`'s/`interview.py`'s/`review.py`'s/`validate_prd.py`'s own shape:
    # `|=` first, `logged(patch)` later, same function, same name.
    tree = ast.parse(
        "async def classify(state, cfg, ports):\n"
        "    patch = {}\n"
        "    patch |= rules.escalation('classification', 'why')\n"
        "    patch = rules.logged(patch)\n"
        "    patch['status'] = WAITING_ON_HUMAN\n"
        "    return patch\n"
    )
    assert unlogged_escalation_violations(tree) == []


def test_accepts_unmergeable_nested_directly_inside_logged() -> None:
    # `develop.py`'s own two shapes: the producer call is itself an argument of
    # `rules.logged(...)`, nested in a dict literal or not.
    tree = ast.parse(
        "def develop(state, cfg, ports):\n"
        "    patch = rules.logged(rules.unmergeable(subtask_id, dep, reason))\n"
        "    other = rules.logged({**patch, **rules.unmergeable(subtask_id, dep, reason)})\n"
        "    return finish(state, other, ports)\n"
    )
    assert unlogged_escalation_violations(tree) == []


def test_accepts_the_annotated_assignment_run_role_py_itself_uses() -> None:
    # `run_role.py`'s own exact shape: `patch: TaskState = {..., **rules.escalation(...)}`
    # (an `AnnAssign`, not a plain `Assign`), then `logged(patch, exc.failures)`.
    tree = ast.parse(
        "def escalation(exc):\n"
        "    patch: TaskState = {\n"
        "        'status': WAITING_ON_HUMAN,\n"
        "        **rules.escalation(cls, str(exc)),\n"
        "    }\n"
        "    return logged(patch, exc.failures)\n"
    )
    assert unlogged_escalation_violations(tree) == []


def test_every_application_module_composes_pause_log_only_through_logged() -> None:
    for path in sorted(APPLICATION.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        assert pause_log_bitor_violations(tree) == [], path.name
        assert unlogged_escalation_violations(tree) == [], path.name
