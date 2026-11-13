"""`split_sweep.repoint_dependents` (GH-619): the dependents of a split task are re-read,
re-pointed to the last part(s) and told so, once; the parts and the task's own issue are
never edited; a rewrite that breaks the post-condition writes nothing."""

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import poll, split_sweep
from agentic.core.process.application.ports import (
    GitError,
    IssueState,
    TrackerRateLimited,
    TrackerUnreachable,
)
from agentic.core.process.application.split_sweep import RepointResult, repoint_dependents
from agentic.core.process.domain import rules
from agentic.core.process.domain.landing import IssueRef
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryInbox, memory_ports

URL = "https://github.com/o/r/issues/{}"


def ports_with(**bodies):
    """Ports whose inbox holds issues by number, the tracker writing through to it."""
    ports = memory_ports(
        inbox=MemoryInbox(
            **{n: {"id": f"GH-{n}", "title": "t", "description": b} for n, b in bodies.items()}
        )
    )
    ports.tracker.inbox = ports.inbox
    return ports


def refs_of(ports):
    return [IssueRef(URL.format(n), "t", i["description"]) for n, i in ports.inbox.issues.items()]


def test_a_dependent_is_edited_once_and_told_and_a_second_pass_finds_nothing():
    ports = ports_with(**{"7": "Blocked by: #5, #6\n\nbody"})
    result = repoint_dependents(ports, 5, [9], refs_of(ports), {5})

    assert result == RepointResult(considered=1, changed=1, failed=0) and result.complete
    assert ports.tracker.updated_bodies == [(7, "Blocked by: #9, #6\n\nbody")]
    ((ref, text),) = ports.tracker.commented_issues
    assert ref == URL.format(7) and "#5" in text and "#9" in text

    again = repoint_dependents(ports, 5, [9], refs_of(ports), {5})
    assert again == RepointResult() and len(ports.tracker.updated_bodies) == 1


def test_a_part_and_the_tasks_own_issue_are_never_edited():
    ports = ports_with(**{"5": "Blocked by: #5", "9": "Blocked by: #5", "7": "Blocked by: #5"})
    result = repoint_dependents(ports, 5, [9], refs_of(ports), {5, 9})

    assert result.changed == 1 and [n for n, _ in ports.tracker.updated_bodies] == [7]


def test_a_dependent_that_is_a_part_of_another_split_keeps_its_marker_bytes():
    marker = rules.split_marker("GH-40", 2, 1)
    body = f"Blocked by: #5\r\n\r\nPart 2.\r\n{marker}\r\n"
    ports = ports_with(**{"7": body})
    repoint_dependents(ports, 5, [9], refs_of(ports), {5})

    ((_, written),) = ports.tracker.updated_bodies
    assert written == body.replace("#5", "#9") and rules.split_part_of(written) == ("GH-40", 2, 1)


def broken_rewrite(body, parent, targets):
    """A rewrite that also changes a fenced line: the post-condition must refuse it."""
    return body.replace(f"#{parent}", f"#{targets[0]}")


def test_a_rewrite_that_touches_more_than_the_blocked_by_line_writes_nothing():
    ports = ports_with(**{"7": "Blocked by: #5\n\n```\nsee #5\n```"})
    result = repoint_dependents(ports, 5, [9], refs_of(ports), {5}, rewrite=broken_rewrite)

    assert result == RepointResult(considered=1, changed=0, failed=1) and not result.complete
    assert ports.tracker.updated_bodies == [] and ports.tracker.commented_issues == []


def test_a_rewrite_that_changes_the_blockers_wrongly_writes_nothing():
    ports = ports_with(**{"7": "Blocked by: #5, #6"})
    result = repoint_dependents(
        ports, 5, [9], refs_of(ports), {5}, rewrite=lambda body, p, t: "Blocked by: #9"
    )

    assert result.failed == 1 and ports.tracker.updated_bodies == []


