"""The baseline of one poller round's `gh` calls (GH-939): what a `poll.run_once` round over a
fixture product launches, by kind, and what GitHub's per-connection rule says that costs.

Seam: `poll.run_once` with real `GhTracker` and `GhInbox` adapters over a counting fake `Run`
that records each argv and spawns no child. The log is this test's own `InProcessGhCallLog`;
nothing here touches `composition.process_call_log()`. If a change makes a round launch more or
fewer `gh` calls, this test is where the number moves and the reviewer sees it."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.github.inbox import GhInbox
from agentic.core.process.adapters.github.tracker import GhTracker
from agentic.core.process.application import poll
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS, GhCallKind, kind_of
from agentic.core.process.domain.terminal_states import OpenView, TerminalMemo, served
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

# The `--json` fields that are connections of their own in GitHub's GraphQL schema.
NESTED_CONNECTIONS = frozenset({"labels", "comments", "assignees", "reviews", "commits", "files"})
GRAPHQL_KINDS: frozenset[GhCallKind] = frozenset({"issue_list", "issue_view", "pr_list", "pr_view"})


def estimated_points(argv: Sequence[str], items_returned: int) -> int:
    """A yardstick model of GitHub's per-connection rule, NOT a measurement: it never reads
    `used`. Each GraphQL-backed `gh` command is one query of at most 100 nodes on its root
    connection (1 point); each nested connection named in `--json` adds one point per query
    only when the root returned something to nest under. `gh api` and every other command cost
    nothing here. The real `used` also moves with other processes and with `gh`'s own paging,
    which is why the poller's `round C points` is compared with this, never equated."""
    if kind_of(argv[1:]) not in GRAPHQL_KINDS:
        return 0
    points = 1
    if items_returned and "--json" in argv:
        fields = argv[argv.index("--json") + 1].split(",")
        points += sum(1 for field in fields if field in NESTED_CONNECTIONS)
    return points


class CountingRun:
    """A `Run` that answers every call with an empty JSON list, records each argv and starts
    no process."""

    def __init__(self) -> None:
        self.argvs: list[Sequence[str]] = []

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.argvs.append(args)
        return subprocess.CompletedProcess(args, 0, "[]", "")


class LockDouble:
    def acquire(self, task_id: str, command: str) -> None:
        return None

    def release(self, task_id: str) -> None:
        return None

    def read(self, task_id: str) -> None:
        return None

    def refusal(self, task_id: str) -> None:
        return None


def one_round(tmp_path: Path) -> tuple[list[str], CountingRun, Mapping[GhCallKind, int]]:
    log = InProcessGhCallLog()
    run = CountingRun()
    ports = memory_ports(
        tracker=GhTracker(tmp_path, run, calls=log),
        inbox=GhInbox(tmp_path, run=run, calls=log),
    )
    lines = poll.run_once(
        Configuration(product_dir=tmp_path),
        ports,
        MemoryRunRegistry(),
        FakeLauncher(),
        datetime(2026, 11, 27, tzinfo=UTC),
        "agentic",
        lock=LockDouble(),
        current_boot=None,
    )
    return lines, run, log.drain()


def test_an_idle_round_launches_four_issue_lists_and_nothing_else(tmp_path) -> None:
    lines, run, calls = one_round(tmp_path)

    assert lines == ["agentic: poll: nothing to do this round"]
    assert {kind: calls[kind] for kind in GH_CALL_KINDS} == {
        "issue_list": 4,
        "issue_view": 0,
        "pr_list": 0,
        "pr_view": 0,
        "api": 0,
        "other": 0,
    }
    assert sum(calls.values()) == len(run.argvs)  # every launch the fake saw was counted


def test_the_recorded_argv_say_which_lists_a_round_reads(tmp_path) -> None:
    _, run, _ = one_round(tmp_path)

    labels = [argv[argv.index("--label") + 1] if "--label" in argv else None for argv in run.argvs]
    assert labels == ["agentic", "agentic", "agentic-hold", None]


def test_the_estimated_points_of_an_idle_round_are_one_per_empty_list(tmp_path) -> None:
    _, run, _ = one_round(tmp_path)

    assert sum(estimated_points(argv, items_returned=0) for argv in run.argvs) == 4


