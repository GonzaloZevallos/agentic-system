"""The signals verdict (ADR-0011, eighth addendum): a role that executes may kill what
it started, never by name, pattern or port. `owner` is a plain dict here -- the real
`/proc` read is the adapter's own (`tests/core/process/adapters/claude_sdk/
test_signals_for_real.py`); this seam is pure."""

import shlex
from pathlib import Path

import pytest

from agentic.core.process.domain import signals

CHECKOUT = Path("/repo/.agentic/worktrees/GH-32.new-sale")
OUTSIDE = Path("/repo/.agentic/worktrees/GH-32")  # a sibling with a prefixed name
MINE_PID = 111
OTHER_PID = 222
MISSING_PID = 333  # no entry at all: owner has never heard of this pid

OWNER = {MINE_PID: CHECKOUT, OTHER_PID: OUTSIDE}


def owner(pid: int) -> Path | None:
    return OWNER.get(pid)


def words(command: str) -> list[str]:
    return shlex.split(command)


@pytest.mark.parametrize(
    "command",
    [
        # selects by pattern or port
        "pkill -f uvicorn",
        "killall uvicorn",
        "fuser -k 8000/tcp",
        "fuser -kn tcp 8000",
        "fuser --kill 8000/tcp",
        "npx kill-port 8000",
        "npx -y kill-port 8000",
        "npm exec kill-port 8000",
        "pnpm dlx kill-port 8000",
        "yarn dlx kill-port 8000",
        "/usr/bin/pkill -f uvicorn",
        # wrappers that do not launder a pattern or a port
        "timeout 5 pkill x",
        "timeout -s KILL 5 pkill x",
        "env -i pkill -f x",
        "sudo -n pkill x",
        "time -p pkill -f x",
        # pid only known at run time, from a pipe -- whatever xargs's own options are
        "xargs kill",
        "xargs -r kill",
        "xargs -n1 kill -9",
        "xargs -I{} kill {}",
        "xargs -rn 1 kill",
        "xargs --replace kill {}",
        # pid ajeno
        f"kill -9 {OTHER_PID}",
        f"/bin/kill -9 {OTHER_PID}",
        f"builtin kill -9 {OTHER_PID}",
        f"command -p kill -9 {OTHER_PID}",
        f"kill {MISSING_PID}",
        f"kill {OTHER_PID}",  # the sibling worktree, prefix and all
        "kill 0",
        # operandos no admitidos: not a literal pid, not $!/$NAME/${NAME}
        "kill -- -123",
        "kill -9 -123",
        "kill -1",
        "kill %1",
        "kill $",
        'kill "$(lsof -ti:8000)"',
        'kill "$(pgrep -f uvicorn)"',
        "kill `pgrep uvicorn`",
        "kill uvicorn",
        # malformados
        "kill",
        "kill -s",
        "kill -n",
        "kill --signal",
    ],
)
def test_denial_refuses_what_a_role_that_executes_may_never_do(command):
    assert signals.denial(words(command), CHECKOUT, owner)


@pytest.mark.parametrize(
    "command",
    [
        f"kill {MINE_PID}",
        "kill $!",
        "kill $P",
        "kill ${P}",
        "kill -n 9 $!",
        "kill -s TERM $!",
        "kill -l",
        "kill -L",
        "command -v kill",
        "command -V kill",
        "type kill",
        "env FOO=1 kill $!",
        "echo kill",
        "git status",
    ],
)
def test_denial_lets_the_role_bring_down_its_own(command):
    assert signals.denial(words(command), CHECKOUT, owner) is None


def test_denial_never_raises_on_an_option_with_no_value():
    # `-s` with nothing after it: the malformed-option clause, exercised without a
    # `try/except` around the call -- an exception here would fail the test itself.
    assert signals.denial(words("kill -s"), CHECKOUT, owner)
    assert signals.denial([], CHECKOUT, owner) is None


@pytest.mark.parametrize(
    ("clause_text", "command"),
    [
        ("selects by pattern or port", "pkill -f uvicorn"),
        ("pid not in your checkout", f"kill {OTHER_PID}"),
        ("pid only known at run time", "xargs kill"),
        ("malformed option", "kill"),
    ],
)
def test_each_clause_of_the_verdict_has_its_own_rejected_entry(clause_text, command):
    reason = signals.denial(words(command), CHECKOUT, owner)
    assert reason and clause_text in reason


def test_command_of_peels_assignments_and_wrappers_in_any_order():
    assert signals.command_of(words("FOO=1 timeout 5 pkill x")) == ["pkill", "x"]
    assert signals.command_of(words("timeout 5 FOO=1 env -i sudo -n pkill x")) == ["pkill", "x"]
    assert signals.command_of(words("echo hi")) == ["echo", "hi"]
    assert signals.command_of([]) == []
