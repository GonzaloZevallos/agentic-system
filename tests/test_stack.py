"""The contract between `.agentic/stack.yaml`, `.github/workflows/ci.yml` and
`[tool.mypy]` in `pyproject.toml` (ADR-0021, subtask `types`).

`checks.types` is `uv run mypy`, with no paths, byte-identical in `.agentic/stack.yaml`
and in the CI step: this is the seam that verifies that identity without running the
checker (this task's base grants `uv run pytest`, never `uv run mypy`). The checked
scope lives in `[tool.mypy] files`, never in the command, so this file also verifies
that scope is well-formed: non-empty, every entry an existing path under `agentic/`,
`agentic/plugins` (a pinned submodule, ADR-0013) never among them.

Only one direction is checked: every value of `checks` and of `setup` in `stack.yaml`
must appear verbatim as a `run:` step in `ci.yml`. The reverse does not hold — a
`run:` step with no matching declared check is exactly the below-floor state ADR-0021
allows (`mypy` in CI, `checks.types` withheld) — and `uses:` steps are not commands.

This file also guards pins in `.github/workflows/**` (ADR-0027, subtask `pin-guard`,
GH-74): a version literal that no command under `checks` or `setup` ever executes,
verified offline because no role in this system's path can query a registry
(`unknown_pins`, below). What it does NOT cover, named so it is not mistaken for
more: a version reaching a step through `env:` rather than `with:`, a composite
action's own internals, a version written in a file the workflow only reads, and
the *existence* of a `uses:` ref — only whether a ref *moved* is decidable offline;
whether it exists at all needs the network this guard refuses to touch.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.adapters.shell.commands import ShellCommands

REPO = Path(__file__).resolve().parent.parent
STACK_YAML = REPO / ".agentic" / "stack.yaml"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO / "pyproject.toml"
STANDARDS = REPO / "CODING_STANDARDS.md"


def _declared_commands() -> list[str]:
    data = yaml.safe_load(STACK_YAML.read_text(encoding="utf-8"))
    declared = stack.checks(data, STACK_YAML)
    declared.update(stack.setup(data, STACK_YAML))
    return list(declared.values())


def _run_steps() -> list[str]:
    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    steps = ci["jobs"]["check"]["steps"]
    return [step["run"] for step in steps if "run" in step]


def _setup_uv_step() -> dict:
    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    steps = ci["jobs"]["check"]["steps"]
    for step in steps:
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv"):
            return step
    raise AssertionError("no astral-sh/setup-uv step in ci.yml")


def _mypy_files() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["mypy"]["files"]


def test_every_declared_check_and_setup_command_is_a_run_step_in_ci():
    run_steps = _run_steps()
    for command in _declared_commands():
        assert command in run_steps, (
            f"{command!r} is declared in {STACK_YAML} but is not a `run:` step in {CI_YML}"
        )


def test_setup_uv_has_an_explicit_version():
    step = _setup_uv_step()
    version = step.get("with", {}).get("version")
    assert version, f"astral-sh/setup-uv in {CI_YML} has no explicit `version:` (ADR-0022)"


def test_mypy_files_is_non_empty():
    assert _mypy_files()


def test_every_mypy_file_exists():
    for entry in _mypy_files():
        assert (REPO / entry).exists(), f"[tool.mypy] files: {entry!r} does not exist"


def test_every_mypy_file_is_inside_agentic():
    for entry in _mypy_files():
        assert entry == "agentic" or entry.startswith("agentic/"), (
            f"[tool.mypy] files: {entry!r} is outside agentic/"
        )


def test_mypy_files_excludes_the_pinned_plugins_submodule():
    for entry in _mypy_files():
        assert not entry.startswith("agentic/plugins"), (
            f"[tool.mypy] files: {entry!r} is inside the pinned plugins submodule (ADR-0013)"
        )


def test_the_declared_witness_runs_at_the_repo_root_and_resolves_under_it():
    """The witness's own contract, separate from the checks/setup <-> ci.yml one above
    (ADR-0024): this repo's declared `witness`, read off `.agentic/stack.yaml` through
    the domain exactly as `GitStack` would, must actually run through the same project
    runner as the checks and print a path that resolves under the repo it just
    measured. `witness` is deliberately not among `_declared_commands()` above and has
    no `run:` step in `ci.yml`: it is not a check, CI delivers nothing and has no
    checkout to verify against."""
    data = yaml.safe_load(STACK_YAML.read_text(encoding="utf-8"))
    command = stack.witness(data, STACK_YAML)
    assert command is not None

    result = ShellCommands().run(REPO, command, timeout_s=120.0)
    assert result.passed, result.output

    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    printed = Path(lines[-1])
    assert printed.is_absolute() and printed.is_relative_to(REPO), result.output


def _mypy_option_names() -> list[str]:
    """Every `[tool.mypy]` key except `files` itself: the registry `CODING_STANDARDS.md`
    is allowed to point at, never one of its options (ADR-0012, adenda `standards`)."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return [key for key in data["tool"]["mypy"] if key != "files"]


