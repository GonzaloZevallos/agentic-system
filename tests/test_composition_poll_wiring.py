"""GH-1157: `poll_wiring` gives the poller's ports and a watch on the latch they share. Every
test goes through `poll_wiring(cfg, NullStopFlag(), run=fake)` and the observable `watch.take()`,
never through a private latch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentic import composition
from agentic.core.process.adapters.github.board import GhBoard
from agentic.core.process.adapters.shell import NullStopFlag
from agentic.core.process.application import preflight
from agentic.core.process.application.ports import Ports, RateLimitWatch, TrackerRateLimited
from agentic.core.process.domain.board import FieldWrite
from agentic.shared.configuration import Configuration
from tests.core.process.adapters.github.test_board import identity, issue_node

RATE_LIMITED = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
GRAPHQL_RATE_LIMITED = {"data": None, "errors": [{"extensions": {"code": "RATE_LIMITED"}}]}


class FakeRun:
    """A `gh` runner: `stderr` fails every call that is not answered by `answers` (a function
    of the argv returning stdout or `None`)."""

    def __init__(self, stderr: str = "", answers=lambda args, body: None) -> None:
        self.stderr = stderr
        self.answers = answers
        self.argv: list[list[str]] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, args, **kwargs):
        args = list(args)
        self.argv.append(args)
        body = None
        if "--input" in args:
            body = json.loads(Path(args[args.index("--input") + 1]).read_text())
            self.bodies.append(body)
        out = self.answers(args, body)
        if out is None:
            raise subprocess.CalledProcessError(1, args, output="", stderr=self.stderr)
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> Configuration:
    monkeypatch.setattr(preflight, "CHECKOUT", _repo(tmp_path / "system"))
    return Configuration(product_dir=_repo(tmp_path / "product"))


def _wire(cfg: Configuration, run: FakeRun) -> tuple[Ports, RateLimitWatch]:
    return composition.poll_wiring(cfg, NullStopFlag(), run=run)


def test_a_rate_limited_tracker_read_is_seen_by_the_watch_once(cfg):
    ports, watch = _wire(cfg, FakeRun(RATE_LIMITED))
    assert watch.take() is False
    with pytest.raises(TrackerRateLimited):
        ports.tracker.queue()
    assert [watch.take(), watch.take()] == [True, False]


def test_a_rate_limited_inbox_read_is_seen_by_the_watch_once(cfg):
    ports, watch = _wire(cfg, FakeRun(RATE_LIMITED))
    with pytest.raises(TrackerRateLimited):
        ports.inbox.open_issues()
    assert [watch.take(), watch.take()] == [True, False]


def _board_run() -> FakeRun:
    def answers(args: list[str], body: dict[str, Any] | None) -> str | None:
        if args[1:3] == ["issue", "view"]:
            return json.dumps(issue_node(7))
        assert body is not None
        query = body["query"]
        if "updateProjectV2ItemFieldValue" in query:
            return json.dumps(GRAPHQL_RATE_LIMITED)
        if "addProjectV2ItemById" in query:
            return json.dumps({"data": {"i7_add": {"item": {"id": "PVTI_7"}}}})
        return json.dumps(identity())

    return FakeRun("", answers)


def test_a_rate_limited_board_body_swallowed_by_the_verdict_is_seen_by_the_watch(cfg, monkeypatch):
    run = _board_run()
    # The board's memory file is named after the product, read from a stack the fixture lacks.
    monkeypatch.setattr(composition.GitStack, "product", lambda self, revision="HEAD": "demo")
    ports, watch = _wire(cfg.model_copy(update={"board_project": "o/3"}), run)
    assert isinstance(ports.board, GhBoard)
    # A new card, nothing sent before: the add goes through, the field mutation is limited. The
    # stop is swallowed by `_attempt` (`_verdict`), and re-raised only because nothing was written.
    with pytest.raises(TrackerRateLimited):
        ports.board.write([FieldWrite(7, "etapa", "Próximo")])
    assert "updateProjectV2ItemFieldValue" in run.bodies[-1]["query"]
    assert [watch.take(), watch.take()] == [True, False]


def test_control_a_board_on_its_own_latch_leaves_the_watch_clear(cfg):
    run = _board_run()
    _, watch = _wire(cfg, run)
    alone = GhBoard(cfg.product_dir, "o", 3, run, log=lambda line: None)
    with pytest.raises(TrackerRateLimited):
        alone.write([FieldWrite(7, "etapa", "Próximo")])
    assert "updateProjectV2ItemFieldValue" in run.bodies[-1]["query"]
    assert watch.take() is False


def test_the_rate_limit_reading_never_marks_but_a_tracker_read_does(cfg):
    ports, watch = _wire(cfg, FakeRun(RATE_LIMITED))
    assert watch.reading() is None
    assert watch.take() is False
    with pytest.raises(TrackerRateLimited):
        ports.tracker.queue()
    assert watch.take() is True


def test_the_watch_reads_the_documents_resources(cfg):
    document = {"resources": {"core": {"remaining": 0, "reset": 99}}}
    _, watch = _wire(cfg, FakeRun("", lambda args, body: json.dumps(document)))
    reading = watch.reading()
    assert reading is not None and reading.resources["core"] == (0, 99.0)
    assert watch.take() is False
