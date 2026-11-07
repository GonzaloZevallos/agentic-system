"""`GhCiLogs` (GH-449): the failed jobs' logs of a pull request head through `gh`, bounded.
The report and the stream are fakes (no network); `bounded_stream` itself runs a local
shell command so its byte cap and its timeout are the real ones."""

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import ci_logs
from agentic.core.process.adapters.github.ci_logs import (
    CI_FLAKE_LOG_BYTES,
    GhCiLogs,
    _Stream,
)
from agentic.core.process.adapters.github.run import StreamRead, bounded_stream
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, JobLog

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "ci_logs"
HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
OTHER = "9f3e1a2b4c5d6e7f8091a2b3c4d5e6f708192a3b"
PR = "https://github.com/o/r/pull/7"


def check(name: str, conclusion: str | None = "FAILURE", job: int = 11, run: int = 5) -> CiCheck:
    link = f"https://github.com/o/r/actions/runs/{run}/job/{job}"
    return CiCheck(name, "COMPLETED", conclusion, link)


class Fake:
    """A report and a stream that remember what they were asked."""

    def __init__(
        self, checks: Sequence[CiCheck], *, head: str = HEAD, answer: StreamRead | None = None
    ) -> None:
        self.report: CiReport | CiUnreadable = CiReport(head, "master", None, None, tuple(checks))
        self.answer = answer or StreamRead(b"E   boom\n", 0, 0, False)
        self.calls: list[tuple[Sequence[str], int, float]] = []
        self.flake_stream: _Stream | None = None
        self.flake_cap = CI_FLAKE_LOG_BYTES

    def ci_report(self, reference: str) -> CiReport | CiUnreadable:
        assert reference == PR
        return self.report

    def stream(self, args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float):
        self.calls.append((args, max_bytes, timeout_s))
        return self.answer

    def logs(self, head: str = HEAD) -> list[JobLog]:
        adapter = GhCiLogs(Path("."), self.ci_report, stream=self.stream)
        return adapter.failed_job_logs(PR, head)


def test_a_red_job_of_the_head_is_read_with_its_run_and_job_ids() -> None:
    fake = Fake([check("check", job=11, run=5)])
    (log,) = fake.logs()
    assert (log.job, log.cause, log.text, log.bytes) == ("check", "read", "E   boom\n", 9)
    assert log.link == "https://github.com/o/r/actions/runs/5/job/11"
    ((args, max_bytes, timeout_s),) = fake.calls
    assert list(args) == ["gh", "run", "view", "5", "--job", "11", "--log-failed"]
    assert max_bytes == 1024 * 1024
    assert 0 < timeout_s <= 60


def test_green_and_pending_jobs_are_not_read() -> None:
    waiting = CiCheck("b", "IN_PROGRESS", None, "https://github.com/o/r/actions/runs/5/job/12")
    fake = Fake([check("a", "SUCCESS"), waiting, check("c", "FAILURE", job=13)])
    assert [log.job for log in fake.logs()] == ["c"]
    assert len(fake.calls) == 1


def test_a_report_for_another_head_downloads_nothing() -> None:
    fake = Fake([check("check")], head=OTHER)
    (log,) = fake.logs()
    assert (log.cause, log.text, log.bytes) == ("other_head", "", 0)
    assert fake.calls == []


def test_a_prefix_of_the_head_is_the_same_head() -> None:
    fake = Fake([check("check")], head=HEAD[:12])
    assert [log.cause for log in fake.logs()] == ["read"]


def test_at_most_four_jobs_are_read() -> None:
    fake = Fake([check(f"shard {n}", job=20 + n) for n in range(6)])
    assert len(fake.logs()) == 4
    assert len(fake.calls) == 4


def test_a_timeout_is_a_closed_cause() -> None:
    fake = Fake([check("check")], answer=StreamRead(b"partial", 0, 0, True))
    (log,) = fake.logs()
    assert (log.cause, log.text) == ("timed_out", "")


