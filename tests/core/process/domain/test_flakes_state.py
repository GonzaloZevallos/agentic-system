"""The `flakes` field of the task state and its reducer (ADR-0066)."""

from agentic.core.process.domain.task import REDUCERS, add_flakes, apply, delta


def record(check="tests", head="h1", gate="delivery", tests=()):
    return {"check": check, "head": head, "gate": gate, "tests": list(tests)}


def test_flakes_is_reduced_by_add_flakes():
    assert REDUCERS["flakes"] is add_flakes


def test_add_flakes_is_idempotent():
    once = add_flakes([record(tests=["a"])], [record(check="lint", tests=["b"])])
    assert add_flakes(once, once) == once
    assert add_flakes(once, [record(tests=["a"])]) == once


def test_add_flakes_is_commutative():
    left = [record(tests=["a", "b"]), record(check="lint", head="h2")]
    right = [record(tests=["b", "c"]), record(gate="merge")]
    assert add_flakes(left, right) == add_flakes(right, left)


def test_the_same_flake_seen_by_two_branches_is_one_record():
    branch_a = [record(tests=["a"])]
    branch_b = [record(tests=["a"])]
    assert add_flakes(add_flakes([], branch_a), branch_b) == [record(tests=["a"])]


def test_the_key_is_check_head_and_gate():
    merged = add_flakes([record()], [record(head="h2"), record(gate="merge"), record(check="x")])
    assert len(merged) == 4


def test_tests_join_as_a_sorted_set():
    merged = add_flakes([record(tests=["b", "a"])], [record(tests=["a", "c"])])
    assert merged[0]["tests"] == ["a", "b", "c"]


def test_tests_are_capped_at_fifty():
    many = [f"t{n:02d}" for n in range(80)]
    assert add_flakes([], [record(tests=many)])[0]["tests"] == many[:50]
    assert add_flakes([record(tests=many[:30])], [record(tests=many[30:])])[0]["tests"] == many[:50]


def test_the_output_is_sorted_by_key():
    merged = add_flakes([record(check="z")], [record(check="a"), record(check="m")])
    assert [r["check"] for r in merged] == ["a", "m", "z"]


def test_an_absent_side_is_tolerated():
    assert add_flakes([], []) == []
    assert add_flakes([record()], []) == [record()]


def test_delta_and_apply_round_trip_with_flakes_present():
    before = {"id": "GH-1", "flakes": add_flakes([], [record(tests=["a"])])}
    after = {
        "id": "GH-1",
        "flakes": add_flakes(before["flakes"], [record(tests=["b"]), record(check="lint")]),
    }
    patch = delta(before, after)
    assert apply(before, patch) == after


def test_delta_sends_only_new_or_grown_records():
    before = {"flakes": add_flakes([], [record(tests=["a"]), record(check="lint")])}
    after = {"flakes": add_flakes(before["flakes"], [record(tests=["b"]), record(check="web")])}
    sent = delta(before, after)["flakes"]
    assert sorted(r["check"] for r in sent) == ["tests", "web"]
    assert apply(before, delta(before, after)) == after


def test_delta_sends_nothing_when_flakes_did_not_change():
    state = {"flakes": [record()]}
    assert "flakes" not in delta(state, {"flakes": [record()]})


def test_delta_takes_the_field_as_it_is_when_the_branch_started_without_it():
    after = {"flakes": [record(tests=["a"])]}
    assert apply({}, delta({}, after)) == after
