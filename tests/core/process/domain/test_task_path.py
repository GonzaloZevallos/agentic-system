"""`task_path`: the path decision table, the owner label, promotion and the menu (GH-591)."""

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain import document, task_path
from agentic.core.process.domain.spec_delivery import SpecRefusal
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
    for key in ("initial_path", "path", "path_cause", "path_label", "path_routed"):
        assert parsed[key] == state[key]


def test_classification_patch_marks_the_document_as_routed():
    patch = task_path.classification_patch(task_path.PathDecision("full", "not_small"), "none")
    assert patch == {
        "initial_path": "full",
        "path": "full",
        "path_cause": "not_small",
        "path_label": "none",
        "path_routed": True,
    }


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


AREAS = (
    ("billing-core", ("src/billing/**",)),
    ("migrations", ("db/migrate/*", "*.sql")),
    ("empty", ()),
)


@pytest.mark.parametrize(
    ("touched", "expected"),
    [
        (["src/billing/rate.py"], task_path.RiskHit("billing-core")),
        (["README.md", "db/migrate/001.py"], task_path.RiskHit("migrations")),
        (["schema.sql"], task_path.RiskHit("migrations")),
        # Declaration order: the first area that matches wins.
        (["schema.sql", "src/billing/x.py"], task_path.RiskHit("billing-core")),
        (["README.md", "src/app.py"], task_path.RiskNone()),
        ([], task_path.RiskNone()),
        # The caller passes both paths of a rename and a deleted path: either one matches.
        (["docs/new.md", "src/billing/old.py"], task_path.RiskHit("billing-core")),
        (["src/billing/gone.py"], task_path.RiskHit("billing-core")),
        # An unreadable diff is never `none`.
        (None, task_path.RiskUnreadable()),
    ],
)
def test_risk_hits(touched, expected):
    assert task_path.risk_hits(AREAS, touched) == expected


def test_an_area_without_paths_never_hits():
    assert task_path.risk_hits((("empty", ()),), ["anything"]) == task_path.RiskNone()


def test_an_invalid_glob_is_unreadable_not_none():
    for glob in ("[", "", "a[b"):
        assert task_path.risk_hits((("bad", (glob,)),), ["a.py"]) == task_path.RiskUnreadable()


def request(kind, text=None, from_role="Developer"):
    return {"kind": kind, "from_role": from_role, "text": text}


@pytest.mark.parametrize(
    ("current", "new", "stored"),
    [
        (None, request("role"), request("role")),
        (None, request("risk_area"), request("risk_area")),
        (request("role", "a"), request("role", "b"), request("role", "b")),
        (request("role"), request("risk_area"), request("risk_area")),
        # A `risk_area` request is never replaced, in either write order.
        (request("risk_area", "m"), request("role"), request("risk_area", "m")),
        (request("risk_area", "m"), request("risk_area", "n"), request("risk_area", "m")),
        (request("risk_area"), request("plan_over_cap"), request("risk_area")),
    ],
)
def test_record_request(current, new, stored):
    assert task_path.record_request(current, new) == stored


def test_request_validator_refuses_a_long_or_multiline_text():
    task_path.validate_request(request("role", "x" * 240))
    for text in ("x" * 241, "a\nb", "a\rb"):
        with pytest.raises(ValueError, match="one line"):
            task_path.validate_request(request("role", text))
    with pytest.raises(ValueError, match="kind"):
        task_path.validate_request(request("nope"))


def test_select_request_order():
    risk_b, risk_a = request("risk_area", "b"), request("risk_area", "a")
    role_1, role_2 = request("role", "1"), request("role", "2")
    cap, plan_role = request("plan_over_cap", from_role="system"), request("role", "p")
    select = task_path.select_request
    assert select(None, {}) is None
    assert select(cap, {"S2": role_2, "S1": role_1}) == role_1
    assert select(plan_role, {"S2": role_2, "S1": role_1}) == role_1
    assert select(cap, {"S3": risk_b, "S2": role_2, "S10": risk_a}) == risk_a  # "S10" < "S3"
    assert select(plan_role, {"S3": risk_b}) == risk_b
    assert select(cap, {}) == cap
    assert select(plan_role, {}) == plan_role


@pytest.mark.parametrize("stored", ["light", "full", "none"])
@pytest.mark.parametrize(
    ("labels", "promoted"),
    [
        (None, False),  # unreadable
        ((), False),  # none
        ((LIGHT,), False),
        ((FULL,), True),
        ((LIGHT, FULL), True),  # both
    ],
)
def test_relabel(stored, labels, promoted):
    state = {**light_state(stored), "id": "T-1"}
    patch, note = task_path.relabel(state, labels, "2026-11-12T00:00:00Z")
    assert note is None
    if not promoted:
        assert patch == {}
        return
    new_label = task_path.owner_label(labels)
    assert patch == {
        "path": "full",
        "path_label": new_label,
        "promotion": {
            "from_role": "owner",
            "kind": "owner",
            "text": None,
            "at": "2026-11-12T00:00:00Z",
        },
    }


def test_relabel_none_and_a_full_task_change_nothing():
    assert task_path.relabel(light_state(), None, "now") == ({}, None)
    assert task_path.relabel({"path": "full"}, (FULL,), "now") == ({}, None)
    assert task_path.relabel({}, (FULL,), "now") == ({}, None)


# --- GH-594 part 4a: the slice's risks replace PjM's ------------------------------------

KNOWN = frozenset({"billing"})


def test_spec_decide_uses_the_slice_risks_not_pjms() -> None:
    # PjM's own `risk` is not an input at all: an empty slice risk list on a small task is light.
    assert task_path.spec_decide("feature", "small", [], (), KNOWN) == task_path.PathDecision(
        "light", "small_no_risk"
    )
    assert task_path.spec_decide(
        "feature", "small", ["data-migration"], (), KNOWN
    ) == task_path.PathDecision("full", "risk_flag")
    assert task_path.spec_decide("feature", "small", ["billing"], (), KNOWN) == (
        task_path.PathDecision("full", "risk_flag")
    )


def test_spec_decide_refuses_an_unknown_risk_naming_the_ids() -> None:
    refusal = task_path.spec_decide(
        "feature", "small", ["billing", "nope", "data-migration", "nada"], (), KNOWN
    )
    assert refusal == SpecRefusal("spec_invalid", ("nope", "nada"))


def test_spec_decide_without_the_stack_is_decide_unchanged() -> None:
    assert task_path.spec_decide("feature", "small", ["whatever"], (), None) == task_path.decide(
        "feature", "small", ["whatever"], (), None
    )
    assert task_path.spec_decide("feature", "small", [], (), None) == task_path.PathDecision(
        "full", "stack_unreadable"
    )


def test_spec_decide_keeps_the_owner_labels() -> None:
    assert task_path.spec_decide(
        "feature", "small", [], (task_path.LABEL_FULL,), KNOWN
    ) == task_path.PathDecision("full", "owner_full")
    assert task_path.spec_decide(
        "feature", "normal", ["billing"], (task_path.LABEL_LIGHT,), KNOWN
    ) == task_path.PathDecision("light", "owner_light")
    assert task_path.spec_decide(
        "feature", "small", [], (task_path.LABEL_LIGHT, task_path.LABEL_FULL), KNOWN
    ) == task_path.PathDecision("full", "label_both")
    assert task_path.spec_decide("feature", None, [], (), KNOWN) == task_path.PathDecision(
        "full", "missing_size"
    )