def test_a_timeout_after_the_cap_was_filled_is_still_a_read_log() -> None:
    full = b"x" * (1024 * 1024)
    fake = Fake([check("check")], answer=StreamRead(full, 2048, -9, True))
    (log,) = fake.logs()
    assert (log.cause, log.bytes, log.truncated_bytes) == ("read", len(full), 2048)


def test_a_failing_exit_below_the_cap_stays_unreadable_even_after_a_timeout_kill() -> None:
    fake = Fake([check("check")], answer=StreamRead(b"x" * 10, 0, -9, False))
    (log,) = fake.logs()
    assert log.cause == "unreadable"


def test_empty_output_is_empty() -> None:
    fake = Fake([check("check")], answer=StreamRead(b" \n", 0, 0, False))
    assert [log.cause for log in fake.logs()] == ["empty"]


def test_the_bytes_past_the_cap_are_counted_not_kept() -> None:
    fake = Fake([check("check")], answer=StreamRead(b"x" * 100, 4096, 0, False))
    (log,) = fake.logs()
    assert (log.cause, log.bytes, log.truncated_bytes) == ("read", 100, 4096)


@pytest.mark.parametrize(
    "answer",
    [StreamRead(b"half a log", 0, 1, False), StreamRead(b"", 0, 1, False)],
)
def test_a_gh_failure_is_unreadable(answer: StreamRead) -> None:
    fake = Fake([check("check")], answer=answer)
    (log,) = fake.logs()
    assert (log.cause, log.text) == ("unreadable", "")


def test_a_check_without_an_actions_link_is_unreadable_and_never_asks_gh() -> None:
    odd = CiCheck("lint", "COMPLETED", "FAILURE", "https://example.com/status/1")
    fake = Fake([odd])
    assert [log.cause for log in fake.logs()] == ["unreadable"]
    assert fake.calls == []


def test_an_unreadable_report_is_one_unreadable_entry() -> None:
    fake = Fake([])
    fake.report = CiUnreadable("gh_unavailable")
    (log,) = fake.logs()
    assert (log.cause, log.text) == ("unreadable", "")
    assert fake.calls == []


def test_no_red_job_is_no_log() -> None:
    assert Fake([check("check", "SUCCESS")]).logs() == []


def test_a_stream_that_raises_is_unreadable_and_never_reaches_the_caller() -> None:
    def boom(args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float) -> StreamRead:
        raise FileNotFoundError("gh")

    fake = Fake([check("check")])
    adapter = GhCiLogs(Path("."), fake.ci_report, stream=boom)
    assert [log.cause for log in adapter.failed_job_logs(PR, HEAD)] == ["unreadable"]


