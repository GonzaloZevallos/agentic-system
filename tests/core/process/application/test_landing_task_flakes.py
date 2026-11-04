"""The flake issues of a task's `flakes` record (ADR-0066, GH-464): nothing is asked of the
tracker until a key is over the threshold, then one issue per check, one comment per (issue,
task) when the task brings a test id the issue does not name, and a tracker that fails never
raises."""

from datetime import UTC, datetime, timedelta

from agentic.core.process.application.landing_flakes import file_task_flakes
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.landing import IssueRef
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryDocuments,
    MemoryInbox,
    MemoryRunRegistry,
    MemoryTracker,
    memory_ports,
)

ISSUE = "https://github.com/o/r/issues/9"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
CFG = Configuration(product_dir="/p")


def record(check="tests", head="h1", gate="delivery", tests=(), at=None):
    return {
        "check": check,
        "head": head,
        "gate": gate,
        "tests": list(tests),
        "at": (at or NOW - timedelta(days=1)).isoformat(),
    }


class FullDisk(MemoryDocuments):
    def record_closure(self, task_id, values):
        raise OSError("disk full")


class BrokenInbox(MemoryInbox):
    def open_issue_refs(self):
        raise OSError("no network")


REGISTRY = MemoryRunRegistry()


def world(flakes, *, open_issues=None, documents=None, inbox=None, prior=2):
    """GH-5 holds `flakes`; `prior` other tasks on disk (GH-3, GH-4) hold the same ones, which
    with the threshold of three puts every key of `flakes` over it."""
    documents = documents if documents is not None else MemoryDocuments()
    documents.saved.append({"id": "GH-5", "flakes": flakes})
    for n in range(prior):
        if isinstance(flakes, list):
            documents.saved.append({"id": f"GH-{3 + n}", "flakes": flakes})
    ports = memory_ports(
        documents=documents,
        inbox=inbox if inbox is not None else MemoryInbox(open_issues=open_issues),
        tracker=MemoryTracker(),
    )
    return ports, documents


def run(ports, documents, log=None, *, registry=None, head=None):
    raw = documents.load("GH-5")
    registry = registry if registry is not None else MemoryRunRegistry()
    return file_task_flakes(
        ports,
        "GH-5",
        raw,
        (log if log is not None else []).append,
        cfg=CFG,
        now=NOW,
        flake_lock=lambda: registry.flake_lock("p"),
        head=head,
    )


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


def test_the_first_flake_is_only_a_record_and_the_tracker_is_never_asked():
    ports, documents = world([record(tests=["t::a"])], prior=0)
    registry = MemoryRunRegistry()

    assert run(ports, documents, registry=registry) == []

    assert ports.inbox.refs_calls == 0 and registry.flake_locks == []
    assert ports.tracker.opened_issues == [] and ports.tracker.commented_issues == []


def test_two_prior_tasks_on_disk_and_the_current_one_in_memory_open_the_issue_in_that_call():
    documents = MemoryDocuments()
    for task in ("GH-3", "GH-4"):
        documents.saved.append({"id": task, "flakes": [record(tests=["t::a"])]})
    documents.saved.append({"id": "GH-5", "flakes": []})
    ports = memory_ports(documents=documents, inbox=MemoryInbox(), tracker=MemoryTracker())
    raw = {"id": "GH-5", "flakes": [record(tests=["t::a"])]}
    registry = MemoryRunRegistry()

    urls = file_task_flakes(
        ports, "GH-5", raw, [].append, cfg=CFG, now=NOW, flake_lock=lambda: registry.flake_lock("p")
    )

    [(title, body)] = ports.tracker.opened_issues
    assert title == "Flaky check: tests" and len(urls) == 1 and "`t::a`" in body
    assert registry.flake_locks == ["p"] and not registry.flake_lock_open


def test_three_tasks_with_three_different_ids_file_nothing():
    documents = MemoryDocuments()
    documents.saved.append({"id": "GH-3", "flakes": [record(tests=["t::a"])]})
    documents.saved.append({"id": "GH-4", "flakes": [record(tests=["t::b"])]})
    ports = memory_ports(documents=documents, inbox=MemoryInbox(), tracker=MemoryTracker())
    raw = {"id": "GH-5", "flakes": [record(tests=["t::c"])]}
    registry = MemoryRunRegistry()

    file_task_flakes(
        ports, "GH-5", raw, [].append, cfg=CFG, now=NOW, flake_lock=lambda: registry.flake_lock("p")
    )

    assert ports.inbox.refs_calls == 0 and ports.tracker.opened_issues == []


def test_a_record_older_than_the_window_does_not_count():
    old = record(tests=["t::a"], at=NOW - timedelta(days=15))
    ports, documents = world([old])

    assert run(ports, documents) == []
    assert ports.inbox.refs_calls == 0


def test_the_landing_rerun_step_counts_as_a_check_level_record():
    documents = MemoryDocuments()
    for task in ("GH-3", "GH-4"):
        documents.saved.append({"id": task, "flakes": [record(tests=[])]})
    ports = memory_ports(documents=documents, inbox=MemoryInbox(), tracker=MemoryTracker())
    raw = {"id": "GH-5", "landing": [{"step": "rerun", "head": "h9", "checks": ["tests"]}]}
    registry = MemoryRunRegistry()

    file_task_flakes(
        ports,
        "GH-5",
        raw,
        [].append,
        cfg=CFG,
        now=NOW,
        flake_lock=lambda: registry.flake_lock("p"),
        head="h9",
    )

    [(title, body)] = ports.tracker.opened_issues
    assert title == "Flaky check: tests" and "h9" in body


def test_a_lock_that_cannot_be_taken_is_logged_and_the_filing_skipped():
    ports, documents = world([record(tests=["t::a"])])
    registry = MemoryRunRegistry()
    registry.flake_lock_acquired = False
    log: list[str] = []

    assert run(ports, documents, log, registry=registry) == []

    assert log == ["agentic: land: GH-5: flakes: lock_unavailable"]
    assert ports.inbox.refs_calls == 0 and ports.tracker.opened_issues == []


def test_a_saturated_list_is_logged_and_the_filing_skipped():
    ports, documents = world([record(tests=["t::a"])])
    ports.inbox.refs_error = ValueError("gh issue list returned 1000 issues")
    log: list[str] = []

    assert run(ports, documents, log) == []

    assert len(log) == 1 and log[0].startswith("agentic: land: GH-5: flakes: list_saturated")
    assert ports.tracker.opened_issues == []


def test_an_issue_found_by_its_title_without_the_marker_gets_a_comment_not_a_second_issue():
    ports, documents = world([record(tests=["t::a"])])
    ports.inbox.refs = [IssueRef(ISSUE, "Flaky check: tests", "no marker here")]

    assert run(ports, documents) == []

    [(reference, text)] = ports.tracker.commented_issues
    assert reference == ISSUE and "t::a" in text
    assert ports.tracker.opened_issues == []


def test_a_filing_without_a_lock_source_is_skipped():
    ports, documents = world([record(tests=["t::a"])])
    raw = documents.load("GH-5")
    log: list[str] = []

    file_task_flakes(ports, "GH-5", raw, log.append, cfg=CFG, now=NOW, flake_lock=None)

    assert log == ["agentic: land: GH-5: flakes: lock_unavailable"]
    assert ports.tracker.opened_issues == []


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