def test_a_failed_read_edit_or_comment_counts_failed_and_the_others_go_on():
    ports = ports_with(**{"7": "Blocked by: #5", "8": "Blocked by: #5", "10": "Blocked by: #5"})
    ports.inbox.task_errors = {7}
    ports.tracker.body_errors = {8}
    result = repoint_dependents(ports, 5, [9], refs_of(ports), {5})
    assert result == RepointResult(considered=3, changed=1, failed=2)

    quiet = ports_with(**{"7": "Blocked by: #5"})
    quiet.tracker.comment_on_issue_fails_on = {0}
    assert repoint_dependents(quiet, 5, [9], refs_of(quiet), {5}).failed == 1
    assert len(quiet.tracker.updated_bodies) == 1


def test_the_post_condition_rejects_a_fenced_line_changed_and_accepts_the_blocked_by_line():
    assert not rules.repoint_confined("```\nBlocked by: #5\n```", "```\nBlocked by: #9\n```", 5)
    assert not rules.repoint_confined("a\nBlocked by: #6", "a\nBlocked by: #9", 5)
    assert not rules.repoint_confined("Blocked by: #5", "Blocked by: #9\nextra", 5)
    assert rules.repoint_confined("a\r\nBlocked by: #5\r\n", "a\r\nBlocked by: #9\r\n", 5)


def test_split_repointed_at_round_trips_through_record_closure_load_and_the_queue(tmp_path):
    """The key a split document gets from the sweep is read back by the next round's queue."""
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save({"id": "GH-5", "title": "t", "description": "d", "status": "split"})
    ports = memory_ports(documents=documents)
    ports.tracker._queue = [5]
    assert "split_repointed_at" not in poll.queue_documents(ports)[5]

    documents.record_closure("GH-5", {"split_repointed_at": "2026-11-13T10:00:00+00:00"})

    assert documents.load("GH-5")["split_repointed_at"] == "2026-11-13T10:00:00+00:00"
    assert poll.queue_documents(ports)[5]["split_repointed_at"] == "2026-11-13T10:00:00+00:00"


# --- the sweep (GH-619 part 2) -------------------------------------------------------

PARTS = [
    {"title": "a", "body": "a", "depends_on": []},
    {"title": "b", "body": "b", "depends_on": [1]},
]


def split_state(task_id="GH-5", first=8, **more):
    """A split parent as an old document holds it: no depth on the parts, checklist posted."""
    return {
        "id": task_id,
        "title": "t",
        "description": "d",
        "status": "split",
        "parts": PARTS,
        "split_issues": {"1": URL.format(first), "2": URL.format(first + 1)},
        "tracker_checklist_posted": {"at": "t", "hash": "h"},
        **more,
    }


def sweep_ports(*states, **bodies):
    ports = ports_with(**bodies)
    ports.inbox.refs = refs_of(ports)
    for state in states:
        ports.documents.save(state)
    ports.tracker._queue = [int(s["id"][3:]) for s in states]
    return ports


def queued(ports):
    return poll.queue_documents(ports)


class CountingInbox(MemoryInbox):
    def __init__(self, **issues):
        super().__init__(**issues)
        self.task_calls = []
        self.task_error = None

    def task(self, number):
        self.task_calls.append(number)
        if self.task_error is not None:
            raise self.task_error
        return super().task(number)


def counting_ports(*states, **bodies):
    ports = memory_ports(
        inbox=CountingInbox(
            **{n: {"id": f"GH-{n}", "title": "t", "description": b} for n, b in bodies.items()}
        )
    )
    ports.tracker.inbox = ports.inbox
    ports.inbox.refs = refs_of(ports)
    for state in states:
        ports.documents.save(state)
    ports.tracker._queue = [int(s["id"][3:]) for s in states]
    return ports


def test_an_old_split_gets_its_dependent_re_pointed_once_and_recorded():
    ports = sweep_ports(split_state(), **{"7": "Blocked by: #5\n\nbody"})

    lines = split_sweep.sweep(ports, queued(ports))

    assert lines == ["agentic: split: GH-5: 1 dependents re-pointed"]
    assert ports.tracker.updated_bodies == [(7, "Blocked by: #9\n\nbody")]
    assert len(ports.tracker.commented_issues) == 1
    assert ports.documents.load("GH-5")["split_repointed_at"]

    ports.inbox.refs_calls = 0
    calls = len(ports.tracker.updated_bodies) + ports.tracker.comment_on_issue_calls
    assert split_sweep.sweep(ports, queued(ports)) == []
    assert ports.inbox.refs_calls == 0
    assert len(ports.tracker.updated_bodies) + ports.tracker.comment_on_issue_calls == calls


