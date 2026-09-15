"""The product's checks as its base commit declares them, in a real repository."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.knowledge.adapters.git import GitChecks


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    git("init", "-q", "-b", "master", cwd=tmp_path)
    (tmp_path / "README.md").write_text("v0\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "v0", cwd=tmp_path)
    return tmp_path


def commit_checks(product: Path, text: str) -> str:
    target = product / ".agentic/checks.yaml"
    target.parent.mkdir(exist_ok=True)
    target.write_text(text)
    git("add", "-A", cwd=product)
    git("commit", "-q", "-m", "checks", cwd=product)
    return git("rev-parse", "--short", "HEAD", cwd=product)


def test_without_the_file_at_the_base_the_default_holds(product):
    base = git("rev-parse", "--short", "HEAD", cwd=product)
    commit_checks(product, "tests: uv run pytest\n")  # after the base: not this task's
    assert GitChecks(product).declared(base) == {"tests": "pytest"}


def test_the_base_commit_decides_not_the_working_tree(product):
    base = commit_checks(product, "tests: uv run pytest\nlint: ruff check .\n")
    (product / ".agentic/checks.yaml").write_text("tests: echo green\n")
    assert GitChecks(product).declared(base) == {"tests": "uv run pytest", "lint": "ruff check ."}


def test_a_broken_file_at_the_base_is_an_error(product):
    base = commit_checks(product, "- pytest\n")
    with pytest.raises(ValueError, match="checks.yaml: expected"):
        GitChecks(product).declared(base)
