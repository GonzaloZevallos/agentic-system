"""`task_path`: the path decision table, the owner label, promotion and the menu (GH-591)."""

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain import document, task_path
from agentic.core.process.domain.task_path import decide, owner_label, promote

TYPES = ("bug", "feature", "platform_enhancement")
LIGHT, FULL = "path:light", "path:full"
KNOWN = frozenset({"billing"})

# size, risk, labels, known_risks -> path, cause
ROWS = [
    ("small", [], (), KNOWN, "light", "small_no_risk"),
    ("small", ["billing"], (), KNOWN, "full", "risk_flag"),
    ("small", ["data-migration"], (), KNOWN, "full", "risk_flag"),
    ("normal", [], (), KNOWN, "full", "not_small"),
    ("large", [], (), KNOWN, "full", "not_small"),
    ("small", [], (FULL,), KNOWN, "full", "owner_full"),
    ("small", [], (LIGHT,), KNOWN, "light", "owner_light"),
    ("small", ["billing"], (LIGHT,), KNOWN, "light", "owner_light"),
    ("large", [], (LIGHT,), KNOWN, "light", "owner_light"),
    ("small", [], (LIGHT, FULL), KNOWN, "full", "label_both"),
    ("small", [], None, KNOWN, "full", "label_unreadable"),
    (None, [], (), KNOWN, "full", "missing_size"),
    ("small", None, (), KNOWN, "full", "missing_risk"),
    ("small", [], (), None, "full", "stack_unreadable"),
    ("small", ["Billing"], (), KNOWN, "full", "unknown_risk"),
    ("small", ["nope"], (), KNOWN, "full", "unknown_risk"),
    # The owner label never beats a missing, unknown or unreadable input.
    (None, [], (LIGHT,), KNOWN, "full", "missing_size"),
    ("small", None, (LIGHT,), KNOWN, "full", "missing_risk"),
    ("small", ["nope"], (LIGHT,), KNOWN, "full", "unknown_risk"),
    ("small", [], (LIGHT,), None, "full", "stack_unreadable"),
    ("small", None, None, KNOWN, "full", "label_unreadable"),
]


@pytest.mark.parametrize("type_", TYPES)
@pytest.mark.parametrize(("size", "risk", "labels", "known", "path", "cause"), ROWS)
def test_decide_truth_table(type_, size, risk, labels, known, path, cause):
    decision = decide(type_, size, risk, labels, known)
    assert (decision.path, decision.cause) == (path, cause)
    assert task_path.path_for(type_, size, risk, labels, known) == path


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (None, "unreadable"),
        ((), "none"),
        (("bug",), "none"),
        ((LIGHT,), "light"),
        ((FULL, "x"), "full"),
        ((LIGHT, FULL), "both"),
    ],
)
def test_owner_label(labels, expected):
    assert owner_label(labels) == expected


def test_the_builtin_risks_equal_the_knowledge_tuple():
    assert tuple(task_path.BUILTIN_RISKS) == stack.BUILTIN_RISK_IDS


def test_risk_menu_lists_builtins_then_product_areas_with_every_id():
    menu = task_path.risk_menu([("billing", "Invoices"), ("search", "The index")])
    lines = menu.splitlines()
    assert lines[: len(task_path.BUILTIN_RISKS)] == [
        f"- `{i}`: {d}" for i, d in task_path.BUILTIN_RISKS.items()
    ]
    assert lines[-2:] == ["- `billing`: Invoices", "- `search`: The index"]
    assert task_path.risk_menu(()).count("\n") == len(task_path.BUILTIN_RISKS) - 1


def test_unknown_risks_names_only_the_ids_outside_both_lists():
    assert task_path.unknown_risks(["billing", "money-rounding", "x"], KNOWN) == ("x",)


def test_a_state_without_the_fields_reads_full_none_unknown():
    assert task_path.path_of({}) == "full"
    assert task_path.path_label_of({}) == "none"
    assert task_path.path_cause_of({}) == "unknown"


def test_an_old_document_without_the_fields_loads_and_reads_the_defaults():
    parsed = document.parse("---\nid: T-1\ntitle: t\n---\n\n## Original\n\nbody\n")
    assert task_path.path_of(parsed) == "full"
    assert task_path.path_label_of(parsed) == "none"
    assert task_path.path_cause_of(parsed) == "unknown"
    assert "initial_path" not in parsed


def test_the_path_fields_round_trip_through_the_document():
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        **task_path.classification_patch(task_path.PathDecision("light", "owner_light"), "light"),
    }
    state.update(promote(state, "Developer", "role", "2026-11-11T00:00:00Z")[0])
    assert state["path"] == "light"  # owner label: nothing applied
    parsed = document.parse(document.render(state, {}, "abc"))
    for key in ("initial_path", "path", "path_cause", "path_label"):
        assert parsed[key] == state[key]


def light_state(label="none"):
    return {
        "initial_path": "light",
        "path": "light",
        "path_cause": "small_no_risk",
        "path_label": label,
    }


def test_promote_goes_light_to_full_once_and_writes_only_path_and_promotion():
    state = light_state()
    patch, note = promote(state, "Tech Lead", "plan_over_cap", "2026-11-11T10:00:00Z", "3 subtasks")
    assert note is None
    assert patch == {
        "path": "full",
        "promotion": {
            "from_role": "Tech Lead",
            "kind": "plan_over_cap",
            "text": "3 subtasks",
            "at": "2026-11-11T10:00:00Z",
        },
    }
    promoted = {**state, **patch}
    assert promote(promoted, "Developer", "role", "later") == ({}, None)
    assert promoted["initial_path"] == "light" and promoted["path_cause"] == "small_no_risk"


def test_promote_under_the_owner_label_light_applies_nothing_and_says_so():
    patch, note = promote(light_state("light"), "Tech Lead", "risk_area", "now")
    assert patch == {}
    assert note is not None and "path:light" in note


def test_promote_never_moves_full_to_light_and_a_document_without_path_is_full():
    assert promote({"path": "full"}, "Developer", "owner", "now") == ({}, None)
    assert promote({}, "Developer", "owner", "now") == ({}, None)


@pytest.mark.parametrize("text", ["x" * 241, "two\nlines", "two\rlines"])
def test_promote_rejects_a_text_over_240_characters_or_with_a_newline(text):
    with pytest.raises(ValueError, match="one line"):
        promote(light_state(), "Developer", "role", "now", text)


def test_promote_accepts_a_text_of_exactly_240_characters():
    patch, _ = promote(light_state(), "Developer", "role", "now", "x" * 240)
    assert patch["promotion"]["text"] == "x" * 240
