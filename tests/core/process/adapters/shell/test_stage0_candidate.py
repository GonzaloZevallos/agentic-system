"""`ShellStage0Candidate` (GH-632, part 2d) behind a recording process runner and a fake
`InstalledProducts`: the argv, the cwd, the timeouts, the environment and the scratch of every
child, and which cause each way of failing maps to. The default runner is tried on a real
child. Nothing here captures a child's output: a double answers an exit code, `TimedOut` or an
`OSError`, the only three things the adapter decides from."""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentic.core.process.adapters.shell.stage0_candidate import (
    CLONE_TIMEOUT_S,
    HELP_TIMEOUT_S,
    POLL_SMOKE_TIMEOUT_S,
    RESOLVE_TIMEOUT_S,
    UV_SYNC_TIMEOUT_S,
    ShellStage0Candidate,
    SubprocessRunner,
    TimedOut,
)
from agentic.core.process.domain.stage0_rotation import (
    CandidateCause,
    Green,
    Prepared,
    Red,
    Unavailable,
)
from tests.fakes import MemoryScratch

SHA = "a" * 40
ORIGIN = "https://github.com/acme/system.git"
Answer = int | TimedOut | OSError
Answerer = Callable[[Sequence[str]], Answer]


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    timeout_s: float
    scratch_live: bool


