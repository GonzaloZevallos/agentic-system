"""The content key of a check result, what makes an environment drift, and how long a
stored pass stays good (ADR-0065): pure functions, `now` always an argument."""

import pytest

from agentic.core.process.domain import check_results as cr

KEY = ("apostrophe", "tests", "uv run pytest", "a" * 40, "f" * 64)


def test_check_key_is_stable_hex_and_changes_with_each_input():
    base = cr.check_key(*KEY)
    assert base == cr.check_key(*KEY)
    assert len(base) == 64 and set(base) <= set("0123456789abcdef")
    for position in range(len(KEY)):
        changed = list(KEY)
        changed[position] += "x"
        assert cr.check_key(*changed) != base, position


def test_check_key_does_not_collide_when_a_separator_moves_between_fields():
    assert cr.check_key("ab", "c", "d", "t", "f") != cr.check_key("a", "bc", "d", "t", "f")


def test_fingerprint_covers_setup_in_order_stage0_and_timeout():
    base = cr.fingerprint(["uv sync", "npm ci"], "abc123", 600.0)
    assert base == cr.fingerprint(["uv sync", "npm ci"], "abc123", 600.0)
    assert base != cr.fingerprint(["npm ci", "uv sync"], "abc123", 600.0)
    assert base != cr.fingerprint(["uv sync"], "abc123", 600.0)
    assert base != cr.fingerprint(["uv sync", "npm ci"], "abc124", 600.0)
    assert base != cr.fingerprint(["uv sync", "npm ci"], "abc123", 601.0)


def test_a_dirty_stage0_has_no_fingerprint():
    assert cr.fingerprint(["uv sync"], "abc123+dirty", 600.0) is None


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "apps/web/package.json",
        ".nvmrc",
        "services/api/requirements-dev.txt",
        "requirements.txt",
        "uv.lock",
        "go.sum",
        "rust-toolchain.toml",
        "deep/er/.tool-versions",
    ],
)
def test_env_drifted_for_manifests_locks_and_tool_versions_at_any_depth(path):
    assert cr.env_drifted([path], [])


@pytest.mark.parametrize(
    "path", ["src/app.py", "tests/test_package.json.py", "docs/uv.lock.md", "requirements.md"]
)
def test_env_drifted_is_false_for_source_files(path):
    assert not cr.env_drifted([path], ["uv sync"])


def test_env_drifted_for_a_path_a_setup_command_names_literally():
    setup = ["pip install -r deps/base.in", "./scripts/bootstrap.sh --fast"]
    assert cr.env_drifted(["deps/base.in"], setup)
    assert cr.env_drifted(["scripts/bootstrap.sh"], setup)
    assert not cr.env_drifted(["deps/other.in"], setup)


def test_env_drifted_survives_a_setup_command_shlex_cannot_split():
    assert cr.env_drifted(["a.in"], ["echo 'unterminated a.in"])


def test_a_stored_entry_is_fresh_only_while_younger_than_max_age():
    assert cr.MAX_AGE_S == 86400
    assert cr.is_fresh(1000.0, 1000.0)
    assert cr.is_fresh(1000.0, 1000.0 + cr.MAX_AGE_S - 1)
    assert not cr.is_fresh(1000.0, 1000.0 + cr.MAX_AGE_S)
    # An entry from the future is not trusted either.
    assert not cr.is_fresh(2000.0, 1000.0)
