"""The owner-decision rules (GH-873): which `Decision:` comments count, where a decision goes,
the patch that consumes it, and the `resume_target` rule. Pure and inert."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import escalate, resume
from agentic.core.process.domain import owner_decision, rules, spec_approval, task
from agentic.core.process.domain.owner_decision import Consumed, newer
from agentic.core.process.domain.restart import settle_open

ROOT = Path(__file__).parent.parent / "agentic"
MODULE = ROOT / "core/process/domain/owner_decision.py"


@dataclass(frozen=True)
class C:
    body: str
    created_at: str


def at(second: int, body: str = "Decision: yes") -> C:
    return C(body, f"2026-11-19T10:00:{second:02d}Z")


def bodies(comments: list[C]) -> list[str]:
    return [c.body for c in comments]


# --- pins and inertness ----------------------------------------------------------------


def test_the_constants_are_pinned_to_the_other_modules():
    assert owner_decision.DECISION == spec_approval.DECISION
    assert owner_decision.FLOOR_PREFIXES == (escalate.ESCALATED, resume.RESUMED)


def _imported(path: Path) -> list[tuple[int, str | None, list[str]]]:
    """Each import of the file as (relative level, module, names)."""
    out: list[tuple[int, str | None, list[str]]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.extend((0, a.name, []) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append((node.level, node.module, [a.name for a in node.names]))
    return out


def test_the_module_imports_only_its_three_siblings_and_the_standard_library():
    stdlib = {"__future__", "collections", "dataclasses", "datetime", "typing"}
    siblings: set[str] = set()
    for level, module, names in _imported(MODULE):
        if level == 0:
            assert (module or "").split(".")[0] in stdlib, module
        elif module is None:
            siblings.update(names)
        else:
            siblings.add(module)
    assert siblings == {"restart", "rules", "task"}


def test_nothing_under_agentic_imports_the_module():
    offenders = [
        str(path)
        for path in ROOT.rglob("*.py")
        if path != MODULE
        for _, module, names in _imported(path)
        if "owner_decision" in (module or "").split(".") or "owner_decision" in names
    ]
    assert offenders == []


# --- newer -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "counts"),
    [
        ("Decision: ship it", True),
        ("  \nDecision: ship it\n", True),
        ("  Decision: indented", False),
        ("Please decide. Decision: no", False),
        ("decision: lower", False),
    ],
)
def test_only_the_column_zero_prefix_counts(body, counts):
    assert bool(newer([C(body, "2026-11-19T10:00:00Z")], None, ())) is counts


def test_seen_is_the_anchor_and_a_tie_is_not_newer():
    comments = [
        at(1, "Decision: a"),
        at(5, "Decision: b"),
        at(5, "Decision: c"),
        at(9, "Decision: d"),
    ]
    assert bodies(newer(comments, "2026-11-19T10:00:05+00:00", ())) == ["Decision: d"]
    assert bodies(newer(comments, "2026-11-19T10:00:04Z", ())) == [
        "Decision: b",
        "Decision: c",
        "Decision: d",
    ]


def test_the_legacy_floor_is_the_newest_system_comment_whatever_the_list_order():
    comments = [
        at(9, "Escalated: x"),
        at(2, "Resumed: y"),
        at(5, "Decision: a"),
        at(12, "Decision: b"),
    ]
    assert bodies(newer(comments, None)) == ["Decision: b"]


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["Escalated: q", "Decision: a", "Resumed: r"], []),
        (["Escalated: q", "Resumed: r", "Decision: a"], ["Decision: a"]),
        (["Decision: a", "Resumed by the poller: n"], ["Decision: a"]),
        (["Decision: a", "Decision: b"], ["Decision: a", "Decision: b"]),
    ],
)
def test_the_floor_replays_and_ignores_the_poller_notes(order, expected):
    comments = [at(i, body) for i, body in enumerate(order, 1)]
    assert bodies(newer(comments, None)) == expected


def test_same_second_as_the_floor_is_lost_and_ties_keep_list_order():
    comments = [
        at(5, "Resumed: r"),
        at(5, "Decision: a"),
        at(7, "Decision: b"),
        at(7, "Decision: c"),
    ]
    assert bodies(newer(comments, None)) == ["Decision: b", "Decision: c"]


@pytest.mark.parametrize(
    ("created_at", "seen", "expected"),
    [
        ("not a date", None, []),
        ("2026-11-19T10:00:00", None, []),  # naive
        ("", None, []),
        ("2026-11-19T10:00:00Z", None, ["Decision: x"]),
        ("2026-11-19T10:00:00+00:00", "garbage", ["Decision: x"]),
        ("2026-11-19T10:00:00Z", "2026-11-19T10:00:00", ["Decision: x"]),  # naive seen is absent
    ],
)
def test_unparseable_instants(created_at, seen, expected):
    assert bodies(newer([C("Decision: x", created_at)], seen, ())) == expected


def test_an_unparseable_seen_falls_back_to_the_floor():
    comments = [at(5, "Resumed: r"), at(3, "Decision: old"), at(8, "Decision: new")]
    assert bodies(newer(comments, "garbage")) == ["Decision: new"]


@pytest.mark.parametrize(
    ("state", "due"),
    [
        ({}, True),
        ({"owner_decision_seen": "2026-11-19T10:00:05Z"}, False),
        ({"owner_decision_seen": "2026-11-19T10:00:04Z"}, True),
    ],
)
def test_reseed_due(state, due):
    assert owner_decision.reseed_due(state, [at(5)]) is due
    assert owner_decision.reseed_due(state, [at(5, "Resumed: r"), at(4)]) is False


# --- decision_route --------------------------------------------------------------------


def stopped_at_verify() -> dict[str, Any]:
    """#726: stopped by hand at verify, the full path, a PRD, no pull request."""
    return {
        "status": "ready_for_review",
        "sections": {"Product Analyst": "prd", "PM": "ok"},
        "subtasks": {
            "api": {"status": "approved", "branch": "agentic/GH-1-api"},
            "integration": {"status": "approved", "branch": "agentic/GH-1"},
        },
    }


