"""ADR-0046: `agentic merge` never runs a partial suite. A stack that declares affected-only
forms and fallback globs gets, through `merge.py`'s real check path, exactly its full
commands, with no base in them and no `mode` on any row; and `merge.py` never hands a plan
to `CheckoutPresubmit`, which this reads out of the source as well."""

from __future__ import annotations

import ast
import inspect

from agentic.core.process.application import merge
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, memory_ports

HEAD = "a" * 40
MASTER = "b" * 40
URL = "https://github.com/o/r/pull/7"
CHECKS = {"tests": "pytest -q", "e2e": "npx playwright test"}
AFFECTED = {"tests": "pytest -q --since {base}", "e2e": "npx playwright test --only-changed={base}"}


def _merged_with(stack: MemoryStack):
    ports = memory_ports(tracker=MemoryTracker(), stack=stack, commands=MemoryCommands())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.existing_branches["agentic/GH-7"] = ("tip", "taskbase1")
    ports.workspace.diff_paths = ("src/app.css",)
    ports.tracker.prs[URL] = PullRequest(
        state="OPEN", created_at="2026-10-05T00:00:00Z", merged_at=None, commits=(), head_oid=HEAD
    )
    ports.documents.save(
        {
            "id": "GH-7",
            "status": "ready_for_review",
            "pull_request": URL,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
        }
    )
    logged: list[str] = []
    cfg = Configuration(product_dir=ports.workspace.ROOT)
    merge.merge_task(
        "GH-7", 7, cfg, ports, Upstream("origin/master", MASTER, None), frozenset(), logged.append
    )
    return ports


def test_merge_runs_exactly_the_full_commands_whatever_the_stack_declares():
    stack = MemoryStack(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    ports = _merged_with(stack)
    ran = [command for _, command in ports.commands.ran]
    assert ran == list(CHECKS.values())
    assert not any("{base}" in c or "taskbase1" in c or "--since" in c for c in ran)
    assert ports.documents.load("GH-7")["merge_checks"]["passed"] is True


def test_merge_never_reads_the_affected_keys():
    class Watching(MemoryStack):
        def __init__(self, **declared) -> None:
            super().__init__(**declared)
            self.asked: list[str] = []

        def affected_checks(self, revision: str) -> dict[str, str]:
            self.asked.append("affected_checks")
            return super().affected_checks(revision)

        def affected_fallback(self, revision: str) -> tuple[str, ...]:
            self.asked.append("affected_fallback")
            return super().affected_fallback(revision)

    stack = Watching(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    _merged_with(stack)
    assert stack.asked == []


def test_a_merge_row_carries_no_mode(monkeypatch):
    stack = MemoryStack(checks=CHECKS, affected_checks=AFFECTED, affected_fallback=("uv.lock",))
    seen: list[dict] = []
    original = merge.CheckoutPresubmit.__call__

    def spy(self):
        outcome = original(self)
        seen.extend(self.results or [])
        return outcome

    monkeypatch.setattr(merge.CheckoutPresubmit, "__call__", spy)
    _merged_with(stack)
    assert [row["name"] for row in seen] == list(CHECKS)
    assert all("mode" not in row for row in seen)


def _presubmit_calls(source: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CheckoutPresubmit"
    ]


def test_no_checkout_presubmit_call_in_merge_passes_an_affected_keyword():
    calls = _presubmit_calls(inspect.getsource(merge))
    assert calls, "merge.py is expected to build its gate through CheckoutPresubmit"
    assert all(k.arg != "affected" for call in calls for k in call.keywords)


def test_the_ast_check_does_reject_a_call_that_passes_a_plan():
    # The reject case for the check above: the same reading, over a source that does it.
    bad = "CheckoutPresubmit(ports, path, 'x', checks, 1.0, affected=plan)"
    calls = _presubmit_calls(bad)
    assert any(k.arg == "affected" for call in calls for k in call.keywords)
