"""The poller's bounded budget read (GH-939): `composition.budget_read`, through the real
`bounded_run` and a `gh` stand-in on PATH.

Seam: `composition.budget_read`. The stand-ins are shell scripts, so no network and no real
`gh` is touched. Every test that reads `composition.process_call_log()` clears its cache
before and after."""

from __future__ import annotations

import json
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic import composition
from agentic.core.process.adapters.github.run import GH_DRAIN_S
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS
from agentic.core.process.domain.github_budget import graphql_reading

DOCUMENT = {
    "resources": {"graphql": {"limit": 5000, "used": 120, "remaining": 4880, "reset": 1900000000}}
}


@pytest.fixture(autouse=True)
def fresh_process_log() -> Iterator[None]:
    composition.process_call_log.cache_clear()
    yield
    composition.process_call_log.cache_clear()


def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """A `gh` on PATH that runs `body`; the directory is first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return script


def test_the_bound_is_five_seconds() -> None:
    assert composition.BUDGET_READ_TIMEOUT_S == 5


def test_a_gh_that_sleeps_past_the_bound_is_given_up_on(tmp_path, monkeypatch) -> None:
    stand_in(tmp_path, monkeypatch, "sleep 30")
    read = composition.budget_read(tmp_path)

    started = time.monotonic()
    result = read()
    elapsed = time.monotonic() - started

    assert result is None
    assert (
        composition.BUDGET_READ_TIMEOUT_S
        <= elapsed
        < (composition.BUDGET_READ_TIMEOUT_S + GH_DRAIN_S + 3)
    )


def test_a_valid_document_is_read(tmp_path, monkeypatch) -> None:
    stand_in(tmp_path, monkeypatch, f"echo '{json.dumps(DOCUMENT)}'")

    reading = graphql_reading(composition.budget_read(tmp_path)())

    assert reading is not None
    assert (reading.used, reading.limit, reading.remaining) == (120, 5000, 4880)


@pytest.mark.parametrize(
    "body",
    [f"echo '{json.dumps(DOCUMENT)}'", "exit 1", "sleep 30"],
    ids=["document", "failure", "past the bound"],
)
def test_the_budget_read_is_never_counted_in_the_process_log(tmp_path, monkeypatch, body) -> None:
    stand_in(tmp_path, monkeypatch, body)

    composition.budget_read(tmp_path)()

    drained = composition.process_call_log().drain()
    assert {kind: drained[kind] for kind in GH_CALL_KINDS} == dict.fromkeys(GH_CALL_KINDS, 0)


def test_the_same_read_through_the_process_log_would_have_been_counted(
    tmp_path, monkeypatch
) -> None:
    """The control: the harness above sees a counted read, so its zeros are not vacuous."""
    from agentic.core.process.adapters.github import account
    from agentic.core.process.adapters.github.run import bounded_run

    stand_in(tmp_path, monkeypatch, f"echo '{json.dumps(DOCUMENT)}'")

    account.read_rate_limit(bounded_run, tmp_path, calls=composition.process_call_log())

    drained = composition.process_call_log().drain()
    assert sum(drained[kind] for kind in GH_CALL_KINDS) == 1
