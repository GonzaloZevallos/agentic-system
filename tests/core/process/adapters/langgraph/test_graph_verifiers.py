"""The verifiers of the integrated head through the compiled graph (GH-469), on the fake
ports: those a task needs start together in the one `verify` node, `deliver` waits for all of
them, and a return carries the findings of every verifier that returned."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentic import composition
from agentic.composition import default_ports
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import AttemptFailed, CommandResult
from agentic.core.process.domain import rules
from agentic.core.process.domain.performance import Limit, Measurement, Run, Scenario
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    run_graph,
    script_for,
    technical,
)
from tests.core.process.adapters.langgraph.test_graph_full_checks import (
    CHECKS,
    FAST,
    CommittingRunner,
    DocsWorkspace,
    SteadyWorkspace,
    _Rebased,
    build,
)
from tests.fakes import (
    FakePerfLock,
    FakePerfRunner,
    MemoryCommands,
    MemorySampleApp,
    MemoryStack,
    consolidation,
    copy_draft,
    copy_finding,
    copy_reviewed,
    delivered,
    design_draft,
    design_findings,
    final_validation,
    planned,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

BOTH = {"touches_ui": True, "touches_copy": True}
UI = {"touches_ui": True, "touches_copy": False}
COPY = {"touches_ui": False, "touches_copy": True}
TEXTS = "### Texts\n\n- `Export` button: Export report."


@pytest.fixture
def config(tmp_path):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def _script(flags, **verifiers):
    queues = {}
    if flags["touches_ui"]:
        queues |= {
            "ux_designer": [design_draft()],
            "ux_lead": [design_findings("approved")],
            "ui_designer": [design_draft()],
            "ui_lead": [design_findings("approved")],
            "pm_consolidator": [consolidation("approved")],
            "ux_lead_final": [design_findings("approved")],
            "ui_lead_final": [design_findings("approved")],
        }
    if flags["touches_copy"]:
        queues["tech_writer"] = [copy_draft(TEXTS)]
    return script_for(
        pm=[verdict("approved", flags=flags)], **queues, **{**technical(), **verifiers}
    )


async def test_every_verifier_that_applies_runs_once_on_the_same_head_before_deliver(config):
    script = _script(
        BOTH,
        tech_writer_review=[copy_reviewed([copy_finding(scope="outside")])],
        pm_final=[final_validation("approved")],
    )

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert script.slugs.count("tech_writer_review") == 1
    assert script.slugs.count("pm_final") == 1
    assert rules.verifiers_due(final) == ()
    assert not final.get("pending_close_docs")
    last_review = max(i for i, slug in enumerate(script.slugs) if slug == "tech_lead_review")
    assert last_review < script.slugs.index("tech_writer_review")
    assert last_review < script.slugs.index("pm_final")


async def test_only_the_verifiers_that_apply_run(config):
    script = _script(COPY, tech_writer_review=[copy_reviewed([copy_finding(scope="outside")])])

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert "tech_writer_review" in script.slugs
    assert not {"pm_final", "qa_performance"} & set(script.slugs)


async def test_a_return_carries_the_sibling_findings_and_the_set_judges_the_fixed_head_again(
    config,
):
    script = script_for(
        pm=[verdict("approved", flags=BOTH)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=[
            copy_reviewed([copy_finding(scope="outside")]),
            copy_reviewed(settled=[]),
        ],
        ux_designer=[design_draft()],
        ux_lead=[design_findings("approved")],
        ui_designer=[design_draft()],
        ui_lead=[design_findings("approved")],
        pm_consolidator=[consolidation("approved")],
        ux_lead_final=[design_findings("approved")] * 2,
        ui_lead_final=[design_findings("approved")] * 2,
        pm_final=[
            final_validation("returned", "too narrow", blocking=["still 1037px"]),
            final_validation("approved"),
        ],
        **technical(
            developer=[delivered(), delivered("fixed the width")],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        ),
    )

    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    # The copy review was not held back by PM's return: it judged the first head too.
    assert script.slugs.count("tech_writer_review") == 2
    assert script.slugs.count("pm_final") == 2
    first = script.slugs.index("tech_writer_review")
    assert first < script.slugs.index("developer", script.slugs.index("pm_final"))
    assert rules.verifiers_due(final) == ()


def _recursive(config):
    return {**config, "recursion_limit": 60}


async def test_a_local_red_full_checks_reopens_the_developer_before_any_other_role_runs(config):
    commands = MemoryCommands(playwright=[CommandResult(1, "e2e failed")] * 2)
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = _script(
        COPY,
        tech_writer_review=[copy_reviewed()],
        **technical(
            developer=[delivered(), delivered("fixed")],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        ),
    )
    graph, _, script = build(script, stack, commands)

    final = await graph.ainvoke(TASK, config=_recursive(config))

    assert final["status"] == "ready_for_review"
    slugs = script.slugs
    first_review = slugs.index("tech_lead_review")
    # The red suite ran alone: the reopened Developer is the next role, no sibling was paid for.
    assert slugs[first_review + 1] == "developer"
    assert slugs.count("tech_writer_review") == 1
    assert slugs.index("tech_writer_review") > slugs.index("tech_lead_review", first_review + 1)


def _performance_graph(script, stack, perf):
    fakes = Fakes()
    fakes.workspace = SteadyWorkspace()
    runner = CommittingRunner(fakes.workspace, script)

    def ports_for(cfg):
        real = default_ports(cfg)
        knowledge = real.knowledge
        knowledge.product_dir = cfg.product_dir
        return replace(
            real,
            runner=runner,
            workspace=fakes.workspace,
            tracker=fakes.tracker,
            knowledge=_Rebased(knowledge, cfg.product_dir),
            stack=stack,
            commands=MemoryCommands(),
            sample_app=MemorySampleApp(),
            check_results=None,
            perf_runner=perf,
            perf_lock=FakePerfLock(),
        )

    graph = build_graph(ports_for, NoTelemetry(), arm_for=composition._arm_for)
    return graph.compile(checkpointer=InMemorySaver()), runner


async def test_two_verifiers_that_return_reopen_one_unit_with_both_reasons(config):
    plan = planned()
    plan.structured_output["performance"] = [
        {
            "name": "list",
            "volume": 1000,
            "volume_unit": "rows",
            "basis": "b",
            "budgets": [
                {"metric": "list_ms", "limit": 100, "unit": "ms", "source": "issue", "basis": "b"}
            ],
        }
    ]
    slow = Run(Measurement(1000, "rows", {"list_ms": 150.0}), 0, False, 0.5, 0.6)
    perf = FakePerfRunner([Run(None, 0, False, 0.5, 0.5), *[slow] * 5])
    stack = MemoryStack(
        performance={
            "list": Scenario("list", "seed", "measure", 1, "rows", {"list_ms": Limit(100, "ms")})
        }
    )
    script = script_for(
        pm=[verdict("approved", flags=UI)],
        ux_designer=[design_draft()],
        ux_lead=[design_findings("approved")],
        ui_designer=[design_draft()],
        ui_lead=[design_findings("approved")],
        pm_consolidator=[consolidation("approved")],
        ux_lead_final=[design_findings("approved")],
        ui_lead_final=[design_findings("approved")],
        pm_final=[final_validation("returned", "too narrow", blocking=["still 1037px"])],
        qa_performance=[reviewed("approved")],
        **technical(tech_lead=[plan]),
    )
    graph, runner = _performance_graph(script, stack, perf)
    # No change request is left to spend: the single return that both verifiers made pauses.
    limited = {"configurable": {**config["configurable"], "max_changes_requested_per_pair": 0}}

    paused = await graph.ainvoke(TASK, config={**limited, "recursion_limit": 60})

    assert {"pm_final", "qa_performance"} <= set(runner.slugs)
    unit = next(iter(paused["subtasks"].values()))
    assert (unit["status"], unit["returned_by"]) == ("returned", "final_validation")
    assert unit["returned_with"] == ["qa_performance"]
    assert "final_validation: too narrow" in unit["reason"]
    assert "qa_performance: list/list_ms over 100ms" in unit["reason"]
    assert rules.returned_by_all(unit) == ("final_validation", "qa_performance")
    assert paused["escalated"]


async def test_a_sibling_that_escalates_keeps_the_files_pm_approved_for_the_resume(config):
    capability = {
        "capability": "dashboard",
        "content": "## Jobs\n\nCuando entreno, quiero ver mi semana.",
    }
    script = _script(
        BOTH,
        tech_writer_review=[
            AttemptFailed("no output", 0.0),
            AttemptFailed("no output", 0.0),
            copy_reviewed(),
        ],
        pm_final=[final_validation("approved", definitions=[capability])],
    )
    workspace = DocsWorkspace()
    graph, fakes, script = build(
        script, MemoryStack(checks=CHECKS), MemoryCommands(), workspace=workspace
    )
    config = {**_recursive(config), "recursion_limit": 80}

    paused = await graph.ainvoke(TASK, config=config)

    assert paused["escalated"] and paused["status"] == "waiting_on_human"
    owed = paused["pending_close_docs"]
    assert owed["definitions"] == [capability]
    # Nothing was written or committed while a sibling was still to judge the head.
    assert "close_docs" not in paused or not paused["close_docs"]
    commits = sum(workspace.delivered_commits.values())

    final = await graph.ainvoke(Command(resume={"decision": None}), config=config)

    assert final["status"] == "ready_for_review"
    assert script.slugs.count("pm_final") == 1  # approved on a head that did not move
    assert script.slugs.count("tech_writer_review") == 3
    # The only commit between the pause and the push is the close-time capability one.
    assert sum(workspace.delivered_commits.values()) == commits + 1
    docs = final["close_docs"]
    assert docs["paths"] == ["docs/product/dashboard.md"]
    assert rules.same_commit(docs["parent"], owed["parent"])
    assert not final.get("pending_close_docs")
    assert fakes.workspace.events.count("push") == 1
