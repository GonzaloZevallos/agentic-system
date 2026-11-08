"""The `full_checks` node, `full_checks_mode` and the review's speculative push judge whether the
product's CI may judge a head through `ci_trust` (GH-565): the task base's diff first, and the
`merge-base(head, master)` range only for a head that diff distrusts, so a long task that merged
master's `.github/` change is no longer run locally as untrusted. Fake ports: the workspace
answers a different diff per range, which is what tells the two reads apart."""

from __future__ import annotations

import pytest

from agentic.core.process.application import ci_trust
from agentic.core.process.application.checkouts import Checkout
from agentic.core.process.application.full_checks import _UNFETCHED_DETAIL, full_checks_mode
from agentic.core.process.application.ports import Upstream
from agentic.core.process.application.review import review
from agentic.core.process.domain.ci import CiCheck, CiReport, DispatchedRun
from agentic.core.process.domain.presubmit import CiDispatch
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    ScriptedRunner,
    full_checks_in_task_checkout,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=100.0, ci_start_grace_s=30.0)
HEAD = "a" * 40
MASTER = "c" * 40
RANGE_BASE = "d" * 40
TASK_FILES = ("src/a.py", "tests/test_a.py")
MASTERS_CI = (".github/workflows/ci.yml", ".agentic/stack.yaml")
REF = "agentic-ci/GH-1"
DISPATCH = CiDispatch(workflow="ci.yml", sha_input="sha")
UPSTREAM = Upstream("origin/master", MASTER, None)


class Ranges(MemoryWorkspace):
    """`task_base...head` carries master's CI files (the task merged master past its base);
    `merge-base...head` is only the task's own."""

    def __init__(self, own: tuple[str, ...] = TASK_FILES, merged: tuple[str, ...] = MASTERS_CI):
        super().__init__()
        self.own = own
        self.merged = merged
        self.merge_bases[(HEAD, MASTER)] = RANGE_BASE
        self.upstream_answer = UPSTREAM

    def full_head(self, path):
        return HEAD

    def changed_paths(self, base, head):
        self.diff_reads.append((base, head))
        return self.own if base == RANGE_BASE else self.own + self.merged


def state_of(speculated: bool = False) -> dict:
    state = {
        "id": "GH-1",
        "title": "Trust",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {
            INTEGRATION: {
                "id": INTEGRATION,
                "title": "integration",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "approved",
                "branch": "agentic/GH-1",
                "base": "b",
            }
        },
        "task_head": HEAD,
    }
    return state


def green() -> CiReport:
    checks = tuple(
        CiCheck(name, "COMPLETED", "SUCCESS", f"https://github.com/o/r/actions/runs/7/{name}")
        for name in ("unit", "web")
    )
    return CiReport(HEAD, None, None, None, checks)


class Tracker(MemoryTracker):
    def __init__(self) -> None:
        super().__init__()
        self.report = green()

    def branch_ci(self, branch):
        self.branch_reads.append(branch)
        return self.report


def ports_of(workspace: MemoryWorkspace | None = None, *, dispatch: CiDispatch | None = None):
    stack = MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",), ci=("unit", "web"))
    stack.declared_ci_dispatch = dispatch
    return memory_ports(
        stack=stack,
        commands=MemoryCommands(),
        workspace=workspace or Ranges(),
        tracker=Tracker(),
    )


async def run(state, ports):
    async def no_sleep(_seconds: float) -> None:
        return None

    return await full_checks_in_task_checkout(
        state, CFG, ports, sleep=no_sleep, push_sleep=lambda _s: None
    )


async def test_a_head_that_only_merged_masters_ci_change_is_judged_on_ci_not_run_locally():
    ports = ports_of()
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["ran_on"] == "ci"
    assert "ci_fallback" not in record
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert ports.commands.ran == []
    checkout = Checkout("agentic/GH-1", MemoryWorkspace.ROOT / "GH-1", "b")
    assert full_checks_mode(state_of(), CFG, ports, checkout) == "wait"
    assert (HEAD, MASTER) in ports.workspace.merge_base_asks
    assert ports.workspace.upstream_calls >= 1


def checkout() -> Checkout:
    return Checkout("agentic/GH-1", MemoryWorkspace.ROOT / "GH-1", "b")


def mode(ports) -> str:
    return full_checks_mode(state_of(), CFG, ports, checkout())


def own_ci_edit() -> Ranges:
    """A head that edits the CI definition itself: both ranges carry it."""
    return Ranges(own=(*TASK_FILES, ".github/workflows/ci.yml"))


def dispatched_green() -> DispatchedRun:
    jobs = tuple(
        CiCheck(name, "COMPLETED", "SUCCESS", f"https://github.com/o/r/actions/runs/99/{name}")
        for name in ("unit", "web")
    )
    return DispatchedRun(
        run_id=99,
        display_title=f"CI {HEAD}",
        link="https://github.com/o/r/actions/runs/99",
        jobs=jobs,
        event="workflow_dispatch",
        status="completed",
        created_at="2026-11-08T10:00:00Z",
    )


async def test_a_head_that_edits_the_ci_itself_is_dispatched_with_the_dispatch_read_at_master():
    ports = ports_of(own_ci_edit())
    ports.stack.declared_ci_dispatch_at[MASTER] = DISPATCH  # not declared at the task base
    ports.tracker.dispatched = [(dispatched_green(),)]
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "ci_dispatch"
    assert ports.commands.ran == []
    assert ports.workspace.ci_ref_calls == [("publish_dispatch", "GH-1")]
    assert mode(ports) == "wait"


