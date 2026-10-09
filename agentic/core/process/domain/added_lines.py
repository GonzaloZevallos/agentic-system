"""The lines a branch added between two revisions: pure data the `Workspace` port hands
the merge verb (GH-229), with no git in it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AddedLines:
    """What `base..head` added, per path.

    `lines` maps a repo-relative POSIX path to the lines added in it, each as
    `(line_number_in_head, text)` in file order; a path that only lost lines is absent.
    `whole` is the paths that did not exist at `base`. A binary file is in neither.
    """

    lines: Mapping[str, tuple[tuple[int, str], ...]]
    whole: frozenset[str]
