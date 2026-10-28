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
