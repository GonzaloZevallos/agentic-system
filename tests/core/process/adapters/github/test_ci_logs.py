"""`GhCiLogs` (GH-449): the failed jobs' logs of a pull request head through `gh`, bounded.
The report and the stream are fakes (no network); `bounded_stream` itself runs a local
shell command so its byte cap and its timeout are the real ones."""

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.ci_logs import GhCiLogs
from agentic.core.process.adapters.github.run import StreamRead, bounded_stream
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, JobLog

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
