"""Read-only ports for a dry poll round."""

from typing import Any

from agentic.core.process.application.ports import Tracker


class ReadOnly:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _spike(inner: object) -> Tracker:
    holder: Tracker = ReadOnly(inner)
    return holder
