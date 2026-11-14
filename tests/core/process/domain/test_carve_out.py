from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentic.core.process.domain import carve_out, rules
from agentic.core.process.domain.carve_out import CarveOut
from agentic.core.process.domain.task import CarveRecord


def _carve(**over: Any) -> CarveOut:
    carve: CarveOut = {
        "steps": ["b"],
        "title": "Carved steps",
        "body": "Does the rest.",
        "kind": "change",
        "moved_criteria": ["it works"],
        "estimated_cost_usd": 10.0,
        "estimated_files": 5,
        "hand_files": 3,
        "hand_lines": 100,
        "test_lines": 50,
        "required_paths": ["src/a.py"],
        "ci_required": ["tests"],
        "integration": "wire a",
        "remainder_green": "the app still builds",
        "guard": "nothing calls it",
    }
    carve.update(over)
    return carve


def _b(brief: str = "Build b, which fixes the parser.", invariants: list[str] | None = None) -> Any:
    return {
        "id": "b",
        "title": "B",
        "brief": brief,
        "invariants": ["b is pure"] if invariants is None else invariants,
        "depends_on": ["a"],
    }


def _plan(integration: str = "wire a and b together", b: Any = None) -> dict[str, object]:
    return {
        "integration": integration,
        "subtasks": [
            {"id": "a", "title": "A", "brief": "Build a.", "depends_on": []},
            _b() if b is None else b,
            {"id": "c", "title": "C", "brief": "Build c.", "depends_on": ["a"]},
        ],
    }


def test_valid_carve_out_has_no_refusal() -> None:
    assert carve_out.validate(_carve(), _plan("wire a")) == []


@pytest.mark.parametrize("steps", [[], ["zzz"], ["a", "b", "c"]])
def test_steps_must_be_a_strict_non_empty_subset(steps: list[str]) -> None:
    assert carve_out.validate(_carve(steps=steps), _plan("")) != []


def test_remaining_depends_on_an_extracted_id_is_refused() -> None:
    reasons = carve_out.validate(_carve(steps=["a"]), _plan(""))
    assert any("depends on extracted" in r for r in reasons)


def test_integration_naming_an_extracted_id_is_refused_as_a_whole_word() -> None:
    refused = carve_out.validate(_carve(integration="wire a and b"), _plan(""))
    assert any("integration" in r for r in refused)
    assert carve_out.validate(_carve(integration="wire a baby"), _plan("")) == []


def test_original_plan_integration_naming_extracted_ids_is_not_refused() -> None:
    # The original integration wires every subtask; only the redeclared one is judged.
    assert carve_out.validate(_carve(integration="wire a"), _plan()) == []


def test_invalid_kind_and_multiline_green_are_refused() -> None:
    assert any("kind" in r for r in carve_out.validate(_carve(kind="feature"), _plan("")))
    multiline = _carve(remainder_green="a\nb")
    assert any("one line" in r for r in carve_out.validate(multiline, _plan("")))


@pytest.mark.parametrize(
    ("brief", "refused"),
    [
        ("Closes #719", True),
        ("fixed #3", True),
        ("Resolves: owner/repo#4", True),
        ("closes https://github.com/o/r/issues/9", True),
        ("fixes the parser", False),
        ("see <!-- x", True),
        ("a --> b", True),
        ("blocked BY: #3", True),
    ],
)
def test_copied_brief_refusals(brief: str, refused: bool) -> None:
    assert bool(carve_out.validate(_carve(), _plan("", _b(brief)))) is refused


def test_comment_in_a_copied_invariant_is_refused() -> None:
    assert carve_out.validate(_carve(), _plan("", _b(invariants=["<!-- hidden"]))) != []


@pytest.mark.parametrize(
    "token", ["<!--", "-->", "Blocked by:", "Closes", "fixes", "RESOLVES", "closes #1"]
)
def test_text_refused_tokens_in_title_and_body(token: str) -> None:
    assert carve_out.text_refused(f"x {token} y", "ok") is not None
    assert carve_out.text_refused("ok", f"x {token} y") is not None


def test_text_refused_title_newline_and_clean_text() -> None:
    assert carve_out.text_refused("a\nb", "ok") is not None
    assert carve_out.text_refused("a title", "a body\nwith lines") is None
    assert carve_out.text_refused("a", "Fixed it") is None


