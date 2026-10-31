"""The `SampleApp` shell adapter against real processes (ADR-0040)."""

from __future__ import annotations

from agentic.core.process.adapters.shell import sample_app as sample_app_module
from agentic.core.process.adapters.shell.sample_app import ShellSampleApp
from agentic.core.process.application.ports import SampleAppHandle
from agentic.core.process.domain.design import NotReady
from tests.fakes import FakeLoadReader


def test_a_sample_app_starts_prints_and_is_killed_on_stop(tmp_path):
    app = ShellSampleApp()
    started = app.start("echo Listening on :4242; sleep 30", tmp_path)
    assert isinstance(started, SampleAppHandle)
    assert any("Listening on :4242" in line for line in started.lines)
    assert started.token.poll() is None  # still running: not killed before `stop`
    app.stop(started)
    assert started.token.poll() is not None  # `stop` killed the whole group


def test_a_command_that_exits_non_zero_yields_the_refusal(tmp_path):
    result = ShellSampleApp().start("echo boom >&2; exit 7", tmp_path)
    assert isinstance(result, str)
    assert "exited 7" in result and "boom" in result


def test_a_command_that_prints_nothing_before_the_deadline_is_not_ready_not_a_refusal(
    monkeypatch, tmp_path
):
    """The rejecting input the deadline itself exists for: a command that stays up but
    never prints is not a handle to walk, and the lapse of the wait is its own type, never
    the text of a command that failed (GH-451)."""
    monkeypatch.setattr(sample_app_module, "SAMPLE_APP_READY_S", 0.2)
    result = ShellSampleApp(load=FakeLoadReader(43.0)).start("sleep 30", tmp_path)
    assert isinstance(result, NotReady)
    assert result.waited_s == 0.2 and result.load == 43.0
    assert "machine load 43.0" in result.said() and "did not fail" in result.said()
