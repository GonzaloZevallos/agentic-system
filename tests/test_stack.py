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
"""

import tomllib
from pathlib import Path

import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.adapters.shell.commands import ShellCommands

REPO = Path(__file__).resolve().parent.parent
STACK_YAML = REPO / ".agentic" / "stack.yaml"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO / "pyproject.toml"


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
