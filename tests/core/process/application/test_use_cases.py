"""The rules of each node, against in-memory ports. No graph: each use case is called
with the state it would receive."""

from datetime import datetime

import pytest

from agentic.core.process.application.classify import classify
from agentic.core.process.application.define import define
from agentic.core.process.application.diagnose import diagnose
from agentic.core.process.application.discard import discard, discard_reason
from agentic.core.process.application.ports import AttemptFailed, IssueState, RawResult, RiskArea
from agentic.core.process.application.validate_prd import validate_prd
from agentic.core.process.domain import task_path
from agentic.core.process.domain.roles import PAIR_PM_PA
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    REPORTS,
    FakeReaper,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    classified,
    defined,
    diagnosed,
    memory_ports,
    questions,
    ruled,
    verdict,
)

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
    assert ports.runner.calls[0]["model"] == Configuration.model_fields["actor_model"].default
    assert "## Product knowledge" not in ports.runner.calls[0]["prompt"]  # PjM does not read it


async def test_classify_continues_a_low_confidence_platform_guess_as_a_feature(cfg):
    # GH-239 edits the case that asserted a low confidence escalated as `classification`.
    ports = memory_ports(
        ScriptedRunner(pjm=[classified(type_="platform_enhancement", confidence="low")])
    )
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "feature"
    assert "escalated" not in patch and "status" not in patch and "escalation_class" not in patch
    section = patch["sections"]["PjM"]
    assert section.startswith("Clear.")
    assert "guessed `platform_enhancement`" in section and "continues as `feature`" in section
    assert ports.documents.last["type"] == "feature"


async def test_classify_keeps_a_low_confidence_bug_a_bug_and_a_confident_guess_unannotated(cfg):
    low = memory_ports(ScriptedRunner(pjm=[classified(type_="bug", confidence="low")]))
    patch = await classify(TASK, cfg, low)
    assert patch["type"] == "bug" and "continues as `bug`" in patch["sections"]["PjM"]
    # Rejected input: a confident guess is neither re-typed nor annotated.
    sure = memory_ports(ScriptedRunner(pjm=[classified(type_="platform_enhancement")]))
    patch = await classify(TASK, cfg, sure)
    assert patch["type"] == "platform_enhancement" and patch["sections"] == {"PjM": "Clear."}


