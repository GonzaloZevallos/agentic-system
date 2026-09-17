"""The stack a product's base commit declares, in a real repository."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.knowledge.domain import stack


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


def commit_stack(product: Path, text: str) -> str:
    target = product / ".agentic/stack.yaml"
    target.parent.mkdir(exist_ok=True)
    target.write_text(text)
    git("add", "-A", cwd=product)
    git("commit", "-q", "-m", "stack", cwd=product)
    return git("rev-parse", "--short", "HEAD", cwd=product)


def test_without_the_file_at_the_base_there_is_no_stack_and_no_default(product):
    base = git("rev-parse", "--short", "HEAD", cwd=product)
    commit_stack(product, "checks:\n  tests: uv run pytest\n")  # after the base: not this task's
    with pytest.raises(stack.NotDeclared, match="declares no stack"):
        GitStack(product).checks(base)


def test_the_setup_is_read_from_the_same_commit(product):
    base = commit_stack(product, "setup:\n  deps: uv sync\nchecks:\n  tests: pytest\n")
    assert GitStack(product).setup(base) == {"deps": "uv sync"}


def test_a_product_that_declares_no_setup_prepares_nothing(product):
    base = commit_stack(product, "checks:\n  tests: pytest\n")
    assert GitStack(product).setup(base) == {}


def test_a_revision_that_is_no_commit_is_told_apart_from_a_missing_file(product):
    with pytest.raises(ValueError, match="no commit"):
        GitStack(product).checks("nope")


def test_the_base_commit_decides_not_the_working_tree(product):
    base = commit_stack(product, "checks:\n  tests: uv run pytest\n  lint: ruff check .\n")
    (product / ".agentic/stack.yaml").write_text("tests: echo green\n")
    assert GitStack(product).checks(base) == {"tests": "uv run pytest", "lint": "ruff check ."}


def test_a_broken_file_at_the_base_is_an_error(product):
    base = commit_stack(product, "- pytest\n")
    with pytest.raises(ValueError, match="stack.yaml: expected"):
        GitStack(product).checks(base)
