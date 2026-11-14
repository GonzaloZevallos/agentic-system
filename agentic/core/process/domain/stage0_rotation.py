"""The rules of stage0 rotation (GH-622, part 1a): which sha stage0 should move to, which
stage directories may be pruned, when a poller must re-exec and when a rotation must be
rolled back. Pure: every fact (CI state, ancestry, mtimes, heartbeats, the clock) arrives
as a parameter, nothing here reads the disk, the clock, the environment or a global, and
paths are compared with `==` only. Nothing calls these rules yet; the later parts of GH-622
do. Every comparison of a commit id goes through `rules.same_commit`."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from .rules import same_commit

CiState = Literal["green", "red", "pending", "cancelled", "unknown"]
"""The CI state of one commit of the window. Translating `ci.CiVerdict` into `CiState`
belongs to the later part that reads CI; this module does not import `domain/ci.py`."""

KeepCause = Literal[
    "pinned",
    "up_to_date",
    "no_green_in_window",
    "unreadable",
    "rejected",
    "busy",
    "smoke_failed",
    "unavailable",
]
"""Why stage0 stays where it is. `target` returns only pinned, up_to_date,
no_green_in_window, unreadable and rejected; busy, smoke_failed and unavailable are produced
by the later parts that rotate (a run in flight, a failed smoke test, a candidate that could
not be prepared or smoked), never by `target`."""

CandidateCause = Literal["clone", "uv", "smoke_timeout", "disk", "control_red"]
"""Why a candidate stage could not be prepared or smoked: the clone failed, `uv` failed, the
smoke test ran out of time, the disk was full, or the control smoke on the known-good stage
was red too (so the candidate is not the one to blame)."""

_PIN = re.compile(r"[0-9a-fA-F]{7,40}")
_STAGE_NAME = re.compile(r"[0-9a-f]{40}")
_POLLER_BEAT_FACTOR = 2


@dataclass(frozen=True)
class WindowEntry:
    """One commit of the first-parent history of origin/master. `descends` is whether `sha`
    STRICTLY descends from the current sha; None means its ancestry could not be read."""

    sha: str
    ci: CiState
    descends: bool | None


@dataclass(frozen=True)
class Target:
    """The sha stage0 should move to. May be an abbreviated pin, lowercased: the part that
    reads git expands it to 40 hex before naming a stage."""

    sha: str


@dataclass(frozen=True)
class Keep:
    """Stage0 stays where it is, for `cause`."""

    cause: KeepCause


@dataclass(frozen=True)
class Unreadable:
    """A fact that could not be read: 'don't know', never 'none'."""


@dataclass(frozen=True)
class Heartbeat:
    """What a poller last wrote about itself: its name, the checkout it runs, when, and
    how often it promises to beat."""

    poller: str
    checkout: Path
    at: datetime
    interval_s: float


@dataclass(frozen=True)
class SwitchRecord:
    """The intent record of a switch (`switch.json`): the stage `current` pointed at before
    (`previous`, None on bootstrap), the stage switched to (`new`), when (`switched_at`,
    aware) and the pollers expected to come back on `new` (`expected`, empty when the record
    carries none)."""

    previous: str | None
    new: str
    switched_at: datetime
    expected: frozenset[str]


@dataclass(frozen=True)
class Prepared:
    """A candidate built as `<sha>.partial`; `sha` is the 40-hex commit it resolved."""

    sha: str


@dataclass(frozen=True)
class Green:
    """The smoke test of a partial passed."""


@dataclass(frozen=True)
class Red:
    """The smoke test of a partial ran and failed."""


@dataclass(frozen=True)
class Unavailable:
    """The candidate could not be prepared or smoked, for `cause`: nothing is known about
    the code."""

    cause: CandidateCause


@dataclass(frozen=True)
class Proceed:
    """A failure policy decided to continue the adoption, holding the `Green` smoke that
    justifies it; `line` is what to print."""

    line: str
    smoke: Green


@dataclass(frozen=True)
class Stop:
    """A failure policy decided to end the adoption; `line` is what to print."""

    line: str


def partial_name(sha: str) -> str:
    """The name of the stage directory a candidate is built in before it is promoted."""
    return f"{sha}.partial"


def _rejected(sha: str, rejected: frozenset[str]) -> bool:
    return any(same_commit(sha, r) for r in rejected)


