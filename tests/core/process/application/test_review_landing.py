"""`review()` and landing evidence (GH-441): a finding that asks for evidence only the pull
request's CI can give is an observation the delivery verifies at landing, never a return or an
escalation; a review with no `landing_checks` produces the patch it always did; and the review
prompts say so. Seams: `application.review.review` through its fakes and the prompt render."""

import re
from pathlib import Path

import pytest

from agentic.core.process.application import preflight
from agentic.core.process.application.review import review
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import roles, rules
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    finding,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

PROMPTS = Path(preflight.__file__).resolve().parents[1] / "domain" / "prompts"
RULES = Path(__file__).resolve().parents[4] / "docs" / "decisiones-del-dueño.md"
HEAD = "a" * 40


def unit(name):
    return {
        "id": name,
        "title": name,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        "branch": f"agentic/GH-441.{name}",
        "base": "base1",
    }


STATE = {
    "id": "GH-441",
    "title": "T",
    "description": "d",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nx"},
    "subtask_id": "api",
    "subtasks": {"api": unit("api"), "web": unit("web")},
}


async def run(result, *, ci=()):
    runner = ScriptedRunner(tech_lead_review=[result])
    ports = memory_ports(runner, stack=MemoryStack(ci=ci))
    patch = await review(STATE, Configuration(product_dir=MemoryWorkspace.ROOT), ports)
    return patch, runner


async def test_a_blocking_landing_request_of_the_plan_scope_approves_without_a_return():
    asking = {**finding(scope="plan"), "landing_checks": ["web", "e2e"]}
    patch, runner = await run(reviewed(findings=[asking]), ci=("web", "e2e"))

    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "escalation" not in patch and patch.get("status") != "waiting_on_human"
    assert "changes_requested" not in patch and "change_history" not in patch
    assert patch["failures"] == [] and len(runner.calls) == 1
    [record] = patch["findings"]
    assert record["severity"] == "observation" and record["reported_severity"] == "blocking"
    assert record["landing_checks"] == ["web", "e2e"] and record["status"] == "open"
    assert (
        "blocking lowered to observation: verified at landing"
        in patch["sections"]["Tech Lead: review api"]
    )
    # What the next stages read: nothing is open that could reopen a unit or refuse a merge.
    state = {
        **STATE,
        "findings": patch["findings"],
        "subtasks": {**STATE["subtasks"], "api": patch["subtasks"]["api"]},
    }
    assert landing_rules.important_owners(state) == ()
    partition = rules.important_partition(state)
    assert not (partition.owned or partition.plan or partition.outside)
    assert not any(
        row["severity"] in ("blocking", "important") for row in rules.open_findings(state)
    )
    facts = rules.MergeFacts(
        task_id="GH-441",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=None,
    )
    assert rules.auto_merge_decision(facts).reason not in (
        "open_findings",
        "open_findings_blocking",
    )


async def test_the_reviewer_is_told_the_landing_gate():
    _, runner = await run(reviewed(), ci=("web", "e2e"))
    assert "`web`, `e2e`" in runner.calls[0]["system_prompt"]
    _, runner = await run(reviewed())
    assert rules.NO_LANDING_GATE in runner.calls[0]["system_prompt"]


async def test_a_prose_only_block_without_the_field_still_returns():
    # The decision reads `landing_checks` alone (invariant 2): this path is today's.
    prose = {**finding(), "claim": "a human must open the PR so CI runs"}
    patch, _ = await run(reviewed(findings=[prose]))
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert "landing_checks" not in patch["findings"][0]


async def test_a_review_with_no_landing_checks_produces_the_patch_it_always_did():
    """The expected values were captured from `review()` before this change."""
    unbacked = {**finding(), "evidence": ""}
    cases = {
        "approved": reviewed("approved"),
        "returned": reviewed("returned", category="spec-behaviour"),
        "no_evidence": reviewed(
            findings=[
                unbacked,
                finding(severity="important", claim="i"),
                finding(severity="observation", claim="o"),
            ]
        ),
        "plan_scope": reviewed(findings=[finding(scope="plan")]),
    }
    for name, result in cases.items():
        patch, _ = await run(result)
        patch.pop("runs")
        patch.pop("prompts")
        assert patch == GOLDEN[name], name


def paragraph(slug):
    text = (PROMPTS / f"{slug}.md").read_text(encoding="utf-8")
    [found] = [p for p in text.split("\n\n") if p.startswith("The pull request does not exist")]
    return found


def test_both_review_prompts_carry_the_same_landing_paragraph():
    full, light = paragraph("tech_lead_review"), paragraph("tech_lead_review_light")
    assert full == light
    assert "{{landing_gate}}" in full and "`landing_checks`" in full
    assert "D-d506fe31" in full and "D-7e11ef60" in full
    assert "asks a human to open the pull request" in full


def test_every_owner_rule_a_prompt_cites_exists():
    known = set(re.findall(r"^### (D-[0-9a-f]{8})\b", RULES.read_text(encoding="utf-8"), re.M))
    cited = {
        (path.name, rule)
        for path in PROMPTS.glob("*.md")
        for rule in re.findall(r"\bD-[0-9a-f]{8}\b", path.read_text(encoding="utf-8"))
    }
    assert cited, "the review prompts cite the owner's rules"
    assert [pair for pair in cited if pair[1] not in known] == []
    # The check rejects what it must: an id the file does not hold is not in `known`.
    assert "D-00000000" not in known


