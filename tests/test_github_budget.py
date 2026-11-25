"""The GitHub account budget (agentic.core.process.domain.github_budget): what a document or
a response's headers state, and whether a delivery may start on it."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentic.core.process.domain.github_budget import (
    BUDGET_TTL,
    DELIVER_COST,
    UNKNOWN,
    Budget,
    Resource,
    ResourceBudget,
    Start,
    Wait,
    budget_from,
    budget_from_document,
    budget_from_headers,
    can_start,
    is_fresh,
    merged,
    split_cost,
)

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp())


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(EPOCH + seconds, tz=UTC)


def doc(**resources: object) -> dict[str, object]:
    return {"resources": resources}


def entry(remaining: object, reset: object) -> dict[str, object]:
    return {"remaining": remaining, "reset": reset}


def all_none(fetched_at: datetime | None = NOW) -> Budget:
    return Budget({"core": None, "graphql": None, "search": None}, fetched_at)


def test_a_full_document_is_read_for_the_three_resources_and_ignores_the_others():
    document = {
        "resources": {
            "core": {"limit": 5000, "used": 1, "remaining": 4999, "reset": EPOCH + 100},
            "graphql": {"remaining": 0, "reset": EPOCH + 200},
            "search": {"remaining": 30, "reset": EPOCH + 300},
            "code_search": {"remaining": 1, "reset": EPOCH + 400},
        },
        "rate": {"remaining": 1, "reset": EPOCH},
    }
    assert budget_from_document(document, NOW) == Budget(
        {
            "core": ResourceBudget(4999, at(100)),
            "graphql": ResourceBudget(0, at(200)),
            "search": ResourceBudget(30, at(300)),
        },
        NOW,
    )


@pytest.mark.parametrize(
    "bad_entry",
    [
        entry(1e20, EPOCH),
        entry(-1, EPOCH),
        entry(float("nan"), EPOCH),
        entry(float("inf"), EPOCH),
        entry(True, EPOCH),
        entry(5.0, EPOCH),
        entry("5", EPOCH),  # a digit string is for headers only
        entry(5, str(EPOCH)),
        entry(5, 1e20),
        entry(5, -1),
        entry(5, float("nan")),
        entry(5, float("inf")),
        entry(5, True),
        entry(5, 10**30),
        entry(5, None),
        entry(None, EPOCH),
        {"remaining": 5},
        {},
        "core",
        None,
        7,
    ],
)
def test_a_malformed_resource_is_none_and_never_raises(bad_entry: object):
    budget = budget_from_document(
        doc(core=bad_entry, graphql=entry(3, EPOCH + 1), search=entry(4, EPOCH + 2)), NOW
    )
    assert budget == Budget(
        {
            "core": None,
            "graphql": ResourceBudget(3, at(1)),
            "search": ResourceBudget(4, at(2)),
        },
        NOW,
    )


def test_a_float_reset_in_a_document_is_accepted():
    budget = budget_from_document(doc(core=entry(3, EPOCH + 0.5)), NOW)
    assert budget.resources["core"] == ResourceBudget(3, at(0) + timedelta(seconds=0.5))


def test_a_document_with_every_resource_malformed_is_not_unknown():
    budget = budget_from_document(doc(core=1, graphql="x", search=None), NOW)
    assert budget == all_none(NOW)
    assert budget != UNKNOWN


@pytest.mark.parametrize("document", [{}, {"resources": None}, {"resources": "core"}])
def test_a_document_without_resources_has_every_resource_none(document: dict[str, object]):
    assert budget_from_document(document, NOW) == all_none(NOW)


def headers(resource: object = "core", remaining: object = "7", reset: object = None):
    result: dict[object, object] = {
        "x-ratelimit-remaining": remaining,
        "x-ratelimit-reset": str(EPOCH + 90) if reset is None else reset,
    }
    if resource is not None:
        result["x-ratelimit-resource"] = resource
    return result


def test_headers_are_read_with_mixed_case_names_into_the_named_resource():
    mixed = {
        "X-RateLimit-Resource": "graphql",
        "X-Ratelimit-Remaining": "7",
        "x-RATELIMIT-reset": str(EPOCH + 90),
        "Content-Type": "application/json",
    }
    assert budget_from_headers(mixed, NOW) == Budget(
        {"core": None, "graphql": ResourceBudget(7, at(90)), "search": None}, NOW
    )


def test_headers_hold_numbers_as_well_as_digit_strings():
    budget = budget_from_headers(headers(remaining=7, reset=EPOCH + 90), NOW)
    assert budget.resources["core"] == ResourceBudget(7, at(90))


def test_headers_without_a_resource_header_are_unknown_and_never_core():
    assert budget_from_headers(headers(resource=None), NOW) == UNKNOWN


@pytest.mark.parametrize("resource", ["code_search", "Core", "", 1, b"core"])
def test_headers_naming_an_unlisted_resource_are_unknown(resource: object):
    assert budget_from_headers(headers(resource=resource), NOW) == UNKNOWN


def test_conflicting_resource_headers_are_unknown():
    conflicting = {**headers("core"), "X-RateLimit-Resource": "search"}
    assert budget_from_headers(conflicting, NOW) == UNKNOWN


def test_conflicting_remaining_headers_make_only_that_resource_none():
    conflicting = {**headers("core"), "X-RateLimit-Remaining": "8"}
    assert budget_from_headers(conflicting, NOW) == all_none(NOW)


def test_an_equal_value_duplicate_is_read():
    duplicate = {**headers("core"), "X-RateLimit-Remaining": "7"}
    assert budget_from_headers(duplicate, NOW).resources["core"] == ResourceBudget(7, at(90))


def test_a_non_str_key_is_ignored():
    odd = {**headers("core"), 5: "x", b"x-ratelimit-resource": "search"}
    assert budget_from_headers(odd, NOW).resources["core"] == ResourceBudget(7, at(90))


@pytest.mark.parametrize(
    "bad",
    ["5, 6", "", "-1", "1.5", " 7", "²", "٣", "9" * 30, None, True, 7.0],
)
def test_a_malformed_remaining_header_makes_the_resource_none(bad: object):
    assert budget_from_headers(headers(remaining=bad), NOW) == all_none(NOW)


@pytest.mark.parametrize("bad", ["5, 6", "", "-1", "1e9", "²", "9" * 30, "9" * 19, None])
def test_a_malformed_reset_header_makes_the_resource_none(bad: object):
    budget = budget_from_headers({**headers(), "x-ratelimit-reset": bad}, NOW)
    assert budget == all_none(NOW)


def test_dispatch_reads_a_document():
    source = doc(core=entry(3, EPOCH + 5))
    assert budget_from(source, NOW) == Budget(
        {"core": ResourceBudget(3, at(5)), "graphql": None, "search": None}, NOW
    )


def test_dispatch_reads_headers_with_a_mixed_case_prefix():
    source = {
        "X-RateLimit-Resource": "search",
        "X-RateLimit-Remaining": "2",
        "X-RateLimit-Reset": str(EPOCH + 9),
    }
    assert budget_from(source, NOW) == Budget(
        {"core": None, "graphql": None, "search": ResourceBudget(2, at(9))}, NOW
    )


def test_dispatch_of_both_is_unknown():
    assert budget_from({**doc(core=entry(3, EPOCH)), **headers()}, NOW) == UNKNOWN


def test_dispatch_of_both_with_a_mixed_case_prefix_is_unknown():
    assert budget_from({**doc(core=entry(3, EPOCH)), "X-RateLimit-Reset": "1"}, NOW) == UNKNOWN


def test_dispatch_of_neither_is_unknown():
    assert budget_from({"other": 1}, NOW) == UNKNOWN
    assert budget_from({}, NOW) == UNKNOWN


@pytest.mark.parametrize("source", ['{"resources": {}}', b'{"resources": {}}', None, 5, [1]])
def test_dispatch_of_a_non_mapping_is_unknown(source: object):
    assert budget_from(source, NOW) == UNKNOWN


def test_a_resources_key_in_another_case_is_not_a_document():
    assert budget_from({"Resources": {"core": entry(1, EPOCH)}}, NOW) == UNKNOWN


def budget(
    core: tuple[int, int] | None = None,
    graphql: tuple[int, int] | None = None,
    search: tuple[int, int] | None = None,
    fetched_at: datetime | None = NOW,
) -> Budget:
    def made(pair: tuple[int, int] | None) -> ResourceBudget | None:
        return None if pair is None else ResourceBudget(pair[0], at(pair[1]))

    return Budget(
        {"core": made(core), "graphql": made(graphql), "search": made(search)}, fetched_at
    )


def test_an_unknown_budget_lets_the_delivery_start():
    assert can_start(UNKNOWN, {"core": 10}, NOW) == Start()


def test_a_budget_expired_at_exactly_the_ttl_lets_the_delivery_start():
    short = budget(core=(0, 500))
    assert BUDGET_TTL == timedelta(seconds=60)
    assert can_start(short, {"core": 1}, NOW + timedelta(seconds=59)) == Wait("core", at(500))
    assert can_start(short, {"core": 1}, NOW + BUDGET_TTL) == Start()


def test_a_budget_fetched_in_the_future_lets_the_delivery_start():
    future = budget(core=(0, 500), fetched_at=NOW + timedelta(seconds=1))
    assert can_start(future, {"core": 1}, NOW) == Start()


def test_a_short_resource_waits_until_its_reset_in_aware_utc():
    result = can_start(budget(core=(2, 30)), {"core": 3}, NOW)
    assert result == Wait("core", at(30))
    assert isinstance(result, Wait)
    assert result.resets_at.utcoffset() == timedelta(0)


def test_an_enough_resource_lets_the_delivery_start():
    assert can_start(budget(core=(3, 30)), {"core": 3}, NOW) == Start()


@pytest.mark.parametrize("reset", [-5, 0])
def test_a_resource_past_or_at_its_reset_lets_the_delivery_start(reset: int):
    assert can_start(budget(core=(0, reset)), {"core": 1}, NOW) == Start()


def test_an_unknown_needed_resource_lets_the_delivery_start():
    assert can_start(budget(graphql=(0, 30)), {"core": 1}, NOW) == Start()


def test_a_resource_not_needed_is_not_collected():
    assert can_start(budget(graphql=(0, 30)), {}, NOW) == Start()


def test_a_needed_amount_of_zero_passes():
    assert can_start(budget(core=(0, 30)), {"core": 0}, NOW) == Start()


def test_the_latest_reset_wins_among_short_resources():
    short = budget(core=(0, 10), graphql=(0, 40), search=(0, 20))
    assert can_start(short, {"core": 1, "graphql": 1, "search": 1}, NOW) == Wait("graphql", at(40))


def test_the_alphabetically_first_resource_wins_a_tie_between_equal_resets():
    tied = budget(core=(0, 30), graphql=(0, 30), search=(0, 30))
    assert can_start(tied, {"search": 1, "graphql": 1, "core": 1}, NOW) == Wait("core", at(30))
    assert can_start(tied, {"search": 1, "graphql": 1}, NOW) == Wait("graphql", at(30))


def test_a_naive_now_raises_in_every_public_function():
    naive = datetime(2026, 11, 23, 12, 0, 0)
    for call in (
        lambda: budget_from(doc(), naive),
        lambda: budget_from(None, naive),
        lambda: budget_from_document(doc(), naive),
        lambda: budget_from_headers(headers(), naive),
        lambda: can_start(UNKNOWN, {}, naive),
    ):
        with pytest.raises(ValueError):
            call()


def test_a_non_utc_now_raises_even_when_it_is_the_same_instant():
    plus_one = NOW.astimezone(timezone(timedelta(hours=1)))
    with pytest.raises(ValueError):
        budget_from(doc(), plus_one)
    with pytest.raises(ValueError):
        can_start(UNKNOWN, {}, plus_one)


class Unreadable(Mapping[object, object]):
    """A mapping that fails when read, so only the order of the checks decides what raises."""

    def __getitem__(self, key: object) -> object:
        raise RuntimeError("read")

    def __iter__(self) -> Iterator[object]:
        raise RuntimeError("read")

    def __len__(self) -> int:
        raise RuntimeError("read")


def test_now_is_validated_before_the_headers_are_read():
    naive = datetime(2026, 11, 23, 12, 0, 0)
    with pytest.raises(ValueError):
        budget_from_headers(Unreadable(), naive)


def test_is_fresh_needs_a_fetched_at() -> None:
    assert not is_fresh(UNKNOWN, NOW)


def test_is_fresh_refuses_a_fetched_at_in_the_future() -> None:
    assert not is_fresh(Budget({}, at(1)), NOW)


def test_is_fresh_refuses_a_budget_exactly_the_ttl_old() -> None:
    assert not is_fresh(Budget({}, NOW - BUDGET_TTL), NOW)


def test_is_fresh_accepts_a_budget_just_under_the_ttl_old() -> None:
    assert is_fresh(Budget({}, NOW - BUDGET_TTL + timedelta(seconds=1)), NOW)
    assert is_fresh(Budget({}, NOW), NOW)


# --- merged -----------------------------------------------------------------------------


def one(resource: Resource, remaining: int, reset: int, fetched_at: datetime | None = NOW) -> Budget:
    resources: dict[Resource, ResourceBudget | None] = {"core": None, "graphql": None, "search": None}
    resources[resource] = ResourceBudget(remaining, at(reset))
    return Budget(resources, fetched_at)


def test_merged_ignores_a_higher_count_in_the_same_window() -> None:
    stored = one("core", 100, 600)
    assert merged(stored, one("core", 101, 600)) == stored


def test_merged_takes_a_lower_count_in_the_same_window() -> None:
    assert merged(one("core", 100, 600), one("core", 99, 600)).resources["core"] == (
        ResourceBudget(99, at(600))
    )


def test_merged_takes_an_equal_count_in_the_same_window() -> None:
    stored = one("core", 100, 600)
    assert merged(stored, one("core", 100, 600)) == stored


def test_merged_takes_a_new_window_even_with_a_higher_count() -> None:
    assert merged(one("core", 5, 600), one("core", 4000, 4200)).resources["core"] == (
        ResourceBudget(4000, at(4200))
    )


def test_merged_ignores_an_older_reset() -> None:
    stored = one("core", 100, 600)
    assert merged(stored, one("core", 1, 300)) == stored


def test_merged_ignores_a_resource_the_store_does_not_hold() -> None:
    stored = one("core", 100, 600)
    result = merged(stored, one("graphql", 10, 600))
    assert result == stored
    assert result.resources["graphql"] is None


def test_merged_keeps_the_stored_resource_when_the_observed_one_is_unknown() -> None:
    stored = one("core", 100, 600)
    assert merged(stored, all_none()) == stored
    assert merged(stored, UNKNOWN) == stored


def test_merged_only_changes_the_resource_it_observed() -> None:
    stored = Budget(
        {"core": ResourceBudget(100, at(600)), "graphql": ResourceBudget(50, at(600)), "search": None},
        NOW,
    )
    result = merged(stored, one("core", 90, 600))
    assert result.resources == {
        "core": ResourceBudget(90, at(600)),
        "graphql": ResourceBudget(50, at(600)),
        "search": None,
    }


def test_merged_keeps_the_stored_fetched_at() -> None:
    stored = one("core", 100, 600, fetched_at=at(-30))
    assert merged(stored, one("core", 90, 600, fetched_at=at(0))).fetched_at == at(-30)
    assert merged(one("core", 100, 600, fetched_at=None), one("core", 90, 600)).fetched_at is None


# --- the declared costs -----------------------------------------------------------------


def test_deliver_cost_is_declared_for_core_and_graphql_and_never_search() -> None:
    assert dict(DELIVER_COST) == {"core": 30, "graphql": 30}


@pytest.mark.parametrize("parts_left", [0, 2, -1])
def test_split_cost_is_ten_plus_five_per_part_left(parts_left: int) -> None:
    charge = 10 + 5 * max(parts_left, 0)
    assert dict(split_cost(parts_left)) == {"core": charge, "graphql": charge}


def test_split_cost_counts_a_negative_as_zero() -> None:
    assert split_cost(-5) == split_cost(0)
