"""Every `TaskState` key survives `document.render` and `document.parse`, or is excluded for a
closed reason (GH-867). `render` writes an explicit allow-list, so a key added to `TaskState`
with no line there vanishes the next time the document is saved and loaded; a merge fact that
reads it then decides on a state the task never had. This test is what fails first.

A key is either in `FIXTURES` (one hand-typed, non-empty value: the round trip must give back an
equal value, never just the key) or in `EXCLUDED` with a `Reason` and a reference the test
resolves, so a reason that stops being true fails here too.

Not covered: keys `render` writes that `TaskState` does not declare (the reverse direction)."""

import ast
import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import pytest

from agentic.core.process.domain import document, drawing, size_budget, spec_delivery, task

ROOT = Path(__file__).resolve().parents[4]
KEYS = frozenset(task.TaskState.__required_keys__ | task.TaskState.__optional_keys__)

NOW = "2026-11-18T10:00:00Z"
SHA = "a" * 40
OTHER_SHA = "b" * 40
PR = "https://github.com/acme/shop/pull/7"

Reason = Literal["branch_local", "transient", "recorded_elsewhere"]

# The keys `document.render` never writes, each with why that is right and the test or the
# production symbol that makes the reason true: a `path::name` is a top-level test (or function)
# of that file, a dotted `module.name` a symbol the import resolves.
EXCLUDED: dict[str, tuple[Reason, str]] = {
    "subtask_id": ("branch_local", "agentic.core.process.domain.task._BRANCH_LOCAL"),
    "review_kind": ("branch_local", "agentic.core.process.domain.task._BRANCH_LOCAL"),
    "presubmit_checks": ("branch_local", "agentic.core.process.domain.task._BRANCH_LOCAL"),
    "plan_promote_request": (
        "transient",
        "tests/core/process/application/test_plan_promote.py"
        "::test_the_plan_node_applies_the_request_after_the_model_ran",
    ),
    "drawing_raised": (
        "transient",
        "tests/core/process/domain/test_drawing_answer.py"
        "::test_fold_raised_escalates_a_raise_and_keeps_a_siblings_deltas",
    ),
    "interview_stats": (
        "transient",
        "tests/core/process/domain/test_document.py::test_interview_stats_never_reaches_the_document",
    ),
    "escalation_cause": (
        "recorded_elsewhere",
        "tests/core/process/domain/test_restart.py"
        "::test_escalation_writes_the_cause_with_the_class_and_the_log",
    ),
}

# One value of each excluded key, to show `render` still writes nothing for it.
EXCLUDED_SAMPLES: dict[str, object] = {
    "subtask_id": "api",
    "review_kind": "first",
    "presubmit_checks": [[{"name": "lint", "command": "ruff", "passed": True}]],
    "plan_promote_request": {"kind": "role", "from_role": "Tech Lead", "text": "bigger"},
    "drawing_raised": [
        drawing.Departure(
            drawing="home",
            element="cta",
            shows="green",
            would_ship="blue",
            recommendation="green",
            reason="token",
            raised_by="UI Lead",
        )
    ],
    "interview_stats": {"interview": "solution", "rounds": 2},
    "escalation_cause": "ci_run_unfinished",
}

_DEPARTURE = {
    "number": 1,
    "key": "home:cta",
    "drawing": "home",
    "element": "cta",
    "shows": "a green button",
    "would_ship": "a blue button",
    "recommendation": "make it green",
    "reason": "the theme token is blue",
    "raised_by": "UI Lead",
    "raised_at": NOW,
    "answer": {"verdict": "accept", "by": "comment", "at": NOW},
    "fix_passes": 1,
}
_SPEC_PATCH = spec_delivery.spec_state_patch(
    {
        "path": "docs/specs/checkout.md",
        "slice": "first-slice",
        "commit": SHA,
        "sha256": "c" * 64,
        "criteria": ["Pay shows a receipt"],
        "screens": [{"name": "Checkout", "copy": ["Pay now\nand confirm", "Back to cart"]}],
        "risks": ["double charge"],
        "touches_ui": False,
        "touches_copy": False,
    }
)
assert not isinstance(_SPEC_PATCH, spec_delivery.SpecRefusal)