def _standards_violations(text: str) -> list[str]:
    """Every forbidden spelling this repo's own `CODING_STANDARDS.md` must not carry: a
    path under `agentic/`, or the name of a `[tool.mypy]` option other than the
    registry key `files` (ADR-0021's dial, not its contents)."""
    found = ["agentic/"] if "agentic/" in text else []
    found += [option for option in _mypy_option_names() if option in text]
    return found


def test_this_repos_standards_name_its_type_checker_and_its_test_runner():
    """A materialisation that names no tool is an empty file passing for a full one
    (ADR-0012, adenda `standards`, subtask `standards`)."""
    text = STANDARDS.read_text(encoding="utf-8")
    assert "mypy" in text
    assert "pytest" in text


def test_this_repos_standards_name_no_path_under_agentic_and_no_mypy_option():
    text = STANDARDS.read_text(encoding="utf-8")
    assert _standards_violations(text) == []


def test_the_standards_violation_check_actually_rejects_a_forbidden_spelling():
    """Proves the guard above is not vacuous: a path under `agentic/` and a bare
    `[tool.mypy]` option both trip it, not only their absence in today's file."""
    bad = "See `agentic/core/knowledge/domain` and set `disallow_untyped_defs = true`."
    assert _standards_violations(bad) == ["agentic/", "disallow_untyped_defs"]


# ---------------------------------------------------------------------------
# Pin guard (ADR-0027, subtask `pin-guard`): GH-74 pinned `astral-sh/setup-uv`'s
# `version:` to `"10.2.0"`, the action's own major, not a uv release -- uv is at
# 0.12.x and the tarball 404'd in CI. Neither the Developer nor the Tech Lead
# reviewer can query a registry, so the defect class is verified offline
# instead: a version literal in a file no command under `checks` or `setup`
# ever executes. `.github/workflows/**` is today's only instance.

# `uses:` prefix -> the local command whose output states this project's version
# of the tool that action pins. One entry today. Never read from a module global
# by `unknown_pins` -- passed in as `table`, so a rejecting case (an unknown
# prefix) is expressible without touching this real table.
PIN_ORACLE: dict[str, str] = {"astral-sh/setup-uv": "uv --version"}

# `uses:` prefix -> the ref pinned the last time a human touched it (ADR-0022).
# Verbatim against `ci.yml` today; `test_unknown_pins_accepts_todays_real_steps`
# is what keeps this table honest, the same discipline `_mypy_option_names`
# already applies to the mypy registry.
PINNED_REFS: dict[str, str] = {
    "actions/checkout": "v4",
    "astral-sh/setup-uv": "v5",
}

_VERSION_KEY = re.compile(r"^version$|(-|_)version$")
_LITERAL_VERSION = re.compile(r"^\d+(\.\d+)*$")
_RUN_VERSION_LITERAL = re.compile(r"==\d+(\.\d+)*|@\d+\.\d+(\.\d+)*|/\d+\.\d+(\.\d+)*(?:/|$)")
_UV_VERSION_LINE = re.compile(r"^uv (\d+(?:\.\d+)*)\b")

