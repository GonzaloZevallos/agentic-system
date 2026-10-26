"""Which code state a check result belongs to (glossary: Resultado reutilizable, ADR-0065).

A passing result is reusable by anyone who asks for the same check, on the same command, on
the same tree and in the same environment. This module is the pure half: the key those four
make, the fingerprint of the environment, the rule that says a change moved the environment
under a result, and how long a stored pass stays good. It reads no clock (`now` is always an
argument) and no file: the store is a port (`ports.CheckResults`)."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from collections.abc import Sequence
from fnmatch import fnmatchcase

# A stored pass stops counting a day after it was written. Content keying says the code
# is the same; it cannot say the check is deterministic, so the age bounds how long a
# flaky pass stays green (ADR-0065).
MAX_AGE_S = 86400
# The store keeps at most this many entries, the oldest pruned first on write.
MAX_ENTRIES = 5000
DIRTY = "+dirty"

# What moves an environment: file names, at any depth. A change to one of these means the
# setup that ran when the checkout was made no longer describes what the checks run on.
_ENV_NAMES = (
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "requirements*.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile.lock",
    ".python-version",
    ".nvmrc",
    ".tool-versions",
    "rust-toolchain.toml",
)


def check_key(product: str, name: str, command: str, tree: str, fingerprint: str) -> str:
    """The one key a result is stored and looked up under: a hex digest over the product,
    the check's name, its exact command, the tree it ran on and the environment
    fingerprint. Encoded as JSON so a field that holds a separator cannot collide with
    its neighbour."""
    return _digest([product, name, command, tree, fingerprint])


def fingerprint(
    setup_commands: Sequence[str], stage0_version: str, check_timeout_s: float
) -> str | None:
    """The environment a result was measured in: the setup commands in the order the
    product declares them, the system's own version and the budget a check ran under.
    None for a system version that is `+dirty`: its code is not a commit, so nothing it
    measures can be named, and it neither reads nor writes the store."""
    if stage0_version.endswith(DIRTY):
        return None
    return _digest([list(setup_commands), stage0_version, check_timeout_s])


def env_drifted(changed_paths: Sequence[str], setup_commands: Sequence[str]) -> bool:
    """Whether a change to `changed_paths` can have moved the environment the setup built:
    a manifest, lock or tool-version file (`_ENV_NAMES`) at any depth, or a path a setup
    command names literally. Conservative on purpose: a false positive only means a pass
    is not stored."""
    named = _named_by(setup_commands)
    for path in changed_paths:
        normal = posixpath.normpath(path)
        if normal in named or any(fnmatchcase(posixpath.basename(normal), n) for n in _ENV_NAMES):
            return True
    return False


def is_fresh(written_at: float, now: float) -> bool:
    """Younger than `MAX_AGE_S` at `now`. An entry dated in the future is not fresh."""
    return 0 <= now - written_at < MAX_AGE_S


def _named_by(setup_commands: Sequence[str]) -> set[str]:
    named: set[str] = set()
    for command in setup_commands:
        try:
            words = shlex.split(command)
        except ValueError:
            words = command.split()
        named.update(posixpath.normpath(word) for word in words if word)
    return named


def _digest(parts: list[object]) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode("utf-8")).hexdigest()