def test_a_cut_split_and_an_unparseable_url_are_skipped_without_a_record():
    cut = split_state("GH-5", split_issues={"1": URL.format(8)})
    bad = split_state("GH-6", first=20, split_issues={"1": "nope", "2": URL.format(21)})
    ports = sweep_ports(cut, bad, **{"7": "Blocked by: #5, #6"})

    assert split_sweep.repoint_targets(cut) is None and split_sweep.repoint_targets(bad) is None
    assert split_sweep.sweep(ports, queued(ports)) == []
    assert ports.inbox.refs_calls == 0 and ports.tracker.updated_bodies == []
    assert "split_repointed_at" not in ports.documents.load("GH-5")


def test_repoint_targets_are_the_last_parts_and_every_part_is_excluded():
    assert split_sweep.repoint_targets(split_state()) == ([9], {8, 9})


def test_a_rate_limit_on_the_first_dependent_stops_the_round_with_no_record():
    ports = counting_ports(
        split_state("GH-5", first=8),
        split_state("GH-6", first=20),
        **{"7": "Blocked by: #5", "11": "Blocked by: #6"},
    )
    ports.inbox.task_error = TrackerRateLimited("rate limited")

    assert split_sweep.sweep(ports, queued(ports)) == []
    assert ports.inbox.task_calls == [7]
    assert ports.tracker.updated_bodies == []
    assert "split_repointed_at" not in ports.documents.load("GH-5")
    assert "split_repointed_at" not in ports.documents.load("GH-6")

    shown = split_sweep.sweep(ports, queued(ports), report_failures=True)
    assert shown == ["agentic: split: re-point skipped: TrackerRateLimited"]


def test_a_rate_limit_on_the_shared_list_read_stops_the_round():
    ports = sweep_ports(split_state(), **{"7": "Blocked by: #5"})
    ports.inbox.refs_error = TrackerRateLimited("rate limited")

    assert split_sweep.sweep(ports, queued(ports)) == []
    assert split_sweep.sweep(ports, queued(ports), report_failures=True) == [
        "agentic: split: re-point skipped: TrackerRateLimited"
    ]
    assert ports.tracker.updated_bodies == [] and ports.inbox.refs_calls == 2
    assert "split_repointed_at" not in ports.documents.load("GH-5")


def test_a_plain_failure_leaves_the_parent_without_a_record_and_the_next_round_finishes():
    ports = sweep_ports(split_state(), **{"7": "Blocked by: #5"})
    ports.tracker.body_errors = {7}

    assert split_sweep.sweep(ports, queued(ports), report_failures=True) == [
        "agentic: split: GH-5: re-point unfinished"
    ]
    assert "split_repointed_at" not in ports.documents.load("GH-5")

    ports.tracker.body_errors = set()
    assert split_sweep.sweep(ports, queued(ports)) == [
        "agentic: split: GH-5: 1 dependents re-pointed"
    ]
    assert ports.documents.load("GH-5")["split_repointed_at"]


def test_an_ambiguous_write_posts_no_second_comment_and_the_next_sweep_records():
    ports = sweep_ports(split_state(), **{"7": "Blocked by: #5"})
    write = ports.tracker.update_issue_body

    def lost_answer(number, body):
        write(number, body)
        raise TrackerUnreachable("response lost")

    ports.tracker.update_issue_body = lost_answer
    assert split_sweep.sweep(ports, queued(ports)) == []
    assert "split_repointed_at" not in ports.documents.load("GH-5")

    ports.tracker.update_issue_body = write
    split_sweep.sweep(ports, queued(ports))
    assert (
        ports.tracker.commented_issues == [] and ports.documents.load("GH-5")["split_repointed_at"]
    )
    assert len(ports.tracker.updated_bodies) == 1


def test_a_split_that_never_had_dependents_is_recorded_without_a_line():
    ports = sweep_ports(split_state(), **{"7": "Blocked by: #2"})

    assert split_sweep.sweep(ports, queued(ports)) == []
    assert ports.documents.load("GH-5")["split_repointed_at"]


# --- the close and the escalation (GH-619 part 3) -------------------------------------