def shell(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def test_bounded_stream_keeps_the_cap_and_counts_the_rest() -> None:
    script = "import sys; sys.stdout.write('x' * 5000)"
    read = bounded_stream(shell(script), cwd=Path("."), max_bytes=1000, timeout_s=20)
    assert (len(read.data), read.truncated, read.returncode, read.timed_out) == (
        1000,
        4000,
        0,
        False,
    )


def test_bounded_stream_stops_waiting_at_the_timeout() -> None:
    script = "import time; print('a', flush=True); time.sleep(30)"
    read = bounded_stream(shell(script), cwd=Path("."), max_bytes=1000, timeout_s=0.5)
    assert read.timed_out is True


def test_bounded_stream_reports_a_failing_exit() -> None:
    read = bounded_stream(
        shell("import sys; sys.exit(3)"), cwd=Path("."), max_bytes=10, timeout_s=20
    )
    assert (read.data, read.returncode, read.timed_out) == (b"", 3, False)


def test_bounded_stream_tail_keeps_the_last_bytes_and_counts_the_head() -> None:
    script = "import sys; sys.stdout.write('a' * 3000 + 'b' * 2000)"
    read = bounded_stream(shell(script), cwd=Path("."), max_bytes=1000, timeout_s=20, tail=True)
    assert read.data == b"b" * 1000
    assert (read.truncated, read.returncode, read.timed_out) == (4000, 0, False)


def test_bounded_stream_tail_under_the_cap_keeps_everything() -> None:
    script = "import sys; sys.stdout.write('hello')"
    read = bounded_stream(shell(script), cwd=Path("."), max_bytes=1000, timeout_s=20, tail=True)
    assert (read.data, read.truncated) == (b"hello", 0)


# `flake_block` (GH-464): the `agentic-flakes:` lines of the green jobs the stack names.


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def flake_fake(
    name: str, *, checks: Sequence[CiCheck] | None = None, cap: int = 1024 * 1024
) -> Fake:
    """A fake whose flake stream is the real tail `bounded_stream` over a fixture file, so
    the cap is the real one."""
    fake = Fake(checks if checks is not None else [check("check", "SUCCESS")])
    path = FIXTURES / name

    def stream(args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float) -> StreamRead:
        fake.calls.append((args, max_bytes, timeout_s))
        return bounded_stream(
            ["cat", str(path)], cwd=cwd, max_bytes=max_bytes, timeout_s=timeout_s, tail=True
        )

    fake.flake_stream = stream
    fake.flake_cap = cap
    return fake


def flake_lines(
    fake: Fake, head: str = HEAD, jobs: Sequence[str] = ("check",)
) -> list[tuple[str, list[str]]] | CiUnreadable:
    adapter = GhCiLogs(
        Path("."),
        fake.ci_report,
        flake_stream=fake.flake_stream or fake.stream,
        flake_max_bytes=fake.flake_cap,
    )
    return adapter.flake_block(PR, head, jobs)


def test_a_matrix_expansion_of_a_declared_job_is_read_and_a_bare_prefix_is_not() -> None:
    """GH-535: the declared `check` claims `check (3.12)`; `checks` and `check-fast (1)` are other
    names and are never read."""
    checks = [
        check("check (3.12)", "SUCCESS", job=11),
        check("check (3.13)", "SUCCESS", job=12),
        check("checks", "SUCCESS", job=13),
        check("check-fast (1)", "SUCCESS", job=14),
    ]
    found = flake_lines(flake_fake("flake_block_green.log", checks=checks))
    assert isinstance(found, list)
    assert [name for name, _ in found] == ["check (3.12)", "check (3.13)"]


def test_a_green_log_gives_the_prefixed_line_without_gh_columns_or_timestamp() -> None:
    fake = flake_fake("flake_block_green.log")
    assert flake_lines(fake) == [
        (
            "check",
            [
                'agentic-flakes: {"v":1,"check":"tests",'
                '"tests":["tests/test_a.py::test_x[1]","tests/test_a.py::test_y"]}'
            ],
        )
    ]
    ((args, max_bytes, timeout_s),) = fake.calls
    assert list(args) == ["gh", "run", "view", "5", "--job", "11", "--log"]
    assert "--log-failed" not in args
    assert max_bytes == 1024 * 1024
    assert 0 < timeout_s <= 60


def test_every_block_of_a_job_comes_back_in_log_order() -> None:
    found = flake_lines(flake_fake("flake_block_repeated.log"))
    assert not isinstance(found, CiUnreadable)
    ((job, lines),) = found
    assert job == "check"
    assert [line.split('"check":"')[1].split('"')[0] for line in lines] == [
        "tests",
        "lint",
        "tests",
    ]


def test_the_tail_of_a_log_over_the_cap_still_holds_its_last_block() -> None:
    fake = flake_fake("flake_block_over_cap_tail.log", cap=2048)
    found = flake_lines(fake)
    assert not isinstance(found, CiUnreadable)
    ((_, lines),) = found
    assert len(lines) == 1
    assert "test_tail.py::kept" in lines[0]


def test_a_block_only_in_the_dropped_head_of_a_capped_log_is_lost_not_unreadable() -> None:
    fake = flake_fake("flake_block_over_cap_head.log", cap=2048)
    assert flake_lines(fake) == [("check", [])]


def test_the_prefix_outside_the_content_position_is_ignored() -> None:
    assert flake_lines(flake_fake("flake_block_outside_prefix.log")) == [("check", [])]


def test_the_default_stream_is_the_tail_of_four_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, object]] = []

    def spy(args: Sequence[str], **kwargs: object) -> StreamRead:
        seen.append(kwargs)
        return StreamRead(b"", 0, 0, False)

    monkeypatch.setattr(ci_logs, "bounded_stream", spy)
    fake = Fake([check("check", "SUCCESS")])
    assert GhCiLogs(Path("."), fake.ci_report).flake_block(PR, HEAD, ["check"]) == [("check", [])]
    assert CI_FLAKE_LOG_BYTES == 4 * 1024 * 1024
    assert [(k["max_bytes"], k["tail"]) for k in seen] == [(CI_FLAKE_LOG_BYTES, True)]