def test_the_model_charges_a_nested_connection_only_under_a_returned_node() -> None:
    argv = ["gh", "issue", "list", "--json", "url,title,body,labels", "--limit", "1000"]

    assert estimated_points(argv, items_returned=0) == 1
    assert estimated_points(argv, items_returned=3) == 2
    assert estimated_points(["gh", "api", "rate_limit"], items_returned=3) == 0


# --- GH-992: the terminal memo of determinate blocker closures --------------------------------

CLOSED_BLOCKER = 50  # CLOSED, COMPLETED, closed by pull request 60 whose merge commit is known
OPEN_BLOCKER = 200
CANDIDATES = (100, 101)


def issue_url(number: int) -> str:
    return f"https://github.com/o/r/issues/{number}"


class BlockerRun:
    """A counting `Run` that answers a queue of `CANDIDATES`, each held by `CLOSED_BLOCKER` and
    `OPEN_BLOCKER`. `closed_blocker_state` and `open_listing` may be changed between rounds;
    `open_listing` is the issue numbers of the open listing, or `None` for a failed read."""

    def __init__(self) -> None:
        self.argvs: list[Sequence[str]] = []
        self.items: list[int] = []
        self.closed_blocker_state = "CLOSED"
        self.open_listing: list[int] | None = [*CANDIDATES, OPEN_BLOCKER]

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.argvs.append(args)
        answer = self.answer(list(args))
        if answer is None:
            self.items.append(0)
            return subprocess.CompletedProcess(args, 1, "", "boom")
        self.items.append(len(answer) if isinstance(answer, list) else 1)
        return subprocess.CompletedProcess(args, 0, json.dumps(answer), "")

    def answer(self, argv: list[str]) -> Any:
        if argv[1:3] == ["issue", "list"]:
            if "--label" in argv:
                label = argv[argv.index("--label") + 1]
                return [{"number": n} for n in CANDIDATES] if label == "agentic" else []
            if self.open_listing is None:
                return None
            return [
                {"url": issue_url(n), "title": "t", "body": "", "labels": []}
                for n in self.open_listing
            ]
        if argv[1:3] == ["issue", "view"]:
            return self.issue(int(argv[3]))
        if argv[1:3] == ["api", "graphql"]:
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "state": "MERGED",
                            "createdAt": "2026-11-01T00:00:00Z",
                            "mergedAt": "2026-11-02T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "commits": {"nodes": []},
                        }
                    }
                }
            }
        return []

    def issue(self, number: int) -> dict[str, Any]:
        if number in CANDIDATES:
            nodes = [{"number": CLOSED_BLOCKER}, {"number": OPEN_BLOCKER}]
            return self.view("OPEN", "", nodes, [])
        if number == CLOSED_BLOCKER:
            closed = self.closed_blocker_state == "CLOSED"
            return self.view(
                self.closed_blocker_state,
                "COMPLETED" if closed else "",
                [],
                [{"url": "https://github.com/o/r/pull/60"}] if closed else [],
            )
        return self.view("OPEN", "", [], [])

    @staticmethod
    def view(state: str, reason: str, nodes: list[dict[str, int]], closing: list[dict[str, str]]):
        return {
            "state": state,
            "stateReason": reason,
            "blockedBy": {"nodes": nodes},
            "closedByPullRequestsReferences": closing,
            "labels": [],
        }


class Totals:
    """What a run of rounds cost: the calls by kind and the model's estimated points."""

    def __init__(self, run: BlockerRun, log: InProcessGhCallLog) -> None:
        self.run = run
        self.log = log

    def calls(self) -> int:
        return len(self.run.argvs)

    def points(self) -> int:
        return sum(estimated_points(a, n) for a, n in zip(self.run.argvs, self.run.items))

    def reads_of(self, number: int) -> int:
        return sum(1 for a in self.run.argvs if a[1:3] == ["issue", "view"] and a[3] == str(number))