ONE_UNIT = {"subtasks": {"api": {"status": "approved", "branch": "agentic/GH-1"}}}
ASKED = {"escalated": "why", "escalation_class": "questions"}
SPEC = {"spec": {"path": "x"}}


@pytest.mark.parametrize(
    ("state", "route", "cause"),
    [
        (stopped_at_verify(), "pm", None),
        (ONE_UNIT, "developer", None),
        ({"subtasks": {"api": {"branch": "other"}}}, "escalate", "owner_decision"),
        (
            {"subtasks": {"a": {"branch": "agentic/GH-1"}, "b": {"branch": "agentic/GH-1"}}},
            "escalate",
            "owner_decision",
        ),
        ({}, "escalate", "owner_decision"),
        ({**stopped_at_verify(), "pull_request": "u"}, "escalate", "owner_decision"),
        ({**ONE_UNIT, "questions_for_human": ["q"]}, "escalate", "owner_decision"),
        ({**ONE_UNIT, **ASKED}, "escalate", "owner_decision"),
        ({**ONE_UNIT, **SPEC}, "escalate", "spec_owned"),
        ({**stopped_at_verify(), **SPEC}, "escalate", "spec_owned"),
        ({**stopped_at_verify(), **SPEC, **ASKED, "pull_request": "u"}, "escalate", "spec_owned"),
    ],
)
def test_decision_route(state, route, cause):
    verdict = owner_decision.decision_route(state, "GH-1")
    assert (verdict.route, verdict.cause) == (route, cause)


def test_a_verdict_is_frozen():
    verdict = owner_decision.decision_route(ONE_UNIT, "GH-1")
    with pytest.raises(FrozenInstanceError):
        verdict.route = "pm"


# --- consumed_patch --------------------------------------------------------------------

NEWEST = "2026-11-19T10:00:30Z"


def finding(subtask: str, location: str, version: int = 1) -> Any:
    return {
        "subtask": subtask,
        "version": version,
        "location": location,
        "claim": "c",
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
    }


def pm_state() -> dict[str, Any]:
    return {
        **stopped_at_verify(),
        "owner_decision_route": "pm",
        "owner_decision_seen": "2026-11-19T10:00:10Z",
        "task_head": "abc",
        "restart_pending": {"subtask": "api", "archived_head": "x"},
        "plan_due": True,
        "restarts": {"k": 1},
        "changes_requested": {"k": 2},
        "full_checks": {"version": 3, "head": "abc", "passed": False},
        "findings": [
            finding("api", "a.py:1"),
            finding("integration", "b.py:1"),
            finding("PM: final validation", "docs/prd.md"),
            finding("docs", "full_checks:tests", 2),
        ],
    }


def test_none_consumes_nothing():
    assert owner_decision.consumed_patch(pm_state(), None) == {}


def test_a_developer_decision_moves_seen_and_clears_the_route_only():
    state = {**ONE_UNIT, "owner_decision_route": "developer", "full_checks": {"version": 1}}
    patch = owner_decision.consumed_patch(state, Consumed("developer", NEWEST))
    assert patch == {"owner_decision_seen": NEWEST, "owner_decision_route": None}
    assert task.apply(state, patch).get("owner_decision_route") is None


def test_the_patch_writes_no_route_key_when_there_was_none():
    assert owner_decision.consumed_patch({}, Consumed("developer", NEWEST)) == {
        "owner_decision_seen": NEWEST
    }


