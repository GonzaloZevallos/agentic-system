"""`legacy_names.rename_legacy_roles`/`has_legacy_roles` (Tech Lead plan, subtask
`rename`): the anti-corruption translation from the old Design Lead vocabulary to UI
Lead, applied at load time. The fixture carries the old name in every field the plan
lists, so a verdict (`rules`) that agrees between the old-named and new-named fixture
proves the rename changes no outcome."""

from agentic.core.process.domain import legacy_names, rules


def _old_fixture() -> dict:
    return {
        "current_role": "Design Lead",
        "sections": {
            "UX Designer": "flow",
            "UX Lead": "walk",
            "UI Designer": "screens",
            "Design Lead": "approved",
            "Design Lead (v1)": "returned once",
            "Design Lead: final comparisons": "final approved",
            "Design Lead: final comparisons (v1)": "final returned once",
        },
        "runs": [
            {"role": "UX Designer", "section": "UX Designer", "slug": "ux_designer"},
            {"role": "UX Lead", "section": "UX Lead", "slug": "ux_lead"},
            {"role": "UI Designer", "section": "UI Designer", "slug": "ui_designer"},
            {"role": "Design Lead", "section": "Design Lead", "slug": "design_lead"},
            {
                "role": "Design Lead",
                "section": "Design Lead: final comparisons",
                "slug": "design_lead_final",
            },
        ],
        "failures": [
            {
                "role": "Design Lead",
                "attempt": 1,
                "error": "Design Lead crashed",
                "cost_usd": 0.1,
                "session_id": "",
                "section": "Design Lead",
                "slug": "design_lead",
            }
        ],
        "changes_requested": {"Design Lead→UI Designer": 1},
        "change_history": [
            {"pair": "Design Lead→UI Designer", "category": "definition", "reason": "r"}
        ],
        "pause_log": [{"kind": "failure", "value": "schema", "section": "Design Lead"}],
        "design_items": [
            {"role_slug": "design_lead", "kind": "blocking", "step": "Pay", "text": "no button"}
        ],
    }


def _new_fixture() -> dict:
    return {
        "current_role": "UI Lead",
        "sections": {
            "UX Designer": "flow",
            "UX Lead": "walk",
            "UI Designer": "screens",
            "UI Lead": "approved",
            "UI Lead (v1)": "returned once",
            "UI Lead: final comparisons": "final approved",
            "UI Lead: final comparisons (v1)": "final returned once",
        },
        "runs": [
            # `role` is not among the translated fields (only `section`/`slug` are):
            # an old document's `role` stays exactly as it was recorded.
            {"role": "UX Designer", "section": "UX Designer", "slug": "ux_designer"},
            {"role": "UX Lead", "section": "UX Lead", "slug": "ux_lead"},
            {"role": "UI Designer", "section": "UI Designer", "slug": "ui_designer"},
            {"role": "Design Lead", "section": "UI Lead", "slug": "ui_lead"},
            {
                "role": "Design Lead",
                "section": "UI Lead: final comparisons",
                "slug": "ui_lead_final",
            },
        ],
        "failures": [
            {
                "role": "Design Lead",
                "attempt": 1,
                # Record text is not vocabulary: NEVER translated, old name kept verbatim.
                "error": "Design Lead crashed",
                "cost_usd": 0.1,
                "session_id": "",
                "section": "UI Lead",
                "slug": "ui_lead",
            }
        ],
        "changes_requested": {"UI Lead→UI Designer": 1},
        "change_history": [
            {"pair": "UI Lead→UI Designer", "category": "definition", "reason": "r"}
        ],
        "pause_log": [{"kind": "failure", "value": "schema", "section": "UI Lead"}],
        "design_items": [
            {"role_slug": "ui_lead", "kind": "blocking", "step": "Pay", "text": "no button"}
        ],
    }


def _listed_fields_as_text(state: dict) -> str:
    """Every listed field, flattened to text, so one assertion covers all of them."""
    parts = [
        state.get("current_role") or "",
        " ".join(state.get("sections", {}).keys()),
        " ".join(r.get("section", "") for r in state.get("runs", [])),
        " ".join(r.get("slug", "") for r in state.get("runs", [])),
        " ".join(f.get("section", "") for f in state.get("failures", [])),
        " ".join(f.get("slug", "") for f in state.get("failures", [])),
        " ".join(state.get("changes_requested", {}).keys()),
        " ".join(h["pair"] for h in state.get("change_history", [])),
        " ".join(e.get("section", "") for e in state.get("pause_log", [])),
        " ".join(i["role_slug"] for i in state.get("design_items", [])),
    ]
    return " ".join(parts)


