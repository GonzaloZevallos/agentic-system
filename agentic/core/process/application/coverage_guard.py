"""The coverage guard (GH-174): the in-turn refusal of a Developer delivery whose
coverage map leaves out one of the plan's invariants, or names a location that cannot be
the Developer's own work. A `SchemaGuard`-shaped callable with its own counter, composed
after the schema guard by `run_with_retry` (`delivery_check`) so the presubmit only ever
sees a delivery both accepted. Never a `check=` hook: it denies in the turn and never
raises, so it cannot become an `AttemptFailed` or a role failure."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..domain import coverage
from .ports import GitError, Workspace

# What the guard asks the checkout about a map: given the paths its locations name, the
# length in lines of each one that is a file, and the paths of the Developer's diff; `None`
# when the checkout cannot say (nothing committed yet, git unreadable), which skips the
# location check. The caller builds it over the ports; the guard knows no port.
LocationView = Callable[[set[str]], tuple[Mapping[str, int], set[str]] | None]
# How many stale locations the refusal quotes; the rest are counted.
_QUOTED = 5


class InvariantCoverageGuard:
    """Built once per role run with the number of plan invariants and, optionally, the
    `LocationView` of the Developer's checkout. Denies with `coverage.missing_invariants`'s
    own reason and the stale locations (`coverage.stale_locations`) in one message, at most
    `coverage.REFUSALS` times for both together; past that the delivery passes with the
    gap, and the Developer's section says which invariants were not mapped
    (`coverage.render`)."""

    def __init__(self, count: int, view: LocationView | None = None):
        self.count = count
        self.view = view
        self.refusals = 0

    def __call__(self, tool_input: dict[str, Any]) -> str | None:
        if self.refusals >= coverage.REFUSALS:
            return None
        parts = [
            part
            for part in (
                coverage.missing_invariants(tool_input, self.count),
                self._stale(tool_input),
            )
            if part is not None
        ]
        if not parts:
            return None
        self.refusals += 1
        return "\n\n".join(parts)

    def _stale(self, tool_input: dict[str, Any]) -> str | None:
        if self.view is None or not isinstance(tool_input, dict):
            return None
        lines = tool_input.get("coverage")
        paths = coverage.referenced_paths(lines)
        seen = self.view(paths) if paths else None
        if seen is None:
            return None
        reasons = coverage.stale_locations(lines, *seen)
        if not reasons:
            return None
        shown = [f"- {reason}." for reason in reasons[:_QUOTED]]
        if len(reasons) > _QUOTED:
            shown.append(f"- and {len(reasons) - _QUOTED} more.")
        return (
            "Some locations of the coverage map are not in your work:\n"
            + "\n".join(shown)
            + "\nGive the lines and files of your own diff, as they are at your last commit."
        )


def checkout_view(workspace: Workspace, path: Path, base: str, before: str) -> LocationView:
    """The `LocationView` of one Developer checkout, over the workspace port the presubmit
    already uses: the files as the last commit holds them and the paths that commit changed
    since `base`. `None` while nothing is committed past `before` (the presubmit refuses
    that on its own) and when git cannot answer, so a failed read never costs the one
    refusal."""

    def look(paths: set[str]) -> tuple[Mapping[str, int], set[str]] | None:
        try:
            head = workspace.head(path)
            if head == before:
                return None
            diff = set(workspace.changed_paths(base, head))
            texts = {name: workspace.show(head, name) for name in paths}
        except (GitError, OSError):
            return None
        return {
            name: len(text.splitlines()) for name, text in texts.items() if text is not None
        }, diff

    return look
