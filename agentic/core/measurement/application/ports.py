"""The ports of `eval run` (GH-470): what the driver needs from git and from the machine,
and nothing of how either is done. The git adapter is `adapters/git/eval_roots.py`."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ..domain.issue_listing import ListedIssue, PageRecord


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


class GhRead(Protocol):
    """One `gh` read with no retry: its stdout. Raises the `TrackerCallFailed` family or
    `TrackerRefused` (both `ValueError`), or `ValueError` / `OSError`."""

    def __call__(self, args: tuple[str, ...]) -> str: ...


PageCause = Literal["rate_limited", "timed_out", "unreachable", "transient", "refused"]
StopCause = Literal[
    "rate_limited",
    "timed_out",
    "unreachable",
    "transient",
    "refused",
    "reserve",
    "quota_unreadable",
]


@dataclass(frozen=True)
class Page:
    """One page of the listing; `size` is the raw item count the page had."""

    items: Sequence[ListedIssue]
    size: int


@dataclass(frozen=True)
class PageUnreadable:
    cause: PageCause


@dataclass(frozen=True)
class QuotaReading:
    """The core quota the account has left, in points."""

    remaining: int


@dataclass(frozen=True)
class QuotaUnreadable:
    """The quota could not be read, whatever the reason."""


@dataclass(frozen=True)
class Spend:
    """What the reads cost: every `GhRead` call (failed ones too) and the core points
    (page calls only: reading the quota costs none)."""

    calls: int
    points: int


NO_READS = Spend(0, 0)


class IssueListing(Protocol):
    """The repository's issues and pull requests, a page at a time (1-based)."""

    def page(self, n: int) -> Page | PageUnreadable:
        """Never raises: a page that cannot be read is a `PageUnreadable`."""
        ...

    def rate_limit(self) -> QuotaReading | QuotaUnreadable:
        """Never raises: a quota that cannot be read is a `QuotaUnreadable`."""
        ...

    def spent(self) -> Spend:
        """The calls and core points spent so far."""
        ...


class ReadCache(Protocol):
    """The pages of the listing read before, by page number (1-based)."""

    def load(self) -> dict[int, PageRecord]:
        """Never raises: nothing readable is `{}`."""
        ...

    def save(self, pages: Mapping[int, PageRecord]) -> None:
        """Keep `pages` as the whole cache."""
        ...


class NoCache:
    """A read cache that keeps nothing: every run reads every page."""

    def load(self) -> dict[int, PageRecord]:
        return {}

    def save(self, pages: Mapping[int, PageRecord]) -> None:
        return None
