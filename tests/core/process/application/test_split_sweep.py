"""`split_sweep.repoint_dependents` (GH-619): the dependents of a split task are re-read,
re-pointed to the last part(s) and told so, once; the parts and the task's own issue are
never edited; a rewrite that breaks the post-condition writes nothing."""

from agentic.core.process.application.split_sweep import RepointResult, repoint_dependents
from agentic.core.process.domain import rules
from agentic.core.process.domain.landing import IssueRef
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