COMPLETED = IssueState("CLOSED", "COMPLETED", None, None, ())
NOT_PLANNED = IssueState("CLOSED", "NOT_PLANNED", None, None, ())
OPEN = IssueState("OPEN", "", None, None, ())


def resolved(number):
    return "resolved"


def close_ports(*states, parts=(COMPLETED, COMPLETED), first=8, **bodies):
    """Split parents in the queue, their parts (issues `first`, `first + 1`) scripted by state."""
    ports = sweep_ports(*states, **bodies)
    for offset, part in enumerate(parts):
        ports.tracker.states[first + offset] = part
    return ports


def judge(ports, verdict=resolved, **more):
    return split_sweep.sweep(ports, queued(ports), verdict=verdict, **more)


def test_split_parent_state_is_abandoned_for_a_not_planned_part_whose_verdict_is_resolved():
    assert rules.split_parent_state([("CLOSED", "NOT_PLANNED", "resolved")]) == "abandoned"
    assert (
        rules.split_parent_state(
            [("CLOSED", "COMPLETED", "resolved"), ("CLOSED", "NOT_PLANNED", "resolved")]
        )
        == "abandoned"
    )


def test_an_old_split_with_all_parts_merged_is_re_pointed_recorded_then_closed_with_its_comment():
    ports = close_ports(split_state(), **{"7": "Blocked by: #5\n\nbody"})
    ports.documents.save({"id": "GH-8", "pull_request": "https://github.com/o/r/pull/80"})
    ports.documents.save({"id": "GH-9", "status": "split", "title": "t", "description": "d"})
    order = []
    close = ports.tracker.close_issue

    def checked_close(task_id, *, comment=None):
        order.append("split_closed_at" in ports.documents.load(task_id))
        close(task_id, comment=comment)

    ports.tracker.close_issue = checked_close

    lines = judge(ports)

    assert lines == [
        "agentic: split: GH-5: 1 dependents re-pointed",
        "agentic: split: GH-5: parent closed",
    ]
    assert ports.tracker.updated_bodies == [(7, "Blocked by: #9\n\nbody")]
    assert order == [True] and ports.tracker.closed_issues == ["GH-5"]
    comment = ports.tracker.close_comments["GH-5"]
    assert "#8: https://github.com/o/r/pull/80" in comment and "#9: split again (see #9)" in comment

    # the parent is gone from the queue on the next round: nothing is called at all
    ports.tracker._queue = []
    ports.inbox.refs_calls = 0
    writes = (len(ports.tracker.updated_bodies), ports.tracker.comment_on_issue_calls)
    assert judge(ports) == [] and ports.inbox.refs_calls == 0
    assert (len(ports.tracker.updated_bodies), ports.tracker.comment_on_issue_calls) == writes
    assert ports.tracker.closed_issues == ["GH-5"]


def test_a_cut_after_the_record_keeps_the_parent_open_and_the_next_round_only_closes():
    ports = close_ports(split_state(), **{"7": "Blocked by: #5"})
    ports.tracker.close_issue_failures = [ValueError("gh issue close failed")]

    assert judge(ports, report_failures=True) == [
        "agentic: split: GH-5: 1 dependents re-pointed",
        "agentic: split: GH-5: close unfinished",
    ]
    assert ports.tracker.closed_issues == []
    first_record = ports.documents.load("GH-5")["split_closed_at"]

    assert judge(ports) == ["agentic: split: GH-5: parent closed"]
    assert ports.tracker.closed_issues == ["GH-5"]
    assert ports.documents.load("GH-5")["split_closed_at"] == first_record
    assert len(ports.tracker.updated_bodies) == 1


def test_a_record_that_cannot_be_written_means_no_close():
    ports = close_ports(split_state())

    def refuse(task_id, values):
        raise OSError("disk full")

    ports.documents.record_closure = refuse

    assert judge(ports, report_failures=True) == ["agentic: split: GH-5: close unfinished"]
    assert ports.tracker.closed_issues == []


