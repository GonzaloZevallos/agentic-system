"""`GhCommitCi`, the CI of a commit by its sha, with a fake `gh` behind a real `GhTracker`.

The payload in `fixtures/commit_ci_synthetic.json` is SYNTHETIC: written by hand to the shape of
`COMMIT_CI_QUERY`, not captured from GitHub (no role of the run has `gh` to record one). The test
names say so: `test_synthetic_payload_<case>`. Nothing here depends on a pull request body."""

import json
import subprocess
from pathlib import Path

import pytest

from agentic.composition import stage0_master_window
from agentic.core.process.adapters.git.master_window import GitMasterWindow
from agentic.core.process.adapters.github import GhTracker
from agentic.core.process.adapters.github.commit_ci import GhCommitCi
from agentic.core.process.adapters.github.tracker import COMMIT_CI_QUERY, commit_report
from agentic.core.process.domain import stage0_window
from agentic.core.process.domain.ci import CiReport, CiUnreadable

FIXTURE = Path(__file__).parent / "fixtures" / "commit_ci_synthetic.json"
OID = "1" * 40


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _commit() -> dict:
    return _payload()["data"]["repository"]["object"]


class Recorder:
    def __init__(self, stdout, error=None):
        self.calls = []
        self.stdout = stdout
        self.error = error

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def _answering(payload) -> Recorder:
    return Recorder(payload if isinstance(payload, str) else json.dumps(payload))


def _reader(run) -> GhCommitCi:
    return GhCommitCi(GhTracker(Path("/p"), run, log=lambda line: None))


def _state(payload, names):
    report = _reader(_answering(payload)).read(OID)
    return stage0_window.ci_state(report, frozenset(names))


def _with_object(obj) -> dict:
    return {"data": {"repository": {"object": obj}}}


def test_synthetic_payload_says_in_its_own_file_that_it_is_synthetic():
    assert "SYNTHETIC" in _payload()["_note"]
    assert "not captured from GitHub" in _payload()["_note"]


def test_synthetic_payload_all_green():
    assert _state(_payload(), {"lint", "ci/legacy"}) == "green"


def test_synthetic_payload_a_red_current_run():
    assert _state(_payload(), {"lint", "e2e"}) == "red"


def test_synthetic_payload_a_pending_run():
    assert _state(_payload(), {"lint", "slow"}) == "pending"


def test_synthetic_payload_a_name_whose_only_runs_are_cancelled():
    report = _reader(_answering(_payload())).read(OID)
    assert isinstance(report, CiReport) and report.cancelled_runs == frozenset({"105"})
    assert stage0_window.ci_state(report, frozenset({"docs"})) == "cancelled"


def test_synthetic_payload_an_unreadable_response():
    assert _reader(_answering("not json")).read(OID) == CiUnreadable("gh_unreadable")
    assert _state("not json", {"lint"}) == "unknown"


def test_synthetic_payload_a_superseded_red_run_does_not_count():
    # `lint` failed in run 99 and passed in run 101: only the current run decides.
    assert _state(_payload(), {"lint"}) == "green"


def _more_checks():
    commit = _commit()
    commit["statusCheckRollup"]["contexts"]["pageInfo"]["hasNextPage"] = True
    return _with_object(commit)


def _other_oid():
    return _with_object({**_commit(), "oid": "2" * 40})


@pytest.mark.parametrize(
    "payload",
    [
        _with_object(None),
        _with_object({}),  # an object that is not a Commit selects nothing
        {"data": {"repository": None}},
        {"data": None},
        [],
        _more_checks(),
        _other_oid(),
    ],
    ids=["object-null", "not-a-commit", "no-repository", "null-data", "list", "cut", "other-oid"],
)
def test_synthetic_payload_an_answer_that_is_not_whole_or_not_that_commit_is_unknown(payload):
    assert _reader(_answering(payload)).read(OID) == CiUnreadable("gh_unreadable")
    assert _state(payload, {"lint"}) == "unknown"


@pytest.mark.parametrize("oid", ["abc1234", "A" * 40, "", "1" * 39, "1" * 41, f"{OID}\n", "g" * 40])
def test_a_malformed_oid_makes_no_gh_call(oid):
    run = _answering(_payload())
    assert _reader(run).read(oid) == CiUnreadable("gh_unreadable")
    assert run.calls == []


def test_a_valid_oid_is_one_call_sent_as_a_raw_string():
    run = _answering(_payload())
    assert isinstance(_reader(run).read(OID), CiReport)
    [call] = run.calls
    assert call[:3] == ["gh", "api", "graphql"]
    flags = [call[i - 1] for i, arg in enumerate(call) if arg == f"oid={OID}"]
    assert flags == ["-f"]  # one oid field, never `-F`, which would coerce or read `@file`


def test_a_timed_out_gh_is_unreadable_never_raised():
    run = Recorder("", error=subprocess.TimeoutExpired(["gh"], 60))
    assert _reader(run).read(OID) == CiUnreadable("gh_unreadable")
    assert len(run.calls) == 1  # never retried


def test_a_missing_gh_is_unavailable_never_raised():
    run = Recorder("", error=FileNotFoundError("gh"))
    assert _reader(run).read(OID) == CiUnreadable("gh_unavailable")


def test_a_failing_gh_is_unreadable_never_raised():
    run = Recorder("", error=subprocess.CalledProcessError(1, ["gh"], stderr="no auth"))
    assert _reader(run).read(OID) == CiUnreadable("gh_unreadable")


def test_synthetic_payload_by_sha_reads_the_same_report_as_by_branch():
    commit = _commit()
    by_branch = GhTracker(
        Path("/p"),
        _answering({"data": {"repository": {"ref": {"target": commit}}}}),
        log=lambda line: None,
    ).branch_ci("master")
    assert isinstance(by_branch, CiReport)
    assert commit_report(commit) == by_branch
    assert _reader(_answering(_payload())).read(OID) == by_branch


@pytest.mark.parametrize(
    "field",
    [
        "oid",
        "statusCheckRollup",
        "contexts(first: 100)",
        "pageInfo { hasNextPage }",
        "nodes",
        "__typename",
        "... on CheckRun { id name status conclusion detailsUrl startedAt completedAt }",
        "... on StatusContext { context state targetUrl }",
        "checkSuites(first: 50)",
        "nodes { status conclusion workflowRun { databaseId } }",
        "object(oid: $oid)",
        "... on Commit",
    ],
)
def test_the_query_asks_for_every_field_the_parser_reads(field):
    assert field in COMMIT_CI_QUERY


def test_the_query_takes_the_oid_as_a_git_object_id_and_not_the_ghost_run_count():
    assert "$oid: GitObjectID!" in COMMIT_CI_QUERY
    assert "checkRuns(first: 0)" not in COMMIT_CI_QUERY


def test_the_factory_builds_a_window_that_reads_ci_by_sha():
    window = stage0_master_window(
        Path("/r"), "https://example.test/o/r.git", Path("/p"), lambda s: ()
    )
    assert isinstance(window, GitMasterWindow)
    assert isinstance(window.ci, GhCommitCi)
