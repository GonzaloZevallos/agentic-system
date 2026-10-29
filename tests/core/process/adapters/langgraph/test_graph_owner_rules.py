"""The `owner_rules` step through the compiled task graph, with fake ports (GH-431): the owner's
`Owner's rule:` comment ends as an entry in the decisions file of the head `deliver` pushes,
and what is not the owner's, or has no file to go to, writes nothing."""

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import composition
from agentic.composition import default_ports
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.owner_rules import owner_rules
from agentic.core.process.application.ports import Checkout, CommandResult, IssueComment
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from agentic.shared.owner_decisions import OWNER_DECISIONS
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    bug,
    script_for,
    technical,
)
from tests.core.process.adapters.langgraph.test_graph_full_checks import (
    CHECKS,
    FAST,
    CommittingRunner,
    SteadyWorkspace,
    _Rebased,
)
from tests.fakes import (
    MemoryCommands,
    MemoryDocuments,
    MemoryInbox,
    MemorySampleApp,
    MemoryStack,
    delivered,
    planned,
    planned_parts,
    reviewed,
    ruled,
)

pytestmark = pytest.mark.anyio

PRODUCT_FILE, SYSTEM_FILE = OWNER_DECISIONS
OWNER = "zeta"


class _Owned(_Rebased):
    """`_Rebased` forwards what it names; the owner-rule readers are the real knowledge's."""

    def owner_parse(self, comments, owner):
        return self.inner.owner_parse(comments, owner)

    def owner_append(self, text, entries):
        return self.inner.owner_append(text, entries)


class RealPathWorkspace(SteadyWorkspace):
    """The task checkout is a real directory, so the step can read and write a file in it; a
    commit of paths is a commit like a Developer's."""

    def __init__(self, root) -> None:
        super().__init__()
        self.root = root

    def task_checkout(self, task_id):
        self.root.mkdir(parents=True, exist_ok=True)
        return Checkout(f"agentic/{task_id}", self.root, "base0", created=False)

    def commit_paths(self, path, relatives, message):
        self.path_commits.append((path, tuple(relatives), message))
        self.commit(path)
        return self.full_head(path)


def comment(body, author=OWNER):
    return IssueComment(body, "2026-10-28T10:00:00Z", author, "https://x/issues/1#issuecomment-1")


def build(tmp_path, comments, stack=None, system_file=False, commands=None, script=None):
    fakes = Fakes()
    fakes.workspace = RealPathWorkspace(tmp_path / "checkout")
    if system_file:
        target = fakes.workspace.root / SYSTEM_FILE
        target.parent.mkdir(parents=True)
        target.write_text("# Decisiones del dueño\n", encoding="utf-8")
    fakes.tracker.timestamped["GH-1"] = comments
    script = CommittingRunner(fakes.workspace, script or script_for(**technical()))
    stack = stack or MemoryStack(checks=CHECKS, delivery_checks=FAST)

    def ports_for(cfg):
        real = default_ports(cfg)
        knowledge = real.knowledge
        knowledge.product_dir = cfg.product_dir
        return replace(
            real,
            runner=script,
            workspace=fakes.workspace,
            tracker=fakes.tracker,
            knowledge=_Owned(knowledge, cfg.product_dir),
            stack=stack,
            commands=commands or MemoryCommands(),
            sample_app=MemorySampleApp(),
            check_results=None,
            inbox=MemoryInbox(),
        )

    graph = build_graph(ports_for, NoTelemetry(), arm_for=composition._arm_for)
    return graph.compile(checkpointer=InMemorySaver()), fakes


def config_of(tmp_path, owner=OWNER):
    return {
        "configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1", "owner_login": owner},
        "recursion_limit": 60,
    }


def written(fakes):
    target = fakes.workspace.root / PRODUCT_FILE
    return target.read_text(encoding="utf-8") if target.exists() else ""


async def test_the_owners_marked_comment_ends_as_an_entry_in_the_head_that_is_delivered(tmp_path):
    graph, fakes = build(tmp_path, [comment("Owner's rule: Un PR lo abre la entrega.")])
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert final["status"] == "ready_for_review"
    assert "Un PR lo abre la entrega." in written(fakes)
    assert written(fakes).count("Un PR lo abre la entrega.") == 1
    docs = final["close_docs"]
    assert docs["paths"] == [PRODUCT_FILE] and docs["head"] == final["task_head"]
    reviewed_head = final["subtasks"]["api"]["reviewed_head"]
    assert rules.head_reviewed(reviewed_head, docs, final["task_head"])
    assert final["full_checks"]["head"] == final["task_head"]


async def test_a_comment_of_another_account_writes_nothing(tmp_path):
    graph, fakes = build(tmp_path, [comment("Owner's rule: Todo se aprueba.", author="intruder")])
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert final["status"] == "ready_for_review"
    assert written(fakes) == "" and not fakes.workspace.path_commits
    assert not final.get("close_docs")


async def test_without_a_configured_owner_nothing_is_written_and_the_document_says_so(tmp_path):
    graph, fakes = build(tmp_path, [comment("Owner's rule: Una regla.")])
    final = await graph.ainvoke(TASK, config=config_of(tmp_path, owner=""))

    assert written(fakes) == "" and not fakes.workspace.path_commits
    assert "AGENTIC_OWNER_LOGIN unset" in final["sections"]["Owner rules"]