def test_only_the_jobs_the_stack_names_are_read() -> None:
    fake = Fake([check("check", "SUCCESS", job=11), check("other", "SUCCESS", job=12)])
    assert flake_lines(fake, jobs=("check", "absent")) == [("check", [])]
    assert [list(args)[5] for args, _, _ in fake.calls] == ["11"]


def test_a_report_for_another_head_reads_nothing() -> None:
    fake = Fake([check("check", "SUCCESS")], head=OTHER)
    assert flake_lines(fake) == CiUnreadable("gh_unreadable")
    assert fake.calls == []


def test_an_unreadable_report_is_unreadable_for_the_flake_block_too() -> None:
    fake = Fake([])
    fake.report = CiUnreadable("gh_unavailable")
    assert flake_lines(fake) == CiUnreadable("gh_unavailable")
    assert fake.calls == []


@pytest.mark.parametrize(
    "answer",
    [
        StreamRead(b"partial", 0, 0, True),
        StreamRead(b"half a log", 0, 1, False),
        StreamRead(b"x" * 100, 5, -9, False),
    ],
    ids=["timeout", "non_zero_exit", "killed"],
)
def test_a_timed_out_or_failed_read_is_unreadable(answer: StreamRead) -> None:
    fake = Fake([check("check", "SUCCESS")], answer=answer)
    assert flake_lines(fake) == CiUnreadable("gh_unreadable")


def test_a_check_without_an_actions_link_makes_the_block_unreadable() -> None:
    odd = CiCheck("check", "COMPLETED", "SUCCESS", "https://example.com/status/1")
    fake = Fake([odd])
    assert flake_lines(fake) == CiUnreadable("gh_unreadable")
    assert fake.calls == []


def test_a_flake_stream_that_raises_is_unreadable_and_never_reaches_the_caller() -> None:
    def boom(args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float) -> StreamRead:
        raise FileNotFoundError("gh")

    fake = Fake([check("check", "SUCCESS")])
    fake.flake_stream = boom
    assert flake_lines(fake) == CiUnreadable("gh_unreadable")


def test_a_report_read_that_raises_is_unreadable() -> None:
    def broken(reference: str) -> CiReport | CiUnreadable:
        raise OSError("gh")

    adapter = GhCiLogs(Path("."), broken)
    assert adapter.flake_block(PR, HEAD, ["check"]) == CiUnreadable("gh_unavailable")


def test_bounded_stream_tail_keeps_the_last_cap_across_many_chunks() -> None:
    script = "import sys; sys.stdout.write('a' * 150000 + 'b' * 150000 + 'c' * 10)"
    read = bounded_stream(shell(script), cwd=Path("."), max_bytes=70000, timeout_s=20, tail=True)
    assert len(read.data) == 70000
    assert read.data == b"b" * 69990 + b"c" * 10
    assert read.truncated == 300010 - 70000
