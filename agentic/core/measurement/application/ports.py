"""The ports of `eval run` (GH-470): what the driver needs from git and from the machine,
and nothing of how either is done. The git adapter is `adapters/git/eval_roots.py`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class EvalRoots(Protocol):
    """The isolated product roots of the evaluation runs, `<product>/.agentic/eval/<id>/`:
    each a detached worktree of the product at a frozen `base`."""

    def resolves(self, base: str) -> bool:
        """Whether `base` names a commit of the product's repository."""
        ...

    def has_stack(self, base: str) -> bool:
        """Whether the product at `base` declares `.agentic/stack.yaml`."""
        ...

    def path(self, eval_id: str) -> Path:
        """Where the root of `eval_id` lives (or would), existing or not."""
        ...

    def create(self, eval_id: str, base: str) -> Path:
        """A detached worktree of the product at `base` as the root of `eval_id`."""
        ...

    def ids(self) -> list[str]:
        """The ids of the roots that exist, sorted."""
        ...

    def remove(self, eval_id: str) -> None:
        """Remove the root of `eval_id`, worktree registration included. A no-op when it
        is already gone."""
        ...

    def delete_branches(self, eval_id: str) -> None:
        """Delete the local branches of `eval_id`: `agentic/<id>` and `agentic/<id>.<unit>`.
        No other branch, whatever its name starts with."""
        ...


class EvalChild(Protocol):
    """A launched `agentic run` child."""

    def wait(self) -> int:
        """Block until the child ends; its exit status."""
        ...


# Starts the child with the environment overlay and returns at once (never waits).
EvalSpawn = Callable[[list[str], dict[str, str]], EvalChild]
