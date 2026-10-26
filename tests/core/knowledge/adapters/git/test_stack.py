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


def test_forbidden_is_read_from_the_base_commit(product):
    declared = (
        "checks:\n  tests: pytest\nforbidden:\n"
        "  - pattern: 'SUPPRESS'\n    extensions: ['.src']\n    reason: 'do not'\n"
    )
    base = commit_stack(product, declared)
    (rule,) = GitStack(product).forbidden(base)
    assert rule.reason == "do not"
    assert rule.extensions == frozenset({".src"})


def test_a_product_that_declares_no_forbidden_rule_forbids_nothing(product):
    base = commit_stack(product, "checks:\n  tests: pytest\n")
    assert GitStack(product).forbidden(base) == ()


def test_the_name_is_read_at_head_and_not_at_the_tasks_base(product):
    """The name says who the repository is, not what held at a commit: a task older than
    the key would otherwise resume with no name at all (issue #47)."""
    base = commit_stack(product, "checks:\n  tests: pytest\n")
    commit_stack(product, "product: gym-saas\nchecks:\n  tests: pytest\n")
    assert GitStack(product).product() == "gym-saas"
    with pytest.raises(ValueError, match="expected `product`"):
        GitStack(product).product(base)


def test_parallel_checks_is_read_from_the_base_commit(product):
    base = commit_stack(product, "checks:\n  tests: pytest\nparallel_checks: true\n")
    commit_stack(product, "checks:\n  tests: pytest\n")  # a later commit drops it: not the base's
    assert GitStack(product).parallel_checks(base) is True
    assert GitStack(product).parallel_checks("HEAD") is False


def test_a_product_that_declares_no_parallel_checks_runs_them_serially(product):
    base = commit_stack(product, "checks:\n  tests: pytest\n")
    assert GitStack(product).parallel_checks(base) is False


def test_a_parallel_checks_that_is_not_a_bool_at_the_base_is_an_error(product):
    base = commit_stack(product, 'checks:\n  tests: pytest\nparallel_checks: "true"\n')
    with pytest.raises(ValueError, match="expected `parallel_checks`"):
        GitStack(product).parallel_checks(base)


def test_the_affected_keys_are_read_from_the_base_commit(product):
    base = commit_stack(
        product,
        "checks:\n  tests: pytest\n"
        "affected_checks:\n  tests: pytest --since {base}\n"
        "affected_fallback:\n  - uv.lock\n",
    )
    commit_stack(product, "checks:\n  tests: pytest\n")
    assert GitStack(product).affected_checks(base) == {"tests": "pytest --since {base}"}
    assert GitStack(product).affected_fallback(base) == ("uv.lock",)
    assert GitStack(product).affected_checks("HEAD") == {}
    assert GitStack(product).affected_fallback("HEAD") == ()


def test_a_product_that_declares_no_affected_keys_reads_them_empty(product):
    base = commit_stack(product, "checks:\n  tests: pytest\n")
    assert GitStack(product).affected_checks(base) == {}
    assert GitStack(product).affected_fallback(base) == ()


def test_a_broken_affected_checks_at_the_base_is_an_error_naming_key_and_source(product):
    base = commit_stack(
        product, "checks:\n  tests: pytest\naffected_checks:\n  tests: pytest --lf\n"
    )
    with pytest.raises(
        ValueError, match=rf"{base}:.agentic/stack.yaml.*'tests'.*affected_checks.*base"
    ):
        GitStack(product).affected_checks(base)


def test_a_broken_affected_fallback_at_the_base_is_an_error_naming_key_and_source(product):
    base = commit_stack(product, "checks:\n  tests: pytest\naffected_fallback: uv.lock\n")
    with pytest.raises(ValueError, match=r"expected `affected_fallback`"):
        GitStack(product).affected_fallback(base)


def test_delivery_checks_are_read_from_the_base_commit(product):
    base = commit_stack(
        product, "checks:\n  tests: pytest\n  lint: ruff\ndelivery_checks: [lint]\n"
    )
    commit_stack(product, "checks:\n  tests: pytest\n  lint: ruff\n")
    assert GitStack(product).delivery_checks(base) == ("lint",)
    assert GitStack(product).delivery_checks("HEAD") == ()


def test_a_broken_delivery_checks_at_the_base_is_an_error_naming_key_and_source(product):
    base = commit_stack(product, "checks:\n  tests: pytest\ndelivery_checks: [e2e]\n")
    with pytest.raises(
        ValueError, match=rf"{base}:.agentic/stack.yaml.*'e2e'.*delivery_checks.*`checks`"
    ):
        GitStack(product).delivery_checks(base)


PERF = (
    "checks:\n  tests: pytest\n"
    "performance:\n  list:\n    seed: ./seed.sh\n    measure: ./measure.sh\n"
    "    volume: 500\n    volume_unit: rows\n"
    "    limits:\n      list_ms: {limit: 200, unit: ms}\n"
)


def test_performance_is_read_from_the_revision_asked_and_is_empty_where_absent(product):
    with_key = commit_stack(product, PERF)
    commit_stack(product, "checks:\n  tests: pytest\n")
    found = GitStack(product).performance(with_key)
    assert list(found) == ["list"]
    assert found["list"].volume == 500
    assert found["list"].limits["list_ms"].limit == 200
    assert GitStack(product).performance("HEAD") == {}


def test_a_malformed_performance_key_at_the_revision_raises(product):
    base = commit_stack(product, PERF.replace("measure: ./measure.sh", "measure: curl x"))
    with pytest.raises(ValueError, match="curl"):
        GitStack(product).performance(base)
    assert GitStack(product).checks(base) == {"tests": "pytest"}