FIXTURES: dict[str, object] = {
    "id": "GH-1",
    "title": "Export the report",
    "description": "Export it as CSV.\n\nWith headers.",
    "decided": True,
    "landing_restarts": 2,
    "landing_cost_carried": 1.5,
    "sections": {"Tech Lead": "### Plan\n\nHex.", "Tech Lead (v1)": "### Plan\n\nOld."},
    "tracker_failures": [{"target": "comment", "cls": "timed_out", "at": NOW}],
    "assignment": {"experiment": "exp-1", "arm": "single", "switches": {"review_mode": "single"}},
    "evaluation": {"issue": 5, "base": SHA, "ci": "local"},
    "type": "feature",
    "status": "planned",
    "current_role": "Tech Lead",
    "flags": {"touches_ui": True},
    "escalated": "Over the cap.",
    "escalation_class": "cap",
    "changes_requested": {"PM→Product Analyst": 1},
    "change_history": [
        {"pair": "PM→Product Analyst", "category": "spec-scope", "reason": "r", "attempt": 1}
    ],
    "failures": [
        {"role": "Developer", "attempt": 1, "error": "boom", "cost_usd": 0.5, "reason": "network"}
    ],
    "runs": [
        {
            "role": "Developer",
            "section": "Developer",
            "version": 1,
            "session_id": "s-1",
            "model": "sonnet",
            "cost_usd": 0.1234,
            "cost_by_model": {"sonnet": 0.1234},
            "turns": 3,
            "subagent_cost_usd": 0.02,
        }
    ],
    "prompts": {"developer": "abc123"},
    "playbooks": {"developer": "def456"},
    "playbooks_changed": {"tech_lead": "0a1b2c"},
    "cost_usd": 1.2346,
    "estimate": {"cost_usd": 20.0, "files": 3, "declared_usd": 20.0},
    "estimate_revisions": [{"cost_usd": 30.0, "source": "human"}],
    "required_paths_revisions": [
        {"revises": ["a"], "paths": ["b"], "decision": 1, "reason": "r", "at": NOW}
    ],
    "proposed_estimate": 40.0,
    "parts": [{"title": "Part 1", "body": "b", "depends_on": [], "depth": 1, "kind": "change"}],
    "tracker_checklist": ["Create the label"],
    "tracker_checklist_posted": {"at": NOW, "hash": "abc"},
    "tracker_items": [
        {"kind": "human", "question": "Which one?", "recommendation": "The first."},
        {"kind": "comment", "text": "Heads up."},
        {"kind": "open_issue", "title": "Follow up", "body": "Later."},
    ],
    "tracker_actions_done": {"abc": {"at": NOW, "kind": "comment", "url": PR}},
    "tracker_human": ["abc"],
    "owner_decision_unbuilt": NOW,
    "owner_decision_seen": NOW,
    "owner_decision_route": "developer",
    "spec_cause": "contradiction",
    "spec_refusal": {"cause": "spec_invalid", "detail": ["slice"]},
    "spec": _SPEC_PATCH["spec"],
    "spec_draft": {"outcome": "written", "path": "docs/specs/draft.md", "sha256": "d" * 64},
    "spec_delivery": True,
    "assumptions": [{"role": "Developer", "text": "The report keeps its column order."}],
    "split_issues": {"1": PR},
    "carve_out": {
        "data": {"files": 3},
        "fit": {"ok": True},
        "at": NOW,
        "issue_url": PR,
        "outcome": "applied",
    },
    "split_repointed_at": NOW,
    "split_closed_at": NOW,
    "split_abandoned_at": "2026-11-19T10:00:00Z",
    "fit_waived": True,
    "fit_question": True,
    "plan_due": True,
    "actual_files": 4,
    "planned_size": size_budget.PlanSize(hand_files=2, hand_lines=40, test_lines=80),
    "actual_size": size_budget.ActualSize(
        hand_files=3, hand_lines=50, test_lines=90, generated_files=1
    ),
    "subtasks": {
        "api": {
            "id": "api",
            "title": "API",
            "brief": "Build it.",
            "seams": ["x.y"],
            "depends_on": ["db"],
            "status": "approved",
            "branch": "agentic/GH-1/api",
            "change_category": "standard-other",
        }
    },
    "invariants": ["Nothing is written twice."],
    "performance": [{"name": "export", "budget_s": 2.0}],
    "ci_required": ["tests"],
    "required_paths": ["docs/"],
    "api_break": "The export header changed.",
    "integration_brief": "Wire both parts.",
    "findings": [
        {
            "subtask": "api",
            "version": 1,
            "location": "a.py:1",
            "claim": "It breaks.",
            "severity": "blocking",
            "scope": "subtask",
            "status": "open",
        }
    ],
    "settled_findings": ["a.py:1"],
    "approval_settled": [{"subtask": "api", "location": "a.py:1", "claim": "c", "version": 1}],
    "restart_settled": [
        {"subtask": "api", "version": 1, "outcome": "discarded", "reason": "restarted"}
    ],
    "restarts": {"PM→Product Analyst": 1},
    "restart_pending": {"subtask": "api", "archived_head": SHA},
    "session_floor": {"Developer": 2},
    "plan_settled": [{"location": "a.py:1", "claim": "c", "version": 1, "by": "approved"}],
    "task_head": SHA,
    "full_checks": {"version": 1, "head": SHA, "passed": True, "duration_s": 12.5},
    "merge_ci": [{"head": SHA, "master": OTHER_SHA, "mode": "push", "outcome": "green"}],
    "stack_parent": {"issue": 7, "task_id": "GH-7", "head": SHA, "started_at": NOW},
    "stack_unfetchable": {"head": SHA, "since": "2026-11-18T10:00:00+00:00"},
    "merge_conflict": {"head": SHA, "master": OTHER_SHA, "at": NOW},
    "merge_refusal": {
        "reason": "ci_untrusted_no_dispatch",
        "head": SHA,
        "master": OTHER_SHA,
        "at": NOW,
    },
    "worktree_release_lapse": {"at": NOW, "cause": "git_failed"},
    "full_checks_healed": OTHER_SHA,
    "branch": "agentic/GH-1",
    "pull_request": PR,
    "replaced_remote_tip": OTHER_SHA,
    "publish_owed": SHA,
    "close_docs": {"parent": "GH-1", "head": SHA, "paths": ["docs/a.md"]},
    "pending_close_docs": {"parent": "GH-1", "definitions": [{"path": "docs/a.md"}]},
    "owner_rules_seen": SHA,
    "owner_rules_elsewhere": [{"rule": "Never do x.", "cls": "system"}],
    "pushed_head": SHA,
    "pushed_at": NOW,
    "ci_speculation": {"head": SHA, "pushed_at": NOW},
    "ci_cancel_rerun": {"head": SHA, "run_id": "9", "rerun_at": NOW},
    "ci_ghost_dispatch": {
        "head": SHA,
        "ghost_run_ids": ["9"],
        "dispatched_at": NOW,
        "escalated": False,
    },
    "reaped_at_cleanup": [{"pid": 12, "command": "node", "outcome": "term"}],
    "criteria_over_cap": ["docs/a.md has 301 lines"],
    "landing": [{"head": SHA, "step": "merge", "at": NOW}],
    "adr_renumbered": [{"from": "0001", "to": "0002", "carried": True}],
    "auto_merge_guarded": ["docs/"],
    "followups_filed": True,
    "landing_queued_at": NOW,
    "head_since_at": NOW,
    "head_since_head": SHA,
    "head_turn_at": NOW,
    "head_charged_s": 120.0,
    "head_seen_at": NOW,
    "head_yielded_at": NOW,
    "head_yields": [{"at": NOW, "head": SHA, "reason": "budget_time"}],
    "landed_slices": [{"units": ["api"], "pull_request": PR, "merged_sha": SHA, "at": NOW}],
    "closed_by_slices": [PR, "https://github.com/acme/shop/pull/8"],
    "questions_for_human": ["Which format?"],
    "proposed_invariants": ["Nothing is written twice."],
    "experiment": {"review_mode": "single"},
    "plan_route": {"pre_estimate_usd": 5.0, "files": 2, "threshold_usd": 10.0, "route": "critic"},
    "initial_path": "light",
    "path": "full",
    "path_cause": "risk_flag",
    "path_label": "none",
    "promotion": {"from_role": "Tech Lead", "kind": "role", "text": "bigger", "at": NOW},
    "path_routed": True,
    "size": "normal",
    "lead_time": {"labeled_at": NOW, "merged_at": "2026-11-19T10:00:00Z"},
    "discard_reason": "Not a bug.",
    "finished_at": NOW,
    "pause_log": [{"kind": "resumed", "at": NOW}],
    "design_items": [{"role_slug": "ui_lead", "kind": "observation", "step": "s", "text": "t"}],
    "flakes": [{"check": "tests", "head": SHA, "gate": "merge", "tests": ["t::a"], "at": NOW}],
    "restorations": [
        {"archive": "a", "head": SHA, "upstream": OTHER_SHA, "at": NOW, "landing_cut": 1}
    ],
    "shadow_reviews": [
        {
            "subtask": "api",
            "head": SHA,
            "status": "ran",
            "cause": None,
            "findings": [],
            "cost_usd": 0.1234,
            "changed_lines": 10,
        }
    ],
    "shadow_wait_s": 1.2345,
    "review_scopes": [
        {"unit": "api", "head": SHA, "base": None, "scope": "full", "cause": "first", "at": NOW}
    ],
    "stack_restacks": [
        {"at": NOW, "upstream": SHA, "conflict": False, "cost_usd": 0.5, "tokens": {"in": 1}}
    ],
    "stack_pauses": [{"parent": 7, "cause": "parent_open", "at": NOW, "resumed_at": None}],
    "full_checks_reads": [
        {
            "version": 1,
            "head": SHA,
            "jobs": [{"job": "tests", "bytes": 10, "cause": "read", "link": PR}],
            "tests": ["abc"],
        }
    ],
    "drawing_departures": [_DEPARTURE],
    "drawing_escalation": {"round": 2, "numbers": [1, 2], "cause": "drawing_departure"},
    "drawing_checks": {
        "head": SHA,
        "rows": [{"subject": "screen", "ref": "home", "verdict": "departs", "settled": True}],
        "open": 0,
    },
}

