"""A placeholder a prompt names must have a value at every call that renders it (GH-304).

`render` is strict (`roles.render` raises on a `{{name}}` it has no value for), so a
placeholder added to a prompt, with a call site that never learned of it, crashes a run
and nothing before it. This scan finds that in CI: it reads every `run_role(...)` call in
`agentic/core/process/application/*.py`, resolves which prompts the call can render, and
checks that each placeholder of each of them is supplied.

Seam: `scan(sources, prompts)`, pure, over in-memory source text and prompt text, plus
`agentic.core.process.application.preflight.PLACEHOLDERS`, read only.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agentic.core.process.application import preflight
from agentic.core.process.application.run_role import run_role
from agentic.core.process.domain import roles

APPLICATION_DIR = Path(preflight.__file__).resolve().parent
PLAN = "agentic/core/process/application/plan.py"

# What `load_prompt` and `render` fill themselves, so no call has to.
ALWAYS_SUPPLIED = frozenset({"date", "max_changes", "max_turns", "flags"})
# The sides of an `Interview` a role expression may name.
SIDES = ("author", "interviewer", "interviewee", "consultant")
# `run_role`'s own named parameters: a keyword with one of these names is not a placeholder.
RUN_ROLE_PARAMS = frozenset(
    name
    for name, param in inspect.signature(run_role).parameters.items()
    if param.kind is not inspect.Parameter.VAR_KEYWORD
)
ROLES_BY_NAME = {name: role for name, role in vars(roles).items() if isinstance(role, roles.Role)}

ENTRY_POINTS = "tests.core.process.application.test_prompt_render_entry_points"
# The call sites the scan cannot resolve (the role is computed, or a `dataclasses.replace`
# local), each with the driven test that renders it for real. Key: `<file>:<function>:<role
# expression as written>`; value: the dotted path of that test.
ALLOWLIST: dict[str, str] = {
    "design.py:_lead_walk:widened": f"{ENTRY_POINTS}.test_design_widened_renders_prompt",
    "final_validation.py:_ui_lead_final:widened": (
        f"{ENTRY_POINTS}.test_final_validation_widened_renders_prompt"
    ),
    "review.py:review:reviewer": f"{ENTRY_POINTS}.test_review_renders_prompt",
    # No driven test among the seven declared drives these three; they point at the
    # nearest one (see the delivery's Decisions), so their proof is still to be written.
    "develop.py:develop:role": f"{ENTRY_POINTS}.test_review_renders_prompt",
    "diagnose.py:diagnose:diagnoser_for(checks)": f"{ENTRY_POINTS}.test_review_renders_prompt",
    "validate_prd.py:validate_prd:role": f"{ENTRY_POINTS}.test_review_renders_prompt",
}
# The only tests an entry may name; `driven-render-tests` writes them.
DRIVEN_TESTS = frozenset(
    f"{ENTRY_POINTS}.{name}"
    for name in (
        "test_design_widened_renders_prompt",
        "test_final_validation_widened_renders_prompt",
        "test_plan_interview_arm_renders_every_prompt",
        "test_plan_cold_arm_renders_author_prompt",
        "test_interview_renders_every_side",
        "test_review_renders_prompt",
        "test_stack_review_renders_every_prompt",
    )
)

type Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True)
class Call:
    """A call expression in a module, with the functions that enclose it, outermost first."""

    module: str
    scope: tuple[Function, ...]
    node: ast.Call
    # Module-level names bound to one `Interview` spec of `roles` (`_SPEC = INTERVIEW_...`).
    specs: Mapping[str, roles.Interview]

    @property
    def function(self) -> str:
        return self.scope[-1].name if self.scope else "<module>"

    @property
    def where(self) -> str:
        return f"{self.module}:{self.function}"


@dataclass(frozen=True)
class Site:
    """A `run_role(...)` call: which slugs it can render (`None`: the scan cannot tell),
    the placeholders it passes literally, and the function it forwards `**placeholders` from."""

    call: Call
    role_expr: str
    slugs: frozenset[str] | None
    keywords: frozenset[str]
    forwarder: Function | None

    @property
    def key(self) -> str:
        return f"{self.call.where}:{self.role_expr}"


def _specs(tree: ast.Module) -> dict[str, roles.Interview]:
    bound = {}
    for statement in tree.body:
        match statement:
            case ast.Assign(targets=[ast.Name(id=name)], value=ast.Name(id=spec)):
                if isinstance(value := vars(roles).get(spec), roles.Interview):
                    bound[name] = value
    return bound


def _calls(sources: Mapping[str, str]) -> list[Call]:
    found: list[Call] = []

    def walk(
        module: str,
        node: ast.AST,
        scope: tuple[Function, ...],
        specs: Mapping[str, roles.Interview],
    ) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scope = (*scope, node)
        if isinstance(node, ast.Call):
            found.append(Call(module, scope, node, specs))
        for child in ast.iter_child_nodes(node):
            walk(module, child, scope, specs)

    for path, text in sources.items():
        tree = ast.parse(text)
        walk(Path(path).name, tree, (), _specs(tree))
    return found


def _callee(node: ast.Call) -> str | None:
    match node.func:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def _forwarder(call: Call) -> Function | None:
    """The enclosing function whose `**kwargs` this call forwards, innermost first."""
    forwarded = {
        k.value.id for k in call.node.keywords if k.arg is None and isinstance(k.value, ast.Name)
    }
    for function in reversed(call.scope):
        if function.args.kwarg is not None and function.args.kwarg.arg in forwarded:
            return function
    return None


def _parameters(function: Function) -> set[str]:
    args = function.args
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}


def _side(side: str, specs: tuple[roles.Interview, ...] = roles.INTERVIEWS) -> frozenset[str]:
    return frozenset(getattr(spec, side).slug for spec in specs if getattr(spec, side) is not None)


def _resolve(call: Call, forwarder: Function | None) -> frozenset[str] | None:
    """The slugs the role argument can be: a role of `roles`, a side of an interview spec,
    or, in a function that forwards `**placeholders`, one of its role parameters."""
    node = call.node
    expr = (
        node.args[1]
        if len(node.args) > 1
        else next((k.value for k in node.keywords if k.arg == "role"), None)
    )
    match expr:
        case ast.Name(id=name) if name in ROLES_BY_NAME:
            return frozenset({ROLES_BY_NAME[name].slug})
        case ast.Attribute(attr=side, value=ast.Name(id=base)) if side in SIDES:
            # A module constant bound to one spec is that spec; anything else (a `spec`
            # parameter) could be any of them.
            return _side(side, (call.specs[base],) if base in call.specs else roles.INTERVIEWS)
        case ast.Attribute(attr=side) if side in SIDES:
            return _side(side)
        case ast.Name(id=name) if forwarder is not None and any(
            name in _parameters(f) for f in call.scope
        ):
            return _side(name) if name in SIDES else _side("interviewer") | _side("interviewee")
    return None


def call_sites(sources: Mapping[str, str]) -> list[Site]:
    sites = []
    for call in _calls(sources):
        if _callee(call.node) != "run_role" or not isinstance(call.node.func, ast.Name):
            continue
        forwarder = _forwarder(call)
        expr = call.node.args[1] if len(call.node.args) > 1 else None
        if expr is None:
            expr = next((k.value for k in call.node.keywords if k.arg == "role"), None)
        keywords = frozenset(
            k.arg for k in call.node.keywords if k.arg and k.arg not in RUN_ROLE_PARAMS
        )
        sites.append(
            Site(
                call,
                ast.unparse(expr) if expr is not None else "<none>",
                _resolve(call, forwarder),
                keywords,
                forwarder,
            )
        )
    return sites


def _origins(
    function: Function, calls: list[Call], seen: frozenset[str]
) -> list[tuple[str, frozenset[str]]]:
    """Every way `function` is reached: where from, and the keywords passed along the way.
    A caller that forwards its own `**kwargs` is followed to its callers, at any depth."""
    own = _parameters(function)
    origins: list[tuple[str, frozenset[str]]] = []
    for call in calls:
        if _callee(call.node) != function.name or call.node is function:
            continue
        passed = frozenset(k.arg for k in call.node.keywords if k.arg and k.arg not in own)
        upstream = _forwarder(call)
        if upstream is not None and upstream.name not in seen:
            for where, more in _origins(upstream, calls, seen | {function.name}):
                origins.append((where, passed | more))
            continue
        origins.append((call.where, passed))
    return origins


def scan(sources: Mapping[str, str], prompts: Mapping[str, str]) -> list[Problem]:
    calls = _calls(sources)
    problems: dict[Problem, None] = {}
    for site in call_sites(sources):
        if site.slugs is None:
            if site.key not in ALLOWLIST:
                problems[
                    Problem(site.call.where, f"run_role({site.role_expr}) cannot be resolved")
                ] = None
            continue
        if site.forwarder is None:
            origins = [(site.call.where, frozenset[str]())]
        else:
            origins = _origins(site.forwarder, calls, frozenset()) or [
                (site.call.where, frozenset())
            ]
        for slug in sorted(site.slugs):
            if slug not in prompts:
                problems[Problem(site.call.where, f"no prompt for {slug}")] = None
                continue
            needed = set(roles._PLACEHOLDER.findall(prompts[slug])) - ALWAYS_SUPPLIED
            for where, passed in origins:
                for name in sorted(needed - site.keywords - passed):
                    problems[Problem(where, f"{slug}: no value for {{{{{name}}}}}")] = None
    return list(problems)


def stale_entries(sites: list[Site], allowlist: Mapping[str, str]) -> list[Problem]:
    """Entries that match no call site, or whose call site the scan resolves on its own."""
    by_key = {site.key: site for site in sites}
    problems = []
    for key in allowlist:
        if key not in by_key:
            problems.append(Problem(key, "allowlist entry matches no run_role call site"))
        elif by_key[key].slugs is not None:
            problems.append(Problem(key, "allowlist entry for a call site the scan resolves"))
    return problems


def find_test(path: str) -> object | None:
    module, _, name = path.rpartition(".")
    try:
        return getattr(importlib.import_module(module), name, None)
    except ModuleNotFoundError as missing:
        if missing.name != module:
            raise
        return None


def missing_tests(
    allowlist: Mapping[str, str], find: Callable[[str], object | None] = find_test
) -> list[Problem]:
    return [
        Problem(key, f"allowlist names a test that does not exist: {path}")
        for key, path in allowlist.items()
        if find(path) is None
    ]


def real_sources() -> dict[str, str]:
    return {
        f"agentic/core/process/application/{path.name}": path.read_text(encoding="utf-8")
        for path in sorted(APPLICATION_DIR.glob("*.py"))
    }


def real_prompts() -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(roles.PROMPTS_DIR.glob("*.md"))
    }


def test_real_sources_and_prompts_have_no_problems() -> None:
    assert scan(real_sources(), real_prompts()) == []


def test_a_placeholder_no_caller_of_interview_passes_is_reported() -> None:
    prompts = real_prompts()
    slug = roles.TECH_LEAD_INTERVIEWEE_PLATFORM.slug
    prompts[slug] += "\n{{probe}}\n"

    problems = scan(real_sources(), prompts)

    assert any("plan.py" in str(p) and "probe" in str(p) for p in problems), problems


def test_a_call_that_stops_passing_cost_history_is_reported() -> None:
    sources = real_sources()
    assert sources[PLAN].count("cost_history=history.text(),") == 2
    sources[PLAN] = sources[PLAN].replace("cost_history=history.text(),", "")

    problems = scan(sources, real_prompts())

    assert any("plan.py" in str(p) and "cost_history" in str(p) for p in problems), problems


def test_a_call_site_the_scan_cannot_resolve_and_no_entry_names_is_reported() -> None:
    sources = real_sources()
    path = "agentic/core/process/application/classify.py"
    sources[path] += (
        "\n\nasync def extra(state, cfg, ports, pick):\n"
        "    return await run_role(state, pick(state), cfg, ports)\n"
    )

    problems = scan(sources, real_prompts())

    assert [str(p) for p in problems] == [
        "classify.py:extra: run_role(pick(state)) cannot be resolved"
    ]


def test_a_placeholder_is_followed_through_every_caller_of_a_forwarding_chain() -> None:
    chain = (
        "async def leaf(spec, **placeholders):\n"
        "    return await run_role(state, spec.interviewer, cfg, ports, **placeholders)\n"
        "async def mid(spec, **placeholders):\n"
        "    return await leaf(spec, **placeholders)\n"
    )
    prompts = {slug: "{{depth}}" for slug in _side("interviewer")}

    bare = scan({"m.py": chain + "async def top(spec):\n    return await mid(spec)\n"}, prompts)
    passing = scan(
        {"m.py": chain + "async def top(spec):\n    return await mid(spec, depth=3)\n"}, prompts
    )

    assert {p.where for p in bare} == {"m.py:top"}
    assert all("{{depth}}" in p.message for p in bare)
    assert passing == []


def test_every_placeholder_of_every_shipped_prompt_is_one_preflight_can_fill() -> None:
    known = set(preflight.PLACEHOLDERS) | ALWAYS_SUPPLIED
    unknown = {
        slug: sorted(set(roles._PLACEHOLDER.findall(text)) - known)
        for slug, text in real_prompts().items()
    }

    assert {slug: names for slug, names in unknown.items() if names} == {}


def test_a_prompt_with_a_placeholder_preflight_cannot_fill_is_told_apart() -> None:
    names = set(roles._PLACEHOLDER.findall("{{round}} {{probe}} {{date}}"))

    assert names - (set(preflight.PLACEHOLDERS) | ALWAYS_SUPPLIED) == {"probe"}


def test_every_allowlist_entry_matches_a_call_site_the_scan_cannot_resolve() -> None:
    assert stale_entries(call_sites(real_sources()), ALLOWLIST) == []


def test_an_allowlist_entry_for_a_call_site_that_does_not_exist_is_stale() -> None:
    entries = {"plan.py:plan:spec.nothing": f"{ENTRY_POINTS}.test_review_renders_prompt"}

    problems = stale_entries(call_sites(real_sources()), entries)

    assert [p.where for p in problems] == ["plan.py:plan:spec.nothing"]


def test_an_allowlist_entry_for_a_call_site_the_scan_resolves_is_stale() -> None:
    entries = {"plan.py:plan:spec.author": f"{ENTRY_POINTS}.test_review_renders_prompt"}

    problems = stale_entries(call_sites(real_sources()), entries)

    assert [p.where for p in problems] == ["plan.py:plan:spec.author"]
    assert "resolves" in problems[0].message


def test_an_allowlist_names_only_the_declared_driven_tests() -> None:
    assert set(ALLOWLIST.values()) <= DRIVEN_TESTS


def test_an_allowlist_test_that_does_not_exist_is_reported() -> None:
    here = f"{__name__}.test_an_allowlist_test_that_does_not_exist_is_reported"
    entries = {"a": here, "b": f"{__name__}.test_nothing", "c": "tests.no_such_module.test_x"}

    problems = missing_tests(entries)

    assert [p.where for p in problems] == ["b", "c"]


def test_every_allowlist_test_exists_in_the_entry_points_module() -> None:
    # Red until `driven-render-tests` lands `test_prompt_render_entry_points.py`.
    assert missing_tests(ALLOWLIST) == []
