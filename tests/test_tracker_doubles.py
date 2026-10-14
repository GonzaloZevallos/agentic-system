"""Static guard (GH-214): every function in `tests/` installed as a Tracker's
`pull_request` or `merge` parses its pull request reference first, through the real
adapter's `pull_request_number` or through the inherited method of the same name, so a bare
issue number fails in every double the way it fails in `MemoryTracker`.

The verdict, `violations(source, path)`, is pure: source text in, a list of violations out.
The only I/O is the walk over `tests/**/*.py` (`tests/llm` included) that feeds it. Files
are parsed, never run. A value that cannot be resolved to a local `def` is a violation,
never a skip.

Limits, on purpose: a method whose first parameter is not named `reference` is invisible
(`Workspace.merge(self, path, branch)` is not a Tracker's), and so is `setattr` with a
non-literal name, or a class-body `pull_request = ...` assignment.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent
VERBS = ("pull_request", "merge")
PARSE = "pull_request_number"

Function = ast.FunctionDef | ast.AsyncFunctionDef


class Site(NamedTuple):
    """A function installed as a Tracker verb: `node` is the `def`, `name` the verb it is
    installed as, `klass` the class it is a method of (None for a closure), `problem` a
    reason it cannot be judged (an unresolvable value), in which case `node` is None."""

    qualname: str
    line: int
    name: str
    node: Function | None
    klass: ast.ClassDef | None
    problem: str | None


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node of `scope`'s own body, without entering a nested function or class."""
    stack = list(reversed(getattr(scope, "body", [])))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _resolve(name: str, scope: ast.AST, before: int) -> Function | None:
    """The last `def name` of `scope`'s own body that precedes line `before`."""
    found: Function | None = None
    for node in _scope_nodes(scope):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            if node.lineno < before and (found is None or node.lineno > found.lineno):
                found = node
    return found


def _installed(call: ast.Call) -> tuple[str, ast.expr] | None:
    """`(verb, value)` of `setattr(obj, "verb", value)` / `x.setattr(obj, "verb", value)`."""
    func = call.func
    named = (isinstance(func, ast.Name) and func.id == "setattr") or (
        isinstance(func, ast.Attribute) and func.attr == "setattr"
    )
    if not named or len(call.args) != 3:
        return None
    verb = call.args[1]
    if isinstance(verb, ast.Constant) and verb.value in VERBS:
        return str(verb.value), call.args[2]
    return None


def _first_parameter(node: Function, skip_self: bool) -> str | None:
    args = node.args.posonlyargs + node.args.args
    index = 1 if skip_self else 0
    return args[index].arg if len(args) > index else None


def sites(tree: ast.Module) -> list[Site]:
    """Every function installed as `pull_request` or `merge` in `tree`."""
    found: list[Site] = []
    seen: set[int] = set()

    def add(site: Site) -> None:
        if site.node is None or id(site.node) not in seen:
            found.append(site)
            if site.node is not None:
                seen.add(id(site.node))

    def install(verb: str, value: ast.expr, line: int, scope: ast.AST, prefix: str) -> None:
        where = f"{prefix}{verb}"
        if not isinstance(value, ast.Name):
            kind = type(value).__name__
            add(
                Site(
                    where,
                    line,
                    verb,
                    None,
                    None,
                    f"`{verb}` is installed as a {kind}, not a local `def`",
                )
            )
            return
        node = _resolve(value.id, scope, line)
        if node is None:
            add(
                Site(
                    where,
                    line,
                    verb,
                    None,
                    None,
                    f"`{verb}` is installed as `{value.id}`, which has no local `def` before it",
                )
            )
        elif _first_parameter(node, skip_self=False) != "reference":
            add(
                Site(
                    f"{prefix}{node.name}",
                    node.lineno,
                    verb,
                    None,
                    None,
                    f"`{value.id}` installed as `{verb}` does not take `reference` first",
                )
            )
        else:
            add(Site(f"{prefix}{node.name}", node.lineno, verb, node, None, None))

    def visit(scope: ast.AST, prefix: str, klass: ast.ClassDef | None) -> None:
        for node in _scope_nodes(scope):
            if isinstance(node, ast.ClassDef):
                visit(node, f"{prefix}{node.name}.", node)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if klass is not None and node.name in VERBS and scope is klass:
                    if _first_parameter(node, skip_self=True) == "reference":
                        add(Site(f"{prefix}{node.name}", node.lineno, node.name, node, klass, None))
                visit(node, f"{prefix}{node.name}.", None)
            else:
                for target in _attribute_targets(node):
                    install(target[0], target[1], node.lineno, scope, prefix)

    visit(tree, "", None)
    return found


