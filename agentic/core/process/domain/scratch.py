"""Where the temporary files of a check or a role attempt live, and who owns them
(GH-391, ADR-0069).

Every check, setup and role attempt gets its own directory on the disk, outside every
repository, under `Configuration.scratch_dir` (the scratch root) and the partition of its
product: `<product key>/<root task>/<kind>-<token prefix>/`, so what it writes never lands
on the RAM-backed `/tmp` nor inside a checkout. This module is the only place that composes
that path, the kept folder of a task's evidence (which stays in the product) and the owner
record that says whose a directory is; no adapter spells a scratch path by hand. Pure: the
filesystem, the clock and `/proc` are the adapters'.

The part below the scratch root is bounded: for a task key of up to 16 characters it is at
most 40 characters without the trailing slash (12 for the product key, `/`, the task key,
`/`, and 10 for `<kind>-<8 characters>`), because a unix socket under a pytest `tmp_path`
has to fit in 108 bytes; the root's own length is the operator's budget.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal, get_args

# The task segment when a run has no root task (a command outside any task).
UNTASKED = "_untasked"

# Where the scratch of a product lived before the root moved out of the repositories
# (GH-397): kept only so the git exclude still covers a tree an older run left behind.
LEGACY_SCRATCH_DIR = PurePosixPath(".agentic/tmp")
KEPT_DIR = PurePosixPath(".agentic/kept")

# Declared, not measured: a directory with no owner record is an orphan only once it is
# this old (a record is written before its directory, so a younger one may be a record
# being replaced), a task's kept folder stays this long after the task closed, and the
# free inodes under which a volume counts as full whatever its free bytes say.
SCRATCH_ORPHAN_GRACE = timedelta(minutes=10)
KEPT_RETENTION = timedelta(days=14)
INODE_FLOOR = 10_000

# What sits between a product key and the token in the name a partition is renamed to.
SWEPT_INFIX = ".swept-"

# `c`: a check or a setup command. `r`: a role attempt.
OwnerKind = Literal["c", "r"]
_KINDS = get_args(OwnerKind)
# How many characters of the reaping token name the directory.
TOKEN_CHARS = 8
_WORD = re.compile(r"[0-9A-Za-z]+")
_PARTITION = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]*")
_SWEPT = re.compile(r"\.[0-9A-Za-z][0-9A-Za-z_-]*\.swept-[0-9A-Za-z]+")


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


def is_partition_name(name: str) -> bool:
    """Whether `name` could be the folder of a product's partition under the scratch root."""
    return _PARTITION.fullmatch(name) is not None


def is_swept_name(name: str) -> bool:
    """Whether `name` is a partition renamed aside (`.<key>.swept-<token>`)."""
    return _SWEPT.fullmatch(name) is not None


def partition(product_key: str) -> PurePosixPath:
    """The folder of one product under the scratch root, named by its reaping key. Refused
    when it is not one plain path segment: it could otherwise name another product's
    partition or something outside the root."""
    if not _PARTITION.fullmatch(product_key):
        raise ValueError(f"not a valid product key for a scratch partition: {product_key!r}")
    return PurePosixPath(product_key)


def path_for(product_key: str, task_key: str | None, owner: Owner) -> PurePosixPath:
    """The directory of `owner`, relative to the scratch root."""
    return partition(product_key) / _segment(task_key) / owner.name


def record_path_for(product_key: str, task_key: str | None, owner: Owner) -> PurePosixPath:
    """The owner record next to the directory: `<owner>.json`, relative to the scratch root."""
    return partition(product_key) / _segment(task_key) / f"{owner.name}.json"


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


@dataclass(frozen=True)
class RecordFact:
    """What the sweep knows of one owner record: when it was last written and whether its
    owner is alive (`liveness.is_alive` over the record's pid and boot id, computed by the
    adapter). `alive` is `None` when the record could not be read."""

    mtime: datetime
    alive: bool | None


@dataclass(frozen=True)
class OwnerEntry:
    """One owner under `<partition>/<task>/`: its directory's mtime (`None` when there is
    no directory) and its record (`None` when there is none)."""

    task: str
    name: str
    directory_mtime: datetime | None
    record: RecordFact | None


@dataclass(frozen=True)
class ScratchListing:
    """What is on the disk under the product's partition: the task directories (an empty one
    included) and every owner in them."""

    tasks: tuple[str, ...]
    owners: tuple[OwnerEntry, ...]


@dataclass(frozen=True)
class TaskFolder:
    """One `<partition>/<task>/` folder of any product: its mtime, its owners and the mtime of
    every child it holds, a hidden temp name (`.<owner>.json.tmp`) included, since a record
    being replaced is all a young owner shows of itself."""

    name: str
    mtime: datetime
    owners: tuple[OwnerEntry, ...]
    child_mtimes: tuple[datetime, ...]


@dataclass(frozen=True)
class PartitionFolder:
    """One `<root>/<product key>/` folder: its mtime and its task folders."""

    name: str
    mtime: datetime
    tasks: tuple[TaskFolder, ...]


@dataclass(frozen=True)
class Leftover:
    """What a sweep renamed aside (`.<key>.swept-<token>`) and did not finish removing."""

    name: str
    mtime: datetime


