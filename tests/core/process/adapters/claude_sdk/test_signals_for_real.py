"""The signals verdict through the real adapter: real `sleep` processes, real
`/proc/<pid>/cwd`. The rest of the suite (`test_signals.py`, `test_hooks.py`) uses a
fake `owner`; a fake cannot show that `hooks._owner`'s own `/proc` read, and
`git_denial`'s own `Path.resolve()`, agree with what the kernel actually reports --
including when the checkout is handed over through a symlink."""

import subprocess
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.claude_sdk import hooks

pytestmark = pytest.mark.skipif(
    not Path("/proc").is_dir(), reason="signals.denial's owner reads /proc: this platform has none"
)


def _sleeping_in(cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(["sleep", "100"], cwd=cwd)


@pytest.fixture
def processes(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "checkout-link"
    link.symlink_to(checkout)

    mine = _sleeping_in(checkout)
    other = _sleeping_in(outside)
    mine_too = _sleeping_in(checkout)
    time.sleep(0.1)  # let each child's cwd land in /proc before anyone reads it
    try:
        yield checkout, link, mine, other, mine_too
    finally:
        for proc in (mine, other, mine_too):
            proc.kill()
            proc.wait(timeout=5)


def test_git_denial_niega_el_proceso_de_afuera_y_deja_pasar_los_de_adentro(processes):
    checkout, link, mine, other, mine_too = processes
    assert hooks.git_denial(f"kill {mine.pid}", checkout) is None
    assert hooks.git_denial(f"kill {other.pid}", checkout)
    # The checkout handed to `git_denial` is a symlink to the real one: `Path.resolve()`
    # must still land on the same directory `/proc/<pid>/cwd` reports.
    assert hooks.git_denial(f"kill {mine_too.pid}", link) is None


def test_git_denial_refuses_a_pattern_and_lets_a_plain_mention_through(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    assert hooks.git_denial("bash -lc 'pkill -f x'", checkout)
    assert hooks.git_denial("echo kill", checkout) is None
