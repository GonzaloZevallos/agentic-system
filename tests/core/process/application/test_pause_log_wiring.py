"""Issue #137: every escalation (and `develop.py`'s own `unmergeable`) now composes its
patch through `rules.logged`, so `pause_log` carries what `rules.auto_resumable` reads.
One test per rewired site, reusing each module's own existing fixtures -- this file
proves the wiring, not the use case's own behaviour again."""

import pytest

from agentic.core.process.application.classify import classify
from agentic.core.process.application.develop import develop
from agentic.core.process.application.interview import interview
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.application.review import review
from agentic.core.process.application.validate_prd import validate_prd
from agentic.core.process.domain.roles import INTERVIEW_DEFINITION, PAIR_PM_PA
from agentic.core.process.domain.task import DependencyState
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, answers, classified, memory_ports, questions, reviewed

pytestmark = pytest.mark.anyio

TASK = {"id": "T-1", "title": "Export report", "description": "Users want CSV."}
ASK, ANSWER = "product_analyst_interviewer", "pm_interviewee"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def sub(id_, status="pending", **more):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        **more,
    }


async def test_run_role_logs_each_failure_then_the_escalation(cfg):
    # classify's own PjM run is `run_role`'s own call: two account_limit failures hit
    # `run_role.escalation`'s own `cls="failure"` path.
    ports = memory_ports(
        ScriptedRunner(
            pjm=[
                AttemptFailed("429 once", reason="account_limit"),
                AttemptFailed("429 twice", reason="account_limit"),
            ]
        )
    )
    patch = await classify(TASK, cfg, ports)
    assert patch["escalation_class"] == "failure"
    assert patch["pause_log"] == [
        {"kind": "failure", "value": "account_limit", "section": "PjM"},
        {"kind": "failure", "value": "account_limit", "section": "PjM"},
        {"kind": "escalation", "value": "failure"},
    ]


async def test_classify_logs_its_own_classification_escalation_with_no_failure(cfg):
    ports = memory_ports(ScriptedRunner(pjm=[classified(confidence="low")]))
    patch = await classify(TASK, cfg, ports)
    assert patch["escalation_class"] == "classification"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "classification"}]


async def test_interview_logs_its_own_questions_escalation_with_no_failure(cfg):
    runner = ScriptedRunner(
        **{
            ASK: [questions(1), questions(2), questions(done=True)],
            ANSWER: [answers(1, for_human=["Which currency?"]), answers(2)],
        }
    )
    _, patch = await interview(TASK, INTERVIEW_DEFINITION, cfg, memory_ports(runner))
    assert patch["escalation_class"] == "questions"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "questions"}]


async def test_review_logs_its_own_returns_escalation_with_no_failure(cfg):
    state = {
        **TASK,
        "subtasks": {"api": sub("api", "delivered", branch="b", base="x"), "b": sub("b")},
        "changes_requested": {"Tech Lead→Developer:api": 2},
        "subtask_id": "api",
    }
    patch = await review(
        state, cfg, memory_ports(ScriptedRunner(tech_lead_review=[reviewed("returned", "still")]))
    )
    assert patch["escalation_class"] == "returns"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "returns"}]


async def test_validate_prd_logs_its_own_returns_escalation_with_no_failure(cfg):
    from tests.fakes import verdict

    ports = memory_ports(ScriptedRunner(pm=[verdict("returned", "still no metrics")]))
    state = {**TASK, "type": "feature", "changes_requested": {PAIR_PM_PA.key: 2}}
    patch = await validate_prd(state, cfg, ports)
    assert patch["escalation_class"] == "returns"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "returns"}]


async def test_develop_logs_unmergeable_when_a_marker_survives_delivery(cfg):
    from tests.fakes import delivered

    state = {
        **TASK,
        "subtasks": {
            "domain": sub("domain", "approved", branch="agentic/GH-12.domain"),
            "api": sub("api", depends_on=["domain"]),
        },
        "subtask_id": "api",
    }
    ports = memory_ports(ScriptedRunner(developer=[delivered()]))
    ports.workspace.conflicts.add("agentic/GH-12.domain")
    ports.workspace.conflict_paths["agentic/GH-12.domain"] = ("pyproject.toml",)
    api = ports.workspace.subtask_checkout("T-1", "api")
    ports.workspace.moved.add(api.path)
    ports.workspace.dependency_states["agentic/GH-12.domain"] = [
        DependencyState(False, False, ()),
        DependencyState(True, False, ("pyproject.toml",)),
    ]
    patch = await develop(state, cfg, ports)
    assert patch["escalation_class"] == "unmergeable"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "unmergeable"}]


async def test_develop_logs_unmergeable_at_once_with_nothing_to_resolve(cfg):
    state = {
        **TASK,
        "subtasks": {
            "domain": sub("domain", "approved", branch="agentic/GH-12.domain"),
            "api": sub("api", depends_on=["domain"]),
        },
        "subtask_id": "api",
    }
    ports = memory_ports(ScriptedRunner())
    ports.workspace.refused_branches.add("agentic/GH-12.domain")
    ports.workspace.subtask_checkout("T-1", "api")
    patch = await develop(state, cfg, ports)
    assert patch["escalation_class"] == "unmergeable"
    assert patch["pause_log"] == [{"kind": "escalation", "value": "unmergeable"}]
