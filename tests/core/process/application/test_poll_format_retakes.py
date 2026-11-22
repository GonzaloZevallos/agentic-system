"""GH-1019 part 2: the poller retakes format failures per rule, counts before it spawns, and never
retakes a task that is held or that a person is talking to. Fake ports, a fixed clock, no `gh`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueComment, RunRegistration
from agentic.core.process.domain import rules
from agentic.core.process.domain.landing import IssueRef
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryDocuments, MemoryRunRegistry, memory_ports

RULE = "schema_invalid"
OTHER = "part_over_budget"
T0 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
URL = "https://github.com/o/r/issues/19"
HOLD = {"rule": RULE, "cause": "rule_spent", "at": "2026-11-22T10:00:00Z"}


def _cfg(tmp_path, **over):
    return Configuration(product_dir=tmp_path, **over)


def _no_sleep(seconds: float) -> None:
    """A child that never registers must not block the test for the real wait."""


class _Locks:
    def read(self, task_id):
        return None

    def refusal(self, task_id):
        return None


def _round(tmp_path, ports, launcher, now, registry=None, **kw):
    cfg = kw.pop("cfg", None) or _cfg(tmp_path, max_concurrent_runs=2)
    return poll.run_once(
        cfg,
        ports,
        registry or MemoryRunRegistry(),
        launcher,
        now,
        "agentic",
        sleep=_no_sleep,
        lock=_Locks(),
        current_boot="boot",
        **kw,
    )


def _pause(
    ports, *, at: datetime = T0, rules_=(RULE,), counts=None, hold=None, number=19, extra=()
):
    """A task paused by format failures on `rules_`, its `Escalated:` comment at `at`, and
    whatever `extra` comments follow it. The document is saved whole, as a child's escalation
    leaves it (the epoch opens after the `resumed` entry the previous retake wrote)."""
    task_id = f"GH-{number}"
    log: list[Any] = [{"kind": "resumed", "at": "2026-11-22T09:00:00Z"}]
    log += [
        {"kind": "failure", "value": "schema", "section": f"S{i}", "rule": r}
        for i, r in enumerate(rules_)
    ]
    log.append({"kind": "escalation", "value": "failure"})
    doc: dict[str, Any] = {"id": task_id, "escalated": "Tech Lead failed", "pause_log": log}
    if counts is not None:
        doc["format_retakes"] = dict(counts)
    if hold is not None:
        doc["format_hold"] = dict(hold)
    ports.documents.save(doc)
    ports.tracker.timestamped[task_id] = [
        IssueComment("Escalated: Tech Lead failed", at.strftime("%Y-%m-%dT%H:%M:%SZ")),
        *extra,
    ]


def _counts(ports, number=19):
    return (ports.documents.load(f"GH-{number}") or {}).get("format_retakes")


def _human(at: datetime = T0 + timedelta(minutes=5)):
    return IssueComment("please look at this", at.strftime("%Y-%m-%dT%H:%M:%SZ"))


def test_three_identical_format_epochs_retake_retake_then_stop_for_the_human(tmp_path):
    ports = memory_ports()
    launcher = FakeLauncher()
    _pause(ports)
    assert _round(tmp_path, ports, launcher, T0 + timedelta(minutes=14)) == [
        "agentic: poll: nothing to do this round"
    ]
    lines = _round(tmp_path, ports, launcher, T0 + timedelta(minutes=16))
    assert lines[0] == "agentic: poll: resume #19 -> pid 4242"
    assert _counts(ports) == {RULE: 1}
    assert ports.tracker.comments == [("GH-19", "Auto-resumed: schema")]

    second = T0 + timedelta(hours=1)  # the child failed again: a new epoch, the count kept
    _pause(ports, at=second, counts={RULE: 1})
    assert _round(tmp_path, ports, launcher, second + timedelta(minutes=29))[0].startswith(
        "agentic: poll: nothing"
    )  # RETAKE_SCHEDULE[1] is 30
    _round(tmp_path, ports, launcher, second + timedelta(minutes=31))
    assert _counts(ports) == {RULE: 2}

    third = T0 + timedelta(hours=3)
    _pause(ports, at=third, counts={RULE: 2})
    state = ports.documents.load("GH-19")
    assert rules.needs_a_human(state) is True  # until the hold subtask replaces the label
    assert poll.gather_resumes(ports, third + timedelta(days=2)) == []
    assert len(launcher.spawned) == 2
    assert _round(tmp_path, ports, launcher, third + timedelta(days=2))[0].startswith(
        "agentic: poll: nothing"
    )
    assert len(launcher.spawned) == 2 and _counts(ports) == {RULE: 2}


def _candidate(ports, **kw):
    carried: dict[int, Any] = {}
    found = poll.gather_resumes(ports, T0 + timedelta(days=3), format_rules=carried, **kw)
    return found, carried


def test_the_count_is_per_rule_and_the_task_has_a_backstop_of_six(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={OTHER: 2})  # another rule's retakes do not spend this one's
    (candidate,), carried = _candidate(ports)
    assert (candidate.prior, candidate.limit, carried) == (2, 4, {19: (RULE,)})

    _pause(ports, counts={RULE: 2})
    assert _candidate(ports) == ([], {})

    _pause(ports, rules_=(RULE, OTHER), counts={RULE: 1})  # the least left over the rules
    (candidate,), carried = _candidate(ports)
    assert (candidate.prior, candidate.limit, carried) == (1, 2, {19: (RULE, OTHER)})

    _pause(ports, counts={RULE: 0, "a": 2, "b": 2, "c": 2})  # six in all: the backstop
    assert _candidate(ports) == ([], {})


def test_prior_is_the_total_and_two_rules_wait_by_the_retake_schedule(tmp_path):
    ports = memory_ports()
    _pause(ports, rules_=(RULE, OTHER), counts={RULE: 1, OTHER: 1})
    wait = rules.RETAKE_SCHEDULE[2]  # the total made, not the retakes of one rule
    launcher = FakeLauncher()
    _round(tmp_path, ports, launcher, T0 + timedelta(minutes=wait - 1))
    assert launcher.spawned == []
    _round(tmp_path, ports, launcher, T0 + timedelta(minutes=wait + 1))
    assert len(launcher.spawned) == 1
    assert _counts(ports) == {RULE: 2, OTHER: 2}


def test_a_candidate_that_was_not_started_is_not_counted(tmp_path):
    ports = memory_ports()
    _pause(ports)
    launcher = FakeLauncher()
    due = T0 + timedelta(minutes=16)
    full = MemoryRunRegistry(
        entries=[RunRegistration(pid=1, host="h", product="o", task_id="GH-1")]
    )
    _round(tmp_path, ports, launcher, due, full, cfg=_cfg(tmp_path, max_concurrent_runs=1))
    assert launcher.spawned == [] and _counts(ports) is None
    assert _round(tmp_path, ports, launcher, due, dry_run=True)[-1] == "would resume issue 19"
    assert launcher.spawned == [] and _counts(ports) is None
    assert poll.gather_resumes(ports, due)  # gathering itself writes nothing either
    assert _counts(ports) is None


def test_a_task_with_a_live_child_is_not_a_candidate(tmp_path):
    ports = memory_ports()
    _pause(ports)
    registry = MemoryRunRegistry(
        [RunRegistration(pid=7, host="h", product="product", task_id="GH-19")]
    )
    launcher = FakeLauncher()
    _round(tmp_path, ports, launcher, T0 + timedelta(minutes=16), registry)
    assert launcher.spawned == [] and _counts(ports) is None


class _LookingLauncher(FakeLauncher):
    """Reads the document the moment the child would start."""

    def __init__(self, ports) -> None:
        super().__init__()
        self.ports = ports
        self.seen: list[Any] = []

    def spawn(self, argv):
        self.seen.append(_counts(self.ports))
        return super().spawn(argv)


def _execute(ports, launcher, carried, registry=None):
    return poll.execute(
        [rules.PollAction("resume", 19)],
        _cfg("/p"),
        ports,
        registry or MemoryRunRegistry(),
        launcher,
        T0,
        "agentic",
        register_timeout_s=0.0,
        sleep=_no_sleep,
        format_rules=carried,
    )


def test_the_launcher_sees_the_count_already_saved_and_a_child_that_never_registers_stays_counted():
    ports = memory_ports()
    _pause(ports, counts={RULE: 1})
    launcher = _LookingLauncher(ports)
    lines = _execute(ports, launcher, {19: (RULE,)})
    assert launcher.seen == [{RULE: 2}]
    assert lines[0] == "agentic: poll: resume #19 -> pid 4242"
    assert _counts(ports) == {RULE: 2}  # no run ever registered: still counted


def test_a_spawn_that_raises_gives_the_count_back():
    ports = memory_ports()
    _pause(ports, counts={RULE: 1})
    lines = _execute(ports, FakeLauncher(fails={0}), {19: (RULE,)})
    assert lines[0].startswith("agentic: poll: resume #19 -> not launched (not counted)")
    assert _counts(ports) == {RULE: 1}


class _DiskFullOnRestore(MemoryDocuments):
    """Saves fine until the retake is counted, then refuses the write that gives it back."""

    def save(self, state):
        if state.get("format_retakes") == {RULE: 1} and self.counted:
            raise OSError("disk full")
        if state.get("format_retakes") == {RULE: 2}:
            self.counted = True
        return super().save(state)

    counted = False


def test_a_restore_that_fails_says_so_and_the_retake_stays_counted():
    ports = replace(memory_ports(), documents=_DiskFullOnRestore())
    _pause(ports, counts={RULE: 1})
    lines = _execute(ports, FakeLauncher(fails={0}), {19: (RULE,)})
    assert "not launched (not counted)" in lines[0]
    assert lines[1] == "agentic: poll: format retake #19 -> count not restored: disk full"
    assert _counts(ports) == {RULE: 2}


def test_an_owner_resume_between_gather_and_execute_is_stale_and_counts_nothing():
    ports = memory_ports()
    _pause(ports, counts={RULE: 1})
    (_,), carried = _candidate(ports)
    state = ports.documents.load("GH-19")
    ports.documents.save(
        {**state, **rules.resumed(state, "Decision: x", at="2026-11-22T11:00:00Z")}
    )
    launcher = FakeLauncher()
    lines = _execute(ports, launcher, carried)
    assert lines == ["agentic: poll: format retake #19 -> stale (not counted)"]
    assert launcher.spawned == [] and ports.tracker.comments == []
    assert (
        ports.documents.load("GH-19").get("format_retakes") is None
    )  # the owner's resume cleared it


def test_a_change_of_the_rules_between_gather_and_execute_is_stale_too():
    ports = memory_ports()
    _pause(ports, counts={RULE: 1})
    (_,), carried = _candidate(ports)
    _pause(ports, rules_=(RULE, OTHER), counts={RULE: 1})
    launcher = FakeLauncher()
    assert _execute(ports, launcher, carried) == [
        "agentic: poll: format retake #19 -> stale (not counted)"
    ]
    assert launcher.spawned == [] and _counts(ports) == {RULE: 1}


def test_a_held_task_is_never_a_candidate_even_after_a_human_comment():
    ports = memory_ports()
    _pause(ports, counts={RULE: 2}, hold=HOLD)
    assert _candidate(ports) == ([], {})
    _pause(ports, counts={RULE: 0}, hold=HOLD, extra=[_human()])
    assert _candidate(ports, labels={19: ()}) == ([], {})
    assert ports.tracker.labels == []  # a hold has its own announcement: no safety net


def test_a_comment_on_a_pending_retake_labels_the_task_once_and_it_is_not_retaken(tmp_path):
    ports = memory_ports()
    _pause(ports, extra=[_human()])
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic",))]
    launcher = FakeLauncher()
    due = T0 + timedelta(minutes=30)
    _round(tmp_path, ports, launcher, due)
    assert ports.tracker.labels == [("GH-19", "needs-human")]
    assert launcher.spawned == [] and _counts(ports) is None

    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic", "needs-human"))]
    _round(tmp_path, ports, launcher, due + timedelta(hours=1))
    assert ports.tracker.labels == [("GH-19", "needs-human")]  # the read says it is there
    assert launcher.spawned == []


def test_a_label_that_failed_is_recorded_and_not_retried_for_an_hour(tmp_path):
    ports = memory_ports()
    _pause(ports, extra=[_human()])
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic",))]
    ports.tracker.label_failures = 1
    launcher = FakeLauncher()
    due = T0 + timedelta(minutes=30)
    _round(tmp_path, ports, launcher, due)
    rows = ports.documents.load("GH-19")["tracker_failures"]
    assert [row["target"] for row in rows] == ["label"]
    assert ports.tracker.labels == []

    stamp = due.strftime("%Y-%m-%dT%H:%M:%SZ")
    ports.documents.record_closure("GH-19", {"tracker_failures": [{**rows[0], "at": stamp}]})
    _round(tmp_path, ports, launcher, due + timedelta(minutes=30))
    assert ports.tracker.labels == []  # a label row from the last hour: no new call
    _round(tmp_path, ports, launcher, due + timedelta(minutes=61))
    assert ports.tracker.labels == [("GH-19", "needs-human")]


def test_a_round_that_reads_no_labels_writes_none(tmp_path):
    ports = memory_ports()
    _pause(ports, extra=[_human()])
    _round(tmp_path, ports, FakeLauncher(), T0 + timedelta(minutes=30), dry_run=True)
    assert ports.tracker.labels == []
    assert poll.gather_resumes(ports, T0 + timedelta(days=1)) == []
    assert ports.tracker.labels == []


def test_a_held_task_is_chosen_by_no_gatherer_of_a_whole_round(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2}, hold=HOLD, extra=[_human()])
    ports.tracker._queue = [19]
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic",))]
    launcher = FakeLauncher()
    for days in (0, 2):
        lines = _round(tmp_path, ports, launcher, T0 + timedelta(days=days, minutes=40))
        assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == [] and ports.tracker.comments == [] and ports.tracker.labels == []
    assert ports.documents.load("GH-19")["format_hold"] == HOLD
    assert poll.gather_retakes(ports, _Locks(), "boot")[0] == []
