"""`resume.prepare` keeps the plan held by a plan-stage drawing pause when the owner accepts every
departure it declared (GH-1004 part 2): the plan check runs again, and only when
`drawing_answer.plan_stands_pre` holds. The paused states come from `plan.plan` (which runs
`_finished`), never from a hand-built `plan_held`. Seams: `resume.prepare`, `Resumption.answer`."""

import pytest

from agentic.core.process.application import resume
from agentic.core.process.application.escalate import ESCALATED
from agentic.core.process.application.plan import plan
from agentic.core.process.domain import drawing, drawing_answer, rules
from agentic.core.process.domain.size_budget import PROVEN_PAIRS
from agentic.core.process.domain.task import apply, departure_records
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_plan_drawing_gate import (
    DESIGN,
    OTHER,
    RAW,
    departing,
    designed,
)
from tests.core.process.application.test_plan_estimate import (
    TASK,
    arm_of,
    over_the_line_by_reference,
    sized_closed,
)
from tests.fakes import MemoryDocuments, MemoryStack, MemoryWorkspace, memory_ports

pytestmark = pytest.mark.anyio

AT = "2026-11-21T10:00:00Z"
ACCEPT = "Drawing 1: accept"


async def paused_by_the_plan(tmp_path, *raws, base=TASK, **kwargs):
    """The state `plan.plan` leaves on a plan-stage drawing pause, as the checkpoint holds it."""
    runner, cfg = arm_of("cold", tmp_path, departing(*(raws or (RAW,)), **kwargs))
    return apply(base, await plan(base, cfg, designed(runner)))


def resuming(*comments, stack=None, workspace=None, documents=None):
    ports = memory_ports(
        stack=stack or MemoryStack(design=DESIGN),
        workspace=workspace,
        documents=documents,
        now=lambda: AT,
    )
    ports.tracker.earlier = [f"{ESCALATED}the owner decides the departures", *comments]
    return ports


def prepared(paused, cfg, ports):
    return resume.prepare("GH-12", paused, "checkpoint", cfg, ports)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def today(paused, cfg, ports):
    """The resumption the same pause gets without the restore: `plan_recheck` forced to False."""
    resumed = prepared(paused, cfg, ports)
    patch = drawing_answer.resume_patch(
        apply(paused, {"publish_owed": None}),
        resumed.decision,
        None,
        resumed.drawing_answers,
        at=AT,
    )
    return apply(apply(paused, {"publish_owed": None}), patch)


