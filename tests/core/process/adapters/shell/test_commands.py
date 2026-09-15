"""The shell adapter against real processes."""

import time

from agentic.core.process.adapters.shell import ShellCommands


def test_a_command_runs_in_the_checkout_and_keeps_both_streams(tmp_path):
    (tmp_path / "marker").write_text("")
    result = ShellCommands().run(tmp_path, "ls && echo oops >&2 && exit 3", timeout_s=10)
    assert result.exit_code == 3 and not result.passed and not result.timed_out
    assert "marker" in result.output and "oops" in result.output


def test_a_green_command_passes(tmp_path):
    assert ShellCommands().run(tmp_path, "true", timeout_s=10).passed


def test_a_command_past_its_time_is_stopped_with_its_children(tmp_path):
    started = time.monotonic()
    result = ShellCommands().run(tmp_path, "echo started; sleep 30 & sleep 30", timeout_s=0.5)
    assert result.timed_out and not result.passed and "started" in result.output
    assert time.monotonic() - started < 5


def test_the_systems_own_virtualenv_does_not_reach_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/system/.venv")
    result = ShellCommands().run(tmp_path, 'echo "venv=[$VIRTUAL_ENV]"', timeout_s=10)
    assert "venv=[]" in result.output
