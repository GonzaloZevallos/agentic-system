"""The scheduling loop and the document it folds (GH-364, ADR-0004).

Two seams: the loop's public function, driven with fake branches and a fake turn, and the
`Documents` decorator, driven through the `TaskDocuments` port it hands to a branch.
"""

import asyncio
import operator
import random
import threading
import time
from collections.abc import Callable

import pytest

from agentic.core.process.application.schedule import FoldingDocuments, run_subtasks
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import (
    REDUCERS,
    add,
    add_flakes,
    add_tracker_failures,
    apply,
    first,
    last,
    merge,
)
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryDocuments, memory_ports

pytestmark = pytest.mark.anyio

WAIT_S = 3.0


def sub(sid: str, *depends_on: str, status: str = "pending") -> dict:
    return {
        "id": sid,
        "title": sid,
        "brief": "b",
        "seams": ["s"],
        "depends_on": list(depends_on),
        "status": status,
    }


def task(*subtasks: dict) -> dict:
    return {
        "id": "GH-1",
        "subtasks": {s["id"]: s for s in subtasks},
        "runs": [],
        "cost_usd": 0.0,
    }


def ready(state, in_flight, limit):
    """A stand-in for `rules.next_to_start`: dispatched, not in flight, up to the bound. It
    ignores `escalated` on purpose: the loop itself must not start anything after one."""
    free = max(0, limit - len(in_flight))
    return [s for s in rules.dispatched_subtasks(state) if s not in in_flight][:free]


class Harness:
    """Fake branches that wait for a gate, save `saves[sid]` times, and end with an outcome."""

    def __init__(self, ids: list[str]) -> None:
        self.events: list[str] = []
        self.started = {sid: asyncio.Event() for sid in ids}
        self.gates = {sid: asyncio.Event() for sid in ids}
        self.outcomes: dict[str, list] = {}
        self.saves: dict[str, int] = {}
        self.turns = 0
        self.turn_active = 0
        self.turn_max_active = 0
        self.starts_during_turn: list[tuple[int, int]] = []
        self.turn_sleep_s = 0.0
        self.turn_patch: Callable[[dict], dict] = lambda folded: {}
        self._lock = threading.Lock()
        self.running = 0
        self.max_running = 0

    def starts(self) -> int:
        return sum(1 for e in self.events if e.startswith("start "))

    async def branch(self, base, ports):
        sid = base["subtask_id"]
        self.events.append(f"start {sid}")
        self.started[sid].set()
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(0.01)
        try:
            await self.gates[sid].wait()
            state = base
            for _ in range(self.saves.get(sid, 0)):
                state = apply(state, {"runs": [{"section": sid}], "cost_usd": 0.5})
                ports.documents.save(state)
                await asyncio.sleep(0)
            outcome = self.outcomes.get(sid, ["approved"])
            status = outcome.pop(0) if len(outcome) > 1 else outcome[0]
            if status == "raise":
                raise RuntimeError(f"{sid} broke")
            patch = {
                "subtasks": {sid: {**state["subtasks"][sid], "status": status}},
                "runs": [{"section": sid}],
                "cost_usd": 0.5,
            }
            if status == "escalate":
                patch["subtasks"][sid]["status"] = "delivered"
                patch["escalated"] = f"{sid} needs a human"
            self.events.append(f"end {sid}")
            return apply(state, patch)
        finally:
            self.running -= 1

    def turn(self, folded, cfg, ports):
        with self._lock:
            self.turns += 1
            self.turn_active += 1
            self.turn_max_active = max(self.turn_max_active, self.turn_active)
        self.events.append("turn")
        before = self.starts()
        time.sleep(self.turn_sleep_s)
        self.starts_during_turn.append((before, self.starts()))
        with self._lock:
            self.turn_active -= 1
        return self.turn_patch(folded)

    def loop(self, state, limit, cfg, rule=ready):
        return asyncio.create_task(
            run_subtasks(state, cfg, memory_ports(), self.branch, self.turn, limit, rule)
        )

    async def started_(self, *ids: str) -> None:
        for sid in ids:
            await asyncio.wait_for(self.started[sid].wait(), WAIT_S)

    async def settle(self, turns: int) -> None:
        async with asyncio.timeout(WAIT_S):
            while self.turns < turns:
                await asyncio.sleep(0.005)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_a_subtask_starts_when_its_dependency_is_approved_not_when_the_wave_ends(cfg):
    h = Harness(["a", "b", "c"])
    run = h.loop(task(sub("a"), sub("b"), sub("c", "a")), 3, cfg)
    await h.started_("a", "b")
    assert not h.started["c"].is_set()

    h.gates["a"].set()
    await h.started_("c")

    # `b`, the unrelated sibling, is still building when `c` starts.
    assert not run.done()
    assert "end b" not in h.events
    h.gates["b"].set()
    h.gates["c"].set()
    result = await asyncio.wait_for(run, WAIT_S)
    assert [s["status"] for s in result["subtasks"].values()] == ["approved"] * 3