def rounds(
    tmp_path: Path,
    count: int,
    memo: TerminalMemo | None,
    before: Callable[[BlockerRun, int], None] = lambda run, index: None,
) -> tuple[Totals, list[list[str]]]:
    log = InProcessGhCallLog()
    run = BlockerRun()
    ports = memory_ports(
        tracker=GhTracker(tmp_path, run, calls=log),
        inbox=GhInbox(tmp_path, run=run, calls=log),
    )
    registry = MemoryRunRegistry()
    lines: list[list[str]] = []
    for index in range(count):
        before(run, index)
        lines.append(
            poll.run_once(
                Configuration(product_dir=tmp_path),
                ports,
                registry,
                FakeLauncher(),
                datetime(2026, 11, 27, tzinfo=UTC),
                "agentic",
                lock=LockDouble(),
                current_boot=None,
                terminal_memo=memo,
            )
        )
    return Totals(run, log), lines


def test_a_shared_memo_lowers_the_calls_and_points_of_a_second_round(tmp_path) -> None:
    without, lines_without = rounds(tmp_path, 2, None)
    with_memo, lines_with = rounds(tmp_path, 2, TerminalMemo())

    assert lines_with == lines_without  # no decision changes
    assert with_memo.calls() < without.calls()
    assert with_memo.points() < without.points()
    assert with_memo.reads_of(CLOSED_BLOCKER) == 1  # round 1 reads it, round 2 is served


def test_two_candidates_sharing_a_closed_blocker_read_it_once_in_a_round(tmp_path) -> None:
    without, _ = rounds(tmp_path, 1, None)
    with_memo, _ = rounds(tmp_path, 1, TerminalMemo())

    assert without.reads_of(CLOSED_BLOCKER) == len(CANDIDATES)
    assert with_memo.reads_of(CLOSED_BLOCKER) == 1


def test_a_reopened_blocker_in_the_open_listing_is_read_live_and_judged_open(tmp_path) -> None:
    memo = TerminalMemo()

    def reopen(run: BlockerRun, index: int) -> None:
        if index:
            run.closed_blocker_state = "OPEN"
            run.open_listing = [*CANDIDATES, CLOSED_BLOCKER, OPEN_BLOCKER]

    totals, lines = rounds(tmp_path, 2, memo, before=reopen)
    expected, expected_lines = rounds(tmp_path, 2, None, before=reopen)

    assert totals.reads_of(CLOSED_BLOCKER) == 1 + len(CANDIDATES)  # round 2: live, per candidate
    assert lines == expected_lines  # the same decision as with no memo
    reopened_view = OpenView(memo, frozenset({CLOSED_BLOCKER}))
    assert served(memo, CLOSED_BLOCKER, reopened_view) is None


def test_a_failed_open_listing_reads_exactly_as_with_no_memo(tmp_path) -> None:
    def failed(run: BlockerRun, index: int) -> None:
        run.open_listing = None

    without, lines_without = rounds(tmp_path, 2, None, before=failed)
    memo = TerminalMemo()
    with_memo, lines_with = rounds(tmp_path, 2, memo, before=failed)

    assert [list(a) for a in with_memo.run.argvs] == [list(a) for a in without.run.argvs]
    assert lines_with == lines_without


def test_a_cut_open_listing_reads_exactly_as_with_no_memo(tmp_path) -> None:
    def cut(run: BlockerRun, index: int) -> None:
        run.open_listing = list(range(1000, 2000))  # as long as the limit it was asked for

    without, _ = rounds(tmp_path, 2, None, before=cut)
    with_memo, _ = rounds(tmp_path, 2, TerminalMemo(), before=cut)

    assert [list(a) for a in with_memo.run.argvs] == [list(a) for a in without.run.argvs]


def test_a_closure_kept_by_a_good_round_is_not_served_on_a_round_with_a_failed_listing(
    tmp_path,
) -> None:
    def fails_after_the_first(run: BlockerRun, index: int) -> None:
        run.open_listing = None if index else [*CANDIDATES, OPEN_BLOCKER]

    totals, _ = rounds(tmp_path, 2, TerminalMemo(), before=fails_after_the_first)

    # Round 1 reads it once (the second candidate is served); round 2 reads it for each.
    assert totals.reads_of(CLOSED_BLOCKER) == 1 + len(CANDIDATES)
