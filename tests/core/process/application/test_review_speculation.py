"""The final unit's review publishes its head to the CI ref before the Tech Lead reads it
(GH-465) and records the push, never a verdict. Fake ports: `MemoryWorkspace.ci_ref_calls`
says what was pushed, the scripted reviewer says approved or returned."""

from __future__ import annotations

from datetime import datetime

import pytest

from agentic.core.process.application.ports import GitError
from agentic.core.process.application.review import review
from agentic.core.process.domain.ci_push import PUSH_ATTEMPTS, PUSH_BACKOFF_S, scrub
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

HEAD = "a" * 40
REF = "agentic-ci/GH-465"
PR = "https://github.com/o/r/pull/7"


class FullIdWorkspace(MemoryWorkspace):
    """Answers a whole commit id as the head, as a real checkout does."""

    def full_head(self, path):
        return HEAD


def state_of(**more) -> dict:
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
        **more,
    }


def ports_of(verdict, *, workspace=None, ci=("check",), delivery=("tests",)):
    runner = ScriptedRunner(tech_lead_review=[verdict])
    stack = MemoryStack(ci=ci, delivery_checks=delivery)
    return memory_ports(runner, stack=stack, workspace=workspace or FullIdWorkspace())


async def run(state, ports, sleeps=None):
    """`sleeps` records the waits of the push's retry: a wait costs no real time here."""
    wait = sleeps.append if sleeps is not None else (lambda _seconds: None)
    return await review(
        state, Configuration(product_dir=MemoryWorkspace.ROOT), ports, push_sleep=wait
    )


async def test_an_approved_final_unit_pushes_its_head_first_and_records_the_push():
    ports = ports_of(reviewed("approved"))
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    record = patch["ci_speculation"]
    assert record["head"] == HEAD
    assert datetime.fromisoformat(record["pushed_at"]).utcoffset() is not None
    assert patch["subtasks"]["api"]["status"] == "approved"


async def test_the_push_happens_before_the_tech_lead_runs():
    order: list[str] = []

    class Spy(FullIdWorkspace):
        def publish_ci_ref(self, path, ref):
            order.append("push")
            super().publish_ci_ref(path, ref)

    class Runner(ScriptedRunner):
        async def run_once(self, *args, **kwargs):
            order.append("review")
            return await super().run_once(*args, **kwargs)

    runner = Runner(tech_lead_review=[reviewed("approved")])
    ports = memory_ports(
        runner, stack=MemoryStack(ci=("check",), delivery_checks=("tests",)), workspace=Spy()
    )
    await run(state_of(), ports)
    assert order[:2] == ["push", "review"]


async def test_a_return_discards_the_record_of_an_earlier_push():
    earlier = {"head": "b" * 40, "pushed_at": "2026-11-04T10:00:00+00:00"}
    ports = ports_of(reviewed("returned"))
    patch = await run(state_of(ci_speculation=earlier), ports)
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert patch["ci_speculation"] is None