def test_a_pm_decision_resets_the_plan_and_keeps_the_counters():
    state = pm_state()
    patch = owner_decision.consumed_patch(state, Consumed("pm", NEWEST))
    assert patch["subtasks"] == task.subtasks_reset()
    assert patch["status"] == "returned"
    assert patch["plan_due"] is False
    assert (patch["task_head"], patch["restart_pending"], patch["pending_close_docs"]) == (
        None,
        None,
        None,
    )
    assert patch["restart_settled"] == settle_open(state, ["api", "integration"], "owner_decision")
    assert {"full_checks", "restarts", "changes_requested"}.isdisjoint(patch)
    after = task.apply(state, patch)
    assert after["full_checks"] == state["full_checks"]
    assert after["restarts"] == {"k": 1}
    assert after["changes_requested"] == {"k": 2}
    assert not after["subtasks"]


@pytest.mark.parametrize("route", ["developer", "pm"])
def test_consuming_twice_is_idempotent_and_seen_never_moves_back(route):
    state = pm_state()
    once = task.apply(state, owner_decision.consumed_patch(state, Consumed(route, NEWEST)))
    twice = task.apply(once, owner_decision.consumed_patch(once, Consumed(route, NEWEST)))
    assert twice.get("owner_decision_seen") == NEWEST
    earlier = owner_decision.consumed_patch(once, Consumed(route, "2026-11-19T10:00:01Z"))
    assert earlier["owner_decision_seen"] == NEWEST
    # what was consumed is no longer newer
    assert owner_decision.newer([at(20), at(30)], twice["owner_decision_seen"], ()) == []


@pytest.mark.parametrize("current", [None, "garbage"])
def test_an_unreadable_seen_is_replaced(current):
    patch = owner_decision.consumed_patch({"owner_decision_seen": current}, Consumed("pm", NEWEST))
    assert patch["owner_decision_seen"] == NEWEST


def test_open_rows_of_other_names_stay_open_and_a_recreated_integration_starts_clean():
    state = pm_state()
    after = task.apply(state, owner_decision.consumed_patch(state, Consumed("pm", NEWEST)))
    rows = {(r["subtask"], r["location"]): r["status"] for r in rules.findings_rows(after)}
    assert rows[("PM: final validation", "docs/prd.md")] == "open"
    assert rows[("docs", "full_checks:tests")] == "open"  # `docs` is no current unit
    assert rows[("api", "a.py:1")] == "dismissed"
    assert rows[("integration", "b.py:1")] == "dismissed"
    # integration is re-created and reviewed again: its new finding is above the bar.
    section = rules.review_section("integration")
    fresh = {
        **after,
        "subtasks": {"integration": {"status": "returned", "branch": "agentic/GH-1"}},
        "runs": [{"role": "Tech Lead", "section": section}],
    }
    version = rules.next_version(fresh, section)
    fresh = {**fresh, "findings": [*fresh["findings"], finding("integration", "c.py:9", version)]}
    assert {r["location"]: r["status"] for r in rules.findings_rows(fresh)}["c.py:9"] == "open"


# --- resume_target ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "target"),
    [
        ({"owner_decision_route": "pm", "status": "definition_ready"}, "define"),
        ({"owner_decision_route": "pm", "status": "ready_for_review", "plan_due": True}, "define"),
        ({"owner_decision_route": "pm", "status": "open", "type": "feature"}, "define"),
        ({**ONE_UNIT, "owner_decision_route": "developer"}, "integrate"),
        ({**ONE_UNIT, "owner_decision_route": "developer", "status": "split"}, "integrate"),
        ({**ONE_UNIT, "owner_decision_route": "developer", "plan_due": True}, "integrate"),
        ({"owner_decision_route": "pm", "escalated": "why"}, None),
        ({**ONE_UNIT, "owner_decision_route": "developer", "escalated": "why"}, None),
        ({**ONE_UNIT, "owner_decision_route": "pm", "pull_request": "u"}, None),
        ({"status": "definition_ready"}, "plan"),
        ({"status": "split"}, None),
        ({"plan_due": True, "status": "defined"}, "plan"),
    ],
)
def test_resume_target_reads_the_route(state, target):
    assert rules.resume_target(state) == target


def test_a_route_does_not_change_what_a_pull_request_owes():
    head = "a" * 40
    owed = {
        "pull_request": "u",
        "task_head": head,
        "full_checks": {"version": 1, "head": head, "passed": True},
        "publish_owed": head,
        "subtasks": {"integration": {"status": "approved", "reviewed_head": head}},
        "head_reviewed": head,
    }
    plain = rules.resume_target(owed)
    assert rules.resume_target({**owed, "owner_decision_route": "pm"}) == plain
    assert rules.resume_target({**owed, "owner_decision_route": "developer"}) == plain


def test_a_726_state_after_the_patch_resumes_by_the_plain_status_fork():
    state = {**pm_state(), "type": "feature", "status": "definition_ready"}
    assert rules.resume_target(state) == "define"
    after = task.apply(state, owner_decision.consumed_patch(state, Consumed("pm", NEWEST)))
    assert after.get("owner_decision_route") is None
    assert rules.resume_target(after) == "define"
