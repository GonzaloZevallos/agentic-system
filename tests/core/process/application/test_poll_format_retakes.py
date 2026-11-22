"""GH-1019 part 2: the poller retakes format failures per rule, counts before it spawns, and never
retakes a task that is held or that a person is talking to. Fake ports, a fixed clock, no `gh`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agentic.core.process.application import format_hold, poll, resume
from agentic.core.process.application.ports import IssueComment, RunRegistration
from agentic.core.process.domain import rules
from agentic.core.process.domain.landing import IssueRef
from agentic.shared.configuration import Configuration
from tests.core.process.application import test_poll_landing_order as landing_order
from tests.core.process.application import test_poll_orphaned as orphaned
from tests.core.process.application import test_poll_rate_limit as limited
from tests.fakes import FakeLauncher, MemoryDocuments, MemoryRunRegistry, memory_ports

RULE = "schema_invalid"
OTHER = "part_over_budget"
T0 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
URL = "https://github.com/o/r/issues/19"
HOLD = {
    "rule": RULE,
    "cause": "rule_spent",
    "at": "2026-11-22T10:00:00Z",
    "noted_at": "2026-11-22T10:01:00Z",
}


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
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic",))]  # the pause never got the label
    assert rules.needs_a_human(ports.documents.load("GH-19")) is False
    assert poll.gather_resumes(ports, third + timedelta(days=2)) == []
    assert len(launcher.spawned) == 2
    lines = _round(tmp_path, ports, launcher, third + timedelta(days=2))
    assert lines[0] == "agentic: poll: #19 format hold (schema_invalid)"
    assert len(launcher.spawned) == 2 and _counts(ports) == {RULE: 2}
    held = ports.documents.load("GH-19")["format_hold"]
    assert held["rule"] == RULE and held["cause"] == "rule_spent" and "noted_at" in held
    assert ports.tracker.labels == [] and ports.tracker.unlabeled == []
    ((task, text),) = [
        c for c in ports.tracker.comments if c[1].startswith(format_hold.FORMAT_HOLD)
    ]
    assert "Retakes spent:" not in text
    assert [t for t in ports.tracker.comments if "Retakes spent:" in t[1]] == []
    again = _round(tmp_path, ports, launcher, third + timedelta(days=3))
    assert again == ["agentic: poll: nothing to do this round"]
    assert len([c for c in ports.tracker.comments if c[1].startswith("Format hold: ")]) == 1


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


def test_a_held_task_is_no_orphan_while_a_planned_one_beside_it_is_retaken():
    ports = orphaned._ports(queue=(7, 19))  # GH-7 planned and dead: the control
    _pause(ports, counts={RULE: 2}, hold=HOLD)
    lines, launcher = orphaned._go(ports)
    assert lines[0] == "agentic: poll: retake #7 -> pid 4242"
    assert [argv[3] for argv in launcher.spawned] == ["7"]
    assert [task for task, _ in ports.tracker.comments] == ["GH-7"]
    assert not any("#19" in line for line in lines)


def test_a_held_task_is_not_given_the_rate_limit_notice_a_paused_one_beside_it_gets(tmp_path):
    ports = limited._ports()
    control = limited._pause(ports)  # GH-7, the GitHub limit outlasted its 12 h
    _pause(ports, counts={RULE: 2}, hold=HOLD, at=limited.AT)
    out: list[str] = []
    poll.rate_limit_escalations(
        _cfg(tmp_path), ports, limited.AT + timedelta(hours=12), lambda task_id: False, out
    )
    assert out == ["agentic: poll: #7 github rate limit persisted -> left to the human"]
    assert ports.tracker.labels == [(control, "needs-human")]
    assert ports.documents.load("GH-19")["format_hold"] == HOLD
    assert ports.documents.load("GH-19").get("rate_limit_noticed_at") is None


def test_an_escalated_held_pull_request_does_not_lead_the_landing_order():
    ports = landing_order._three()  # #7 is the oldest approval, then #5, then #6
    ports.documents.record_closure(
        "GH-7",
        {
            "escalated": "Tech Lead failed",
            "format_retakes": {RULE: 2},
            "format_hold": HOLD,
            "pause_log": [
                {"kind": "failure", "value": "schema", "section": "S", "rule": RULE},
                {"kind": "escalation", "value": "failure"},
            ],
        },
    )
    found, _ = landing_order._merges(ports)
    assert found == [5]


def _held_round(tmp_path, ports, launcher=None, days=2, **kw):
    return _round(tmp_path, ports, launcher or FakeLauncher(), T0 + timedelta(days=days), **kw)


def _notes(ports):
    return [c for c in ports.tracker.comments if c[1].startswith(format_hold.FORMAT_HOLD)]


def test_a_task_labelled_before_the_hold_ends_with_no_label_and_one_note(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic", "needs-human"))]
    lines = _held_round(tmp_path, ports)
    assert lines[0] == "agentic: poll: #19 format hold (schema_invalid)"
    assert ports.tracker.unlabeled == [("GH-19", "needs-human")]
    ((_, text),) = _notes(ports)
    assert "no system issue was filed for schema_invalid; see tracker_failures" in text
    assert "2 retakes for schema_invalid" in text and "`agentic resume --issue 19`" in text
    assert text.endswith(format_hold.marker(ports.documents.load("GH-19")["format_hold"]))
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic",))]
    _held_round(tmp_path, ports, days=3)
    assert len(_notes(ports)) == 1 and ports.tracker.unlabeled == [("GH-19", "needs-human")]


def test_the_issue_url_of_the_rule_is_in_the_hold_and_the_note(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    doc = ports.documents.load("GH-19")
    ports.documents.save({**doc, "format_issue": [{"rule": RULE, "url": URL, "at": HOLD["at"]}]})
    _held_round(tmp_path, ports)
    assert ports.documents.load("GH-19")["format_hold"]["issue_url"] == URL
    assert f"Refusal issue: {URL}" in _notes(ports)[0][1]


def test_the_six_retake_backstop_names_the_last_rule_and_its_cause(tmp_path):
    ports = memory_ports()
    _pause(
        ports,
        counts={
            OTHER: 1,
            "parts_when_fits": 1,
            "estimate_does_not_fit": 1,
            "output_missing": 1,
            "unmapped": 1,
            RULE: 1,
        },
    )
    _held_round(tmp_path, ports)
    hold = ports.documents.load("GH-19")["format_hold"]
    assert (hold["rule"], hold["cause"]) == (RULE, "task_spent")
    assert "6 format retakes on this task" in _notes(ports)[0][1]


def test_a_hold_with_noted_at_makes_no_call_and_a_marker_already_there_posts_nothing(tmp_path):
    ports = memory_ports()
    noted = {**HOLD, "noted_at": "2026-11-22T10:01:00Z"}
    _pause(ports, counts={RULE: 2}, hold=noted)
    ports.tracker.comment_failures = 1  # would be hit by any post
    assert _held_round(tmp_path, ports)[0].startswith("agentic: poll: nothing")
    assert ports.tracker.comments == [] and ports.tracker.unlabeled == []
    unnoted = {k: v for k, v in noted.items() if k != "noted_at"}
    told = IssueComment(f"Format hold: x\n\n{format_hold.marker(unnoted)}", "2026-11-22T10:02:00Z")
    _pause(ports, counts={RULE: 2}, hold=unnoted, extra=[told])
    ports.tracker.comment_failures = 0
    _held_round(tmp_path, ports, days=3)
    assert ports.tracker.comments == []
    assert "noted_at" in ports.documents.load("GH-19")["format_hold"]


def test_a_failed_note_lands_in_tracker_failures_and_is_retried(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    ports.tracker.comment_failures = 1
    _held_round(tmp_path, ports)
    doc = ports.documents.load("GH-19")
    assert [row["target"] for row in doc["tracker_failures"]] == ["comment"]
    assert "noted_at" not in doc["format_hold"] and _notes(ports) == []
    _held_round(tmp_path, ports, days=3)
    assert len(_notes(ports)) == 1
    assert "noted_at" in ports.documents.load("GH-19")["format_hold"]
    _held_round(tmp_path, ports, days=4)
    assert len(_notes(ports)) == 1


def test_a_failed_label_removal_is_recorded_and_retried_without_a_second_note(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic", "needs-human"))]
    ports.tracker.drop_label_failures = 1
    _held_round(tmp_path, ports)
    doc = ports.documents.load("GH-19")
    assert [row["target"] for row in doc["tracker_failures"]] == ["label"]
    assert "noted_at" not in doc["format_hold"] and len(_notes(ports)) == 1
    _held_round(tmp_path, ports, days=3)
    assert ports.tracker.unlabeled == [("GH-19", "needs-human")] and len(_notes(ports)) == 1
    assert "noted_at" in ports.documents.load("GH-19")["format_hold"]


def test_an_unreadable_thread_writes_nothing_and_the_next_round_tries_again(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    original = ports.tracker.timestamped_comments

    def broken(task_id):
        raise OSError("no gh")

    ports.tracker.timestamped_comments = broken
    lines = poll.hold_format_failures(ports, _Locks(), set())
    assert lines == ["agentic: poll: #19 format hold (schema_invalid)"]
    assert _notes(ports) == [] and "noted_at" not in ports.documents.load("GH-19")["format_hold"]
    ports.tracker.timestamped_comments = original
    assert poll.hold_format_failures(ports, _Locks(), set()) == []
    assert len(_notes(ports)) == 1


def test_a_dry_round_a_live_child_and_a_live_lock_hold_nothing(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    _held_round(tmp_path, ports, dry_run=True)
    assert "format_hold" not in ports.documents.load("GH-19") and ports.tracker.comments == []
    busy = {"GH-19"}
    assert poll.hold_format_failures(ports, _Locks(), busy) == []

    class _Alive(_Locks):
        def refusal(self, task_id):
            return "alive"

    assert poll.hold_format_failures(ports, _Alive(), set()) == []
    assert "format_hold" not in ports.documents.load("GH-19")


def test_a_pause_that_is_not_spent_or_not_a_format_one_is_not_held(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 1})
    ports.documents.save(
        {
            "id": "GH-20",
            "escalated": "x",
            "pause_log": [
                {"kind": "failure", "value": "schema", "section": "S"},
                {"kind": "escalation", "value": "failure"},
            ],
            "format_retakes": {RULE: 2},
        }
    )
    ports.tracker.timestamped["GH-20"] = []
    assert poll.hold_format_failures(ports, _Locks(), set()) == []


def test_the_note_starts_with_a_system_prefix_so_no_one_has_spoken_after_it():
    assert format_hold.FORMAT_HOLD in resume.SYSTEM_COMMENTS
    text = format_hold.note({**HOLD, "cause": "rule_spent"}, 19)
    assert text.startswith(resume.SYSTEM_COMMENTS)
    assert text.splitlines()[0] == "Format hold: schema_invalid"
    comments = [IssueComment("Escalated: x", "t1"), _human(), IssueComment(text, "t2")]
    assert poll._human_spoke_after_the_system(comments) is False
    assert poll._human_spoke_after_the_system([*comments, _human()]) is True


def test_the_hold_note_builder_words_both_causes_and_the_missing_issue():
    spent = {**HOLD, "cause": "task_spent", "issue_url": URL}
    text = format_hold.note(spent, 7)
    assert "held after 6 format retakes on this task" in text and f"Refusal issue: {URL}" in text
    assert "`gh issue close 7 --reason 'not planned'`" in text
    assert text.splitlines()[-1] == f"<!-- agentic-format-hold: {RULE} {HOLD['at']} -->"


def test_a_failed_note_keeps_the_label_and_a_later_round_posts_then_drops_it(tmp_path):
    ports = memory_ports()
    _pause(ports, counts={RULE: 2})
    ports.inbox.refs = [IssueRef(URL, "t", "b", ("agentic", "needs-human"))]
    ports.tracker.comment_failures = 1
    _held_round(tmp_path, ports)
    doc = ports.documents.load("GH-19")
    assert ports.tracker.unlabeled == [] and _notes(ports) == []
    assert [row["target"] for row in doc["tracker_failures"]] == ["comment"]
    assert "noted_at" not in doc["format_hold"]
    _held_round(tmp_path, ports, days=3)
    assert len(_notes(ports)) == 1 and ports.tracker.unlabeled == [("GH-19", "needs-human")]
    assert "noted_at" in ports.documents.load("GH-19")["format_hold"]