def test_remainder_drops_extracted_and_redeclares_without_mutating() -> None:
    plan = _plan()
    out = carve_out.remainder(plan, _carve(estimated_cost_usd=7.0, integration="only a"))
    assert [s["id"] for s in out["subtasks"]] == ["a", "c"]
    assert out["estimated_cost_usd"] == 7.0
    assert out["integration"] == "only a"
    assert len(plan["subtasks"]) == 3


def test_remainder_refusal_asks_the_injected_fits() -> None:
    seen: list[object] = []

    def refuses(plan: object) -> bool:
        seen.append(plan)
        return False

    assert carve_out.remainder_refusal(_plan(), _carve(), refuses) is not None
    assert carve_out.remainder_refusal(_plan(), _carve(), lambda _p: True) is None
    assert len(seen) == 1


def test_marker_round_trip_and_split_part_of_ignores_it() -> None:
    text = carve_out.marker("GH-719", 720)
    assert text == "<!-- agentic-carve-out: GH-719 of #720 -->"
    assert carve_out.marked_part_issue(f"body\n{text}\n") == 720
    assert rules.split_part_of(text) is None
    assert carve_out.marked_part_issue(rules.split_marker("GH-719", 1, 2)) is None


def test_marker_names_the_part_issue_not_its_index() -> None:
    first = carve_out.marker("GH-1", 100)
    second = carve_out.marker("GH-2", 200)  # both parents' part 1
    assert carve_out.marked_part_issue(first) == 100
    assert carve_out.marked_part_issue(second) == 200


def test_follow_up_body_order_and_verbatim_copy() -> None:
    body = carve_out.follow_up_body(
        "Model text.", [_b()], 720, "Context line", "change", task_id="GH-719"
    )
    order = [
        "Model text.",
        "## Carved-out steps",
        "Build b, which fixes the parser.",
        "- b is pure",
        "Kind: change",
        "Context line",
        "Blocked by: #720",
        carve_out.marker("GH-719", 720),
    ]
    positions = [body.index(part) for part in order]
    assert positions == sorted(positions)
    assert body.endswith(carve_out.marker("GH-719", 720))
    assert rules.blockers_from_body(body) == (720,)


def test_follow_up_body_is_total_where_validate_refuses() -> None:
    sub = _b("Closes #5")
    assert carve_out.validate(_carve(), _plan("", sub)) != []
    body = carve_out.follow_up_body("m", [sub], 7, "ctx", "change", task_id="GH-1")
    assert "Closes #5" in body


@pytest.mark.parametrize(
    "label", ["needs-human", "agentic-hold", "agentic-stop", "agentic", "path:src/x"]
)
def test_copied_labels_drop_each_excluded_label(label: str) -> None:
    assert carve_out.copied_labels(["bug", label]) == ("agentic", "bug")


def test_copied_labels_none_and_duplicates() -> None:
    assert carve_out.copied_labels(None) == ("agentic",)
    assert carve_out.copied_labels(["bug", "bug", "ui"]) == ("agentic", "bug", "ui")


def test_append_blocker_inserts_after_parent_and_is_idempotent() -> None:
    new = carve_out.append_blocker("Intro\nBlocked by: #5\nTail\n", 5, [5, 9])
    assert new == "Intro\nBlocked by: #5, #9\nTail\n"
    assert carve_out.append_blocker(new, 5, [5, 9]) is None


def test_append_blocker_keeps_other_blockers_and_lines() -> None:
    body = "x  \r\nBlocked by: #3, #5, #8\r\n```\nBlocked by: #5\n```\n"
    assert carve_out.append_blocker(body, 5, [5, 9]) == (
        "x  \r\nBlocked by: #3, #5, #9, #8\r\n```\nBlocked by: #5\n```\n"
    )


def test_append_blocker_needs_parent_first_and_a_blocked_by_line() -> None:
    assert carve_out.append_blocker("Blocked by: #5", 5, [9, 5]) is None
    assert carve_out.append_blocker("Blocked by: #5", 5, []) is None
    assert carve_out.append_blocker("See #5 and Blocked by: #5 inline", 5, [5, 9]) is None


@dataclass
class _Ref:
    url: str
    body: str


