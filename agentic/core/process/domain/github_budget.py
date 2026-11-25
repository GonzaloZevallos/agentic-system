"""The GitHub account's request budget, read from a document or from response headers.

Pure: no clock, environment, file or subprocess; `now` is always a parameter. The budget
is advisory: unknown or expired means the delivery may start (`can_start`). Every public
function validates `now` through `_utc`; no other input raises, a bad value only makes
the resource it belongs to unknown.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Resource = Literal["core", "graphql", "search"]

# In name order: `can_start` breaks a tie between equal resets with the earlier one.
RESOURCES: tuple[Resource, ...] = ("core", "graphql", "search")

BUDGET_TTL = timedelta(seconds=60)

_HEADER_PREFIX = "x-ratelimit-"
_MAX_DIGITS = 19  # an epoch or a count never needs more; bounds `int(str)`.


@dataclass(frozen=True)
class ResourceBudget:
    remaining: int
    reset: datetime


@dataclass(frozen=True)
class Budget:
    """What each resource has left, as of `fetched_at`; `None` is a resource not known."""

    resources: Mapping[Resource, ResourceBudget | None]
    fetched_at: datetime | None


UNKNOWN = Budget({resource: None for resource in RESOURCES}, None)


@dataclass(frozen=True)
class Start:
    """The delivery may start."""


@dataclass(frozen=True)
class Wait:
    """The delivery waits until `resource` resets at `resets_at` (aware UTC)."""

    resource: Resource
    resets_at: datetime


# A header name that two keys spell with different values: the name cannot be read.
_UNREADABLE = object()


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be an aware UTC datetime")
    return now


def _digits(value: object) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdigit():
        if len(value) <= _MAX_DIGITS:
            return int(value)
    return None


def _remaining(value: object, from_headers: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return _digits(value) if from_headers else None


def _epoch(value: object, from_headers: bool) -> datetime | None:
    seconds: float | None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, float):
        seconds = value if math.isfinite(value) else None
    else:
        seconds = _digits(value) if from_headers else None
    if seconds is None or seconds < 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _resource_budget(remaining: object, reset: object, from_headers: bool) -> ResourceBudget | None:
    count = _remaining(remaining, from_headers)
    at = _epoch(reset, from_headers)
    if count is None or at is None:
        return None
    return ResourceBudget(count, at)


def _resource(value: object) -> Resource | None:
    for resource in RESOURCES:
        if value == resource:
            return resource
    return None


def _lowercased(headers: Mapping[object, object]) -> dict[str, object]:
    """The `str` keys lowercased; other keys are ignored. A name spelled by two keys with
    different values maps to `_UNREADABLE`, so it is present but never read."""
    view: dict[str, object] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        name = key.lower()
        if name in view and view[name] != value:
            view[name] = _UNREADABLE
        else:
            view.setdefault(name, value)
    return view


def _from_lowercased(view: Mapping[str, object], now: datetime) -> Budget:
    resource = _resource(view.get(_HEADER_PREFIX + "resource"))
    if resource is None:
        return UNKNOWN
    read = _resource_budget(
        view.get(_HEADER_PREFIX + "remaining"), view.get(_HEADER_PREFIX + "reset"), True
    )
    return Budget({r: read if r == resource else None for r in RESOURCES}, now)


def budget_from_document(doc: Mapping[str, object], now: datetime) -> Budget:
    """The budget of a `gh api rate_limit` document: `doc['resources'][r]` for each
    resource, the others ignored."""
    now = _utc(now)
    resources = doc.get("resources")
    read: dict[Resource, ResourceBudget | None] = {}
    for resource in RESOURCES:
        entry = resources.get(resource) if isinstance(resources, Mapping) else None
        read[resource] = (
            _resource_budget(entry.get("remaining"), entry.get("reset"), False)
            if isinstance(entry, Mapping)
            else None
        )
    return Budget(read, now)


def budget_from_headers(headers: Mapping[object, object], now: datetime) -> Budget:
    """The budget one response's `x-ratelimit-*` headers state, for the resource
    `x-ratelimit-resource` names; without a listed resource it is `UNKNOWN`."""
    now = _utc(now)
    return _from_lowercased(_lowercased(headers), now)


def budget_from(source: object, now: datetime) -> Budget:
    """A document (a mapping with `resources` and no `x-ratelimit-*` key) or headers (a
    mapping with such a key and no `resources`); anything else is `UNKNOWN`."""
    now = _utc(now)
    if not isinstance(source, Mapping):
        return UNKNOWN
    view = _lowercased(source)
    is_headers = any(name.startswith(_HEADER_PREFIX) for name in view)
    is_document = "resources" in source
    if is_headers and not is_document:
        return _from_lowercased(view, now)
    if is_document and not is_headers:
        return budget_from_document(source, now)
    return UNKNOWN


def is_fresh(budget: Budget, now: datetime) -> bool:
    """The budget was fetched, not after `now`, and under `BUDGET_TTL` ago."""
    fetched_at = budget.fetched_at
    return fetched_at is not None and fetched_at <= now and now - fetched_at < BUDGET_TTL


def can_start(budget: Budget, needed: Mapping[Resource, int], now: datetime) -> Start | Wait:
    """`Start` when the budget is unknown, expired or enough; otherwise `Wait` until the
    latest reset among the resources that are short."""
    now = _utc(now)
    if not is_fresh(budget, now):
        return Start()
    wait: Wait | None = None
    for resource in RESOURCES:
        known = budget.resources.get(resource)
        if resource not in needed or known is None:
            continue
        if known.reset > now and known.remaining < needed[resource]:
            if wait is None or known.reset > wait.resets_at:
                wait = Wait(resource, known.reset)
    return wait if wait is not None else Start()


def _takes(stored: ResourceBudget | None, observed: ResourceBudget | None) -> bool:
    if stored is None or observed is None:
        return False
    return observed.reset > stored.reset or (
        observed.reset == stored.reset and observed.remaining <= stored.remaining
    )


def merged(stored: Budget, observed: Budget) -> Budget:
    """`stored` refreshed by one response's `observed` budget. A resource is taken from
    `observed` only when both hold it and the observed reset is later than the stored one,
    or equal to it with a remaining that is not higher: a late response never raises a count
    inside its window. A resource `stored` does not hold is ignored, and `stored.fetched_at`
    stays, since only a `rate_limit` read says when the whole budget was fetched."""
    taken = {
        resource: (
            observed.resources.get(resource)
            if _takes(stored.resources.get(resource), observed.resources.get(resource))
            else stored.resources.get(resource)
        )
        for resource in RESOURCES
    }
    return Budget(taken, stored.fetched_at)


# What a delivery or a split asks of the account before its first GitHub write. Declared, not
# measured: a margin, not a count. Search is never charged. These are the only copy.
DELIVER_COST: Mapping[Resource, int] = {"core": 30, "graphql": 30}


def split_cost(parts_left: int) -> Mapping[Resource, int]:
    """What a split asks of the account: 10 plus 5 per part not yet created (a negative count
    is 0), on core and on graphql, never on search. Declared, not measured."""
    cost = 10 + 5 * max(parts_left, 0)
    return {"core": cost, "graphql": cost}