async def test_a_delivery_is_reviewed_without_waiting_for_the_siblings(cfg):
    # A delivered subtask is a dispatched one: it starts again (its review) while `b` builds.
    h = Harness(["a", "b"])
    h.outcomes["a"] = ["delivered", "approved"]
    run = h.loop(task(sub("a"), sub("b")), 2, cfg)
    await h.started_("a", "b")

    h.gates["a"].set()
    await asyncio.wait_for(_until(lambda: h.events.count("start a") == 2), WAIT_S)

    assert "end b" not in h.events
    h.gates["b"].set()
    await asyncio.wait_for(run, WAIT_S)


async def test_the_bound_is_never_exceeded(cfg):
    ids = [f"s{n}" for n in range(6)]
    h = Harness(ids)
    for gate in h.gates.values():
        gate.set()

    await asyncio.wait_for(h.loop(task(*[sub(s) for s in ids]), 2, cfg), WAIT_S)

    assert h.starts() == 6
    assert h.max_running == 2


async def test_a_returned_subtask_restarts_only_after_the_turn_ran(cfg):
    h = Harness(["a"])
    h.outcomes["a"] = ["returned", "approved"]
    h.gates["a"].set()

    await asyncio.wait_for(h.loop(task(sub("a")), 1, cfg), WAIT_S)

    assert h.events == ["start a", "end a", "turn", "start a", "end a", "turn"]


async def test_the_turn_runs_once_per_completion(cfg):
    h = Harness(["a", "b", "c"])
    run = h.loop(task(sub("a"), sub("b"), sub("c")), 3, cfg)
    await h.started_("a", "b", "c")

    for n, sid in enumerate(["a", "b", "c"], start=1):
        h.gates[sid].set()
        await h.settle(n)
    await asyncio.wait_for(run, WAIT_S)

    assert h.turns == 3


async def test_branches_finishing_in_one_wake_up_each_get_their_own_turn(cfg):
    h = Harness(["a", "b", "c"])
    h.turn_sleep_s = 0.1
    run = h.loop(task(sub("a"), sub("b"), sub("c")), 3, cfg)
    await h.started_("a", "b", "c")

    h.gates["a"].set()
    await h.settle(1)
    # `b` and `c` both end while the turn after `a` runs, so they are found done together.
    h.gates["b"].set()
    h.gates["c"].set()
    await asyncio.wait_for(run, WAIT_S)

    assert h.turns == 3
    assert h.turn_max_active == 1
    assert h.events.count("turn") == 3


async def test_nothing_starts_while_a_turn_runs_and_turns_never_overlap(cfg):
    h = Harness(["a", "b", "c", "d"])
    h.turn_sleep_s = 0.1
    run = h.loop(task(sub("a"), sub("b"), sub("c", "a"), sub("d", "b")), 4, cfg)
    await h.started_("a", "b")

    h.gates["a"].set()
    await h.settle(1)
    h.gates["b"].set()  # ends while the first turn is still running
    h.gates["c"].set()
    h.gates["d"].set()
    await asyncio.wait_for(run, WAIT_S)

    assert h.starts() == 4
    assert h.turn_max_active == 1
    assert all(before == after for before, after in h.starts_during_turn)


async def test_an_escalation_drains_what_runs_and_starts_nothing_new(cfg):
    h = Harness(["a", "b", "c"])
    h.outcomes["a"] = ["escalate"]
    run = h.loop(task(sub("a"), sub("b"), sub("c", "a")), 3, cfg)
    await h.started_("a", "b")

    h.gates["a"].set()
    await h.settle(1)
    assert not run.done()  # `b` is still running: never cancelled
    h.gates["b"].set()
    result = await asyncio.wait_for(run, WAIT_S)

    assert not h.started["c"].is_set()
    assert result["escalated"] == "a needs a human"
    assert result["subtasks"]["b"]["status"] == "approved"