def test_rename_legacy_roles_translates_every_listed_field():
    renamed = legacy_names.rename_legacy_roles(_old_fixture())
    text = _listed_fields_as_text(renamed)
    assert "Design Lead" not in text
    assert "design_lead" not in text
    assert renamed["sections"] == _new_fixture()["sections"]
    assert renamed["runs"] == _new_fixture()["runs"]
    assert renamed["changes_requested"] == _new_fixture()["changes_requested"]
    assert renamed["change_history"] == _new_fixture()["change_history"]
    assert renamed["pause_log"] == _new_fixture()["pause_log"]
    assert renamed["design_items"] == _new_fixture()["design_items"]
    assert renamed["current_role"] == "UI Lead"


def test_rename_legacy_roles_never_touches_records():
    old = _old_fixture()
    old["sections"]["Design Lead"] = "the record text names Design Lead, untouched"
    old["escalated"] = "Design Lead hit its return limit"
    renamed = legacy_names.rename_legacy_roles(old)
    assert renamed["sections"]["UI Lead"] == "the record text names Design Lead, untouched"
    assert renamed["escalated"] == "Design Lead hit its return limit"
    assert renamed["failures"][0]["error"] == "Design Lead crashed"


def test_rename_legacy_roles_never_touches_prompts():
    old = _old_fixture()
    old["prompts"] = {"design_lead": "abcd1234"}
    renamed = legacy_names.rename_legacy_roles(old)
    assert renamed["prompts"] == {"design_lead": "abcd1234"}


def test_rename_legacy_roles_agrees_with_rules_over_the_new_names_fixture():
    old_rules_state = {**_old_fixture(), "type": "feature", "flags": {"touches_ui": True}}
    new_rules_state = {**_new_fixture(), "type": "feature", "flags": {"touches_ui": True}}
    renamed = legacy_names.rename_legacy_roles(old_rules_state)
    assert rules.design_step(renamed) == rules.design_step(new_rules_state)
    assert rules.resume_target(renamed) == rules.resume_target(new_rules_state)
    assert rules.design_effect_by_role(renamed) == rules.design_effect_by_role(new_rules_state)
    # One UI Lead return (`change_history`) plus one UI Lead blocking item (`design_items`):
    # the fixture raises no UX Lead return, so `PAIR_UX_LEAD`'s own key never matches.
    assert rules.design_effect_by_role(renamed) == {"ui_lead": 2}


def test_rename_legacy_roles_tolerates_a_change_history_entry_without_a_pair():
    """A `change_history` entry recorded before `pair` existed has no such key
    (`rules.resumed_status`'s own `h.get("pair", ...)` reads around the same gap):
    translation must not raise on it, and must leave it exactly as it was."""
    state = {
        "change_history": [
            {"category": "definition", "reason": "r"},
            {"pair": "Design Lead→UI Designer", "category": "definition", "reason": "r2"},
        ]
    }
    renamed = legacy_names.rename_legacy_roles(state)
    assert renamed["change_history"][0] == {"category": "definition", "reason": "r"}
    assert renamed["change_history"][1]["pair"] == "UI Lead→UI Designer"


def test_rename_legacy_roles_is_idempotent():
    once = legacy_names.rename_legacy_roles(_old_fixture())
    twice = legacy_names.rename_legacy_roles(once)
    assert once == twice
    assert legacy_names.has_legacy_roles(twice) is False


def test_has_legacy_roles():
    assert legacy_names.has_legacy_roles(_old_fixture()) is True
    assert legacy_names.has_legacy_roles(_new_fixture()) is False
    assert legacy_names.has_legacy_roles({}) is False


def test_rename_legacy_roles_rejects_a_sections_collision():
    old = _old_fixture()
    old["sections"]["UI Lead"] = "already new"
    try:
        legacy_names.rename_legacy_roles(old, source="GH-9")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        message = str(exc)
        assert "GH-9" in message
        assert "sections" in message
        assert "Design Lead" in message and "UI Lead" in message


def test_rename_legacy_roles_rejects_a_changes_requested_collision():
    old = _old_fixture()
    old["changes_requested"]["UI Lead→UI Designer"] = 1
    try:
        legacy_names.rename_legacy_roles(old, source="GH-9")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        message = str(exc)
        assert "GH-9" in message
        assert "changes_requested" in message
        assert "Design Lead→UI Designer" in message and "UI Lead→UI Designer" in message