async def test_a_system_rule_without_a_system_file_is_a_checkbox_in_the_ready_comment(
    tmp_path,
):
    graph, fakes = build(tmp_path, [comment("Owner's rule (system): Se parte arriba de 150 USD.")])
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert written(fakes) == "" and not fakes.workspace.path_commits
    assert final["owner_rules_elsewhere"] == [
        {"rule": "Se parte arriba de 150 USD.", "cls": "system"}
    ]
    ready = [body for _, body in fakes.tracker.comments if "Se parte arriba de 150 USD." in body]
    assert ready and "- [ ] Owner's rule not written (system): Se parte" in ready[0]


async def test_a_system_rule_goes_to_the_system_file_when_the_checkout_has_it(tmp_path):
    graph, fakes = build(
        tmp_path, [comment("Owner's rule (system): Se parte arriba de 150 USD.")], system_file=True
    )
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    text = (fakes.workspace.root / SYSTEM_FILE).read_text(encoding="utf-8")
    assert "Se parte arriba de 150 USD." in text
    assert final["close_docs"]["paths"] == [SYSTEM_FILE]
    assert not final.get("owner_rules_elsewhere")


def test_after_integrate_routes_to_the_step_before_the_full_suite():
    state = {
        **TASK,
        "subtasks": {"api": {"status": "approved", "reviewed_head": "r1"}},
        "task_head": "r1",
    }
    assert rules.owner_rules_due(state)
    assert rules.after_integrate(state) == "owner_rules"
    seen = {**state, "owner_rules_seen": "r1"}
    assert rules.after_integrate(seen) != "owner_rules"
    assert rules.after_owner_rules(seen) == "integrate"
    assert rules.after_owner_rules({**seen, "escalated": "x"}) is None


async def test_a_red_full_run_reopens_the_integration_and_the_entry_is_kept_exactly_once(tmp_path):
    script = script_for(
        **technical(
            developer=[delivered(), delivered()],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        )
    )
    commands = MemoryCommands(playwright=[CommandResult(1, "e2e failed")] * 2)
    graph, fakes = build(
        tmp_path,
        [comment("Owner's rule: Un PR lo abre la entrega.")],
        commands=commands,
        script=script,
    )
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert final["status"] == "ready_for_review"
    assert final["full_checks"]["version"] == 2
    assert written(fakes).count("Un PR lo abre la entrega.") == 1
    assert len(fakes.workspace.path_commits) == 1


async def test_a_task_with_an_integration_writes_on_the_task_branch_head(tmp_path):
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(
        **technical(
            tech_lead=[plan],
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain")],
            tech_lead_review__api=[reviewed("approved")],
            tech_lead_review__domain=[reviewed("approved")],
            tech_lead_review=[reviewed("approved")],
        )
    )
    graph, fakes = build(
        tmp_path, [comment("Owner's rule: Un PR lo abre la entrega.")], script=script
    )
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert INTEGRATION in final["subtasks"] and final["status"] == "ready_for_review"
    assert written(fakes).count("Un PR lo abre la entrega.") == 1
    reviewed_head = final["subtasks"][INTEGRATION]["reviewed_head"]
    assert rules.head_reviewed(reviewed_head, final["close_docs"], final["task_head"])


async def test_a_split_never_reaches_the_step(tmp_path):
    script = script_for(**technical(tech_lead=[planned_parts()]))
    graph, fakes = build(tmp_path, [comment("Owner's rule: Una regla.")], script=script)
    fakes.tracker.issue_numbers_from = 101
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert final["status"] == "split"
    assert "owner_rules_seen" not in final and not fakes.workspace.path_commits
    assert written(fakes) == ""


async def test_a_discard_never_reaches_the_step(tmp_path):
    script = bug(pm_bug=[ruled("approved", "expected_behaviour", "weekly by definition")])
    graph, fakes = build(tmp_path, [comment("Owner's rule: Una regla.")], script=script)
    final = await graph.ainvoke(TASK, config=config_of(tmp_path))

    assert final["status"] == "discarded"
    assert "owner_rules_seen" not in final and not fakes.workspace.path_commits
    assert written(fakes) == ""


async def test_under_an_open_pull_request_the_step_only_observes_the_head(tmp_path):
    """A landing pass reaching the fan-in again must not push an owner commit nobody reviewed."""
    cfg = Configuration(product_dir=tmp_path, owner_login=OWNER)
    fakes = Fakes()
    fakes.workspace = RealPathWorkspace(tmp_path / "checkout")
    fakes.tracker.timestamped["GH-1"] = [comment("Owner's rule: Una regla.")]
    ports = replace(
        default_ports(cfg),
        workspace=fakes.workspace,
        tracker=fakes.tracker,
        documents=MemoryDocuments(),
    )
    state = {
        **TASK,
        "pull_request": "https://x/pull/7",
        "task_head": "h2",
        "subtasks": {"api": {"status": "approved", "reviewed_head": "h1"}},
    }
    patch = await owner_rules(state, cfg, ports)

    assert patch == {"owner_rules_seen": "h2"} and not fakes.workspace.path_commits