async def test_a_failing_branch_lets_the_others_drain_and_is_raised(cfg):
    h = Harness(["a", "b", "c"])
    h.outcomes["a"] = ["raise"]
    run = h.loop(task(sub("a"), sub("b"), sub("c")), 2, cfg)
    await h.started_("a", "b")

    h.gates["a"].set()
    await asyncio.sleep(0.05)
    assert not run.done()  # `b` is draining
    h.gates["b"].set()

    with pytest.raises(RuntimeError, match="a broke"):
        await asyncio.wait_for(run, WAIT_S)
    assert "end b" in h.events
    assert not h.started["c"].is_set()
    assert h.turns == 0


async def test_a_failing_turn_is_raised_after_the_branches_drain(cfg):
    h = Harness(["a", "b"])

    def broken(folded):
        raise RuntimeError("merge broke")

    h.turn_patch = broken
    run = h.loop(task(sub("a"), sub("b")), 2, cfg)
    await h.started_("a", "b")
    h.gates["a"].set()
    await asyncio.sleep(0.05)
    h.gates["b"].set()

    with pytest.raises(RuntimeError, match="merge broke"):
        await asyncio.wait_for(run, WAIT_S)
    assert "end b" in h.events


async def test_the_result_is_the_delta_the_parent_folds_once(cfg):
    h = Harness(["a", "b"])
    h.saves["b"] = 2
    for gate in h.gates.values():
        gate.set()
    start = task(sub("a"), sub("b"))

    result = await asyncio.wait_for(h.loop(start, 2, cfg), WAIT_S)

    folded = apply(start, result)
    assert len(folded["runs"]) == 4  # a: 1, b: 2 saved + 1 final
    assert folded["cost_usd"] == pytest.approx(2.0)
    assert "subtask_id" not in result


async def test_the_document_keeps_a_folded_sibling_while_another_saves(cfg):
    inner = MemoryDocuments()
    h = Harness(["a", "b"])
    h.saves["b"] = 2
    run = asyncio.create_task(
        run_subtasks(
            task(sub("a"), sub("b")),
            cfg,
            memory_ports(documents=inner),
            h.branch,
            h.turn,
            2,
            ready,
        )
    )
    await h.started_("a", "b")
    h.gates["a"].set()
    await h.settle(1)

    h.gates["b"].set()
    await asyncio.wait_for(run, WAIT_S)

    during_b = [d for d in inner.saved if d["subtasks"]["b"]["status"] == "pending"]
    assert during_b, "b saved while pending"
    last_save = during_b[-1]
    assert last_save["subtasks"]["a"]["status"] == "approved"
    assert len(last_save["runs"]) == 3  # a's one and b's two, none twice
    assert last_save["cost_usd"] == pytest.approx(1.5)


async def test_the_turn_saves_through_the_decorator_with_the_branches_in_flight(cfg):
    inner = MemoryDocuments()
    h = Harness(["a", "b"])
    h.saves["b"] = 1

    def turn(folded, cfg_, ports):
        ports.documents.save(folded)
        return {}

    run = asyncio.create_task(
        run_subtasks(
            task(sub("a"), sub("b")), cfg, memory_ports(documents=inner), h.branch, turn, 2, ready
        )
    )
    await h.started_("a", "b")
    h.gates["b"].set()
    await asyncio.sleep(0.05)
    h.gates["a"].set()
    await asyncio.wait_for(run, WAIT_S)

    # The turn that followed `b`'s end wrote `b` approved; the document it saved is `a`'s
    # in-flight delta on top of it, never the turn's view alone.
    assert any(d["subtasks"]["b"]["status"] == "approved" for d in inner.saved)


async def _until(condition) -> None:
    while not condition():
        await asyncio.sleep(0.005)


# The decorator, through the `TaskDocuments` port a branch is handed.

PLAIN_KEYS = {"branch", "task_head"}


def value_for(key: str, branch: str, n: int):
    """One patch for `key` written by `branch` for the n-th time. Every `last` and plain
    value is unique (a branch writing a key back to the value it started from is the one
    thing a delta cannot see, here and in `task.delta`), and the dict and list keys carry the
    branch in their keys so that two branches never fight over one entry."""
    reducer = REDUCERS.get(key)
    if reducer is add:
        return 0.25 * (n % 3 + 1)
    if reducer is operator.add:
        return [f"{branch}:{key}:{n}"]
    if reducer is merge or reducer is first:
        return {f"{branch}:{n % 2}": f"{key}:{n}"}
    if reducer is add_flakes:
        return [{"check": f"c{n % 2}", "head": "h", "gate": "g", "tests": [f"{branch}{n}"]}]
    if reducer is add_tracker_failures:
        # Four distinct keys at most, and one `at` per key: the first row seen wins, so a
        # row that differed by branch would depend on which branch landed first.
        target = "comment" if n % 2 else "label"
        return [{"target": target, "cls": "transient", "at": f"{target}-at"}]
    if reducer is last:
        return f"{branch}:{key}:{n}"
    return f"{branch}:{key}:{n}"


