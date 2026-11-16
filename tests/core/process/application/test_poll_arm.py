"""GH-470, subtask `poller-arm`: the poller assigns each new launch an experiment arm from
the product's declaration (`_assign_arms`, after `rules.poll_plan`), writes it into the launch
record and into `_argv`, and does exactly what it did before when there is no usable
declaration."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueState, LaunchAttempt
from agentic.core.process.domain.experiment import ARM_FLAG
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import _Locks, _no_sleep, _seed_body
from tests.fakes import FakeExperimentFile, FakeLauncher, MemoryRunRegistry, memory_ports

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _declaration(assignment: str = "alternate") -> dict:
    return {
        "id": "light-vs-default",
        "assignment": assignment,
        "arms": [
            {"name": "control", "share": 0.5, "switches": {}},
            {"name": "light", "share": 0.5, "switches": {"review_mode": "light"}},
        ],
    }


def _ports(tmp_path, issues, declaration=None, error=None):
    ports = memory_ports(experiment=FakeExperimentFile(declaration, error))
    ports.tracker._queue = list(issues)
    for number in issues:
        ports.tracker.states[number] = IssueState("OPEN", "", (), None)
        _seed_body(ports, number)
    return ports


def _cfg(tmp_path, **over):
    return Configuration(product_dir=tmp_path, **over)


def _recorded(registry):
    """The arm each launch record of the product holds, by issue."""
    return {n: attempt.arm for (_, n), attempt in registry.attempts.items()}


def _round(cfg, ports, registry, launcher):
    return poll.run_once(
        cfg,
        ports,
        registry,
        launcher,
        NOW,
        "agentic",
        sleep=_no_sleep,
        lock=_Locks(),
        current_boot=None,
    )


def _rounds(cfg, ports, registry, launcher, count):
    for _ in range(count):
        _round(cfg, ports, registry, launcher)


def test_three_launches_split_two_to_one_on_a_fifty_fifty_alternate(tmp_path):
    ports = _ports(tmp_path, [1, 2, 3], _declaration())
    registry = MemoryRunRegistry()
    _rounds(_cfg(tmp_path), ports, registry, FakeLauncher(), 3)
    assert _recorded(registry) == {1: "control", 2: "light", 3: "control"}


def test_a_candidate_the_plan_does_not_launch_gets_no_arm_and_is_not_counted(tmp_path):
    ports = _ports(tmp_path, [5, 6], _declaration())
    given = {"experiment": "light-vs-default", "switches": {}}
    ports.documents.save({"id": "GH-9", "assignment": {**given, "arm": "control"}})
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    # one slot: #5 launches and #6 waits, whatever order the plan puts them in
    _round(_cfg(tmp_path, max_concurrent_runs=1), ports, registry, launcher)
    assert len(launcher.spawned) == 1
    launched = int(launcher.spawned[0][3])
    waiting = ({5, 6} - {launched}).pop()
    # the one that left got the arm that is behind (control has the document's one)
    assert registry.attempts[("product", launched)].arm == "light"
    assert ("product", waiting) not in registry.attempts
    assert launcher.spawned[0][4:6] == [ARM_FLAG, "light"]


def test_a_record_with_an_arm_counts_and_its_relaunch_keeps_the_arm(tmp_path):
    ports = _ports(tmp_path, [5, 6], _declaration())
    registry = MemoryRunRegistry()
    registry.attempts[("product", 5)] = LaunchAttempt(
        datetime(2026, 11, 3, 0, 0, tzinfo=UTC), 1, "light"
    )
    launcher = FakeLauncher()
    _rounds(_cfg(tmp_path), ports, registry, launcher, 2)
    # 5 keeps `light` and counts for it, so 6 goes to the arm that is behind
    assert launcher.spawned[0][3:6] == ["5", ARM_FLAG, "light"]
    assert _recorded(registry) == {5: "light", 6: "control"}


def test_a_record_whose_arm_the_declaration_dropped_is_assigned_again(tmp_path):
    ports = _ports(tmp_path, [5], _declaration())
    registry = MemoryRunRegistry()
    registry.attempts[("product", 5)] = LaunchAttempt(
        datetime(2026, 11, 3, 0, 0, tzinfo=UTC), 1, "gone"
    )
    _round(_cfg(tmp_path), ports, registry, FakeLauncher())
    assert _recorded(registry) == {5: "control"}


def test_a_document_under_the_experiment_counts_once_even_when_a_record_exists(tmp_path):
    ports = _ports(tmp_path, [8], _declaration())
    given = {"experiment": "light-vs-default", "switches": {}}
    ports.documents.save({"id": "GH-7", "assignment": {**given, "arm": "control"}})
    ports.documents.save({"id": "GH-9", "assignment": {**given, "arm": "light"}})
    ports.documents.save({"id": "GH-10", "assignment": {**given, "arm": "light"}})
    ports.documents.save({"id": "GH-11", "assignment": {"experiment": "other", "arm": "control"}})
    registry = MemoryRunRegistry()
    registry.attempts[("product", 7)] = LaunchAttempt(NOW, 1, "control")  # 7 has a document
    # control 1, light 2 (another experiment's document and 7's record do not count again)
    _round(_cfg(tmp_path), ports, registry, FakeLauncher())
    assert registry.attempts[("product", 8)].arm == "control"


def test_seeded_assignment_is_deterministic(tmp_path):
    declaration = {**_declaration("seeded"), "seed": 7}
    first, again = MemoryRunRegistry(), MemoryRunRegistry()
    _rounds(_cfg(tmp_path), _ports(tmp_path, [1, 2, 3, 4], declaration), first, FakeLauncher(), 4)
    _rounds(_cfg(tmp_path), _ports(tmp_path, [4, 3, 2, 1], declaration), again, FakeLauncher(), 4)
    assert _recorded(first) == _recorded(again)
    assert set(_recorded(first).values()) <= {"control", "light"}


def test_without_a_declaration_argv_record_and_candidates_are_those_of_today(tmp_path):
    ports = _ports(tmp_path, [5])
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert launcher.spawned == [
        ["agentic", "run", "--issue", "5", "--detach", "--product-dir", str(tmp_path)]
    ]
    assert registry.attempts[("product", 5)].arm is None
    assert poll._argv("agentic", "run", 5, "/p", "--detach") == [
        "agentic", "run", "--issue", "5", "--detach", "--product-dir", "/p",
    ]  # fmt: skip


def test_env_review_mode_light_without_an_experiment_is_as_today(tmp_path):
    ports = _ports(tmp_path, [5])
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, review_mode="light"), ports, MemoryRunRegistry(), launcher)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert ARM_FLAG not in launcher.spawned[0]


def test_an_invalid_declaration_launches_unassigned_with_one_message(tmp_path):
    broken = {**_declaration(), "arms": [{"name": "Bad Name", "share": 1}]}
    ports = _ports(tmp_path, [5, 6], broken)
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, registry, launcher)
    messages = [line for line in lines if "experiment" in line]
    assert len(messages) == 1
    assert ".agentic/experiment.yaml" in messages[0] and "unassigned" in messages[0]
    assert any("launch #5" in line for line in lines)
    assert all(ARM_FLAG not in argv for argv in launcher.spawned)
    assert all(a.arm is None for a in registry.attempts.values())


def test_an_unreadable_declaration_launches_unassigned_with_one_message(tmp_path):
    ports = _ports(tmp_path, [5], error=".agentic/experiment.yaml: cannot be read: boom")
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher)
    assert len([line for line in lines if "experiment" in line]) == 1
    assert ARM_FLAG not in launcher.spawned[0]


def test_an_env_conflict_launches_unassigned_with_one_message(tmp_path):
    ports = _ports(tmp_path, [5, 6], _declaration())
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path, review_mode="light"), ports, registry, launcher)
    messages = [line for line in lines if "experiment" in line]
    assert len(messages) == 1 and "review_mode" in messages[0]
    assert all(ARM_FLAG not in argv for argv in launcher.spawned)
    assert all(a.arm is None for a in registry.attempts.values())


def test_an_assigned_launch_is_recorded_and_spawned_with_its_arm(tmp_path):
    ports = _ports(tmp_path, [5], _declaration())
    registry = MemoryRunRegistry()
    launcher = FakeLauncher()
    _round(_cfg(tmp_path), ports, registry, launcher)
    assert launcher.spawned[0] == [
        "agentic", "run", "--issue", "5", ARM_FLAG, "control", "--detach",
        "--product-dir", str(tmp_path),
    ]  # fmt: skip
    assert registry.attempts[("product", 5)].arm == "control"