def test_followups_of_reads_only_markers_of_the_given_parts() -> None:
    refs = [
        _Ref("https://github.com/o/r/issues/30", carve_out.marker("GH-1", 11)),
        _Ref("https://github.com/o/r/issues/31", carve_out.marker("GH-2", 12)),
        _Ref("https://github.com/o/r/issues/32", rules.split_marker("GH-1", 1, 1)),
        _Ref("https://github.com/o/r/issues/33", carve_out.marker("GH-1", 11)),
        _Ref("https://github.com/o/r/issues/x", carve_out.marker("GH-1", 11)),
    ]
    assert carve_out.followups_of(refs, {11}) == [30, 33]
    assert carve_out.followups_of(refs, {11, 12}) == [30, 31, 33]
    assert carve_out.followups_of(refs, set()) == []


def test_carve_out_due() -> None:
    judged = {"fit_question": "does it fit?"}
    assert carve_out.carve_out_due({}, judged, writes_tracker=True)
    assert not carve_out.carve_out_due({}, judged, writes_tracker=False)
    assert not carve_out.carve_out_due({}, {"fit_question": ""}, writes_tracker=True)
    assert not carve_out.carve_out_due({}, {}, writes_tracker=True)
    assert not carve_out.carve_out_due({"carve_out": {}}, judged, writes_tracker=True)


def test_carve_out_pending() -> None:
    assert carve_out.carve_out_pending({"carve_out": {"data": {}}})
    assert not carve_out.carve_out_pending({"carve_out": {"data": {}, "outcome": "done"}})
    assert not carve_out.carve_out_pending({"carve_out": {}})
    assert not carve_out.carve_out_pending({"carve_out": "weird"})
    assert not carve_out.carve_out_pending({})


def test_marked_by_returns_both_parts_of_the_marker() -> None:
    assert carve_out.marked_by(carve_out.marker("GH-721", 731)) == ("GH-721", 731)
    assert carve_out.marked_by(f"body\n{carve_out.marker('GH-1', 5)}\nmore") == ("GH-1", 5)


@pytest.mark.parametrize(
    "body", ["<!-- agentic-split: GH-721 part 1 depth 2 -->", "no marker here", ""]
)
def test_marked_by_is_none_for_a_split_marker_or_no_marker(body: str) -> None:
    assert carve_out.marked_by(body) is None
    assert carve_out.marked_part_issue(body) is None


def test_marked_part_issue_agrees_with_marked_by() -> None:
    for body in (carve_out.marker("GH-721", 731), "nothing", rules.split_marker("GH-721", 1, 2)):
        found = carve_out.marked_by(body)
        assert carve_out.marked_part_issue(body) == (found[1] if found else None)


def test_the_carve_out_marker_never_matches_split_part_of() -> None:
    assert rules.split_part_of(carve_out.marker("GH-721", 731)) is None


def test_writes_tracker_is_false_with_an_evaluation_and_true_without() -> None:
    assert not carve_out.writes_tracker({"evaluation": {"arm": "a"}})
    assert carve_out.writes_tracker({})
    assert carve_out.writes_tracker({"evaluation": None})


@pytest.mark.parametrize("evaluation", ["absent", None, {}, {"arm": "a"}])
def test_writes_tracker_is_the_opposite_of_what_after_integrate_reads(evaluation: Any) -> None:
    """The oracle: `rules.after_integrate` ends at `evaluated` exactly when the run has an
    evaluation, which is when nothing is written to the tracker."""
    state: Any = {
        "type": "feature",
        "flags": {},
        "subtasks": {"api": {"status": "approved"}},
        "runs": [{"role": "Tech Lead", "section": rules.review_section("api"), "cost_usd": 0.1}],
    }
    if evaluation != "absent":
        state["evaluation"] = evaluation
    reached = rules.after_integrate(state)
    assert reached in ("evaluated", "deliver")
    assert (reached == "evaluated") == (not carve_out.writes_tracker(state))


def test_carve_out_pending_reads_a_carve_record() -> None:
    open_record: CarveRecord = {"data": {"steps": ["b"]}, "fit": {}}
    closed: CarveRecord = {**open_record, "outcome": "applied"}
    assert carve_out.carve_out_pending({"carve_out": open_record})
    assert not carve_out.carve_out_pending({"carve_out": closed})
