"""`AccountKeys`: which GitHub account this process talks to, as the key the budget store
files its budget under (GH-1071), and `read_rate_limit`, the one `gh api rate_limit` read.

The key is `github.com-` and the first 16 hex characters of the token's sha256, so two
processes with the same token share a file and the token itself is never written anywhere.
The token is only ever a local: of `key`, or of `_gh_token`, whose `gh` call is silent
(`log` writes nothing) and whose failure text never carries what `gh` printed. Only the key
that came from `gh` is remembered, for `BUDGET_TTL`, so a `gh` that is asked once a minute
is not asked on every call; a token in the environment is read on every call."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from ...domain.github_budget import BUDGET_TTL
from .run import Run, run_gh

_HOST = "github.com"


class AccountKeys:
    def __init__(
        self,
        run: Run,
        cwd: Path,
        environ: Mapping[str, str],
        clock: Callable[[], datetime],
    ):
        self._run = run
        self._cwd = cwd
        self._environ = environ
        self._clock = clock
        # (when it was read, the key or None): what `gh` answered, never the token.
        self._memo: tuple[datetime, str | None] | None = None

    def key(self) -> str | None:
        """The account key, or `None` for a host other than github.com and for a token
        that is missing, empty or has whitespace in it."""
        host = self._environ.get("GH_HOST")
        if host and host != _HOST:
            return None
        env_token = self._environ.get("GH_TOKEN") or self._environ.get("GITHUB_TOKEN")
        if env_token:
            return _key_of(env_token)
        now = self._clock()
        memo = self._memo
        if memo is not None and memo[0] <= now and now - memo[0] < BUDGET_TTL:
            return memo[1]
        key = _key_of(_gh_token(self._run, self._cwd))
        self._memo = (now, key)
        return key


def _key_of(token: str | None) -> str | None:
    if not token or any(character.isspace() for character in token):
        return None
    return f"{_HOST}-{hashlib.sha256(token.encode()).hexdigest()[:16]}"


def _gh_token(run: Run, cwd: Path) -> str | None:
    try:
        return run_gh(
            run,
            ("auth", "token", "--hostname", _HOST),
            cwd=cwd,
            not_installed="gh is not installed",
            failed=lambda _: "gh auth token failed",
            log=lambda _: None,
        )
    except ValueError:
        return None


def read_rate_limit(run: Run, cwd: Path) -> object | None:
    """The `gh api rate_limit` document, `None` when `gh` failed or printed no JSON."""
    try:
        out = run_gh(
            run,
            ("api", "rate_limit"),
            cwd=cwd,
            not_installed="gh is not installed",
            failed=lambda _: "gh api rate_limit failed",
            log=lambda _: None,
        )
        document: object = json.loads(out)
    except ValueError:
        return None
    return document
