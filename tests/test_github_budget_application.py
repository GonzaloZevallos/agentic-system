"""`budget_for` (agentic.core.process.application.github_budget): the account budget read
through the store, at most once a minute, whoever asks."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.budget_store import FilesystemBudgetStore
from agentic.core.process.application.github_budget import budget_for
from agentic.core.process.domain.github_budget import (
    BUDGET_TTL,
    UNKNOWN,
    Budget,
    ResourceBudget,
    budget_from,
)

KEY = "github.com-0123456789abcdef"
NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp())


def at(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


def entry(remaining: int = 100) -> dict[str, int]:
    return {"remaining": remaining, "reset": EPOCH + 3600}


def valid_document() -> dict[str, object]:
    return {"resources": {"core": entry(), "graphql": entry(50), "search": entry(5)}}


@dataclass
class FakeStore:
    """A BudgetStore that follows the port's claim and put rules, in memory."""

    read_at: dict[str, datetime] = field(default_factory=dict)
    budgets: dict[str, Budget] = field(default_factory=dict)
    puts: list[tuple[str, datetime, Budget]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def load(self, key: str) -> Budget | None:
        self.calls.append("load")
        return self.budgets.get(key)

    def claim(self, key: str, now: datetime) -> datetime | None:
        self.calls.append("claim")
        taken = self.read_at.get(key)
        if taken is not None and taken <= now and now - taken < BUDGET_TTL:
            return None
        instant = now.replace(microsecond=0)
        self.read_at[key] = instant
        self.budgets.setdefault(key, UNKNOWN)
        return instant

    def put(self, key: str, claim: datetime, budget: Budget) -> None:
        self.calls.append("put")
        self.puts.append((key, claim, budget))
        taken = self.read_at.get(key)
        if taken is not None and taken > claim:
            return
        self.read_at[key] = claim
        self.budgets[key] = budget


class Reader:
    """A `read` that counts its calls and answers what it was given."""

    def __init__(self, answer: object | None = None, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def __call__(self) -> object | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.answer


def test_a_none_key_is_unknown_without_a_read_or_a_store_call() -> None:
    store, read = FakeStore(), Reader(valid_document())

    assert budget_for(store, None, read, NOW) is UNKNOWN
    assert read.calls == 0
    assert store.calls == []


def test_a_fresh_stored_budget_is_returned_without_a_read() -> None:
    store, read = FakeStore(), Reader(valid_document())
    stored = budget_from(valid_document(), at(-10))
    store.budgets[KEY] = stored

    assert budget_for(store, KEY, read, NOW) is stored
    assert read.calls == 0
    assert "claim" not in store.calls


def test_a_stored_budget_fetched_in_the_future_is_stale_so_it_is_read() -> None:
    store, read = FakeStore(), Reader(valid_document())
    store.budgets[KEY] = budget_from(valid_document(), at(30))

    result = budget_for(store, KEY, read, NOW)

    assert read.calls == 1
    assert result == budget_from(valid_document(), NOW)


def test_two_calls_within_a_minute_read_once_and_a_call_after_it_reads_again() -> None:
    store, read = FakeStore(), Reader(valid_document())

    first = budget_for(store, KEY, read, NOW)
    second = budget_for(store, KEY, read, at(59))
    assert (read.calls, second) == (1, first)

    budget_for(store, KEY, read, at(61))
    assert read.calls == 2


def test_a_read_that_returns_none_is_unknown_and_the_minute_is_spent() -> None:
    store, read = FakeStore(), Reader(None)

    assert budget_for(store, KEY, read, NOW) is UNKNOWN
    assert budget_for(store, KEY, read, at(30)) is UNKNOWN
    assert read.calls == 1
    assert store.puts == []


def test_a_read_that_raises_propagates_and_the_minute_is_spent() -> None:
    store, read = FakeStore(), Reader(error=RuntimeError("gh is gone"))

    with pytest.raises(RuntimeError, match="gh is gone"):
        budget_for(store, KEY, read, NOW)
    assert budget_for(store, KEY, read, at(30)) is UNKNOWN
    assert read.calls == 1


def test_a_spent_minute_returns_the_stored_budget_when_the_read_raised() -> None:
    store, read = FakeStore(), Reader(error=RuntimeError("gh is gone"))
    stored = budget_from(valid_document(), at(-61))
    store.budgets[KEY] = stored

    with pytest.raises(RuntimeError):
        budget_for(store, KEY, read, NOW)

    assert budget_for(store, KEY, read, at(30)) is stored
    assert read.calls == 1


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(42, id="not a mapping"),
        pytest.param(
            {"x-ratelimit-resource": "core", "x-ratelimit-remaining": "1", **valid_document()},
            id="headers and a resources key",
        ),
        pytest.param(
            {"resources": {"core": {"remaining": -1}, "graphql": "x", "search": None}},
            id="every resource invalid",
        ),
    ],
)
def test_malformed_read_output_is_unknown_without_a_put_and_keeps_the_stored_budget(
    raw: object,
) -> None:
    store, read = FakeStore(), Reader(raw)
    stored = budget_from(valid_document(), at(-120))
    store.budgets[KEY] = stored

    assert budget_for(store, KEY, read, NOW) is UNKNOWN
    assert store.puts == []
    assert store.load(KEY) is stored
    assert read.calls == 1


def test_a_partial_result_is_put_once_with_the_claim_and_returned() -> None:
    store = FakeStore()
    raw: Mapping[str, object] = {"resources": {"core": entry(7)}}
    read = Reader(raw)
    now = NOW + timedelta(milliseconds=500)

    result = budget_for(store, KEY, read, now)

    assert result == budget_from(raw, now)
    assert result.resources["core"] == ResourceBudget(7, at(3600))
    assert result.resources["graphql"] is None
    assert store.puts == [(KEY, NOW, result)]


def test_a_future_fetched_at_with_a_recent_read_at_is_returned_without_a_read() -> None:
    store, read = FakeStore(), Reader(valid_document())
    stored = budget_from(valid_document(), at(30))
    store.budgets[KEY] = stored
    store.read_at[KEY] = at(-10)

    assert budget_for(store, KEY, read, NOW) is stored
    assert read.calls == 0
    assert store.puts == []


def test_two_store_instances_on_one_directory_read_once_a_minute(tmp_path: Path) -> None:
    first, second = FilesystemBudgetStore(tmp_path), FilesystemBudgetStore(tmp_path)
    read = Reader(valid_document())

    one = budget_for(first, KEY, read, NOW)
    two = budget_for(second, KEY, read, at(10))

    assert read.calls == 1
    assert one == budget_from(valid_document(), NOW)
    assert two == one