async def test_every_departure_accepted_with_a_green_recheck_restores_the_held_plan(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r = prepared(paused, cfg, resuming(ACCEPT))

    assert r.plan_recheck is True and r.answer()["plan_recheck"] is True
    assert r.target == "integrate"
    assert r.resumed["status"] == "planned" and not r.resumed.get("plan_due")
    assert r.resumed["plan_held"] is None
    assert list(r.resumed["subtasks"]) == ["api"]
    assert not r.resumed["escalated"]


async def test_a_green_recheck_differs_from_today_only_where_the_restore_says(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    ports = resuming(ACCEPT)
    r = prepared(paused, cfg, ports)
    plain = today(paused, cfg, resuming(ACCEPT))

    differing = {k for k in {*r.resumed, *plain} if r.resumed.get(k) != plain.get(k)}
    assert differing == {"status", "subtasks", "plan_due"}
    assert plain["plan_due"] is True and rules.resume_target(plain) == "plan"


async def test_a_rejection_resumes_as_today_and_emits_no_recheck(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r = prepared(paused, cfg, resuming("Drawing 1: reject"))

    assert r.plan_recheck is False and "plan_recheck" not in r.answer()
    assert r.resumed["plan_due"] is True and r.resumed["plan_held"] is None
    assert r.target == "plan"


async def test_an_answer_with_text_besides_the_verdicts_plans_again(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r = prepared(paused, cfg, resuming(f"{ACCEPT}\n\nbut keep the title short"))

    assert r.plan_recheck is False and r.target == "plan" and r.resumed["plan_due"] is True


async def test_an_unrelated_rejected_delivery_row_does_not_block_the_restore(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    (delivery,) = departure_records(
        paused["drawing_departures"], [drawing.Departure(**{**RAW, "element": "sidebar"})], "t0"
    )
    rejected = {**delivery, "answer": {"verdict": "reject", "by": "comment", "at": "t1"}}
    paused = apply(paused, {"drawing_departures": [rejected]})

    r = prepared(paused, cfg, resuming("Drawing 1: accept"))
    assert r.plan_recheck is True and r.target == "integrate"


async def test_two_departures_answered_in_one_resume_restore(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path, RAW, OTHER)
    r = prepared(paused, cfg, resuming("Drawing 1: accept\nDrawing 2: accept"))
    assert r.plan_recheck is True and r.target == "integrate"


class UnreadableDesign(MemoryStack):
    def design(self, revision):
        raise ValueError("stack.yaml: design is not readable")


class NoDesign(MemoryStack):
    def design(self, revision):
        return None


@pytest.mark.parametrize("stack", [UnreadableDesign(), NoDesign()])
async def test_a_design_that_cannot_be_read_or_is_gone_plans_again(stack, cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r = prepared(paused, cfg, resuming(ACCEPT, stack=stack))

    assert r.plan_recheck is False and "plan_recheck" not in r.answer()
    assert r.target == "plan" and r.resumed["plan_due"] is True and r.resumed["plan_held"] is None


async def test_a_workflow_read_that_raises_does_not_restore_nor_crash_the_resume(cfg, tmp_path):
    class Unreadable(MemoryWorkspace):
        def tree_names(self, ref, directory):
            raise RuntimeError("git went away")

    paused = await paused_by_the_plan(tmp_path)
    r = prepared(paused, cfg, resuming(ACCEPT, workspace=Unreadable()))
    assert r.plan_recheck is False and r.target == "plan"


async def test_the_plan_node_s_own_held_plan_passes_the_recheck(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path, RAW, invariants=["Keep it pure."])
    assert prepared(paused, cfg, resuming(ACCEPT)).plan_recheck is True


async def test_a_history_that_puts_the_plan_over_the_fit_line_fails_the_recheck(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    ports = over_the_line_by_reference(resuming(ACCEPT))
    r = prepared(paused, cfg, ports)

    assert r.plan_recheck is False and r.target == "plan" and r.resumed["plan_due"] is True


async def test_a_history_that_puts_the_plan_over_the_size_budget_fails_the_recheck(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path, hand_files=6)
    assert prepared(paused, cfg, resuming(ACCEPT)).plan_recheck is True  # unproven ratio: fits

    ports = resuming(ACCEPT)
    ports.documents.saved.extend(sized_closed(PROVEN_PAIRS))
    r = prepared(paused, cfg, ports)
    assert r.plan_recheck is False and r.target == "plan" and r.resumed["plan_due"] is True


# -- nothing is read unless the plan can stand ---------------------------------------------------


class Counting:
    """Counts the reads the recheck makes: workflows, the stack's ci and design, the documents."""

    def __init__(self):
        self.workflow = self.ci = self.design = self.documents = 0

    def ports(self, *comments):
        counts = self

        class Workspace(MemoryWorkspace):
            def tree_names(self, ref, directory):
                counts.workflow += 1
                return super().tree_names(ref, directory)

            def show(self, ref, relative):
                counts.workflow += 1
                return super().show(ref, relative)

        class Stack(MemoryStack):
            def ci(self, revision):
                counts.ci += 1
                return super().ci(revision)

            def design(self, revision):
                counts.design += 1
                return super().design(revision)

        class Documents(MemoryDocuments):
            def ids(self):
                counts.documents += 1
                return super().ids()

        return resuming(
            *comments,
            stack=Stack(design=DESIGN),
            workspace=Workspace(),
            documents=Documents(),
        )

    def reads(self):
        return self.workflow, self.ci, self.design, self.documents


async def test_a_resume_that_cannot_restore_reads_no_workflow_stack_or_document(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    for comment in ("Drawing 1: reject", f"{ACCEPT}\n\nand add a footer"):
        counts = Counting()
        r = prepared(paused, cfg, counts.ports(comment))
        assert r.plan_recheck is False
        assert counts.reads() == (0, 0, 0, 0)


async def test_a_resume_that_can_restore_reads_each_once(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    counts = Counting()
    r = prepared(paused, cfg, counts.ports(ACCEPT))

    assert r.plan_recheck is True
    assert counts.ci == 1 and counts.design == 1 and counts.documents == 1
    assert counts.workflow == 1  # the directory listing: the base declares no workflow to show


async def test_a_pause_that_is_not_a_drawing_one_reads_nothing_either(cfg, tmp_path):
    paused = {k: v for k, v in (await paused_by_the_plan(tmp_path)).items() if k != "plan_held"}
    counts = Counting()
    r = prepared(paused, cfg, counts.ports(ACCEPT))
    assert r.plan_recheck is False and counts.reads() == (0, 0, 0, 0)
