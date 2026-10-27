"""When what master gained cannot change a recorded check pass (glossary: Ganancia inerte
del master, ADR-0045 addendum GH-353).

A recorded pass ran on a head merged with an older master. If master has moved since, the
pass still stands only when every path master gained is *inert*: it matches a glob the
product declared under `merge_inert`, and it is not in the never-inert set below. Anything
that cannot be read is "not covered" and the checks run. This module is the only place the
verdict is computed; it reads no file and no clock: the paths come from the workspace."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase

from agentic.core.process.domain.check_results import env_drifted

STACK_FILE = ".agentic/stack.yaml"
# Task documents are the one thing under `.agentic/` a product may declare inert.
_TASK_DOCUMENT = ".agentic/tasks/*.md"
_WILDCARDS = "*?["


def matches(glob: str, path: str) -> bool:
    """Whether `path` matches `glob`, segment by segment: `*`, `?` and `[...]` stay inside
    one segment and a whole segment `**` matches zero or more segments. Both are
    normalised first, and the match is case-sensitive."""
    return _match(_segments(glob), _segments(path))


def glob_refusal(glob: str) -> str | None:
    """The closed reason a declared glob is refused, or None: `empty`, `backslash`,
    `absolute`, `parent` (a `..` segment) or `wildcard_root` (the first segment is a
    pattern, so `**`, `*`, `*.md` and `**/x` could whitelist the whole tree)."""
    if not glob.strip():
        return "empty"
    if "\\" in glob:
        return "backslash"
    if glob.startswith("/"):
        return "absolute"
    if ".." in glob.split("/"):
        return "parent"
    first = _segments(glob)[0]
    if any(char in first for char in _WILDCARDS):
        return "wildcard_root"
    return None


def gain_inert(paths: Sequence[str], globs: Sequence[str], setup_commands: Sequence[str]) -> bool:
    """Whether a gain of `paths` is inert: not empty, no path never inert, and every path
    matches some declared glob. No declared glob means nothing is inert."""
    if not paths or not globs:
        return False
    return all(
        not never_inert(path, setup_commands) and any(matches(glob, path) for glob in globs)
        for path in paths
    )


def masters_covered(
    current: str,
    row_masters: Sequence[str],
    gains: Mapping[str, Sequence[str] | None],
    globs: Sequence[str],
    setup_commands: Sequence[str],
    task_touched: Sequence[str] | None,
) -> bool | None:
    """Whether every recorded row's master is current or only behind by an inert gain.

    `row_masters` are the distinct masters the rows ran on; `gains` maps each one that
    differs from `current` to the paths master gained since, or None when that could not
    be read or it is not an ancestor. None when no row master differs (the caller keeps
    today's equality); False when anything is in doubt: no declared glob, task paths
    unknown or touching the stack file, a gain unread or empty between different shas,
    a gain that is not inert."""
    behind = [master for master in dict.fromkeys(row_masters) if master != current]
    if not behind:
        return None
    if not globs or task_touched is None:
        return False
    if any(posixpath.normpath(path) == STACK_FILE for path in task_touched):
        return False
    for master in behind:
        paths = gains.get(master)
        if paths is None or not gain_inert(paths, globs, setup_commands):
            return False
    return True


def never_inert(path: str, setup_commands: Sequence[str]) -> bool:
    """Whether `path` can change what every check runs on, whatever the product declared:
    the stack file, `.agentic/` but for task documents, `.github/`, the coding standards,
    a path outside the repo, or anything `check_results.env_drifted` flags. Defined here
    once; it does not ask `rules.guarded_path`."""
    normal = posixpath.normpath(path)
    if normal.startswith(("/", "../")) or normal in (".", ".."):
        return True
    if normal == STACK_FILE or normal == "CODING_STANDARDS.md":
        return True
    if normal.startswith(".github/"):
        return True
    if normal.startswith(".agentic/") and not matches(_TASK_DOCUMENT, normal):
        return True
    return env_drifted([normal], setup_commands)


def _segments(text: str) -> list[str]:
    return [part for part in posixpath.normpath(text).split("/") if part]


def _match(glob: list[str], path: list[str]) -> bool:
    if not glob:
        return not path
    head, rest = glob[0], glob[1:]
    if head == "**":
        return any(_match(rest, path[skip:]) for skip in range(len(path) + 1))
    return bool(path) and fnmatchcase(path[0], head) and _match(rest, path[1:])