PIN_NO_ORACLE = (
    "`ci.yml`'s `{step}` step pins `with: {key}: {value!r}` but `{prefix}` has no "
    "oracle in PIN_ORACLE (tests/test_stack.py): add one naming the local command "
    "that states this project's version of what `{prefix}` pins, or take the pin "
    "out."
)
PIN_NOT_LITERAL = (
    "`ci.yml`'s `{step}` step pins `{key}: {value!r}`, which is not a single "
    "literal version (the accepted shape: digits and dots only, stated once here "
    "and pointed at by CODING_STANDARDS.md). `latest`, a range and a `${{ }}` "
    "expression all reinstate the mobile uv `version:` exists to prevent "
    "(ADR-0022); pin a literal release instead."
)
PIN_MALFORMED_OBSERVED = (
    "`ci.yml`'s `{step}` step pins `{key}`, but `{command}` printed `{output}`, "
    "not a parseable version: the guard cannot state this tool's major here, so "
    "it fails closed. Run `{command}` locally and confirm it prints `uv X.Y.Z "
    "(...)`."
)
PIN_MISMATCH = (
    '`ci.yml`\'s `{step}` step pins `{key}: "{pinned}"`; the uv that runs this '
    "repo's checks reports `{observed}`, from `{command}`. `{key}` claims uv "
    "`{pinned}` shares a major with that uv; it does not -- uv went major. Bump "
    "`version:` in `ci.yml`'s `{step}` step to a `{observed_major}.x` release, a "
    "human commit (ADR-0022), and name the bump in the pull request body."
)
PIN_RUN_LITERAL = (
    "`ci.yml`'s `{step}` step's `run:` carries a version literal (`{token}`) with "
    "no oracle: add one, or take the version out of the command. Being declared "
    "under `checks` or `setup` does not exempt it -- only carrying no version "
    "literal does."
)
PIN_REF_CHANGED = (
    "`ci.yml`'s `{step}` step's `uses:` ref changed from `{prefix}@{expected}` to "
    "`{actual}`: a `uses:` ref is a human commit (ADR-0022); revert it and name "
    "the bump in the pull request body."
)


def _is_version_key(key: str) -> bool:
    """A `with:` key this guard governs: named exactly `version`, or ending in
    `-version`/`_version` -- spelled by key name so two Developers cannot draw
    the boundary differently."""
    return bool(_VERSION_KEY.search(key))


def _uses_prefix(uses: str) -> str:
    return uses.split("@", 1)[0]


def _parse_pin_version(value: object) -> str | None:
    """The accepted shape a `with:` version input must hold: a single literal
    version, digits and dots only. `latest`, a range, a `${{ }}` expression and
    an empty string all return `None`."""
    if not isinstance(value, str) or not _LITERAL_VERSION.match(value):
        return None
    return value


def _version_major(version: str) -> str:
    return version.split(".", 1)[0]


def _parse_observed_uv_version(output: str) -> str | None:
    """`uv --version` prints `uv 0.12.13 (<sha> <date>)`; anything else -- an
    unreachable binary's shell error, a future format -- fails closed."""
    match = _UV_VERSION_LINE.match(output.strip())
    return match.group(1) if match else None


def _step_label(step: dict[str, Any], index: int) -> str:
    name = step.get("name")
    return str(name) if name else f"step {index}"