# Keys `render` writes when set, however empty: `0`, `0.0`, `{}` stay (the document tells "none
# recorded" from "recorded as none"), and `None` is no record. A falsy fixture cannot show it.
EMPTY_STILL_WRITTEN: dict[str, object] = {
    "actual_files": 0,
    "shadow_wait_s": 0.0,
    "drawing_checks": {},
    "stack_parent": {},
}

# The keys `record_closure` also writes, so their line in `render` must have the shape it gives.
CLOSURE_KEYS: dict[str, object] = {
    "stack_unfetchable": {"head": OTHER_SHA, "since": "2026-11-19T10:00:00+00:00"},
    "worktree_release_lapse": {"at": "2026-11-19T10:00:00Z", "cause": "os_error"},
    "closed_by_slices": ["https://github.com/acme/shop/pull/9"],
    "split_repointed_at": "2026-11-19T10:00:00Z",
    "split_closed_at": "2026-11-19T11:00:00Z",
    "split_abandoned_at": "2026-11-19T12:00:00Z",
}

BASE: dict[str, Any] = {"id": "GH-1", "title": "t", "description": "d"}


def reference_problem(key: str, reference: str) -> str | None:
    """Why `reference` does not resolve, `None` when it does. `path::name` is read with `ast` (a
    top-level def or async def of that name, never imported); `module.name` is imported."""
    if "::" in reference:
        path, name = reference.split("::", 1)
        file = ROOT / path
        if not file.is_file():
            return f"{key}: reference {reference!r} names no file"
        found = any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
            for node in ast.parse(file.read_text()).body
        )
        return None if found else f"{key}: reference {reference!r} finds no top-level def {name!r}"
    module, _, name = reference.rpartition(".")
    try:
        getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError, ValueError):
        return f"{key}: reference {reference!r} does not resolve to a symbol"
    return None