async def test_a_failed_push_never_fails_the_review_and_leaves_no_record():
    workspace = FullIdWorkspace()
    workspace.ci_ref_error = "push refused"
    ports = ports_of(reviewed("approved"), workspace=workspace)
    patch = await run(state_of(), ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "ci_speculation" not in patch
    assert "escalation" not in patch and patch.get("status") != "waiting_on_human"


async def test_a_failed_push_on_a_return_still_clears_the_older_record():
    workspace = FullIdWorkspace()
    workspace.ci_ref_error = "push refused"
    earlier = {"head": "b" * 40, "pushed_at": "2026-11-04T10:00:00+00:00"}
    ports = ports_of(reviewed("returned"), workspace=workspace)
    patch = await run(state_of(ci_speculation=earlier), ports)
    assert patch["ci_speculation"] is None


async def test_a_product_without_ci_makes_no_new_call():
    ports = ports_of(reviewed("approved"), ci=())
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == []
    assert ports.workspace.diff_reads == []
    assert "ci_speculation" not in patch


async def test_a_product_without_delivery_checks_does_not_push():
    ports = ports_of(reviewed("approved"), delivery=())
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


async def test_a_review_with_a_pull_request_does_not_push():
    ports = ports_of(reviewed("approved"))
    patch = await run(state_of(pull_request=PR), ports)
    assert ports.workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


async def test_a_head_that_touches_the_ci_definition_does_not_push():
    workspace = FullIdWorkspace()
    workspace.diff_paths = (".github/workflows/ci.yml",)
    ports = ports_of(reviewed("approved"), workspace=workspace)
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


async def test_a_diff_git_cannot_read_does_not_push():
    class Unreadable(FullIdWorkspace):
        def changed_paths(self, base, head):
            raise GitError("bad revision")

    ports = ports_of(reviewed("approved"), workspace=Unreadable())
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == []
    assert patch["subtasks"]["api"]["status"] == "approved"


async def test_a_unit_that_is_not_the_final_one_does_not_push():
    state = state_of()
    state["subtasks"]["domain"] = {**state["subtasks"]["api"], "id": "domain"}
    ports = ports_of(reviewed("approved"))
    patch = await run(state, ports)
    assert ports.workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


async def test_a_short_head_is_pushed_but_never_recorded():
    class Short(FullIdWorkspace):
        def full_head(self, path):
            return "abc1234"

    ports = ports_of(reviewed("approved"), workspace=Short())
    patch = await run(state_of(), ports)
    assert "ci_speculation" not in patch


TRANSIENT = (
    "fatal: unable to access 'https://x-access-token:ghp_secret1234@github.com/o/r/': "
    "Could not resolve host: github.com"
)
PERMANENT = (
    "remote: Permission to o/r.git denied to workflow. https://u:ghp_secret1234@github.com/o/r"
)


class Scripted(FullIdWorkspace):
    """A push that says each scripted text in turn, then goes through."""

    def __init__(self, *says: str, always: str | None = None):
        super().__init__()
        self.says = list(says)
        self.always = always

    def publish_ci_ref(self, path, ref):
        super().publish_ci_ref(path, ref)
        said = self.says.pop(0) if self.says else self.always
        if said is not None:
            raise GitError("git push failed", stderr=said)


async def test_a_transient_failure_that_heals_still_records_the_push():
    sleeps: list[float] = []
    ports = ports_of(reviewed("approved"), workspace=Scripted(TRANSIENT))
    patch = await run(state_of(), ports, sleeps)
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("publish", REF)]
    assert sleeps == [PUSH_BACKOFF_S[0]]
    assert patch["ci_speculation"]["head"] == HEAD
    assert patch["subtasks"]["api"]["status"] == "approved"


async def test_a_permanent_refusal_is_tried_once_leaves_no_record_and_logs_what_git_said(capsys):
    sleeps: list[float] = []
    ports = ports_of(reviewed("approved"), workspace=Scripted(always=PERMANENT))
    patch = await run(state_of(), ports, sleeps)
    assert ports.workspace.ci_ref_calls == [("publish", REF)] and sleeps == []
    assert "ci_speculation" not in patch
    assert patch["subtasks"]["api"]["status"] == "approved"
    err = capsys.readouterr().err
    assert (
        f"agentic: review: GH-465: speculative push failed after 1 attempt(s) (permanent): "
        f"{scrub(PERMANENT)}\n"
    ) == err
    assert "ghp_secret1234" not in err


async def test_a_transient_failure_that_never_heals_is_logged_after_every_attempt(capsys):
    ports = ports_of(reviewed("approved"), workspace=Scripted(always=TRANSIENT))
    patch = await run(state_of(), ports)
    assert len(ports.workspace.ci_ref_calls) == PUSH_ATTEMPTS
    assert "ci_speculation" not in patch
    err = capsys.readouterr().err
    assert f"after {PUSH_ATTEMPTS} attempt(s) (transient): {scrub(TRANSIENT)}" in err
