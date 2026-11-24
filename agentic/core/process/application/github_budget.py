"""`budget_for`: the GitHub account's budget, read through the shared store at most once a
minute (GH-1071). Nothing calls it yet.

Nothing is caught here: the store and the account key already guarantee that nothing they
raise gets past them. `read` must not raise either -- it returns `None` for anything it could
not read; whoever wires `gh api rate_limit` wraps the call so a failure becomes `None`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..domain.github_budget import UNKNOWN, Budget, budget_from, is_fresh
from .ports import BudgetStore


def budget_for(
    store: BudgetStore,
    key: str | None,
    read: Callable[[], object | None],
    now: datetime,
) -> Budget:
    """The budget of account `key`: the stored one while it is fresh, else the one `read`
    returns, when this call wins the claim. A claim already taken within the minute, an
    unreadable result or a result that states no resource leaves the stored budget (or
    `UNKNOWN`) as the answer, and the spent claim keeps the next call from reading."""
    if key is None:
        return UNKNOWN
    stored = store.load(key)
    if stored is not None and is_fresh(stored, now):
        return stored
    claim = store.claim(key, now)
    if claim is None:
        return UNKNOWN if stored is None else stored
    raw = read()
    if raw is None:
        return UNKNOWN
    budget = budget_from(raw, now)
    if all(resource is None for resource in budget.resources.values()):
        return UNKNOWN
    store.put(key, claim, budget)
    return budget