def key_problems(
    keys: frozenset[str],
    fixtures: Mapping[str, object],
    excluded: Mapping[str, tuple[Reason, str]],
    render: Callable[[dict[str, Any]], str] = document.render,
) -> list[str]:
    """Every way `keys` fail the guard, one line each: a key with neither a fixture nor an
    exclusion; an exclusion that is not a key, or that also has a fixture, or whose reference does
    not resolve; a fixture that is empty (the render line writes nothing for it); a key absent
    after `render` and `parse`; a key that comes back unequal."""
    out: list[str] = []
    for key in sorted(keys - set(fixtures) - set(excluded)):
        out.append(
            f"{key}: no fixture and no exclusion; add a fixture and a line in document.render, "
            "or an EXCLUDED entry with its reason and the test that makes it true"
        )
    for key, (_, reference) in sorted(excluded.items()):
        if key not in keys:
            out.append(f"{key}: excluded but not a TaskState key")
        if key in fixtures:
            out.append(f"{key}: both excluded and written; it needs one or the other")
        if (problem := reference_problem(key, reference)) is not None:
            out.append(problem)
    written = {key: value for key, value in fixtures.items() if key in keys and key not in excluded}
    parsed = document.parse(render({**BASE, **written}))
    for key, value in sorted(written.items()):
        if key not in parsed and not value:
            out.append(
                f"{key}: bad fixture, {value!r} is empty so render writes nothing for it; give it "
                "a non-empty value (an empty one still written goes in EMPTY_STILL_WRITTEN)"
            )
        elif key not in parsed:
            out.append(
                f"{key}: not written by document.render (or dropped by its reader): add a line to "
                "render that parse reads back, or exclude the key with its reason"
            )
        elif parsed[key] != value:
            out.append(f"{key}: round trip changed the value: {value!r} came back {parsed[key]!r}")
    return out


