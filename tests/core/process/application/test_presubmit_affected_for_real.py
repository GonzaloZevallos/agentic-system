"""`CheckoutPresubmit` under an `AffectedPlan` over a real git repository and the real
shell (ADR-0046): the base the affected command receives is the real commit the branch
started from, the changed paths come from `git diff`, and a red affected run is followed
by the full form."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan
from agentic.shared.configuration import Configuration
from tests.fakes import FakeCheckScheduler, real_commands


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "master", ".", cwd=work)
    (work / "a.txt").write_text("a\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "base", cwd=work)
    base = git("rev-parse", "HEAD", cwd=work)
    (work / "src.py").write_text("x = 1\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "deliver", cwd=work)
    ports = type(
        "Ports",
        (),
        {
            "workspace": GitWorkspace(work, Configuration(product_dir=work).git_identity),
            "commands": real_commands(),
            "check_scheduler": FakeCheckScheduler(),
        },
    )()
    return ports, work, base, tmp_path


def plan_for(base: str, out: Path, fallback: tuple[str, ...] = ()) -> AffectedPlan:
    command = f"echo affected >> {out}/order; printf %s {{base}} > {out}/base; exit 1"
    return AffectedPlan(base=base, commands={"tests": command}, fallback=fallback)


def test_the_affected_command_gets_the_real_task_base_and_the_full_form_runs_after_it(repo):
    ports, work, base, out = repo
    checks = {"tests": f"echo full >> {out}/order"}
    gate = CheckoutPresubmit(
        ports,
        work,
        base,
        checks,
        20.0,
        affected=plan_for(base, out),
        priority=CheckPriority.SUBTASK,
    )
    assert gate() is None  # the full rerun passed and decides
    assert (out / "base").read_text() == base
    assert (out / "order").read_text().split() == ["affected", "full"]
    assert gate.results is not None
    assert [(r["mode"], r["passed"]) for r in gate.results] == [
        ("affected-only", False),
        ("full", True),
    ]


def test_a_changed_path_matching_a_fallback_glob_runs_only_the_full_form_for_real(repo):
    ports, work, base, out = repo
    checks = {"tests": f"echo full >> {out}/order"}
    plan = plan_for(base, out, fallback=("*.py",))
    gate = CheckoutPresubmit(
        ports, work, base, checks, 20.0, affected=plan, priority=CheckPriority.SUBTASK
    )
    assert gate() is None
    assert (out / "order").read_text().split() == ["full"]
    assert not (out / "base").exists()


def test_a_full_form_that_fails_refuses_for_real(repo):
    ports, work, base, out = repo
    gate = CheckoutPresubmit(
        ports,
        work,
        base,
        {"tests": "echo broken; exit 1"},
        20.0,
        affected=plan_for(base, out),
        priority=CheckPriority.SUBTASK,
    )
    reason = gate()
    assert reason is not None and "broken" in reason
