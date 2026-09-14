"""The rules of each node, against in-memory ports. No graph: each use case is called
with the state it would receive."""

import pytest

from agentic.application.classify import classify
from agentic.application.define import define
from agentic.application.ports import AttemptFailed
from agentic.application.validate_definition import validate_definition
from agentic.domain.configuration import Configuration
from agentic.domain.roles import PAIR_PM_PA
from agentic.domain.task import apply
from tests.fakes import ScriptedRunner, classified, defined, memory_ports, verdict

pytestmark = pytest.mark.anyio

TASK = {"id": "T-1", "title": "Export report", "description": "Users want CSV."}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_classify_sets_the_type_and_saves_the_document(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[classified()]))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "feature" and "escalated" not in patch
    assert patch["sections"] == {"PjM": "Clear."}
    assert ports.documents.last["id"] == "T-1" and ports.documents.last["type"] == "feature"
    assert ports.runner.calls[0]["model"] == "claude-sonnet-5"
    assert "## Product knowledge" not in ports.runner.calls[0]["prompt"]  # PjM does not read it


async def test_classify_escalates_on_low_confidence(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[classified(confidence="low")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    assert "PjM could not classify" in patch["escalated"]


async def test_classify_escalates_paths_not_built_yet(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[classified(type_="bug")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "bug"
    assert patch["escalated"] == "bug path not implemented yet"


async def test_define_reads_the_knowledge_and_proposes_flags(cfg):
    ports = memory_ports(ScriptedRunner(product_analyst=[defined()]))
    ports.knowledge.text = "## Product knowledge\n\nOld."
    patch = await define({**TASK, "type": "feature"}, cfg, ports)
    assert patch["status"] == "defined"
    assert patch["flags"] == {"touches_ui": False, "touches_copy": True}
    assert ports.runner.calls[0]["prompt"].startswith("## Product knowledge\n\nOld.")


async def test_validate_approval_distils_into_the_knowledge(cfg):
    ports = memory_ports(
        ScriptedRunner(
            pm=[
                verdict(
                    "approved",
                    definitions=[{"capability": "reports", "content": "# Reports"}],
                    glossary=[{"term": "Report", "definition": "A CSV.", "avoid": "export"}],
                )
            ]
        )
    )
    state = {**TASK, "type": "feature", "sections": {"Product Analyst": "v1"}}
    patch = await validate_definition(state, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert patch["flags"] == {"touches_ui": True, "touches_copy": False}  # PM replaces PA's set
    assert ports.knowledge.definitions == {"reports": "# Reports"}
    assert ports.knowledge.terms == {"Report": {"definition": "A CSV.", "avoid": "export"}}
    assert ports.runner.calls[0]["model"] == "claude-opus-5"
    assert "You have 2 left" in ports.runner.calls[0]["system_prompt"]


async def test_validate_return_counts_against_the_pair_and_writes_nothing(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "gap")]))
    state = {**TASK, "type": "feature", "sections": {"Product Analyst": "v1"}}
    patch = await validate_definition(state, cfg, ports)
    assert patch["status"] == "returned"
    assert patch["returns"] == {PAIR_PM_PA.key: 1}
    assert ports.knowledge.definitions == {} and ports.knowledge.terms == {}

    again = apply(state, patch)
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "still")]))
    patch = await validate_definition(again, cfg, ports)
    assert patch["returns"] == {PAIR_PM_PA.key: 2}
    assert "You have 1 left" in ports.runner.calls[0]["system_prompt"]


async def test_validate_escalates_at_the_limit(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "still no metrics")]))
    state = {**TASK, "type": "feature", "returns": {PAIR_PM_PA.key: 2}}
    patch = await validate_definition(state, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == f"{PAIR_PM_PA.key} hit 2 returns: still no metrics"
    assert "returns" not in patch
    assert "You have 0 left" in ports.runner.calls[0]["system_prompt"]


async def test_approving_a_feature_without_definitions_is_a_role_failure(cfg):
    empty = verdict("approved", definitions=[])
    ports = memory_ports(ScriptedRunner(pm=[empty, empty]))
    state = {**TASK, "type": "feature"}
    patch = await validate_definition(state, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    assert "without `definitions`" in patch["escalated"]
    assert "without `definitions`" in ports.runner.calls[1]["prompt"]
    assert ports.documents.last["failures"] == patch["failures"]


async def test_role_failure_is_saved_with_the_document(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[AttemptFailed("x", 0.05), AttemptFailed("y", 0.03)]))
    patch = await classify(TASK, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    saved = ports.documents.last
    assert saved["status"] == "waiting_on_human" and saved["cost_usd"] == pytest.approx(0.08)
    assert [f["error"] for f in saved["failures"]] == ["x", "y"]
