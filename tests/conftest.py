import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True, scope="session")
def git_stops_at_the_temporary_directory(tmp_path_factory):
    """A test's temporary directory is never inside a repository, wherever `TMPDIR` points.
    When it sits under a checkout (the delivery gate's does: `.agentic/tmp/<task>/`), a
    "plain directory" would resolve to that checkout's repository and the tests of "not a
    repository" and of "runs on its own checkout" would fail for a reason that is not the
    code's. `GIT_CEILING_DIRECTORIES` stops git's upward search at pytest's base directory;
    a repository a test creates inside its own `tmp_path` is still found."""
    ceiling = str(tmp_path_factory.getbasetemp().resolve().parent)
    previous = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = (
        ceiling if not previous else f"{previous}{os.pathsep}{ceiling}"
    )
    yield
    if previous is None:
        os.environ.pop("GIT_CEILING_DIRECTORIES", None)
    else:
        os.environ["GIT_CEILING_DIRECTORIES"] = previous


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


@pytest.fixture(autouse=True)
def no_real_inhibitor(monkeypatch):
    """GH-176: `_run`/`_resume` hold a logind inhibitor for their lifetime; a test that
    runs one must not take a real sleep/shutdown lock on the machine running the suite.
    A test of the warning itself overrides this with its own `cli.inhibitor`."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def nothing(why):
        yield None

    # The module `cli` holds is replaced, never `inhibitor.hold` itself: the adapter's own
    # tests use the real one.
    monkeypatch.setattr("agentic.cli.inhibitor", SimpleNamespace(hold=nothing))


@pytest.fixture(autouse=True)
def no_real_langfuse_transport(monkeypatch):
    """GH-283: a test never reaches the real Langfuse. `LangfuseScores` reads
    `urllib_transport` from its module at call time (`transport or urllib_transport`), so
    replacing it here makes any writer built without an injected transport fail loudly.
    Together with `isolated_env` unsetting `OTEL_EXPORTER_OTLP_ENDPOINT`, both the score
    path and the OTLP exporter are closed. A test of the real transport imports
    `urllib_transport` at its own module import (before this runs) and injects it."""

    def unreachable(url, body, headers, timeout_s):
        raise AssertionError("the real Langfuse transport is not reachable from tests")

    monkeypatch.setattr(
        "agentic.core.measurement.adapters.langfuse.scores.urllib_transport", unreachable
    )
