"""`agentic perf` through `cli.main` (GH-318, ADR-0058): one test per exit code, the usage
error that only this verb overrides, a product tree left byte for byte as it was, and a
SIGTERM that leaves no worktree and no lock. The runner is a fake, the repository real."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agentic import cli, composition
from agentic.core.process.domain.performance import Measurement, Run
from tests.fakes import FakePerfLock, FakePerfRunner

STACK = """\
product: shop
checks:
  tests: pytest
performance:
  list:
    seed: "true"
    measure: "true"
    volume: 1000
    volume_unit: rows
    limits: {list_ms: {limit: 100, unit: ms}}
"""
ROOT = Path(__file__).resolve().parents[1]


def shop(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / ".agentic").mkdir(parents=True)
    (root / ".agentic/stack.yaml").write_text(STACK)
    (root / "app.py").write_text("print('hi')\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "master", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v0"], cwd=root, check=True)
    (root / "untracked.txt").write_text("scratch\n")
    return root


def measured(value: float) -> list[Run]:
    seed = Run(None, 0, False, 0.1, 0.1)
    line = Run(Measurement(1000, "rows", {"list_ms": value}), 0, False, 0.1, 0.1)
    return [seed, *[line] * 5]


def invoke(monkeypatch, root: Path, script: list[Run], *args: str, lock: bool = True) -> int:
    runner = FakePerfRunner(script)
    monkeypatch.setattr(composition, "perf_runner", lambda cfg, ports: runner)
    monkeypatch.setattr(composition, "perf_lock", lambda cfg: FakePerfLock(available=lock))
    argv = ["agentic", "perf", "shop", *args, "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as info:
        cli.main()
    return int(info.value.code or 0)


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def test_within_exits_0_and_prints_the_table(monkeypatch, tmp_path, capsys):
    root = shop(tmp_path)
    assert invoke(monkeypatch, root, measured(50)) == 0
    out = capsys.readouterr().out
    assert "harness" in out and "overlay none" in out
    assert "list" in out and "list_ms" in out and "100ms" in out and "within" in out


def test_over_exits_1(monkeypatch, tmp_path, capsys):
    assert invoke(monkeypatch, shop(tmp_path), measured(500)) == 1
    assert "over" in capsys.readouterr().out


def test_inconclusive_exits_2_and_a_busy_lock_too(monkeypatch, tmp_path, capsys):
    root = shop(tmp_path)
    assert invoke(monkeypatch, root, [Run(None, 1, False, None, None)]) == 2
    assert invoke(monkeypatch, root, [], lock=False) == 2
    assert "busy" in capsys.readouterr().out


def test_a_refusal_exits_3_with_its_message_on_stderr(monkeypatch, tmp_path, capsys):
    root = shop(tmp_path)
    assert invoke(monkeypatch, root, [], "nope") == 3
    assert "no scenario 'nope'" in capsys.readouterr().err


def test_a_bad_flag_on_perf_exits_3_and_on_every_other_verb_it_stays_2(monkeypatch, tmp_path):
    assert invoke(monkeypatch, shop(tmp_path), [], "--bogus") == 3
    monkeypatch.setattr("sys.argv", ["agentic", "run", "--bogus"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_the_product_tree_is_byte_identical_after_the_verb(monkeypatch, tmp_path):
    root = shop(tmp_path)
    porcelain = ["git", "status", "--porcelain"]
    before = (tree(root), subprocess.run(porcelain, cwd=root, capture_output=True).stdout)
    assert invoke(monkeypatch, root, measured(50)) == 0
    after = (tree(root), subprocess.run(porcelain, cwd=root, capture_output=True).stdout)
    assert after == before
    assert not list((root / ".agentic/worktrees").glob("perf-*"))


CHILD = """\
import sys, time
from pathlib import Path
from agentic import cli, composition

ready, product = sys.argv[1:3]

class Slow:
    def run(self, checkout, command, extra_env, timeout_s):
        Path(ready).write_text(str(checkout))
        time.sleep(120)

composition.perf_runner = lambda cfg, ports: Slow()
sys.argv = ["agentic", "perf", "shop", "--product-dir", product]
cli.main()
"""


def test_sigterm_leaves_no_worktree_and_no_lock(tmp_path):
    root = shop(tmp_path)
    ready, lock = tmp_path / "ready", tmp_path / "state" / "perf.lock"
    env = {**os.environ, "AGENTIC_PERF_LOCK_PATH": str(lock)}
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(CHILD), str(ready), str(root)],
        cwd=ROOT,
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), child.communicate()[1]
        assert lock.exists() and Path(ready.read_text()).is_dir()
        child.send_signal(signal.SIGTERM)
        _, err = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
    assert child.returncode == 128 + signal.SIGTERM, err
    assert not list((root / ".agentic/worktrees").glob("perf-*"))
    assert not lock.exists()