async def test_a_head_that_edits_the_ci_itself_runs_locally_when_no_dispatch_is_declared():
    ports = ports_of(own_ci_edit())
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["ran_on"] == "local" and record["ci_fallback"] == "untrusted"
    assert patch["sections"]["Full checks"].startswith("CI not used (untrusted): ran locally.")
    assert ports.workspace.ci_ref_calls == []
    assert mode(ports) == "local"


async def test_a_head_the_task_base_trusts_costs_no_upstream_and_no_merge_base_ask():
    ports = ports_of(Ranges(merged=()))
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "ci"
    assert mode(ports) == "wait"
    assert ports.workspace.upstream_calls == 0
    assert ports.workspace.merge_base_asks == []


async def test_an_upstream_that_fell_back_to_head_never_trusts_the_head():
    workspace = Ranges()
    workspace.upstream_answer = Upstream("HEAD", HEAD, "no remote")
    ports = ports_of(workspace)
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    text = patch["sections"]["Full checks"]
    assert text.startswith(f"CI not used (untrusted){_UNFETCHED_DETAIL}: ran locally.")
    assert ports.workspace.merge_base_asks == []
    assert mode(ports) == "local"


async def test_an_upstream_that_was_not_fetched_and_a_still_untrusted_head_says_so():
    workspace = own_ci_edit()
    workspace.upstream_answer = Upstream("origin/master", MASTER, "fetch failed")
    patch = await run(state_of(), ports_of(workspace))
    text = patch["sections"]["Full checks"]
    assert text.startswith(f"CI not used (untrusted){_UNFETCHED_DETAIL}: ran locally.")


async def test_an_unreadable_merge_base_keeps_the_bare_untrusted_line():
    workspace = Ranges()
    workspace.merge_base_error = "no merge base"
    ports = ports_of(workspace)
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    text = patch["sections"]["Full checks"]
    assert text.startswith("CI not used (untrusted): ran locally.")
    assert _UNFETCHED_DETAIL not in text
    assert mode(ports) == "local"


async def test_a_dispatch_that_does_not_load_at_master_is_no_dispatch_and_raises_nothing():
    ports = ports_of(own_ci_edit())
    ports.stack.stack_error_at[MASTER] = "ci_dispatch: not a mapping"
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    assert mode(ports) == "local"


async def test_a_job_masters_ci_renamed_escalates_unknown_names_instead_of_running_locally():
    ports = ports_of()
    ports.tracker.report = CiReport(
        HEAD,
        None,
        None,
        None,
        (
            CiCheck("unit", "COMPLETED", "SUCCESS", "https://x/unit"),
            CiCheck("web-renamed", "COMPLETED", "SUCCESS", "https://x/web"),
        ),
        True,
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    patch = await full_checks_in_task_checkout(
        state_of(), CFG, ports, sleep=no_sleep, push_sleep=lambda _s: None
    )
    assert patch["escalation_class"] == "ci"
    assert "no check run named `web`" in patch["escalated"]
    assert ports.commands.ran == []


def review_state() -> dict:
    return {
        "id": "GH-465",
        "title": "Speculation",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtask_id": "api",
        "subtasks": {
            "api": {
                "id": "api",
                "title": "api",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "delivered",
                "branch": "agentic/GH-465.api",
                "base": "base1",
            }
        },
    }


async def test_a_long_task_head_is_pushed_speculatively_by_the_review():
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    stack = MemoryStack(ci=("check",), delivery_checks=("tests",))
    ports = memory_ports(runner, stack=stack, workspace=Ranges())
    patch = await review(
        review_state(),
        Configuration(product_dir=MemoryWorkspace.ROOT),
        ports,
        push_sleep=lambda _s: None,
    )
    assert ports.workspace.ci_ref_calls == [("publish", "agentic-ci/GH-465")]
    assert patch["ci_speculation"]["head"] == HEAD


async def test_a_head_that_edits_the_ci_itself_is_still_not_pushed_speculatively():
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    stack = MemoryStack(ci=("check",), delivery_checks=("tests",))
    ports = memory_ports(runner, stack=stack, workspace=own_ci_edit())
    patch = await review(
        review_state(),
        Configuration(product_dir=MemoryWorkspace.ROOT),
        ports,
        push_sleep=lambda _s: None,
    )
    assert ports.workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


def test_trust_master_refuses_head_and_a_missing_sha():
    assert ci_trust.trust_master(Upstream("origin/master", MASTER, None)) == MASTER
    assert ci_trust.trust_master(Upstream("HEAD", HEAD, None)) is None
    assert ci_trust.trust_master(Upstream("origin/master", None, "x")) is None


def test_read_range_reports_an_unreadable_merge_base_and_a_dispatch_that_does_not_load():
    workspace = Ranges()
    workspace.merge_base_error = "no merge base"
    ports = ports_of(workspace)
    ports.stack.stack_error_at[MASTER] = "broken"
    result = ci_trust.read_range(ports, HEAD, MASTER)
    assert result.merge_base_error == "no merge base"
    assert result.paths is None and result.dispatch is None and not result.trusted
    assert ci_trust.dispatch_at(ports, MASTER) is None