def _attribute_targets(node: ast.AST) -> list[tuple[str, ast.expr]]:
    """The `(verb, value)` pairs one statement or expression installs."""
    if isinstance(node, ast.Assign):
        return [
            (target.attr, node.value)
            for target in node.targets
            if isinstance(target, ast.Attribute) and target.attr in VERBS
        ]
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        installed = _installed(node.value)
        return [installed] if installed else []
    return []


def _base_chain(klass: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> set[str]:
    """`MemoryTracker` and the classes of the file that `klass` descends from."""
    chain = {"MemoryTracker"}
    pending = [klass]
    while pending:
        for base in pending.pop().bases:
            if isinstance(base, ast.Name) and base.id in classes and base.id not in chain:
                chain.add(base.id)
                pending.append(classes[base.id])
    return chain


def _first_value(function: Function) -> ast.expr | None:
    body = function.body[1:] if function.body and _is_docstring(function.body[0]) else function.body
    if not body:
        return None
    first = body[0]
    if isinstance(first, ast.Expr | ast.Return):
        return first.value
    if isinstance(first, ast.Assign) and len(first.targets) == 1:
        return first.value
    return None


def _is(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _parses(site: Site, classes: dict[str, ast.ClassDef]) -> bool:
    """Is the first statement of the site directly one of the three accepted calls?"""
    node = site.node
    assert node is not None
    call = _first_value(node)
    if not isinstance(call, ast.Call) or any(isinstance(a, ast.Starred) for a in call.args):
        return False
    method = site.klass is not None
    parameter = _first_parameter(node, skip_self=method)
    assert parameter is not None
    func, args = call.func, call.args
    if _is(func, PARSE):
        return len(args) == 1 and not call.keywords and _is(args[0], parameter)
    if not isinstance(func, ast.Attribute) or func.attr != site.name or not method:
        return False
    owner = func.value
    if isinstance(owner, ast.Call) and _is(owner.func, "super") and not owner.args:
        return bool(args) and _is(args[0], parameter)
    assert site.klass is not None
    if isinstance(owner, ast.Name) and owner.id in _base_chain(site.klass, classes):
        return len(args) >= 2 and _is(args[0], "self") and _is(args[1], parameter)
    return False


def violations(source: str, path: str) -> list[str]:
    """The guard's verdict on one file: one string per violation, naming the path, the
    line and the qualified name of the site."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return [f"{path}:{error.lineno or 0}: does not parse: {error.msg}"]
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    found: list[str] = []
    for site in sites(tree):
        if site.problem is not None:
            found.append(f"{path}:{site.line}: {site.qualname}: {site.problem}")
        elif not _parses(site, classes):
            found.append(
                f"{path}:{site.line}: {site.qualname}: its first statement is not "
                f"`{PARSE}(reference)` or the inherited `{site.name}` called with `reference`"
            )
    return found


def _tree_files() -> list[Path]:
    return sorted((REPO / "tests").rglob("*.py"))


def _relative(file: Path) -> str:
    return file.relative_to(REPO).as_posix()


# -- the real tree -------------------------------------------------------------------------


def test_no_double_in_tests_takes_a_pull_request_reference_unparsed():
    found = [
        v for file in _tree_files() for v in violations(file.read_text("utf-8"), _relative(file))
    ]
    assert found == []


EXPECTED = (
    "FakePullRequestTracker.pull_request",
    "_FlakyPullRequestTracker.pull_request",
    "_TrackerWithPullRequest.pull_request",
    "MemoryTracker.pull_request",
    "MemoryTracker.merge",
    "MergingTracker.merge",
    "test_a_pull_request_read_that_raises_oserror_still_prints_the_not_open_line.raising",
    "test_a_tracker_that_raises_reading_the_pull_request_is_a_refusal_with_its_line.raising",
    "test_a_merge_that_fails_but_landed_keeps_the_intent_and_closes_as_auto_merged.landed",
    "test_the_failure_is_recorded_before_the_pull_request_is_read_again.spy",
)


def _real_sites() -> dict[str, list[tuple[str, str]]]:
    """Qualified name of every site on the real tree -> the `(path, source)` it is in."""
    found: dict[str, list[tuple[str, str]]] = {}
    for file in _tree_files():
        source = file.read_text("utf-8")
        for site in sites(ast.parse(source)):
            found.setdefault(site.qualname, []).append((_relative(file), source))
    return found


def test_the_guard_finds_every_site_it_is_known_to_guard():
    found = _real_sites()
    missing = [name for name in EXPECTED if name not in found]
    assert not missing, (
        f"the guard no longer finds these sites (renamed, moved or unseen): {missing}"
    )


def _without_first_statement(source: str, qualname: str) -> str:
    tree = ast.parse(source)
    (site,) = [s for s in sites(tree) if s.qualname == qualname]
    function = site.node
    assert function is not None
    drop = 1 if _is_docstring(function.body[0]) else 0
    del function.body[drop]
    if not function.body:
        function.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def test_removing_the_parse_from_any_known_site_is_reported_on_exactly_that_site():
    real = _real_sites()
    for qualname in EXPECTED:
        for path, source in real[qualname]:
            reported = violations(_without_first_statement(source, qualname), path)
            assert reported, (
                f"{qualname}: the guard accepted a double with its first statement removed"
            )
            assert all(f": {qualname}: " in line for line in reported), (qualname, reported)


# -- rejected inputs -----------------------------------------------------------------------


def _double(body: str, verb: str = "pull_request") -> str:
    indented = "\n".join("        " + line for line in body.splitlines())
    return f"class Double(MemoryTracker):\n    def {verb}(self, reference):\n{indented}\n"


REJECTED_METHODS = {
    "a literal argument": "pull_request_number('9')",
    "a different name as the argument": "pull_request_number(other)",
    "the call in an if": "if reference:\n    pull_request_number(reference)",
    "the parse as the second statement": (
        "self.refs.append(reference)\npull_request_number(reference)"
    ),
    "the call nested in another expression": "x = f(pull_request_number(reference))",
    "super of another name": "return super().merge(reference)",
    "a record before the parse": (
        "self.pull_request_refs.append(reference)\npull_request_number(reference)"
    ),
    "a call in a try": "try:\n    pull_request_number(reference)\nexcept ValueError:\n    raise",
    "a call in a with": "with lock:\n    pull_request_number(reference)",
    "a keyword argument": "pull_request_number(reference=reference)",
    "a class outside the chain": "return Elsewhere.pull_request(self, reference)",
    "no body but the docstring": '"""Only a docstring."""',
}


def test_each_unparsed_shape_is_rejected():
    for label, body in REJECTED_METHODS.items():
        found = violations(_double(body), "tests/x.py")
        assert len(found) == 1 and "tests/x.py:2: Double.pull_request:" in found[0], (label, found)


def test_super_of_the_other_verb_is_rejected_for_merge_too():
    source = _double("return super().pull_request(reference)", verb="merge")
    assert len(violations(source, "tests/x.py")) == 1


def test_an_assigned_lambda_is_rejected():
    source = "def test_a(tracker):\n    tracker.pull_request = lambda reference: None\n"
    (found,) = violations(source, "tests/x.py")
    assert "tests/x.py:2:" in found and "pull_request" in found and "Lambda" in found


def test_an_assigned_name_with_no_local_def_is_rejected():
    source = "def test_a(tracker):\n    tracker.merge = imported\n"
    (found,) = violations(source, "tests/x.py")
    assert "tests/x.py:2:" in found and "imported" in found and "no local `def`" in found


def test_an_attribute_and_a_call_as_the_installed_value_are_rejected():
    for value in ("real", "functools.partial(real, 1)"):
        source = f"def test_a(tracker, real):\n    tracker.pull_request = {value}\n"
        assert len(violations(source, "tests/x.py")) == 1, value


def test_a_def_defined_after_the_assignment_does_not_resolve():
    source = (
        "def test_a(tracker):\n    tracker.merge = later\n"
        "    def later(reference, head):\n        pull_request_number(reference)\n"
    )
    assert len(violations(source, "tests/x.py")) == 1


def test_monkeypatch_setattr_with_a_literal_name_at_a_def_that_does_not_parse_is_rejected():
    source = (
        "def test_a(monkeypatch, tracker):\n"
        "    def spy(reference):\n"
        "        return reference\n"
        "    monkeypatch.setattr(tracker, 'pull_request', spy)\n"
    )
    (found,) = violations(source, "tests/x.py")
    assert "tests/x.py:2: test_a.spy:" in found


def test_a_plain_setattr_with_a_literal_name_is_a_site_too():
    source = (
        "def test_a(tracker):\n"
        "    def spy(reference):\n        return 1\n"
        "    setattr(tracker, 'merge', spy)\n"
    )
    assert len(violations(source, "tests/x.py")) == 1


def test_a_resolved_def_whose_first_parameter_is_not_reference_is_rejected():
    source = (
        "def test_a(tracker):\n"
        "    def spy(ref, head):\n"
        "        pull_request_number(ref)\n"
        "    tracker.merge = spy\n"
    )
    (found,) = violations(source, "tests/x.py")
    assert "tests/x.py:2: test_a.spy:" in found and "does not take `reference` first" in found


def test_a_source_that_does_not_parse_is_a_violation_naming_its_path():
    (found,) = violations("def broken(:\n", "tests/broken.py")
    assert found.startswith("tests/broken.py:") and "does not parse" in found


# -- accepted inputs -----------------------------------------------------------------------


ACCEPTED_METHODS = {
    "an expression": "pull_request_number(reference)\nreturn None",
    "a return": "return pull_request_number(reference)",
    "an assignment": "number = pull_request_number(reference)\nreturn number",
    "after a docstring": '"""Parse first."""\npull_request_number(reference)',
    "super of the same name": "return super().pull_request(reference)",
    "the base class by name": "return MemoryTracker.pull_request(self, reference)",
}


def test_each_accepted_statement_form_passes():
    for label, body in ACCEPTED_METHODS.items():
        assert violations(_double(body), "tests/x.py") == [], label


def test_an_assigned_super_merge_passes():
    source = _double("method = super().merge(reference, head_sha)\nreturn method", verb="merge")
    assert (
        violations(source.replace("(self, reference)", "(self, reference, head_sha)"), "t.py") == []
    )


def test_a_class_of_the_same_file_in_the_base_chain_passes():
    source = (
        "class Middle(MemoryTracker):\n    pass\n\n"
        "class Double(Middle):\n"
        "    def pull_request(self, reference):\n"
        "        return Middle.pull_request(self, reference)\n"
    )
    assert violations(source, "tests/x.py") == []


def test_a_closure_that_parses_first_passes_through_every_install_form():
    source = (
        "def test_a(monkeypatch, tracker):\n"
        "    def one(reference):\n        pull_request_number(reference)\n"
        "    def two(reference, head):\n        return pull_request_number(reference)\n"
        "    tracker.pull_request = one\n"
        "    tracker.merge = two\n"
        "    monkeypatch.setattr(tracker, 'pull_request', one)\n"
    )
    assert violations(source, "tests/x.py") == []


def test_a_method_whose_first_parameter_is_not_reference_is_not_a_site():
    source = "class Workspace:\n    def merge(self, path, branch):\n        return None\n"
    assert violations(source, "tests/x.py") == []