def test_the_key_set_is_the_whole_state():
    assert KEYS
    assert KEYS >= task.TaskInput.__required_keys__ | task.TaskInput.__optional_keys__
    assert "drawing_checks" in KEYS


def test_every_state_key_is_written_and_read_back_equal_or_excluded_with_a_resolving_reason():
    assert key_problems(KEYS, FIXTURES, EXCLUDED) == []


def test_a_key_with_no_fixture_and_no_exclusion_is_refused():
    problems = key_problems(KEYS | {"brand_new"}, FIXTURES, EXCLUDED)
    assert len(problems) == 1
    assert problems[0].startswith("brand_new: no fixture and no exclusion")


def test_a_fixture_for_a_key_render_does_not_write_is_refused():
    def forgetful(state: dict[str, Any]) -> str:
        return document.render({k: v for k, v in state.items() if k != "stack_parent"})

    problems = key_problems(KEYS, FIXTURES, EXCLUDED, forgetful)
    assert len(problems) == 1
    assert problems[0].startswith("stack_parent: not written by document.render")


def test_an_empty_fixture_is_refused_as_a_bad_fixture():
    problems = key_problems(KEYS | {"x"}, {**FIXTURES, "x": []}, EXCLUDED)
    assert [p.split(":")[0] for p in problems] == ["x"]
    assert "bad fixture" in problems[0]


def test_a_value_that_comes_back_different_is_refused():
    def lossy(state: dict[str, Any]) -> str:
        return document.render({**state, "stack_parent": {**state["stack_parent"], "issue": 8}})

    problems = key_problems(KEYS, FIXTURES, EXCLUDED, lossy)
    assert len(problems) == 1
    assert problems[0].startswith("stack_parent: round trip changed the value")


@pytest.mark.parametrize(
    ("key", "reference", "fragment"),
    [
        ("interview_stats", "tests/core/process/domain/test_document.py::test_nope", "finds no"),
        ("interview_stats", "tests/core/process/domain/no_such.py::test_x", "names no file"),
        ("subtask_id", "agentic.core.process.domain.task._NO_SUCH_SET", "does not resolve"),
    ],
)
def test_an_exclusion_whose_reference_does_not_resolve_is_refused(
    key: str, reference: str, fragment: str
):
    excluded = {**EXCLUDED, key: ("transient", reference)}
    problems = key_problems(KEYS, FIXTURES, excluded)
    assert len(problems) == 1
    assert problems[0].startswith(f"{key}:") and fragment in problems[0]