async def test_classify_lets_a_bug_through(cfg):
    """The three paths exist (issues #46, #65): nothing to escalate on a confident type,
    and where it goes next is the routing's call, not this node's."""
    ports = memory_ports(ScriptedRunner(pjm=[classified(type_="bug")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "bug"
    assert "escalated" not in patch and "status" not in patch


async def test_classify_lets_a_platform_enhancement_through(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[classified(type_="platform_enhancement")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "platform_enhancement"
    assert "escalated" not in patch and "status" not in patch


async def test_classify_passes_the_declared_product_name_to_the_prompt(cfg):
    # ADR-0019: the name reaches the prompt as data, the same class of fact as
    # `{{date}}`, so the model does not have to guess it from issue vocabulary.
    ports = memory_ports(ScriptedRunner(pjm=[classified()]), stack=MemoryStack(product="gym-saas"))
    await classify(TASK, cfg, ports)
    assert "gym-saas" in ports.runner.calls[0]["system_prompt"]


async def test_classify_falls_back_to_unknown_when_the_name_cannot_be_read(cfg):
    # `preflight.product_name`, never a bare `ports.stack.product()`: a `ValueError`
    # here must not kill the node mid-run (ADR-0019).
    ports = memory_ports(ScriptedRunner(pjm=[classified()]), stack=MemoryStack(product=None))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "feature"
    assert "unknown" in ports.runner.calls[0]["system_prompt"]


async def test_classify_lets_a_documented_discrepancy_through_without_escalating(cfg):
    # gym-saas #20's shape: the ticket offers both resolutions (fix the code, or the
    # doc) and PjM still returns `bug` with high confidence; the fix was the prompt,
    # not this routing (docs/product/classification.md).
    ports = memory_ports(ScriptedRunner(pjm=[classified(type_="bug")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["type"] == "bug"
    assert "escalated" not in patch and "status" not in patch


async def test_define_reads_the_knowledge_and_proposes_flags(cfg):
    runner = ScriptedRunner(
        product_analyst_interviewer=[questions(done=True)], product_analyst=[defined()]
    )
    ports = memory_ports(runner)
    ports.knowledge.text = "## Product knowledge\n\nOld."
    patch = await define({**TASK, "type": "feature"}, cfg, ports)
    assert patch["status"] == "defined"
    assert patch["flags"] == {"touches_ui": False, "touches_copy": True}
    for call in runner.calls:
        assert call["prompt"].startswith("## Product knowledge\n\nOld.")


# The bug path (issue #65): the diagnosis in place of the definition, PM's ruling on it.

BUG = {**TASK, "type": "bug", "sections": {"PjM": "A bug."}}


async def test_diagnose_reproduces_in_the_task_checkout_with_the_products_checks(cfg):
    runner = ScriptedRunner(product_analyst_bug=[diagnosed()])
    ports = memory_ports(runner, stack=MemoryStack(checks={"tests": "uv run pytest"}))
    ports.knowledge.text = "## Product knowledge\n\nOld."
    patch = await diagnose(BUG, cfg, ports)
    assert patch["status"] == "defined"  # what the routing reads as "PM has to validate"
    assert patch["branch"] == "agentic/T-1"
    assert list(patch["sections"]) == ["Product Analyst"]
    assert "flags" not in patch  # a bug proposes none
    call = runner.calls[0]
    assert call["slug"] == "product_analyst_bug"
    assert call["cwd"] == MemoryWorkspace.ROOT / "T-1"  # reproduces in the task's checkout
    # It reads the code and runs the product's checks, and has no tool that writes.
    assert "Bash(uv run pytest:*)" in call["role"].allowed
    assert not {"Edit", "Write"} & set(call["role"].tools)
    assert "- `tests`: `uv run pytest`" in call["system_prompt"]
    assert call["prompt"].startswith("## Product knowledge\n\nOld.")
    assert "## PjM\n\nA bug." in call["prompt"]
    assert ports.workspace.commits == []  # nothing on disk to commit


async def test_validate_approves_a_bug_and_consolidates_what_pm_wrote(cfg):
    written = [{"capability": "reports", "content": REPORTS}]
    ports = memory_ports(ScriptedRunner(pm_bug=[ruled("approved", "bug", definitions=written)]))
    state = {**BUG, "sections": {"Product Analyst": "### Ruling\n\nundefined"}}
    patch = await validate_prd(state, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert "flags" not in patch
    assert ports.knowledge.definitions == {"reports": REPORTS}
    assert ports.workspace.commits == [(MemoryWorkspace.ROOT / "T-1", "T-1: product definition")]
    call = ports.runner.calls[0]
    assert (
        call["slug"] == "pm_bug"
        and call["model"] == Configuration.model_fields["verifier_model"].default
    )
    assert "## Product Analyst\n\n### Ruling\n\nundefined" in call["prompt"]
    assert "You have 2 left" in call["system_prompt"]


async def test_validate_approves_a_bug_against_a_written_definition_without_writing(cfg):
    ports = memory_ports(ScriptedRunner(pm_bug=[ruled("approved", "bug")]))
    patch = await validate_prd(BUG, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert ports.knowledge.definitions == {} and ports.workspace.commits == []


async def test_validate_discards_a_bug_whose_behaviour_is_expected(cfg):
    # The prompt asks PM for no definition here; one sent anyway goes nowhere, since a
    # discarded task delivers nothing. The product is right, so nothing is built.
    written = [{"capability": "reports", "content": REPORTS}]
    ruling = ruled("approved", "expected_behaviour", "weekly by definition", definitions=written)
    ports = memory_ports(ScriptedRunner(pm_bug=[ruling]))
    patch = await validate_prd(BUG, cfg, ports)
    assert patch["status"] == "discarded"
    assert patch["discard_reason"] == "weekly by definition"
    assert ports.knowledge.definitions == {} and ports.workspace.commits == []
    assert "changes_requested" not in patch and "escalated" not in patch


async def test_validate_returns_a_diagnosis_against_the_same_pair(cfg):
    ports = memory_ports(ScriptedRunner(pm_bug=[ruled("returned", reason="not reproduced")]))
    patch = await validate_prd(BUG, cfg, ports)
    assert patch["status"] == "returned"
    assert patch["changes_requested"] == {PAIR_PM_PA.key: 1}
    assert patch["change_history"] == [
        {"pair": PAIR_PM_PA.key, "category": "definition", "reason": "not reproduced"}
    ]
    assert ports.knowledge.definitions == {}


async def test_discard_tells_the_issue_why_and_leaves_it_to_the_human(cfg):
    ports = memory_ports()
    state = {**BUG, "status": "discarded", "discard_reason": "weekly by definition"}
    patch = await discard(state, cfg, ports)
    assert ports.tracker.comments == [
        ("T-1", "Discarded: weekly by definition\n\nTask document: `.agentic/tasks/T-1.md`")
    ]
    assert ports.tracker.labels == [] and ports.tracker.pull_requests == []
    assert ports.workspace.cleaned == ["T-1"]  # an end keeps no worktree, like a delivery
    assert patch["current_role"] == "PjM"
    assert datetime.fromisoformat(patch["finished_at"]).tzinfo is not None
    assert ports.documents.last["status"] == "discarded"
    assert ports.documents.last["finished_at"] == patch["finished_at"]
    # A document that lost the reason falls back to PM's section, past its heading.
    section = "### Verdict\n\napproved: the report is weekly\n\n### Adjustments"
    assert discard_reason({"sections": {"PM": section}}) == "approved: the report is weekly"
    assert discard_reason({"discard_reason": None}) == "expected behaviour"


async def test_validate_approval_consolidates_into_the_knowledge(cfg):
    ports = memory_ports(
        ScriptedRunner(
            pm=[
                verdict(
                    "approved",
                    definitions=[{"capability": "reports", "content": REPORTS}],
                    glossary=[{"term": "Report", "definition": "A CSV.", "avoid": "export"}],
                )
            ]
        )
    )
    state = {**TASK, "type": "feature", "sections": {"Product Analyst": "v1"}}
    patch = await validate_prd(state, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert patch["flags"] == {"touches_ui": False, "touches_copy": False}  # PM replaces PA's set
    assert ports.knowledge.definitions == {"reports": REPORTS}
    assert ports.knowledge.terms == {
        "Report": {"definition": "A CSV.", "avoid": "export", "context": ""}
    }
    assert ports.runner.calls[0]["model"] == Configuration.model_fields["verifier_model"].default
    assert "You have 2 left" in ports.runner.calls[0]["system_prompt"]


async def test_validate_return_counts_against_the_pair_and_writes_nothing(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "gap")]))
    state = {**TASK, "type": "feature", "sections": {"Product Analyst": "v1"}}
    patch = await validate_prd(state, cfg, ports)
    assert patch["status"] == "returned"
    assert patch["changes_requested"] == {PAIR_PM_PA.key: 1}
    assert patch["change_history"] == [
        {"pair": PAIR_PM_PA.key, "category": "definition", "reason": "gap"}
    ]
    assert ports.knowledge.definitions == {} and ports.knowledge.terms == {}

    again = apply(state, patch)
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "still")]))
    patch = await validate_prd(again, cfg, ports)
    assert patch["changes_requested"] == {PAIR_PM_PA.key: 2}
    assert "You have 1 left" in ports.runner.calls[0]["system_prompt"]


async def test_validate_escalates_at_the_limit(cfg):
    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "still no metrics")]))
    state = {**TASK, "type": "feature", "changes_requested": {PAIR_PM_PA.key: 2}}
    patch = await validate_prd(state, cfg, ports)
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == f"{PAIR_PM_PA.key} hit 2 returns: still no metrics"
    assert patch["escalation_class"] == "returns"
    assert "changes_requested" not in patch
    assert "You have 0 left" in ports.runner.calls[0]["system_prompt"]


