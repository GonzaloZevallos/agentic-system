import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No AGENTIC_* from the shell or `.env` leaks into a test."""
    import os

    for name in list(os.environ):
        if name.startswith("AGENTIC_"):
            monkeypatch.delenv(name)
    # Nor the collector's endpoint: a test never exports telemetry.
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("agentic.cli.load_dotenv", lambda: None)


@pytest.fixture(autouse=True)
def no_real_run_registry(tmp_path, monkeypatch):
    """GH-137: `Configuration.run_registry_dir` defaults under `XDG_STATE_HOME`
    (`_default_run_registry_dir`'s own `default_factory`, read by pydantic directly --
    never through the generic `AGENTIC_RUN_REGISTRY_DIR` override unless a test
    builds its `Configuration` through `from_sources`). Redirecting `XDG_STATE_HOME`
    itself is the one point that covers both construction paths, so no test --
    `cli._run`/`_resume` included, with no `registry=` of their own -- ever registers a
    live run under the real `~/.local/state/agentic/runs` of the machine running the
    suite. A test that wants the real default back overrides this with its own
    `monkeypatch.setenv`/`delenv`, same as any other fixture value."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