def unknown_pins(
    steps: list[dict[str, Any]],
    table: dict[str, str],
    observed: dict[str, str] | None = None,
    pinned_refs: dict[str, str] | None = None,
) -> list[str]:
    """PURE: no subprocess, no filesystem. One message per offending step,
    fail-closed by enumeration over EVERY step.

    `table` maps a `uses:` prefix to the oracle command naming this project's
    version of what that prefix pins; passed as a parameter, never read from a
    module global, so a rejecting case is expressible without touching the real
    `PIN_ORACLE`. `observed` maps that same command string to its raw output, so
    both version parses -- the workflow value and the oracle's output line --
    live inside this pure layer; the caller owns the one subprocess. `pinned_refs`
    defaults to `PINNED_REFS`.

    Three independent rules walk every step:
    1. A `with:` key named exactly `version` or ending in `-version`/`_version`
       whose `uses:` prefix is absent from `table` fails (fail-closed
       enumeration). Present but not a single literal version fails. Present,
       literal, and the prefix's oracle has been observed: fails unless the
       pinned and observed versions share a major (a namespace claim, not
       equality -- a patch bump on either side never fails this).
    2. A `uses:` ref whose prefix is in `pinned_refs` but whose suffix changed
       fails: `uses:` refs are a human commit (ADR-0022).
    3. A `run:` step (no `uses:`) carrying a version-literal token fails with "no
       oracle for this pin", whether or not that command is declared under
       `checks`/`setup` -- the exemption is conjunctive (declared AND
       version-free); a declared command that gains a literal fails like any
       other, which is GH-74's shape reinstated as a `run:` step.
    """
    observed = observed or {}
    pinned_refs = PINNED_REFS if pinned_refs is None else pinned_refs
    violations: list[str] = []

    for index, step in enumerate(steps):
        label = _step_label(step, index)
        uses = step.get("uses")
        prefix = _uses_prefix(uses) if uses else None

        for key, value in (step.get("with") or {}).items():
            if not _is_version_key(key):
                continue
            if prefix is None or prefix not in table:
                violations.append(
                    PIN_NO_ORACLE.format(
                        step=label, key=key, value=value, prefix=prefix or "(no uses:)"
                    )
                )
                continue
            pinned = _parse_pin_version(value)
            if pinned is None:
                violations.append(PIN_NOT_LITERAL.format(step=label, key=key, value=value))
                continue
            command = table[prefix]
            output = observed.get(command)
            if output is None:
                continue
            observed_version = _parse_observed_uv_version(output)
            if observed_version is None:
                violations.append(
                    PIN_MALFORMED_OBSERVED.format(
                        step=label, key=key, command=command, output=output
                    )
                )
                continue
            if _version_major(pinned) != _version_major(observed_version):
                violations.append(
                    PIN_MISMATCH.format(
                        step=label,
                        key=key,
                        pinned=pinned,
                        observed=observed_version,
                        command=command,
                        observed_major=_version_major(observed_version),
                    )
                )

        if uses and prefix in pinned_refs:
            expected = pinned_refs[prefix]
            actual = uses.split("@", 1)[1] if "@" in uses else ""
            if actual != expected:
                violations.append(
                    PIN_REF_CHANGED.format(
                        step=label, prefix=prefix, expected=expected, actual=actual
                    )
                )

        if uses is None and "run" in step:
            match = _RUN_VERSION_LITERAL.search(step["run"])
            if match:
                violations.append(PIN_RUN_LITERAL.format(step=label, token=match.group(0)))

    return violations


def _all_ci_steps() -> list[dict[str, Any]]:
    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = ci["jobs"]["check"]["steps"]
    return steps


def test_unknown_pins_accepts_todays_real_steps():
    assert unknown_pins(_all_ci_steps(), PIN_ORACLE) == []


def test_unknown_pins_rejects_a_with_key_absent_from_the_oracle_table():
    step = {"uses": "actions/setup-python@v5", "with": {"python-version": "3.13"}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "actions/setup-python" in violations[0]


def test_unknown_pins_accepts_a_step_whose_uses_is_in_the_table():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"enable-cache": True}}
    assert unknown_pins([step], PIN_ORACLE) == []


def test_unknown_pins_rejects_the_historical_setup_uv_major_mismatch():
    """GH-74: `version: "10.2.0"` was the action's own major, not a uv release;
    uv on `PATH` was 0.12.x and the tarball 404'd in CI."""
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": "10.2.0"}}
    observed = {"uv --version": "uv 0.12.13 (deadbeef 2026-08-01)"}
    violations = unknown_pins([step], PIN_ORACLE, observed=observed)
    assert violations and "10.2.0" in violations[0] and "0.12.13" in violations[0]


def test_unknown_pins_accepts_a_patch_only_drift():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": "0.12.13"}}
    observed = {"uv --version": "uv 0.12.14 (deadbeef 2026-08-01)"}
    assert unknown_pins([step], PIN_ORACLE, observed=observed) == []