class RecordingRunner:
    """Records every call and answers `answer(argv)`; a `git clone` leaves the stage with its
    `.git/HEAD` the way a real one does, so the adapter can read the commit it resolved."""

    def __init__(self, scratch: MemoryScratch, answer: Answerer) -> None:
        self.calls: list[Call] = []
        self.scratch = scratch
        self.answer = answer
        self.head = SHA

    def run(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> Answer:
        tmpdir = env.get("TMPDIR")
        live = tmpdir is not None and Path(tmpdir) in self.scratch.live
        self.calls.append(Call(tuple(argv), cwd, dict(env), timeout_s, live))
        result = self.answer(argv)
        if list(argv[:2]) == ["git", "clone"] and result == 0:
            git = Path(argv[-1]) / ".git"
            git.mkdir(parents=True)
            (git / "HEAD").write_text(self.head + "\n")
        return result


def succeed(argv: Sequence[str]) -> Answer:
    return 0


def failing(word: str, result: Answer) -> Answerer:
    """Answers `result` to every command with `word` among its arguments, 0 to the rest."""
    return lambda argv: result if word in argv else 0


def build(
    tmp_path: Path, answer: Answerer = succeed, products: tuple[str, ...] = ()
) -> tuple[ShellStage0Candidate, RecordingRunner, MemoryScratch]:
    scratch = MemoryScratch(root=tmp_path / "scratch")
    runner = RecordingRunner(scratch, answer)
    held = tuple(Path(d) for d in products)
    candidate = ShellStage0Candidate(tmp_path, scratch, held, ORIGIN, runner)
    return candidate, runner, scratch


def venv_agentic(root: Path, stage: str) -> str:
    return str(root / stage / ".venv" / "bin" / "agentic")


def test_smoke_runs_help_then_one_dry_poll_per_product_in_the_stage(tmp_path: Path) -> None:
    candidate, runner, _ = build(
        tmp_path,
        products=(
            "/srv/a",
            "/srv/b",
        ),
    )
    stage = tmp_path / SHA
    agentic = venv_agentic(tmp_path, SHA)

    assert candidate.smoke(SHA) == Green()

    assert [(c.argv, c.cwd, c.timeout_s) for c in runner.calls] == [
        ((agentic, "--help"), stage, HELP_TIMEOUT_S),
        (
            (agentic, "poll", "--product-dir", "/srv/a", "--dry-run", "--once"),
            stage,
            POLL_SMOKE_TIMEOUT_S,
        ),
        (
            (agentic, "poll", "--product-dir", "/srv/b", "--dry-run", "--once"),
            stage,
            POLL_SMOKE_TIMEOUT_S,
        ),
    ]
    assert (HELP_TIMEOUT_S, POLL_SMOKE_TIMEOUT_S) == (60, 300)


def test_smoke_with_no_products_runs_only_help(tmp_path: Path) -> None:
    candidate, runner, _ = build(tmp_path)

    assert candidate.smoke(SHA) == Green()

    assert [c.argv for c in runner.calls] == [(venv_agentic(tmp_path, SHA), "--help")]


def test_smoke_of_the_candidate_and_of_current_differ_only_in_the_stage_path(
    tmp_path: Path,
) -> None:
    candidate, runner, _ = build(tmp_path, products=("/srv/a",))
    partial = f"{SHA}.partial"

    candidate.smoke(partial)
    candidate.smoke(SHA)

    first, second = runner.calls[:2], runner.calls[2:]
    assert [(c.cwd, c.argv, c.timeout_s) for c in first] == [
        (
            tmp_path / partial,
            tuple(a.replace(SHA, partial) if a.startswith(str(tmp_path)) else a for a in c.argv),
            c.timeout_s,
        )
        for c in second
    ]


def test_a_timeout_of_the_poll_is_smoke_timeout(tmp_path: Path) -> None:
    candidate, runner, _ = build(
        tmp_path,
        failing("poll", TimedOut()),
        (
            "/srv/a",
            "/srv/b",
        ),
    )

    assert candidate.smoke(SHA) == Unavailable("smoke_timeout")
    assert len(runner.calls) == 2  # the second product is never reached


def test_a_timeout_of_help_is_smoke_timeout(tmp_path: Path) -> None:
    candidate, _, _ = build(tmp_path, failing("--help", TimedOut()))

    assert candidate.smoke(SHA) == Unavailable("smoke_timeout")


@pytest.mark.parametrize("flag", ["--help", "poll"])
def test_a_non_zero_exit_of_the_smoke_is_red(tmp_path: Path, flag: str) -> None:
    candidate, _, _ = build(tmp_path, failing(flag, 3), ("/srv/a",))

    assert candidate.smoke(SHA) == Red()


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_a_full_disk_during_the_smoke_is_disk(tmp_path: Path, code: int) -> None:
    candidate, _, _ = build(tmp_path, failing("--help", OSError(code, "full")))

    assert candidate.smoke(SHA) == Unavailable("disk")


def test_a_smoke_that_cannot_start_is_unavailable_never_red(tmp_path: Path) -> None:
    candidate, _, _ = build(tmp_path, failing("--help", FileNotFoundError(errno.ENOENT, "no venv")))

    assert candidate.smoke(SHA) == Unavailable("uv")


def test_prepare_clones_the_mirror_detached_and_syncs_with_its_origin_set(tmp_path: Path) -> None:
    candidate, runner, _ = build(tmp_path)
    partial = tmp_path / f"{SHA}.partial"

    assert candidate.prepare(SHA) == Prepared(SHA)

    assert [(c.argv, c.cwd, c.timeout_s) for c in runner.calls] == [
        (
            ("git", "clone", "--no-checkout", str(tmp_path / ".mirror"), str(partial)),
            tmp_path,
            CLONE_TIMEOUT_S,
        ),
        (("git", "checkout", "--detach", SHA), partial, RESOLVE_TIMEOUT_S),
        (("git", "remote", "set-url", "origin", ORIGIN), partial, RESOLVE_TIMEOUT_S),
        (("uv", "sync", "--locked"), partial, UV_SYNC_TIMEOUT_S),
    ]


def test_prepare_of_an_abbreviated_pin_names_the_partial_by_the_resolved_commit(
    tmp_path: Path,
) -> None:
    candidate, runner, _ = build(tmp_path)
    runner.head = "ab" * 20

    assert candidate.prepare("abababa") == Prepared("ab" * 20)

    assert (tmp_path / f"{'ab' * 20}.partial" / ".git" / "HEAD").is_file()
    assert not (tmp_path / "abababa.partial").exists()
    assert runner.calls[-1].cwd == tmp_path / f"{'ab' * 20}.partial"


@pytest.mark.parametrize("bad", ["../../etc", "abc", "g" * 40, "a" * 41, ""])
def test_prepare_refuses_a_name_that_is_not_a_commit_prefix_before_touching_anything(
    tmp_path: Path, bad: str
) -> None:
    candidate, runner, _ = build(tmp_path)

    assert candidate.prepare(bad) == Unavailable("clone")
    assert runner.calls == []


@pytest.mark.parametrize(
    ("step", "cause"),
    [("clone", "clone"), ("--detach", "clone"), ("set-url", "clone"), ("sync", "uv")],
)
@pytest.mark.parametrize("failure", [1, TimedOut()])
def test_prepare_maps_a_failed_or_timed_out_step_to_its_cause(
    tmp_path: Path, step: str, cause: CandidateCause, failure: int | TimedOut
) -> None:
    candidate, _, _ = build(tmp_path, failing(step, failure))

    assert candidate.prepare(SHA) == Unavailable(cause)


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
@pytest.mark.parametrize("step", ["clone", "sync"])
def test_prepare_maps_a_full_disk_to_disk_whichever_step_hits_it(
    tmp_path: Path, code: int, step: str
) -> None:
    candidate, _, _ = build(tmp_path, failing(step, OSError(code, "full")))

    assert candidate.prepare(SHA) == Unavailable("disk")


def test_prepare_maps_another_oserror_to_the_cause_of_its_step(tmp_path: Path) -> None:
    denied = OSError(errno.EACCES, "denied")
    on_clone, _, _ = build(tmp_path / "a", failing("clone", denied))
    on_sync, _, _ = build(tmp_path / "b", failing("sync", denied))

    assert on_clone.prepare(SHA) == Unavailable("clone")
    assert on_sync.prepare(SHA) == Unavailable("uv")


def test_prepare_never_removes_the_partial_it_leaves(tmp_path: Path) -> None:
    candidate, _, _ = build(tmp_path, failing("sync", 1))

    assert candidate.prepare(SHA) == Unavailable("uv")
    assert (tmp_path / f"{SHA}.partial").is_dir()


def test_settle_rebuilds_the_venv_at_its_final_path_and_runs_it(tmp_path: Path) -> None:
    stage = tmp_path / SHA
    (stage / ".venv" / "bin").mkdir(parents=True)
    candidate, runner, _ = build(tmp_path, products=("/srv/a",))
    venv_present: list[bool] = []

    def answer(argv: Sequence[str]) -> Answer:
        venv_present.append((stage / ".venv").exists())
        return 0

    runner.answer = answer

    assert candidate.settle(SHA) == Green()

    assert [(c.argv, c.cwd, c.timeout_s) for c in runner.calls] == [
        (("uv", "sync", "--locked"), stage, UV_SYNC_TIMEOUT_S),
        ((venv_agentic(tmp_path, SHA), "--help"), stage, HELP_TIMEOUT_S),
    ]
    assert venv_present[0] is False  # the old venv, with its absolute paths, went before the sync


@pytest.mark.parametrize(
    ("answer", "cause"),
    [
        (failing("sync", 1), "uv"),
        (failing("sync", TimedOut()), "uv"),
        (failing("--help", 1), "uv"),
        (failing("--help", TimedOut()), "smoke_timeout"),
        (failing("sync", OSError(errno.ENOSPC, "full")), "disk"),
        (failing("--help", OSError(errno.EDQUOT, "quota")), "disk"),
    ],
)
def test_settle_maps_each_failure_to_its_cause(
    tmp_path: Path, answer: Answerer, cause: CandidateCause
) -> None:
    (tmp_path / SHA).mkdir()
    candidate, _, _ = build(tmp_path, answer)

    assert candidate.settle(SHA) == Unavailable(cause)


def test_every_child_gets_the_clean_environment_and_a_scratch_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", "/elsewhere")
    monkeypatch.setenv("AGENTIC_STAGE0_PIN", "abc")
    monkeypatch.setenv("VIRTUAL_ENV", "/own/venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/own/venv")
    monkeypatch.setenv("KEEP_ME", "yes")
    (tmp_path / SHA).mkdir()
    candidate, runner, scratch = build(tmp_path, products=("/srv/a",))

    candidate.prepare(SHA)
    candidate.smoke(SHA)
    candidate.settle(SHA)

    assert len(runner.calls) == 4 + 2 + 2
    for call in runner.calls:
        assert not [k for k in call.env if k.startswith("AGENTIC_")]
        assert "VIRTUAL_ENV" not in call.env
        assert "UV_PROJECT_ENVIRONMENT" not in call.env
        assert call.env["KEEP_ME"] == "yes"
        assert Path(call.env["TMPDIR"]).is_relative_to(tmp_path / "scratch")
        assert call.scratch_live
    assert not scratch.live
    assert {kind for _, kind, _, slug in scratch.opened if slug == "stage0-rotate"} == {"c"}
    assert len({token for _, _, token, _ in scratch.opened}) == len(scratch.opened)


def test_the_scratch_is_gone_after_a_step_that_timed_out_or_raised(tmp_path: Path) -> None:
    timed_out, _, scratch = build(tmp_path, failing("--help", TimedOut()))
    timed_out.smoke(SHA)
    assert not scratch.live

    def explode(argv: Sequence[str]) -> Answer:
        raise RuntimeError("runner bug")

    broken, _, scratch2 = build(tmp_path, explode)
    with pytest.raises(RuntimeError):
        broken.smoke(SHA)
    assert not scratch2.live


def test_default_runner_returns_the_exit_code_and_captures_nothing(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner = SubprocessRunner()
    env = dict(os.environ)

    assert runner.run(["sh", "-c", "echo out; echo err >&2; exit 3"], tmp_path, env, 10) == 3
    assert runner.run(["true"], tmp_path, env, 10) == 0
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


def test_default_runner_kills_the_whole_group_on_timeout(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    marker = tmp_path / "grandchild.pid"
    script = f"sleep 60 & echo $! > {marker}; wait"

    started = time.monotonic()
    result = runner.run(["sh", "-c", script], tmp_path, dict(os.environ), 1)

    assert result == TimedOut()
    assert time.monotonic() - started < 30
    pid = int(marker.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the grandchild outlived its process group")


def test_default_runner_returns_the_oserror_instead_of_raising(tmp_path: Path) -> None:
    result = SubprocessRunner().run(["true"], tmp_path / "missing", dict(os.environ), 5)

    assert isinstance(result, OSError)
