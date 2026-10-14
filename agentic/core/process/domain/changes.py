"""One path a branch changed and how (GH-243): pure data the `Workspace` port hands the
merge verb, with no git in it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChangeStatus = Literal["A", "M", "D", "R", "C", "T"]


@dataclass(frozen=True)
class ChangedPath:
    """A repo-relative POSIX `path` and its `status`, the letter of `git diff --name-status`
    without a similarity score: `A`dded, `M`odified, `D`eleted, `R`enamed, `C`opied or
    type-`T`changed. For a rename or a copy `path` is the new path."""

    path: str
    status: ChangeStatus
