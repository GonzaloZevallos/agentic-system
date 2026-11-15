"""A delivery under a spec for the carry-gate tests (GH-594, part 4b-3b): the record the intake
leaves in `state["spec"]` and a reader that says where the text moved."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from agentic.core.process.application import spec_guard

SPEC_PATH = "docs/specs/a.md"
SPEC_RECORDED = "a" * 40
BODY = "## Goal\n\nText.\n"
TEXT = "---\nstatus: approved\n---\n" + BODY
CHANGED = TEXT.replace("Text.", "Other.")
SPEC: Any = {
    "path": SPEC_PATH,
    "commit": SPEC_RECORDED,
    "sha256": hashlib.sha256(BODY.encode()).hexdigest(),
}


class SpecAt:
    """The spec reader of a delivery whose text is `TEXT` at the recorded commit and at every
    commit `moved` does not name, and `CHANGED` at the ones it does. `calls` are the reads."""

    def __init__(self, moved: Callable[[str], bool] = lambda commit: False) -> None:
        self.moved = moved
        self.calls: list[tuple[str, str]] = []

    def read(self, commit: str, path: str) -> str:
        self.calls.append((commit, path))
        return CHANGED if commit != SPEC_RECORDED and self.moved(commit) else TEXT


class NeverRead:
    """A spec reader that fails the test that calls it: nothing reads the spec of a delivery
    that has none."""

    def read(self, commit: str, path: str) -> str:
        raise AssertionError(f"the spec was read at {commit}")


def spy_intact_at(monkeypatch) -> list[bool]:
    """Wraps `spec_guard.intact_at`: one entry per call, whether the state it got had a spec."""
    seen: list[bool] = []
    real = spec_guard.intact_at

    def spy(state: Any, ports: Any, head: str) -> bool:
        seen.append(bool(state.get("spec")))
        return real(state, ports, head)

    monkeypatch.setattr(spec_guard, "intact_at", spy)
    return seen
