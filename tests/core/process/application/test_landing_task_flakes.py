"""The flake issues of a task's `flakes` record (ADR-0066): one issue per check, one comment
per (issue, task) when the task brings a test id the issue does not name, and a tracker that
fails never raises."""

from agentic.core.process.application.landing_flakes import file_task_flakes
from agentic.core.process.domain import landing as landing_rules
from tests.fakes import MemoryDocuments, MemoryInbox, MemoryTracker, memory_ports

ISSUE = "https://github.com/o/r/issues/9"


def record(check="tests", head="h1", gate="delivery", tests=()):
    return {"check": check, "head": head, "gate": gate, "tests": list(tests)}


class FullDisk(MemoryDocuments):
    def record_closure(self, task_id, values):
        raise OSError("disk full")


class BrokenInbox(MemoryInbox):
    def open_issue_bodies(self):
        raise OSError("no network")


def world(flakes, *, open_issues=None, documents=None, inbox=None):
    documents = documents if documents is not None else MemoryDocuments()
    documents.saved.append({"id": "GH-5", "flakes": flakes})
    ports = memory_ports(
        documents=documents,
        inbox=inbox if inbox is not None else MemoryInbox(open_issues=open_issues),
        tracker=MemoryTracker(),
    )
    return ports, documents


def run(ports, documents, log=None):
    raw = documents.load("GH-5")
    return file_task_flakes(ports, "GH-5", raw, (log if log is not None else []).append)


def test_a_new_check_opens_one_issue_naming_the_observed_heads():
    ports, documents = world(
        [record(head="h1", tests=["t::a"]), record(head="h2", gate="merge", tests=["t::b"])]
    )

    urls = run(ports, documents)

    [(title, body)] = ports.tracker.opened_issues
    assert title == "Flaky check: tests" and len(urls) == 1
    assert ports.tracker.opened_labels == ["agentic"]
    assert body.splitlines()[0] == landing_rules.flake_marker("tests")
    assert "h1" in body and "h2" in body and "merge" in body and "t::a" in body and "t::b" in body
    assert "GH-5" in body


def test_an_open_issue_and_a_new_test_id_gives_one_comment_and_a_recorded_reference():
    ports, documents = world(
        [record(tests=["t::new"])],
        open_issues=[(ISSUE, landing_rules.flake_marker("tests") + "\nt::old")],
    )

    run(ports, documents)

    [(reference, text)] = ports.tracker.commented_issues
    assert reference == ISSUE and "t::new" in text and "h1" in text
    assert ports.tracker.opened_issues == []
    assert documents.load("GH-5")["flake_comments"] == [ISSUE]


def test_a_second_call_comments_nothing():
    ports, documents = world(
        [record(tests=["t::new"])],
        open_issues=[(ISSUE, landing_rules.flake_marker("tests") + "\nt::old")],
    )

    run(ports, documents)
    run(ports, documents)

    assert len(ports.tracker.commented_issues) == 1
    assert documents.load("GH-5")["flake_comments"] == [ISSUE]


def test_an_open_issue_with_no_new_id_gets_no_comment_and_writes_nothing():
    ports, documents = world(
        [record(tests=["t::old"]), record(head="h2", tests=[])],
        open_issues=[(ISSUE, landing_rules.flake_marker("tests") + "\nt::old")],
    )

    run(ports, documents)

    assert ports.tracker.commented_issues == []
    assert documents.closures == {}


def test_two_checks_give_two_issues():
    ports, documents = world([record(check="tests"), record(check="web-e2e", tests=["w::x"])])

    run(ports, documents)

    assert [t for t, _ in ports.tracker.opened_issues] == [
        "Flaky check: tests",
        "Flaky check: web-e2e",
    ]


def test_a_record_with_no_flakes_files_nothing():
    ports, documents = world([])

    assert run(ports, documents) == []
    assert ports.tracker.opened_issues == [] and ports.tracker.commented_issues == []


def test_a_tracker_error_is_logged_and_the_function_returns_normally():
    ports, documents = world(
        [record(check="tests"), record(check="lint", tests=["l::x"])],
        open_issues=[(ISSUE, landing_rules.flake_marker("lint") + "\n")],
    )
    ports.tracker.open_issue_fails_on = {0}
    ports.tracker.comment_on_issue_fails_on = {0}
    log: list[str] = []

    assert run(ports, documents, log) == []

    assert len(log) == 2 and all(line.startswith("agentic: land: GH-5: ") for line in log)
    assert documents.closures == {}


def test_an_unreadable_inbox_is_logged_and_nothing_is_filed():
    ports, documents = world([record()], inbox=BrokenInbox())
    log: list[str] = []

    assert run(ports, documents, log) == []
    assert log and log[0].startswith("agentic: land: GH-5: ")
    assert ports.tracker.opened_issues == []


def test_a_document_that_cannot_record_the_comment_is_logged_and_does_not_raise():
    ports, documents = world(
        [record(tests=["t::new"])],
        open_issues=[(ISSUE, landing_rules.flake_marker("tests") + "\n")],
        documents=FullDisk(),
    )
    log: list[str] = []

    run(ports, documents, log)

    assert any("disk full" in line for line in log)


def test_a_malformed_flakes_value_is_ignored():
    ports, documents = world("not a list")

    assert run(ports, documents) == []
    assert ports.tracker.opened_issues == []
