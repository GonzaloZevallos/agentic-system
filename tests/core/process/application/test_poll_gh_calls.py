"""The baseline of one poller round's `gh` calls (GH-939): what a `poll.run_once` round over a
fixture product launches, by kind, and what GitHub's per-connection rule says that costs.

Seam: `poll.run_once` with real `GhTracker` and `GhInbox` adapters over a counting fake `Run`
that records each argv and spawns no child. The log is this test's own `InProcessGhCallLog`;
nothing here touches `composition.process_call_log()`. If a change makes a round launch more or
fewer `gh` calls, this test is where the number moves and the reviewer sees it."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.github.inbox import GhInbox
from agentic.core.process.adapters.github.tracker import GhTracker
from agentic.core.process.application import poll
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS, GhCallKind, kind_of
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