def test_unknown_pins_rejects_latest():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": "latest"}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "literal" in violations[0]


def test_unknown_pins_rejects_a_range():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": ">=0.12,<1"}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "literal" in violations[0]


def test_unknown_pins_rejects_an_expression():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": "${{ vars.UV_VERSION }}"}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "literal" in violations[0]


def test_unknown_pins_rejects_an_empty_string():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": ""}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "literal" in violations[0]


def test_unknown_pins_rejects_a_malformed_uv_version_line():
    step = {"uses": "astral-sh/setup-uv@v5", "with": {"version": "0.12.13"}}
    observed = {"uv --version": "command not found: uv"}
    violations = unknown_pins([step], PIN_ORACLE, observed=observed)
    assert violations and "not a parseable version" in violations[0]


def test_unknown_pins_rejects_a_run_step_with_a_version_literal():
    step = {"run": "pipx install uv==10.2.0"}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "no oracle" in violations[0]


def test_unknown_pins_rejects_a_declared_command_that_gains_a_version_literal():
    """The exemption is conjunctive (declared under `checks`/`setup` AND
    version-free): a command sharing a declared prefix still fails once it
    carries a literal -- GH-74's shape reinstated as a `run:` step. A blanket
    "every undeclared run: step fails" would be wrong too (see the accepting
    case below), which is why this rule checks the literal, not declaration."""
    declared = _declared_commands()
    assert "uv sync --locked" in declared
    step = {"run": "uv sync --locked && pipx install uv==10.2.0"}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "no oracle" in violations[0]


def test_unknown_pins_accepts_a_declared_version_free_run_step():
    declared = _declared_commands()
    step = {"run": declared[0]}
    assert unknown_pins([step], PIN_ORACLE) == []


def test_unknown_pins_rejects_a_changed_uses_ref():
    step = {"uses": "astral-sh/setup-uv@v7", "with": {"version": "0.12.13"}}
    violations = unknown_pins([step], PIN_ORACLE)
    assert violations and "human commit" in violations[0]


def test_pin_mismatch_message_names_all_four_parts():
    """On the model of `test_the_standards_violation_check_actually_rejects_a_
    forbidden_spelling`: proves `PIN_MISMATCH` is not vacuous -- the pinned value
    and its source, the observed value and the command that produced it, the
    claim, and the single edit that fixes it, all land in a formatted instance."""
    message = PIN_MISMATCH.format(
        step="setup-uv",
        key="version",
        pinned="10.2.0",
        observed="0.12.13",
        command="uv --version",
        observed_major="0",
    )
    assert "10.2.0" in message and "ci.yml" in message and "setup-uv" in message
    assert "0.12.13" in message and "uv --version" in message
    assert "major" in message
    assert "version:" in message and "human commit" in message


def test_pin_guard_accepts_the_real_ci_workflow_against_the_locally_observed_uv():
    """The ONE test in this file that spawns the `uv --version` subprocess
    (ADR-0027, subtask `pin-guard`). When uv is unreachable this fails HERE, by
    name, naming the pin guard -- never at import time, never as a collection
    error elsewhere in this file. Runs through `ShellCommands`, the same runner
    the checks use, since `uv` itself (unlike `python`, ADR-0024) is not a
    per-venv artefact `without_own_toolchain` would strip off `PATH`."""
    command = PIN_ORACLE["astral-sh/setup-uv"]
    result = ShellCommands().run(REPO, command, timeout_s=30.0)
    assert result.passed, (
        f"the pin guard's oracle command {command!r} did not run, so it cannot "
        f"verify astral-sh/setup-uv's `version:` in {CI_YML}: {result.output}"
    )
    assert _parse_observed_uv_version(result.output) is not None, (
        f"the pin guard's oracle command {command!r} printed {result.output!r}, "
        "which does not parse as `uv X.Y.Z (...)`"
    )
    assert unknown_pins(_all_ci_steps(), PIN_ORACLE, observed={command: result.output}) == []