async def test_approving_a_feature_without_definitions_is_a_role_failure(cfg):
    empty = verdict("approved", definitions=[])
    ports = memory_ports(ScriptedRunner(pm=[empty, empty]))
    state = {**TASK, "type": "feature"}
    patch = await validate_prd(state, cfg, ports)
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


async def test_a_discard_records_what_the_release_reaped(cfg):
    reaped = ({"pid": 7, "command": "nohup ./cap.sh", "outcome": "term"},)
    ports = memory_ports(reaper=FakeReaper(reaped))
    state = {**BUG, "status": "discarded", "discard_reason": "weekly by definition"}

    patch = await discard(state, cfg, ports)

    assert patch["reaped_at_cleanup"] == list(reaped) and patch["current_role"] == "PjM"
    assert ports.documents.last["reaped_at_cleanup"] == list(reaped)


async def test_a_discard_that_reaped_nothing_leaves_the_key_out(cfg):
    ports = memory_ports()
    state = {**BUG, "status": "discarded", "discard_reason": "weekly by definition"}

    patch = await discard(state, cfg, ports)

    assert "reaped_at_cleanup" not in patch
    assert "reaped_at_cleanup" not in ports.documents.last


# --- GH-591, part 1: size, risks and the recorded path ---------------------------------------


def sized(size="small", risk=None, type_="feature", confidence="high"):
    """A classification that also declares its size and risks."""
    return RawResult(
        {
            "markdown": "Clear.",
            "type": type_,
            "confidence": confidence,
            "reason": "r",
            "size": size,
            "risk": [] if risk is None else risk,
        },
        0.01,
    )


