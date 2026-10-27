"""Where the temporary files of a check or a role attempt live, and who owns them
(GH-391, ADR-0069).

Every check, setup and role attempt gets its own directory on the disk under the product,
`.agentic/tmp/<root task>/<kind>-<token prefix>/`, so what it writes never lands on the
RAM-backed `/tmp`. This module is the only place that composes that path, the kept folder
of a task's evidence and the owner record that says whose a directory is; no adapter
spells `.agentic/tmp` by hand. Pure: the filesystem, the clock and `/proc` are the
adapters'.

The part below the product root is bounded: for a task key of up to 16 characters it is at
most 40 characters without the trailing slash (12 for `.agentic/tmp/`, the key, `/`, and
10 for `<kind>-<8 characters>`), because a unix socket under a pytest `tmp_path` has to fit
in 108 bytes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal, get_args

# The task segment when a run has no root task (a command outside any task).
UNTASKED = "_untasked"

SCRATCH_DIR = PurePosixPath(".agentic/tmp")
KEPT_DIR = PurePosixPath(".agentic/kept")

# Declared, not measured: a directory with no owner record is an orphan only once it is
# this old (a record is written before its directory, so a younger one may be a record
# being replaced), a task's kept folder stays this long after the task closed, and the
# free inodes under which a volume counts as full whatever its free bytes say.
SCRATCH_ORPHAN_GRACE = timedelta(minutes=10)
KEPT_RETENTION = timedelta(days=14)
INODE_FLOOR = 10_000

# `c`: a check or a setup command. `r`: a role attempt.
OwnerKind = Literal["c", "r"]
_KINDS = get_args(OwnerKind)
# How many characters of the reaping token name the directory.
TOKEN_CHARS = 8
_WORD = re.compile(r"[0-9A-Za-z]+")


@dataclass(frozen=True)
class Owner:
    """Who a scratch directory belongs to: the kind of caller and the head of the token
    its processes are marked with (`reaping.mark`), which is how a directory is tied to
    the attempt that reaps them."""

    kind: OwnerKind
    prefix: str

    @property
    def name(self) -> str:
        return f"{self.kind}-{self.prefix}"


def owner(kind: OwnerKind, token: str) -> Owner:
    if kind not in _KINDS:
        raise ValueError(f"not a scratch owner kind: {kind!r}")
    prefix = token[:TOKEN_CHARS]
    if not _WORD.fullmatch(prefix):
        raise ValueError(f"not a plain token to name a directory by: {token!r}")
    return Owner(kind, prefix)


def _segment(task_key: str | None) -> str:
    """The root task key as a path segment; `UNTASKED` when there is none. Refused when it
    could name anything but one directory under the scratch root."""
    if task_key is None:
        return UNTASKED
    if not task_key or "/" in task_key or "\\" in task_key or ".." in task_key:
        raise ValueError(f"not a valid task key for a scratch path: {task_key!r}")
    return task_key


def path_for(task_key: str | None, owner: Owner) -> PurePosixPath:
    """The directory of `owner`, relative to the product root."""
    return SCRATCH_DIR / _segment(task_key) / owner.name


def record_path_for(task_key: str | None, owner: Owner) -> PurePosixPath:
    """The owner record next to the directory: `<owner>.json`, relative to the product root."""
    return SCRATCH_DIR / _segment(task_key) / f"{owner.name}.json"


def kept_path_for(task_key: str | None) -> PurePosixPath:
    """Where a task's evidence is kept, relative to the product root."""
    return KEPT_DIR / _segment(task_key)


@dataclass(frozen=True)
class OwnerRecord:
    """Who made a scratch directory: enough for a later round to tell whether that process
    is still there (`liveness.is_alive` over `pid` and `boot_id`)."""

    pid: int
    boot_id: str | None
    slug: str
    kind: OwnerKind
    created_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "boot_id": self.boot_id,
                "slug": self.slug,
                "kind": self.kind,
                "created_at": self.created_at.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, text: str) -> OwnerRecord | None:
        """`None` for anything that is not a record: a half-written or foreign file is
        an orphan to the sweep, never an error."""
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            pid, boot_id, slug = data["pid"], data["boot_id"], data["slug"]
            kind, created_at = data["kind"], datetime.fromisoformat(data["created_at"])
        except (ValueError, KeyError, TypeError):
            return None
        if (
            type(pid) is not int
            or not (boot_id is None or isinstance(boot_id, str))
            or not isinstance(slug, str)
            or kind not in _KINDS
        ):
            return None
        return cls(pid, boot_id, slug, kind, created_at)


@dataclass(frozen=True)
class SpaceReading:
    """What one volume has left: bytes a user may still write and inodes still free."""

    free_bytes: int
    free_inodes: int