@pytest.mark.parametrize("slug", ["tech_lead_review", "tech_lead_review_light"])
def test_the_review_prompts_render_through_the_preflight_stand_ins(slug):
    role = next(r for r in roles.ROLES if r.slug == slug)
    raw = (PROMPTS / f"{slug}.md").read_text(encoding="utf-8")
    prompt = roles.render(raw, PROMPTS / f"{slug}.md", **preflight.PLACEHOLDERS, max_changes=2)
    assert "{{" not in prompt.text and rules.NO_LANDING_GATE in prompt.text
    assert role.slug == slug
    assert preflight.PLACEHOLDERS["landing_gate"] == rules.NO_LANDING_GATE
    # A prompt that names a placeholder with no stand-in must fail to render.
    without = {k: v for k, v in preflight.PLACEHOLDERS.items() if k != "landing_gate"}
    with pytest.raises(ValueError, match="landing_gate"):
        roles.render(raw, Path(slug), **without, max_changes=2)


GOLDEN = {
    "approved": {
        "approval_settled": [],
        "cost_usd": 0.2,
        "current_role": "Tech Lead",
        "experiment": {},
        "failures": [],
        "findings": [],
        "review_kind": "first",
        "sections": {
            "Tech Lead: review api": "### Verdict\n\n**approved**: fine\n\n### Findings\n\nNone."
        },
        "settled_findings": [],
        "subtasks": {
            "api": {
                "base": "base1",
                "branch": "agentic/GH-441.api",
                "brief": "b",
                "depends_on": [],
                "id": "api",
                "reviewed_head": "base",
                "seams": ["s"],
                "status": "approved",
                "title": "api",
            }
        },
    },
    "no_evidence": {
        "approval_settled": [],
        "cost_usd": 0.2,
        "current_role": "Tech Lead",
        "experiment": {},
        "failures": [],
        "findings": [
            {
                "category": "spec-behaviour",
                "claim": "wrong",
                "location": "x.py:1",
                "reported_severity": "blocking",
                "scope": "subtask",
                "severity": "observation",
                "status": "dismissed",
                "subtask": "api",
                "version": 1,
            },
            {
                "category": "spec-behaviour",
                "claim": "i",
                "location": "x.py:1",
                "scope": "subtask",
                "severity": "important",
                "status": "open",
                "subtask": "api",
                "version": 1,
            },
            {
                "category": "spec-behaviour",
                "claim": "o",
                "location": "x.py:1",
                "scope": "subtask",
                "severity": "observation",
                "status": "open",
                "subtask": "api",
                "version": 1,
            },
        ],
        "review_kind": "first",
        "sections": {
            "Tech Lead: review api": "### Verdict\n"
            "\n"
            "**approved**: fine\n"
            "\n"
            "Lowered by the system:\n"
            "\n"
            "- `x.py:1` blocking lowered to "
            "observation: no evidence\n"
            "\n"
            "### Findings\n"
            "\n"
            "None."
        },
        "settled_findings": [],
        "subtasks": {
            "api": {
                "base": "base1",
                "branch": "agentic/GH-441.api",
                "brief": "b",
                "depends_on": [],
                "id": "api",
                "reviewed_head": "base",
                "seams": ["s"],
                "status": "approved",
                "title": "api",
            }
        },
    },
    "plan_scope": {
        "approval_settled": [],
        "cost_usd": 0.2,
        "current_role": "Tech Lead",
        "experiment": {},
        "failures": [],
        "findings": [
            {
                "category": "spec-behaviour",
                "claim": "wrong",
                "location": "x.py:1",
                "reported_severity": "blocking",
                "scope": "plan",
                "severity": "important",
                "status": "open",
                "subtask": "api",
                "version": 1,
            }
        ],
        "review_kind": "first",
        "sections": {
            "Tech Lead: review api": "### Verdict\n"
            "\n"
            "**approved**: fine\n"
            "\n"
            "Lowered by the system:\n"
            "\n"
            "- `x.py:1` blocking lowered to "
            "important: scope plan, not fixable in "
            "this subtask\n"
            "\n"
            "### Findings\n"
            "\n"
            "None."
        },
        "settled_findings": [],
        "subtasks": {
            "api": {
                "base": "base1",
                "branch": "agentic/GH-441.api",
                "brief": "b",
                "depends_on": [],
                "id": "api",
                "reviewed_head": "base",
                "seams": ["s"],
                "status": "approved",
                "title": "api",
            }
        },
    },
    "returned": {
        "change_history": [
            {"category": "spec-behaviour", "pair": "Tech Lead→Developer:api", "reason": "fine"}
        ],
        "changes_requested": {"Tech Lead→Developer:api": 1},
        "cost_usd": 0.2,
        "current_role": "Tech Lead",
        "experiment": {},
        "failures": [],
        "findings": [
            {
                "category": "spec-behaviour",
                "claim": "fine",
                "location": "x.py:1",
                "scope": "subtask",
                "severity": "blocking",
                "status": "open",
                "subtask": "api",
                "version": 1,
            }
        ],
        "review_kind": "first",
        "sections": {
            "Tech Lead: review api": "### Verdict\n"
            "\n"
            "**returned** (`spec-behaviour`): fine\n"
            "\n"
            "### Findings\n"
            "\n"
            "None."
        },
        "settled_findings": [],
        "subtasks": {
            "api": {
                "base": "base1",
                "branch": "agentic/GH-441.api",
                "brief": "b",
                "change_category": "spec-behaviour",
                "depends_on": [],
                "id": "api",
                "reason": "fine",
                "reviewed_head": "base",
                "seams": ["s"],
                "status": "returned",
                "title": "api",
            }
        },
    },
}