@dataclass(frozen=True)
class PartitionListing:
    """What is under the scratch root across products: every partition and every leftover."""

    partitions: tuple[PartitionFolder, ...]
    leftovers: tuple[Leftover, ...]


@dataclass(frozen=True)
class Orphans:
    """What the orphan rule returns: the names of other products' partitions to rename aside
    and remove, and the names of leftovers (`.<key>.swept-<token>`) to remove."""

    partitions: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeptFolder:
    """A task's `.agentic/kept/<task>/`, with when its task closed (`None`: it has not)."""

    task: str
    closed_at: datetime | None


@dataclass(frozen=True)
class Removal:
    """One owner to remove: its directory (when `directory`), then its record (when `record`)."""

    task: str
    name: str
    directory: bool
    record: bool


@dataclass(frozen=True)
class SweepDecision:
    """What a round removes: whole `<partition>/<task>/` folders, single owners, `kept/<task>/`
    folders, and orphan partitions and leftovers under the root."""

    tasks: tuple[str, ...] = ()
    owners: tuple[Removal, ...] = ()
    kept: tuple[str, ...] = ()
    # Other products' partitions (renamed aside, then removed) and leftovers of an earlier
    # rename (removed): what `orphan_partitions` returned (GH-397).
    partitions: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.tasks or self.owners or self.kept or self.partitions or self.leftovers)


def task_path_for(product_key: str, task_key: str | None) -> PurePosixPath:
    """The folder of one task's owners, relative to the scratch root."""
    return partition(product_key) / _segment(task_key)


def _owner_removal(entry: OwnerEntry, now: datetime) -> Removal | None:
    has_directory = entry.directory_mtime is not None
    record = entry.record
    if record is None:
        # A directory nobody claims is an orphan once it is older than the grace; a
        # younger one may be a record being replaced.
        if entry.directory_mtime is not None and now - entry.directory_mtime > SCRATCH_ORPHAN_GRACE:
            return Removal(entry.task, entry.name, directory=True, record=False)
        return None
    if record.alive is True:
        return None
    if record.alive is None and now - record.mtime <= SCRATCH_ORPHAN_GRACE:
        # Unreadable and young: it may be mid-write, so it counts as alive.
        return None
    return Removal(entry.task, entry.name, directory=has_directory, record=True)


def sweep_decision(
    listing: ScratchListing,
    closed: Collection[str],
    kept: Sequence[KeptFolder],
    now: datetime,
) -> SweepDecision:
    """What to remove this round. The whole folder of a task in `closed`; of the rest, an
    owner whose record is dead, an unreadable record older than `SCRATCH_ORPHAN_GRACE`
    (younger counts as alive) and a directory with no record older than the grace; and a
    kept folder once `KEPT_RETENTION` has passed since its task closed. An alive owner is
    never removed."""
    tasks = tuple(sorted(t for t in listing.tasks if t in closed))
    owners = tuple(
        removal
        for entry in listing.owners
        if entry.task not in closed and (removal := _owner_removal(entry, now)) is not None
    )
    expired = tuple(
        sorted(
            k.task for k in kept if k.closed_at is not None and now - k.closed_at >= KEPT_RETENTION
        )
    )
    return SweepDecision(tasks, owners, expired)


def swept_name(product_key: str, token: str) -> str:
    """The name a partition is renamed to before it is removed, at the scratch root: hidden, so
    it is never a partition, and carrying the key it came from."""
    if not _WORD.fullmatch(token):
        raise ValueError(f"not a plain token to name a leftover by: {token!r}")
    return f".{partition(product_key).as_posix()}{SWEPT_INFIX}{token}"


def _record_settled(record: RecordFact | None, limit: datetime) -> bool:
    """Whether a record is dead (not alive, not unreadable) and older than `limit`."""
    return record is None or (record.alive is False and record.mtime < limit)


def _quiet(folder: PartitionFolder, limit: datetime) -> bool:
    """Whether nothing in `folder` is younger than `limit` and no owner may still run."""
    return folder.mtime < limit and all(
        task.mtime < limit
        and all(m < limit for m in task.child_mtimes)
        and all(
            (entry.directory_mtime is None or entry.directory_mtime < limit)
            and _record_settled(entry.record, limit)
            for entry in task.owners
        )
        for task in folder.tasks
    )


def orphan_partitions(
    listing: PartitionListing,
    own_key: str,
    now: datetime,
    grace: timedelta = SCRATCH_ORPHAN_GRACE,
) -> Orphans:
    """What a sweep may remove beyond its own product's partition (GH-397). A partition of
    another product whose owners are all gone, with nothing in it (its folder, its task
    folders, any child, a hidden temp name included) younger than `grace`; a record that is
    alive or cannot be read keeps the whole partition. Every leftover of an earlier rename
    older than `grace`, whichever product it was of. Never the partition of `own_key`."""
    limit = now - grace
    partitions = tuple(
        sorted(f.name for f in listing.partitions if f.name != own_key and _quiet(f, limit))
    )
    leftovers = tuple(sorted(x.name for x in listing.leftovers if x.mtime < limit))
    return Orphans(partitions, leftovers)