def _aware(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.utcoffset() is not None


def target(
    window: Sequence[WindowEntry],
    current: str | None,
    pin: str | None,
    rejected: frozenset[str],
) -> Target | Keep:
    """The sha stage0 should move to, or why it stays. `window` is the first-parent history
    of origin/master, newest first, at most 10 entries. Precedence:

    1. a pin that is not 7-40 hex (the empty string included) -> Keep(unreadable), never
       'no pin';
    2. a pin naming `current` -> Keep(pinned) (staying is not adopting, so it is checked
       before `rejected`);
    3. a pin in `rejected` -> Keep(rejected) (a human releases it by removing the pin or
       the rejection);
    4. any other valid pin -> Target(pin), lowercased, whatever its CI or ancestry (only a
       pin goes backwards);
    5. without a pin, the newest entry that is green, STRICTLY descends from `current` and
       is not rejected -> Target(entry.sha);
    6. nothing adopted because a green descendant was rejected -> Keep(rejected);
    7. nothing adopted and some entry descends or has unreadable ancestry ->
       Keep(no_green_in_window);
    8. otherwise (empty window, or nothing descends from `current`) -> Keep(up_to_date).

    Pending, cancelled, red, unknown and unreadable ancestry are skipped; cancelled is never
    treated as red."""
    if pin is not None:
        if _PIN.fullmatch(pin) is None:
            return Keep("unreadable")
        if same_commit(pin, current):
            return Keep("pinned")
        if _rejected(pin, rejected):
            return Keep("rejected")
        return Target(pin.lower())
    skipped_rejected = False
    for entry in window:
        if entry.ci == "green" and entry.descends is True:
            if _rejected(entry.sha, rejected):
                skipped_rejected = True
                continue
            return Target(entry.sha)
    if skipped_rejected:
        return Keep("rejected")
    if any(entry.descends is not False for entry in window):
        return Keep("no_green_in_window")
    return Keep("up_to_date")


def prunable(
    stages: Sequence[tuple[str, datetime]],
    current: str | None,
    own: str | None,
    users: frozenset[str] | Unreadable,
    *,
    keep: int = 2,
    previous: str | None = None,
) -> frozenset[str] | Unreadable:
    """The stage names that may be deleted. Only names of exactly 40 lowercase hex are
    candidates (never `*.partial`, uppercase or abbreviated names). Never pruned: `current`,
    `own`, `previous`, anything in `users`, nor the `keep` newest candidates by
    (mtime, name). `keep` keeps the N newest by mtime and gives NO guarantee about the
    rollback target: the part that rotates must pass `previous`. Unreadable means 'don't
    know' (unreadable `users`, or a naive mtime): nothing is decided and the caller should
    warn. An empty frozenset means decided, nothing to prune."""
    if isinstance(users, Unreadable):
        return Unreadable()
    if not all(_aware(mtime) for _, mtime in stages):
        return Unreadable()
    candidates = [(name, mtime) for name, mtime in stages if _STAGE_NAME.fullmatch(name)]
    newest = sorted(
        range(len(candidates)), key=lambda i: (candidates[i][1], candidates[i][0]), reverse=True
    )
    kept = set(newest[: max(keep, 0)])
    protected = [current, own, previous, *users]
    return frozenset(
        name
        for index, (name, _) in enumerate(candidates)
        if index not in kept and not any(same_commit(name, p) for p in protected)
    )


def reexec_due(
    argv0_abs: Path,
    argv0_real: Path,
    current_target: Path | None,
    own_checkout: Path,
) -> Path | None:
    """The checkout to re-exec into, or None. None without a current target; the current
    target when it differs from `own_checkout` or when `argv0_abs != argv0_real` (the
    poller was started through a link). `current_target` and `own_checkout` arrive
    normalized in the same form by the caller that reads the disk (comparable with
    `shared/stage0.CHECKOUT`, already resolved); the re-exec uses the returned path as is.
    A fixed point: called again with the returned path as `own_checkout` and
    `argv0_abs == argv0_real`, it returns None."""
    if current_target is None:
        return None
    if current_target != own_checkout or argv0_abs != argv0_real:
        return current_target
    return None


def expected_pollers(
    heartbeats_before: Mapping[str, Heartbeat | Unreadable],
    switched_at: datetime,
) -> frozenset[str]:
    """The pollers expected to come back on the new checkout: those whose heartbeat existed
    before the switch, with `at` strictly before `switched_at` and no older than 2 x their
    `interval_s` (the border included). Unreadable heartbeats, naive `at` and an
    `interval_s` of 0 or less leave a poller out. A naive `switched_at` raises ValueError."""
    if not _aware(switched_at):
        raise ValueError("switched_at must be timezone-aware")
    return frozenset(
        poller
        for poller, beat in heartbeats_before.items()
        if isinstance(beat, Heartbeat)
        and _aware(beat.at)
        and beat.interval_s > 0
        and beat.at < switched_at
        and switched_at - beat.at <= timedelta(seconds=_POLLER_BEAT_FACTOR * beat.interval_s)
    )


def rollback_due(
    switched_at: datetime,
    new: Path,
    expected: frozenset[str],
    heartbeats: Mapping[str, Heartbeat | Unreadable],
    now: datetime,
    grace: timedelta = timedelta(minutes=15),
) -> bool:
    """Whether the rotation to `new` must be rolled back. Never while `now - switched_at`
    is within `grace` (the border included). Past it, true when some poller in `expected`
    has no heartbeat showing checkout `new` (missing or unreadable counts as absent; the
    heartbeat's own `at` is not looked at, so a naive `at` on a heartbeat showing `new`
    still counts as present). A poller outside `expected` never causes a
    rollback. A naive `now` or `switched_at` raises ValueError."""
    if not _aware(now) or not _aware(switched_at):
        raise ValueError("now and switched_at must be timezone-aware")
    if now - switched_at <= grace:
        return False
    for poller in expected:
        beat = heartbeats.get(poller)
        if not isinstance(beat, Heartbeat) or beat.checkout != new:
            return True
    return False