def test_a_part_whose_verdict_is_unknown_leaves_the_parent_open_and_another_is_still_judged():
    first = split_state("GH-5", first=8)
    second = split_state("GH-6", first=20)
    ports = close_ports(first, second)
    ports.tracker.states.update({20: COMPLETED, 21: COMPLETED})

    judge(ports, verdict=lambda n: "unknown" if n == 9 else "resolved")

    assert ports.tracker.closed_issues == ["GH-6"]
    assert "split_closed_at" not in ports.documents.load("GH-5")


def test_part_verdict_reads_a_git_failure_as_unknown(tmp_path):
    ports = memory_ports()
    ports.tracker.states[8] = COMPLETED
    ports.documents.save({"id": "GH-8", "merge_commit": "abc"})

    def boom(*args, **kwargs):
        raise GitError("is_ancestor failed")

    ports.workspace.is_ancestor = boom
    cfg = Configuration(product_dir=tmp_path)

    assert poll._part_verdict(cfg, ports, ports.workspace.upstream, 8) == "unknown"


def test_a_part_still_in_the_queue_means_no_call_for_that_parent():
    ports = close_ports(split_state())
    ports.tracker._queue = [5, 9]
    ports.documents.save({"id": "GH-9", "title": "t", "description": "d"})

    assert judge(ports) == []
    assert ports.tracker.closed_issues == [] and ports.tracker.commented_issues == []


def abandoned_ports(parts=(COMPLETED, NOT_PLANNED), **bodies):
    return close_ports(split_state(), parts=parts, **bodies)


def test_a_not_planned_last_part_gets_one_comment_across_two_sweeps_and_the_parent_stays_open():
    ports = abandoned_ports()

    assert judge(ports) == ["agentic: split: GH-5: part 2 abandoned, escalated"]
    assert judge(ports) == []

    ((reference, text),) = ports.tracker.commented_issues
    assert reference == "5" and text.endswith("<!-- agentic-split-abandoned: GH-5 part 2 -->")
    assert "re-pointed dependents are now free" in text
    assert ports.tracker.closed_issues == [] and ports.tracker.labels == []
    assert ports.documents.load("GH-5")["split_abandoned_at"]


def test_a_not_planned_middle_part_says_the_dependents_still_wait_and_a_second_adds_nothing():
    ports = abandoned_ports(parts=(NOT_PLANNED, OPEN))

    assert judge(ports) == ["agentic: split: GH-5: part 1 abandoned, escalated"]
    ((_, text),) = ports.tracker.commented_issues
    assert "still wait on part(s) #9" in text and "may launch over this one" in text

    ports.tracker.states[9] = NOT_PLANNED
    assert judge(ports) == [] and len(ports.tracker.commented_issues) == 1


def test_a_marker_already_in_the_comments_posts_nothing_and_the_record_is_written():
    ports = abandoned_ports()
    ports.tracker.earlier = ["<!-- agentic-split-abandoned: GH-5 part 2 -->"]

    assert judge(ports) == ["agentic: split: GH-5: part 2 abandoned, escalated"]
    assert ports.tracker.commented_issues == []
    assert ports.documents.load("GH-5")["split_abandoned_at"]


def test_an_abandoned_part_that_is_reopened_and_merged_lets_the_parent_close():
    ports = abandoned_ports()
    judge(ports)
    ports.tracker.states[9] = COMPLETED

    assert judge(ports) == ["agentic: split: GH-5: parent closed"]
    assert ports.tracker.closed_issues == ["GH-5"]


def test_the_closing_note_says_no_pr_recorded_for_a_part_with_no_document():
    ports = close_ports(split_state())

    judge(ports)

    assert ports.tracker.close_comments["GH-5"].count("no PR recorded") == 2


def test_split_closed_at_and_split_abandoned_at_survive_a_real_document_round_trip(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save(split_state("GH-5"))
    documents.save(split_state("GH-6", first=20))
    ports = memory_ports(documents=documents)
    ports.tracker._queue = [5, 6]
    ports.tracker.states.update({8: COMPLETED, 9: COMPLETED, 20: COMPLETED, 21: NOT_PLANNED})

    split_sweep.sweep(ports, poll.queue_documents(ports), verdict=resolved)

    assert documents.load("GH-5")["split_closed_at"].endswith("+00:00")
    assert documents.load("GH-6")["split_abandoned_at"].endswith("+00:00")
    assert isinstance(poll.queue_documents(ports)[5]["split_closed_at"], str)