ISSUE_TASK = {"id": "GH-7", "title": "Export report", "description": "Users want CSV."}


def no_labels(ports):
    return with_labels(ports, ())


def with_labels(ports, labels):
    ports.tracker.states[7] = IssueState("OPEN", "", (), None, labels)
    return ports


async def test_classify_records_a_small_task_without_risk_as_light(cfg):
    ports = no_labels(memory_ports(ScriptedRunner(pjm=[sized()])))
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert patch["initial_path"] == patch["path"] == "light"
    assert (patch["path_cause"], patch["path_label"]) == ("small_no_risk", "none")
    assert "path `light` (small_no_risk)" in patch["sections"]["PjM"]
    assert patch["size"] == "small"


async def test_classify_with_no_size_or_risk_records_full(cfg):
    ports = no_labels(memory_ports(ScriptedRunner(pjm=[classified()])))
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert patch["path"] == "full" and patch["path_cause"] == "missing_size"
    assert "size" not in patch


async def test_classify_lets_the_owner_label_light_win_over_a_risk_and_says_so(cfg):
    ports = with_labels(
        memory_ports(ScriptedRunner(pjm=[sized(risk=["money-rounding"])])), ("path:light",)
    )
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert (patch["path"], patch["path_cause"], patch["path_label"]) == (
        "light",
        "owner_light",
        "light",
    )
    assert "Override `path:light` over risk money-rounding" in patch["sections"]["PjM"]


async def test_classify_with_unreadable_labels_records_full(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[sized()]))
    ports.tracker.state_errors = {7}
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert (patch["path"], patch["path_cause"]) == ("full", "label_unreadable")
    assert patch["path_label"] == "unreadable"


async def test_classify_with_the_stack_raising_records_full_even_for_no_risk(cfg):
    stack = MemoryStack()
    stack.risk_areas_error = "stack.yaml: broken"
    ports = no_labels(memory_ports(ScriptedRunner(pjm=[sized()]), stack=stack))
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert (patch["path"], patch["path_cause"]) == ("full", "stack_unreadable")


async def test_classify_accepts_a_product_risk_area_and_refuses_an_unknown_id(cfg):
    areas = (
        RiskArea("billing", "Invoices", ("app/billing/**",)),
        RiskArea("search-index", "The search index", ()),
    )
    known = no_labels(
        memory_ports(
            ScriptedRunner(pjm=[sized(risk=["billing"])]), stack=MemoryStack(risk_areas=areas)
        )
    )
    patch = await classify(ISSUE_TASK, cfg, known)
    assert (patch["path"], patch["path_cause"]) == ("full", "risk_flag")
    prompt = known.runner.calls[0]["system_prompt"]
    assert "- `billing`: Invoices" in prompt
    assert "- `search-index`: The search index" in prompt
    # Rejected input: an id neither built-in nor declared is never a valid flag.
    unknown = no_labels(memory_ports(ScriptedRunner(pjm=[sized(risk=["billing"])])))
    patch = await classify(ISSUE_TASK, cfg, unknown)
    assert patch["path_cause"] == "unknown_risk"
    assert "Unknown risk ids: `billing`" in patch["sections"]["PjM"]


async def test_classify_stores_the_path_from_the_resolved_type_of_a_low_confidence_guess(cfg):
    ports = no_labels(
        memory_ports(ScriptedRunner(pjm=[sized(type_="platform_enhancement", confidence="low")]))
    )
    patch = await classify(ISSUE_TASK, cfg, ports)
    assert patch["type"] == "feature" and patch["path"] == "light"


async def test_classify_shows_pjm_the_built_in_menu_and_the_size_rule(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[sized()]))
    await classify(TASK, cfg, ports)
    prompt = ports.runner.calls[0]["system_prompt"]
    for identifier in task_path.BUILTIN_RISKS:
        assert f"`{identifier}`" in prompt
    assert "A new screen or a new flow is never `small`" in prompt


async def test_classify_for_a_task_with_no_issue_has_unreadable_labels_and_is_full(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[sized()]))
    patch = await classify(TASK, cfg, ports)
    assert (patch["path"], patch["path_cause"]) == ("full", "label_unreadable")
