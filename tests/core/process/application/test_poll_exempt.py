"""GH-263, subtask poller: `run_once` over the children that spend no model session. A
merge or checks-only land child takes no slot of `AGENTIC_MAX_CONCURRENT_RUNS`, at most one
is live on the machine, and none starts for a task that already has a live child; every
merge or land the plan holds back is a `deferred` line. On in-memory ports and the registry
and launcher doubles."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from agentic.core.process.application import landing, poll
from agentic.core.process.application.ports import (
    IssueComment,
    IssueState,
    RunRegistration,
    Upstream,
)
from agentic.core.process.domain import document, exempt_queue, rules
from tests.core.process.application.test_land_run_kind import _failed_local_check_after_a_rerun
from tests.core.process.application.test_poll import (
    ESCALATED_AT,
    _cfg,
    _Locks,
    _no_sleep,
    _round,
    _seed_body,
)
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    _HeldLocks,
    _merge_argv,
    _ports,
    _unapproved,
)
from tests.core.process.application.test_poll_scratch import _free
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryScratch, MemoryStack

NO_WORK = "agentic: poll: nothing to do this round"
LIVE = frozenset({"rerun", "fix", "re_review"})


def _child(kind: rules.RunKind, task_id: str, product: str = "product", pid: int = 1):
    return RunRegistration(pid=pid, host="h", product=product, task_id=task_id, kind=kind)


def _roles() -> list[RunRegistration]:
    return [_child("role", "GH-1"), _child("role", "GH-2", pid=2)]


def _product_of(ports):
    assert isinstance(ports.stack, MemoryStack)
    return ports.stack.product()


def test_a_ready_merge_is_spawned_though_two_role_runs_hold_both_slots(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()

    lines = _round(
        _cfg(tmp_path, max_concurrent_runs=2),
        ports,
        MemoryRunRegistry(_roles()),
        launcher,
        ESCALATED_AT,
    )

    assert launcher.spawned == [_merge_argv(5, tmp_path)]
    assert lines == ["agentic: poll: merge #5 -> pid 4242"]


def test_the_roles_are_unchanged_beside_the_merge_when_a_slot_is_free(tmp_path):
    ports = _ports(5)
    ports.tracker._queue = [9]
    ports.tracker.states[9] = IssueState("OPEN", "", None, None)
    _seed_body(ports, 9)
    launcher = FakeLauncher()

    lines = _round(
        _cfg(tmp_path, max_concurrent_runs=2),
        ports,
        MemoryRunRegistry(_roles()[:1]),
        launcher,
        ESCALATED_AT,
    )

    assert [argv[1] for argv in launcher.spawned] == ["run", "merge"]
    assert launcher.spawned[0][3] == "9"
    assert lines == ["agentic: poll: launch #9 -> pid 4242", "agentic: poll: merge #5 -> pid 4242"]


def test_a_live_exempt_child_defers_a_second_one_and_the_round_is_not_idle(tmp_path):
    ports = _ports(5)
    live = _child("merge", "GH-9", product="other-product")
    launcher = FakeLauncher()

    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry([live]), launcher, ESCALATED_AT)

    assert launcher.spawned == []
    assert lines == [poll.deferred_line(rules.Deferred("merge", 5, "exempt_busy"))]
    assert NO_WORK not in lines


def test_a_live_checks_land_defers_a_merge_the_same_way(tmp_path):
    ports = _ports(5)
    live = _child("land_checks", "GH-9", product="other-product")

    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry([live]), FakeLauncher(), ESCALATED_AT)

    assert lines == [poll.deferred_line(rules.Deferred("merge", 5, "exempt_busy"))]


def test_a_live_exempt_child_of_another_product_on_a_shared_registry_defers(tmp_path):
    first = _ports(5)
    second = dataclasses.replace(_ports(6), stack=MemoryStack(product="second"))
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()

    _round(_cfg(tmp_path), first, registry, launcher, ESCALATED_AT)
    # What the spawned `agentic merge` registers, which the fake launcher does not.
    registry.entries.append(_child("merge", "GH-5", product=_product_of(first)))
    lines = _round(_cfg(tmp_path), second, registry, launcher, ESCALATED_AT)

    assert [argv[3] for argv in launcher.spawned] == ["5"]
    assert lines == [poll.deferred_line(rules.Deferred("merge", 6, "exempt_busy"))]


def test_no_second_child_starts_for_a_task_that_already_has_one(tmp_path):
    for kind in ("merge", "land_checks", "land_model", "role"):
        ports = _ports(5)
        launcher = FakeLauncher()
        registry = MemoryRunRegistry([_child(kind, "GH-5", product=_product_of(ports))])

        lines = _round(
            _cfg(tmp_path, max_concurrent_runs=3), ports, registry, launcher, ESCALATED_AT
        )

        assert launcher.spawned == [], kind
        assert lines == [NO_WORK], kind


def test_a_live_child_of_another_product_with_the_same_task_id_does_not_block_this_one(tmp_path):
    ports = _ports(5)
    launcher = FakeLauncher()
    registry = MemoryRunRegistry([_child("role", "GH-5", product="not-this-one")])

    _round(_cfg(tmp_path, max_concurrent_runs=3), ports, registry, launcher, ESCALATED_AT)

    assert launcher.spawned == [_merge_argv(5, tmp_path)]


def test_gather_merges_skips_a_busy_task_before_assessing_it():
    ports = _ports(5, 6)
    lines: list[str] = []

    found = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), lines, busy=frozenset({"GH-5"})
    )

    assert found == [6]


def test_no_issue_is_both_a_merge_and_a_land_candidate():
    ports = _ports(5, 6, 7)
    _unapproved(ports, 6)
    _failed_local_check_after_a_rerun(ports)  # task 5: a model step
    lands: list[rules.LandCandidate] = []

    merges = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), [], lands, live=LIVE, now=ESCALATED_AT
    )

    assert merges == [7]
    assert {c.issue for c in lands} == {5, 6}
    assert not set(merges) & {c.issue for c in lands}


def test_gather_merges_classifies_each_land_by_its_step():
    ports = _ports(5, 6, checks=False)
    ports.documents.record_closure(
        "GH-5",
        {
            "merge_checks": {
                "head": "a" * 40,
                "master": MASTER,
                "passed": False,
                "failed_step": "checks",
                "checks": [{"name": "tests", "passed": False, "tail": ["boom"]}],
            }
        },
    )
    _unapproved(ports, 6)
    lands: list[rules.LandCandidate] = []

    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands, live=LIVE, now=ESCALATED_AT)

    assert sorted(lands, key=lambda c: c.issue) == [
        rules.LandCandidate(5, "land_checks"),
        rules.LandCandidate(6, "land_model"),
    ]


def _execute_land(tmp_path, kinds):
    launcher = FakeLauncher()
    poll.execute(
        [rules.PollAction("land", 5)],
        _cfg(tmp_path),
        _ports(),
        MemoryRunRegistry(),
        launcher,
        ESCALATED_AT,
        "agentic",
        register_timeout_s=3.0,
        sleep=_no_sleep,
        land_kinds=kinds,
    )
    return launcher.spawned


def test_execute_spawns_land_with_the_kind_of_its_candidate(tmp_path):
    for kind in ("land_checks", "land_model"):
        [argv] = _execute_land(tmp_path, {5: kind})
        assert argv == [
            "agentic",
            "land",
            "--issue",
            "5",
            "--run-kind",
            kind,
            "--product-dir",
            str(tmp_path),
        ]


def test_execute_spawns_a_land_it_never_classified_as_land_model(tmp_path):
    [argv] = _execute_land(tmp_path, None)
    assert argv[4:6] == ["--run-kind", "land_model"]


class _StaleLauncher(FakeLauncher):
    """Runs `land` in-process the way the real child would, with the kind it was spawned
    under, after the world moved on since the poller classified it: the rerun was spent."""

    def __init__(self, ports, tmp_path):
        super().__init__()
        self.ports, self.tmp_path = ports, tmp_path
        self.results: list[landing.LandResult] = []

    def spawn(self, argv):
        pid = super().spawn(argv)
        _failed_local_check_after_a_rerun(self.ports)
        self.results.append(
            landing.land(
                "GH-5",
                _cfg(self.tmp_path),
                self.ports,
                _HeldLocks(),
                Upstream("origin/master", MASTER, None),
                frozenset(),
                lambda line: None,
                live=LIVE,
                run_kind=argv[5],
                turn="head",
            )
        )
        return pid


def test_a_classification_gone_stale_is_a_mismatch_and_the_next_round_spawns_land_model(tmp_path):
    ports = _ports(5, checks=False)
    ports.documents.record_closure(
        "GH-5",
        {
            "merge_checks": {
                "head": "a" * 40,
                "master": MASTER,
                "passed": False,
                "failed_step": "checks",
                "checks": [{"name": "tests", "passed": False, "tail": ["boom"]}],
            }
        },
    )
    stale = _StaleLauncher(ports, tmp_path)
    now: datetime = ESCALATED_AT

    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), stale, now, live_steps=LIVE)

    assert stale.spawned[0][4:6] == ["--run-kind", "land_checks"]
    assert stale.results == [landing.LandResult("skipped", "run_kind_mismatch")]
    again = FakeLauncher()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), again, now, live_steps=LIVE)
    assert again.spawned[0][4:6] == ["--run-kind", "land_model"]


# ---- GH-270 (ADR-0045 addendum): a model land uses the reserved slot ---------------------


def _owed_lands(*extra_unapproved: int):
    """Task 5 owes a model step (`fix`); each extra number is an unapproved PR, also a
    `land_model` candidate."""
    ports = _ports(5, *extra_unapproved)
    _failed_local_check_after_a_rerun(ports)
    for number in extra_unapproved:
        _unapproved(ports, number)
    return ports


def _land_round(tmp_path, ports, entries, cap=2, **kw):
    registry = MemoryRunRegistry(entries)
    launcher = FakeLauncher()
    lines = _round(
        _cfg(tmp_path, max_concurrent_runs=cap),
        ports,
        registry,
        launcher,
        ESCALATED_AT,
        live_steps=LIVE,
        **kw,
    )
    return [line for line in lines if " land #" in line], launcher, registry


def _land_argv(number: int, tmp_path) -> list[str]:
    return [
        "agentic",
        "land",
        "--issue",
        str(number),
        "--run-kind",
        "land_model",
        "--product-dir",
        str(tmp_path),
    ]


def test_a_full_cap_of_role_runs_still_starts_the_owed_model_land(tmp_path):
    lines, launcher, _ = _land_round(tmp_path, _owed_lands(), _roles())

    assert launcher.spawned == [_land_argv(5, tmp_path)]
    assert lines == ["agentic: poll: land #5 -> pid 4242"]


def test_a_full_cap_plus_the_headroom_defers_the_land_no_slot(tmp_path):
    entries = [*_roles(), _child("land_model", "GH-9", pid=3)]

    lines, launcher, _ = _land_round(tmp_path, _owed_lands(), entries)

    assert launcher.spawned == []
    assert lines == ["agentic: poll: land #5 waits: no_slot"]


def test_a_registry_entry_without_a_kind_counts_toward_the_cap(tmp_path):
    kindless = [
        RunRegistration(pid=1, host="h", product="product", task_id="GH-1"),
        RunRegistration(pid=2, host="h", product="product", task_id="GH-2"),
        RunRegistration(pid=3, host="h", product="product", task_id="GH-3"),
    ]

    lines, launcher, _ = _land_round(tmp_path, _owed_lands(), kindless)

    assert launcher.spawned == []
    assert lines == ["agentic: poll: land #5 waits: no_slot"]


def test_a_land_waits_for_the_pending_account_limit_pause_of_its_product(tmp_path):
    ports = _owed_lands()
    ports.documents.save(
        {
            "id": "GH-19",
            "escalated": "Developer failed 2 times: API Error: 429",
            "pause_log": [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        }
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-02T19:30:00Z",
        )
    ]

    lines, launcher, _ = _land_round(tmp_path, ports, [])

    assert launcher.spawned == []
    assert lines == ["agentic: poll: land #5 waits: pause_waiting"]


def test_the_gap_before_the_resume_registers_counts_the_land_and_defers_another(tmp_path):
    ports = _owed_lands(6)
    cap_full = _roles()
    first, launcher, registry = _land_round(tmp_path, ports, cap_full)
    assert launcher.spawned == [_land_argv(5, tmp_path)]
    assert first == ["agentic: poll: land #5 -> pid 4242", "agentic: poll: land #6 waits: no_slot"]

    # The land registered itself and exited into its wait; the resume it spawned has not
    # registered yet: the second round sees cap + 1 through the land's own entry.
    land_entry = _child("land_model", "GH-5", product=_product_of(ports), pid=3)
    second, again, _ = _land_round(tmp_path, ports, [*cap_full, land_entry])

    assert again.spawned == []
    assert second == ["agentic: poll: land #6 waits: no_slot"]


# -- GH-500: the exempt queue over the registry ------------------------------------------------

T0 = ESCALATED_AT


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _tasks_root(cfg):
    return (cfg.product_dir / document.TASKS_SUBDIR).resolve()


def _merged_issues(launcher) -> list[str]:
    return [argv[3] for argv in launcher.spawned if argv[1] == "merge"]


def test_the_merge_that_just_started_goes_behind_the_others_next_round(tmp_path):
    cfg, ports, registry = _cfg(tmp_path), _ports(478, 487, 492), MemoryRunRegistry()
    for issue in (478, 487, 492):
        registry.record_exempt_seen("product", issue, "merge", T0)

    first, second = FakeLauncher(), FakeLauncher()
    _round(cfg, ports, registry, first, _at(5))
    _round(cfg, ports, registry, second, _at(6))

    assert _merged_issues(first) == ["478"]
    assert _merged_issues(second) == ["487"]
    assert registry.exempt_attempts("product")[(478, "merge")].last_attempt == _at(5)


def test_a_candidate_is_noted_the_first_time_it_is_seen_and_keeps_that_instant(tmp_path):
    cfg, ports, registry = _cfg(tmp_path), _ports(5), MemoryRunRegistry()

    _round(cfg, ports, registry, FakeLauncher(), _at(1))
    _round(cfg, ports, registry, FakeLauncher(), _at(2))

    seen = registry.exempt_attempts("product")[(5, "merge")]
    assert (seen.first_seen, seen.last_attempt) == (_at(1), _at(2))


def _two_products(tmp_path):
    a = dataclasses.replace(_ports(5), stack=MemoryStack(product="a"))
    b = dataclasses.replace(_ports(5), stack=MemoryStack(product="b"))
    return a, b, _cfg(tmp_path / "a"), _cfg(tmp_path / "b")


def test_the_product_whose_candidate_waited_longer_makes_the_other_yield(tmp_path):
    a_ports, b_ports, a_cfg, b_cfg = _two_products(tmp_path)
    registry = MemoryRunRegistry()
    registry.record_exempt_seen("b", 5, "merge", _at(4))
    registry.record_exempt_seen("a", 5, "merge", T0)
    a_key = rules.root_key(str(_tasks_root(a_cfg)))
    older = exempt_queue.ExemptCandidate("merge", "a", a_key, 5, first_seen=T0)
    registry.publish_exempt_wait(_tasks_root(a_cfg), "a", older, _at(5))
    launcher = FakeLauncher()

    lines = _round(b_cfg, b_ports, registry, launcher, _at(6))

    assert launcher.spawned == []
    assert "agentic: poll: merge #5 waits: exempt_busy" in lines
    # B's own candidate could start, so it is published behind A's, and A never yields to it.
    assert {w.product for w in registry.exempt_waits(_at(6))[0]} == {"a", "b"}
    a_launcher = FakeLauncher()
    _round(a_cfg, a_ports, registry, a_launcher, _at(7))
    assert _merged_issues(a_launcher) == ["5"]
    assert [w.product for w in registry.exempt_waits(_at(7))[0]] == ["b"]


def test_a_held_round_removes_its_wait_so_the_other_product_starts(tmp_path):
    a_ports, b_ports, a_cfg, b_cfg = _two_products(tmp_path)
    registry = MemoryRunRegistry()
    registry.record_exempt_seen("b", 5, "merge", _at(4))
    a_key = rules.root_key(str(_tasks_root(a_cfg)))
    registry.publish_exempt_wait(
        _tasks_root(a_cfg),
        "a",
        exempt_queue.ExemptCandidate("merge", "a", a_key, 5, first_seen=T0),
        _at(5),
    )
    held = dataclasses.replace(
        a_ports, scratch=MemoryScratch(), free_space=_free(a_cfg.product_dir, tmp_mb=10)
    )
    held_cfg = _cfg(a_cfg.product_dir, scratch_dir=a_cfg.product_dir / "scratch-root")

    _round(held_cfg, held, registry, FakeLauncher(), _at(6))
    launcher = FakeLauncher()
    _round(b_cfg, b_ports, registry, launcher, _at(6))

    assert registry.exempt_wait_publishes[1] == (_tasks_root(a_cfg), "a", None)
    assert _merged_issues(launcher) == ["5"]


def test_a_filtered_or_busy_candidate_keeps_its_record_and_a_closed_task_loses_it(tmp_path):
    cfg, ports, registry = _cfg(tmp_path), _ports(5, 6), MemoryRunRegistry()
    registry.record_exempt_seen("product", 5, "merge", T0)
    registry.record_exempt_seen("product", 6, "merge", T0)
    registry.entries.append(_child("role", "GH-5"))  # a live child of #5: never a candidate

    for minute in (1, 2):
        _round(cfg, ports, registry, FakeLauncher(), _at(minute))
        assert (5, "merge") in registry.exempt_attempts("product")

    ports.documents.record_closure("GH-6", {"closed_at": "2026-11-04T09:00:00.000+00:00"})
    _round(cfg, ports, registry, FakeLauncher(), _at(3))

    assert set(registry.exempt_attempts("product")) == {(5, "merge")}


# -- GH-500: the starvation line ---------------------------------------------------------------


def _starved(lines) -> list[str]:
    return [line for line in lines if " starved: " in line]


def test_a_merge_waiting_behind_a_live_exempt_child_is_reported_once_naming_it(tmp_path):
    cfg, ports, registry = _cfg(tmp_path), _ports(5), MemoryRunRegistry()
    registry.entries.append(_child("merge", "GH-9", product="other", pid=7))
    registry.record_exempt_seen("product", 5, "merge", T0)
    minutes = int(exempt_queue.EXEMPT_STARVED_AFTER.total_seconds() // 60)

    early = _round(cfg, ports, registry, FakeLauncher(), _at(minutes - 1))
    first = _round(cfg, ports, registry, FakeLauncher(), _at(minutes + 1))
    again = _round(cfg, ports, registry, FakeLauncher(), _at(minutes + 2))

    assert _starved(early) == [] and _starved(again) == []
    assert _starved(first) == [
        f"agentic: poll: merge #5 starved: {minutes + 1} min behind "
        "product other task GH-9 kind merge"
    ]
    assert registry.exempt_attempts("product")[(5, "merge")].starved_reported


def test_a_merge_yielding_to_another_product_is_reported_naming_that_product(tmp_path):
    a_ports, b_ports, a_cfg, b_cfg = _two_products(tmp_path)
    registry = MemoryRunRegistry()
    registry.record_exempt_seen("b", 5, "merge", T0)
    a_key = rules.root_key(str(_tasks_root(a_cfg)))
    older = exempt_queue.ExemptCandidate("merge", "a", a_key, 5, first_seen=_at(-10))
    registry.publish_exempt_wait(_tasks_root(a_cfg), "a", older, _at(29))
    late = exempt_queue.EXEMPT_STARVED_AFTER + timedelta(minutes=9)

    lines = _round(b_cfg, b_ports, registry, FakeLauncher(), T0 + late)

    assert _starved(lines) == [
        f"agentic: poll: merge #5 starved: {int(late.total_seconds() // 60)} min behind "
        "yielding to product a"
    ]


def test_the_time_a_merge_spent_in_backoff_does_not_count_toward_starvation(tmp_path):
    """GH-500 finding: a `ci_untrusted_no_dispatch` backoff outlasts `EXEMPT_STARVED_AFTER`, so
    the first `exempt_busy` round after it ended reported the merge as starved at once."""
    cfg, ports, registry = _cfg(tmp_path), _ports(5), MemoryRunRegistry()
    registry.entries.append(_child("merge", "GH-9", product="other", pid=7))
    registry.record_exempt_attempt("product", 5, "merge", T0)
    ports.documents.record_closure(
        "GH-5",
        {
            "merge_refusal": {
                "reason": "ci_untrusted_no_dispatch",
                "head": HEAD,
                "master": MASTER,
                "at": T0.isoformat(),
            }
        },
    )
    ends = int(exempt_queue.EXEMPT_REFUSAL_BACKOFF.total_seconds() // 60)
    after = int(exempt_queue.EXEMPT_STARVED_AFTER.total_seconds() // 60)

    inside = _round(cfg, ports, registry, FakeLauncher(), _at(ends - 1))
    just_out = _round(cfg, ports, registry, FakeLauncher(), _at(ends + 1))
    waited = _round(cfg, ports, registry, FakeLauncher(), _at(ends + after + 1))

    assert _starved(inside) == [] and _starved(just_out) == []
    assert _starved(waited) == [
        f"agentic: poll: merge #5 starved: {after + 1} min behind "
        "product other task GH-9 kind merge"
    ]
