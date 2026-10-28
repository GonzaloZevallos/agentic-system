import pytest

from agentic.core.process.domain import rules


@pytest.mark.parametrize("prefix", rules.UNSTAGEABLE)
def test_every_prefix_and_what_lies_under_it_is_unstageable(prefix):
    assert rules.unstageable(f"{prefix}GH-1.md")
    assert rules.unstageable(f"{prefix}deep/er/file")
    assert rules.unstageable(f"./{prefix}GH-1.md")
    assert rules.unstageable(prefix.rstrip("/"))
    assert rules.unstageable(prefix)


@pytest.mark.parametrize(
    "relative",
    [
        ".agentic/tasks.md",
        ".agentic/stack.yaml",
        ".agentic/tasksX/GH-1.md",
        ".agentic/index/standards.md",
        "agentic/.agentic/tasks/GH-1.md",
        "docs/.agentic/tasks/GH-1.md",
        "tasks/GH-1.md",
        "",
    ],
)
def test_a_near_miss_is_stageable(relative):
    assert not rules.unstageable(relative)


def test_the_set_is_the_four_system_directories():
    assert rules.UNSTAGEABLE == (
        ".agentic/tasks/",
        ".agentic/runs/",
        ".agentic/logs/",
        ".agentic/worktrees/",
    )


def test_only_the_tasks_own_document_is_archivable():
    assert rules.archivable_document(".agentic/tasks/GH-1.md", "GH-1")
    # Another task's document, a nested path, another directory, a lookalike name.
    assert not rules.archivable_document(".agentic/tasks/GH-2.md", "GH-1")
    assert not rules.archivable_document(".agentic/tasks/GH-1.md.bak", "GH-1")
    assert not rules.archivable_document(".agentic/tasks/sub/GH-1.md", "GH-1")
    assert not rules.archivable_document(".agentic/runs/GH-1.md", "GH-1")
    assert not rules.archivable_document("README.md", "GH-1")
