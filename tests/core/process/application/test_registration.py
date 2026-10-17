"""GH-270: the wait for a spawned child to show up in the run registry, shared by the poller
and by the `land` child waiting for the resume it spawned. Against the in-memory registry."""

from __future__ import annotations

from agentic.core.process.application import poll, registration
from agentic.core.process.application.ports import RunRegistration
from tests.fakes import MemoryRunRegistry


def _entry(task_id="GH-5", product="product", kind="role", pid=1) -> RunRegistration:
    return RunRegistration(pid=pid, host="h", product=product, task_id=task_id, kind=kind)


def _wait(entries, *, timeout_s=3.0, **match):
    slept: list[float] = []
    found = registration.await_registration(
        MemoryRunRegistry(entries), "GH-5", timeout_s, slept.append, **match
    )
    return found, slept


def test_with_no_kind_and_no_product_it_matches_the_task_id_alone():
    # Today's behaviour, byte for byte: the poller's call sites pass neither.
    for kind in ("role", "merge", "land_checks", "land_model"):
        assert _wait([_entry(kind=kind, product="anything")]) == (True, [])


def test_with_no_match_it_sleeps_the_whole_timeout_in_poll_steps_and_gives_up():
    found, slept = _wait([_entry(task_id="GH-6")], timeout_s=3.0)
    assert found is False
    assert slept == [registration.REGISTER_POLL_INTERVAL_S] * 3


def test_a_kind_rejects_an_entry_of_the_same_task_under_another_kind():
    assert _wait([_entry(kind="land_model")], kind="role")[0] is False
    assert _wait([_entry(kind="role")], kind="role")[0] is True


def test_a_product_rejects_the_same_task_id_in_another_product():
    assert _wait([_entry(product="other")], product="product")[0] is False
    assert _wait([_entry(product="product")], product="product")[0] is True


def test_it_registers_late_when_the_entry_appears_mid_wait():
    registry = MemoryRunRegistry([])
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        registry.entries.append(_entry())

    found = registration.await_registration(
        registry, "GH-5", 10.0, sleep, kind="role", product="product"
    )
    assert found is True and len(slept) == 1


def test_the_poller_keeps_the_names_it_always_had():
    assert poll.REGISTER_WAIT_S == registration.REGISTER_WAIT_S == 60.0
