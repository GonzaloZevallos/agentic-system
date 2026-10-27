"""The two `StopFlag`s: the one only `run`/`resume` can mark, and the one that never is.

`NullStopFlag` is the default of every composition: a verb that is not `run` or `resume`
(`merge`, `land`, `perf`, `poll`, ...) cannot make `ShellCommands.run` raise `RunStopped`,
because nothing it holds can mark its flag. The marking one wraps a `threading.Event`, which
is safe to set from a signal handler's thread and to read from the thread of a command.
"""

from __future__ import annotations

import threading


class NullStopFlag:
    """A flag that is never set: `set()` does nothing."""

    def is_set(self) -> bool:
        return False

    def set(self) -> None:
        return None


class EventStopFlag:
    """A flag that can be set once and stays set."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()
