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