def test_an_exclusion_that_is_also_written_or_not_a_key_is_refused():
    both = key_problems(KEYS, {**FIXTURES, "subtask_id": "api"}, EXCLUDED)
    assert both == ["subtask_id: both excluded and written; it needs one or the other"]
    stray = key_problems(KEYS, FIXTURES, {**EXCLUDED, "ghost": EXCLUDED["subtask_id"]})
    assert stray == ["ghost: excluded but not a TaskState key"]


def test_the_exclusions_are_in_the_state_and_the_render_writes_nothing_for_them():
    assert set(EXCLUDED) == set(EXCLUDED_SAMPLES) and set(EXCLUDED) <= KEYS
    for key in EXCLUDED:
        assert key not in document.parse(document.render({**BASE, key: EXCLUDED_SAMPLES[key]}))


def test_the_branch_local_exclusions_are_the_branch_local_keys():
    local = {key for key, (reason, _) in EXCLUDED.items() if reason == "branch_local"}
    assert local == task._BRANCH_LOCAL


def test_the_document_is_stable_under_a_second_round_trip():
    state = {**BASE, **{k: v for k, v in FIXTURES.items() if k not in EXCLUDED}}
    text = document.render(state)
    assert document.render({**BASE, **document.parse(text)}) == text


@pytest.mark.parametrize("key", sorted(EMPTY_STILL_WRITTEN))
def test_an_empty_value_is_still_written_and_read_back_equal(key: str):
    empty = EMPTY_STILL_WRITTEN[key]
    parsed = document.parse(document.render({**BASE, key: empty}))
    assert key in parsed
    assert parsed[key] == empty and type(parsed[key]) is type(empty)


@pytest.mark.parametrize("key", ["stack_parent", "stack_unfetchable", "drawing_checks"])
def test_a_none_value_writes_no_key_and_leaves_the_document_unchanged(key: str):
    assert document.render({**BASE, key: None}) == document.render(BASE)


@pytest.mark.parametrize("key", sorted(CLOSURE_KEYS))
def test_a_closure_key_has_the_line_record_closure_writes_and_is_replaced_in_place(key: str):
    held = FIXTURES[key]
    text = document.render({**BASE, key: held})
    # Writing the value the document holds changes nothing: the line is `record_closure`'s own.
    assert document.record_closure(text, {key: held}) == text
    closed = document.record_closure(text, {key: CLOSURE_KEYS[key]})
    assert document.parse(closed)[key] == CLOSURE_KEYS[key]
    frontmatter = closed.split("\n---\n", 1)[0]
    assert len([ln for ln in frontmatter.splitlines() if ln.startswith(f"{key}:")]) == 1
    # And a document that never had the key gets it appended by the same surgery.
    appended = document.record_closure(document.render(BASE), {key: CLOSURE_KEYS[key]})
    assert document.parse(appended)[key] == CLOSURE_KEYS[key]


def test_a_spec_survives_with_the_commit_the_guard_moves():
    spec = FIXTURES["spec"]
    assert isinstance(spec, dict)
    moved = {**spec, "commit": OTHER_SHA}
    assert document.parse(document.render({**BASE, "spec": moved}))["spec"] == moved


@pytest.mark.parametrize(
    ("key", "unset"),
    [
        ("spec", {}),
        ("stack_unfetchable", None),
        ("worktree_release_lapse", {}),
        ("closed_by_slices", []),
        ("split_repointed_at", None),
        ("split_closed_at", ""),
        ("split_abandoned_at", None),
    ],
)
def test_a_key_added_by_this_task_writes_nothing_when_unset(key: str, unset: object):
    text = document.render({**BASE, key: unset})
    assert text == document.render(BASE)
    assert key not in document.parse(text)
