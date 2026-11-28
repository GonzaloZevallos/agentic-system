"""The ports of a process that must not talk to GitHub (GH-1152): `agentic stage0 smoke` proves a
candidate stage starts and can read its product, and a GitHub-bound round is exactly what made
the old smoke run out of time. `NoGithub` stands in for the tracker, the inbox and the board;
any use of it raises `GithubCalled`, so a call that sneaks into the smoke fails it instead of
slowing it."""

from __future__ import annotations

from typing import Any


class GithubCalled(RuntimeError):
    """The smoke reached for a GitHub-backed port."""


class NoGithub:
    """A port that answers nothing. Reading any public member raises, before it is even called,
    so a port handed around and called later is caught as well."""

    def __init__(self, port: str) -> None:
        self._port = port

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise GithubCalled(f"{self._port}.{name}: the stage0 smoke makes no GitHub call")