def test_every_reducer_key_of_the_state_has_a_generator():
    # A reducer added to `TaskState` that `value_for` cannot generate would make the
    # property below prove nothing about it.
    for key, reducer in REDUCERS.items():
        assert reducer in (
            add,
            operator.add,
            merge,
            first,
            add_flakes,
            add_tracker_failures,
            last,
        ), key


def net(patches: list[dict]) -> dict:
    out: dict = {}
    for p in patches:
        out = apply(out, p)
    return out


@pytest.mark.parametrize("seed", range(40))
def test_the_document_equals_the_sequential_fold_under_random_interleavings(seed):
    rng = random.Random(seed)
    keys = [*REDUCERS, *PLAIN_KEYS]
    inner = MemoryDocuments()
    initial = {"id": "GH-1", "cost_usd": 0.0, "runs": [], "subtasks": {}}
    documents = FoldingDocuments(inner, initial)
    folded_ref = dict(initial)
    started = 0
    branches: dict[str, dict] = {}  # id -> {state, docs, patches, saved}
    for _ in range(60):
        move = rng.choice(["start", "patch", "patch", "save", "save", "complete"])
        if move == "start" and len(branches) < 4:
            bid = f"b{started}"
            started += 1
            base = dict(folded_ref)
            branches[bid] = {
                "state": base,
                "docs": documents.branch(bid, base),
                "patches": [],
                "saved": [],
                "n": 0,
            }
        elif move == "patch" and branches:
            bid = rng.choice(list(branches))
            b = branches[bid]
            patch = {k: value_for(k, bid, b["n"]) for k in rng.sample(keys, rng.randint(1, 6))}
            b["n"] += 1
            b["state"] = apply(b["state"], patch)
            b["patches"].append(patch)
        elif move == "save" and branches:
            bid = rng.choice(list(branches))
            b = branches[bid]
            b["docs"].save(b["state"])
            b["saved"] = list(b["patches"])
            expected = folded_ref
            for other in branches.values():
                expected = apply(expected, net(other["saved"]))
            assert inner.last == expected
        elif move == "complete" and branches:
            bid = rng.choice(list(branches))
            b = branches.pop(bid)
            documents.complete(bid, b["state"])
            folded_ref = apply(folded_ref, net(b["patches"]))
            assert documents.folded == folded_ref


def test_a_branch_saving_twice_replaces_its_delta_and_keeps_a_folded_sibling():
    inner = MemoryDocuments()
    start = task(sub("a"), sub("b"))
    documents = FoldingDocuments(inner, start)
    a_base, b_base = apply(start, {"subtask_id": "a"}), apply(start, {"subtask_id": "b"})
    a_docs, b_docs = documents.branch("a", a_base), documents.branch("b", b_base)
    a_done = apply(
        a_base,
        {
            "subtasks": {"a": {**sub("a"), "status": "approved"}},
            "runs": [{"s": "a"}],
            "cost_usd": 1.0,
        },
    )
    a_docs.save(a_done)
    documents.complete("a", a_done)

    first_save = apply(b_base, {"runs": [{"s": "b1"}], "cost_usd": 0.5})
    b_docs.save(first_save)
    b_docs.save(apply(first_save, {"runs": [{"s": "b2"}], "cost_usd": 0.5}))

    saved = inner.last
    assert saved["subtasks"]["a"]["status"] == "approved"
    assert [r["s"] for r in saved["runs"]] == ["a", "b1", "b2"]
    assert saved["cost_usd"] == pytest.approx(2.0)
    assert "subtask_id" not in saved


def test_the_branch_documents_hand_everything_but_save_to_the_wrapped_port():
    inner = MemoryDocuments()
    start = task(sub("a"))
    docs = FoldingDocuments(inner, start).branch("a", start)

    docs.save(start)

    assert docs.path_for("GH-1") == inner.path_for("GH-1")
    assert docs.ids() == ["GH-1"]
    assert docs.load("GH-1") is not None
    docs.record_closure("GH-1", {"closed_at": "now"})
    assert inner.closures["GH-1"] == {"closed_at": "now"}
