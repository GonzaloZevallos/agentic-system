"""The pure rules of the format retakes (GH-1019, part 1): readers, composers and the keys they
write. Inert: nothing here is read by a decision until part 2."""

import copy
import itertools
from typing import Any, get_args

import pytest

from agentic.core.process.domain import format_retakes as fr
from agentic.core.process.domain import rules, task
from agentic.core.process.domain.task import RefusalRule


def failure(rule: str | None = "schema_invalid", reason: str = "schema", section: str = "") -> Any:
    row: dict[str, Any] = {
        "role": "r",
        "attempt": 1,
        "error": "boom",
        "cost_usd": 0.0,
        "session_id": "s",
        "reason": reason,
        "section": section,
    }
    if rule is not None:
        row["rule"] = rule
    return row


def epoch(*failures: Any) -> list[Any]:
    """The terminal entries `rules.logged` leaves for one escalation of these failures."""
    logged = rules.logged({"escalation_class": "failure"}, failures, at="2026-11-21T10:00:00Z")
    return rules.terminal_failures(logged["pause_log"])


def test_the_table_two_retakes_per_rule_then_a_hold_with_rule_spent():
    rule_epoch = fr.format_rules(epoch(failure("schema_invalid")))
    assert rule_epoch == ("schema_invalid",)
    for made, left in ((0, 2), (1, 1)):
        assert fr.limit({"schema_invalid": made}, rule_epoch) == left
        assert not fr.hold_due({"schema_invalid": made}, rule_epoch)
        assert fr.hold_cause({"schema_invalid": made}, rule_epoch) is None
    spent = {"schema_invalid": 2}
    assert fr.limit(spent, rule_epoch) == 0
    assert fr.hold_due(spent, rule_epoch)
    assert fr.hold_cause(spent, rule_epoch) == "rule_spent"
    assert fr.held_rule(spent, rule_epoch, epoch(failure("schema_invalid"))) == "schema_invalid"


def test_two_rules_in_one_epoch_are_distinct_in_order_and_the_tightest_one_limits():
    entries = epoch(
        failure("part_over_budget", "check", "a"),
        failure("schema_invalid", "schema", "b"),
        failure("part_over_budget", "check", "c"),
    )
    found = fr.format_rules(entries)
    assert found == ("part_over_budget", "schema_invalid")
    assert fr.limit({"part_over_budget": 1, "schema_invalid": 0}, found) == 1
    assert fr.limit({"part_over_budget": 2, "schema_invalid": 0}, found) == 0
    assert fr.held_rule({"part_over_budget": 0, "schema_invalid": 2}, found, entries) == (
        "schema_invalid"
    )


def test_a_fresh_rule_beside_two_rules_at_one_has_one_retake_left():
    found = ("unmapped", "output_missing", "schema_invalid")
    counts = {"unmapped": 1, "output_missing": 1}
    assert fr.limit(counts, found) == 1


def test_the_task_backstop_holds_with_task_spent_on_the_last_failure_rule():
    six = (
        "preview_slice_missing",
        "visible_without_drawing_check",
        "drawing_path_undeclared",
        "parts_when_fits",
        "estimate_does_not_fit",
        "part_over_budget",
    )
    counts: Any = {rule: 1 for rule in six}
    entries = epoch(failure("part_over_budget", "check", "a"), failure("unmapped", "schema", "b"))
    found = fr.format_rules(entries)
    assert found == ("part_over_budget", "unmapped")
    assert fr.limit(counts, found) == 0
    assert fr.hold_due(counts, found)
    assert fr.hold_cause(counts, found) == "task_spent"
    assert fr.held_rule(counts, found, entries) == "unmapped"


def test_a_spent_rule_wins_over_a_spent_task():
    counts: Any = {"unmapped": 2, "part_over_budget": 2, "parts_when_fits": 2}
    entries = epoch(failure("part_over_budget", "check", "a"), failure("unmapped", "schema", "b"))
    found = fr.format_rules(entries)
    assert sum(counts.values()) == fr.FORMAT_RETAKE_TASK_MAX
    assert fr.hold_cause(counts, found) == "rule_spent"
    assert fr.held_rule(counts, found, entries) == "part_over_budget"


def test_a_rule_with_no_count_yet_reads_as_zero_and_counts_are_not_mutated():
    counts: Any = {}
    assert fr.limit(counts, ("unmapped",)) == fr.FORMAT_RETAKE_MAX
    assert counts == {}


def test_an_empty_rules_list_fails_closed():
    assert fr.limit({}, ()) == 0
    assert not fr.hold_due({}, ())
    assert fr.hold_cause({}, ()) is None
    assert fr.held_rule({}, (), []) is None


def test_the_format_reasons_are_schema_and_check():
    assert fr.FORMAT_REASONS == ("schema", "check")
    assert (fr.FORMAT_RETAKE_MAX, fr.FORMAT_RETAKE_TASK_MAX) == (2, 6)


def test_is_format_epoch_rejects_what_is_not_a_clean_format_failure():
    assert fr.is_format_epoch(epoch(failure("schema_invalid")))
    assert fr.is_format_epoch(epoch(failure("part_over_budget", "check")))
    # a schema failure that carries no rule
    assert not fr.is_format_epoch(epoch(failure(None)))
    assert fr.format_rules(epoch(failure(None))) == ()
    # a mix with an external failure
    mixed = epoch(failure("schema_invalid"), failure(None, "account_limit", "b"))
    assert not fr.is_format_epoch(mixed)
    assert fr.format_rules(mixed) == ()
    # an empty epoch, and an epoch of an escalation alone
    assert not fr.is_format_epoch([])
    assert not fr.is_format_epoch(epoch())


def test_an_escalation_with_a_cause_or_another_class_is_not_a_format_epoch():
    failures = epoch(failure("schema_invalid"))
    caused = [*failures[:-1], {**failures[-1], "cause": "ci_flake"}]
    other = [*failures[:-1], {**failures[-1], "value": "ci"}]
    assert not fr.is_format_epoch(caused)
    assert not fr.is_format_epoch(other)


def test_a_lapse_passes_through_a_format_epoch():
    entries = [*epoch(failure("schema_invalid")), {"kind": "lapse", "value": "step: Boom"}]
    assert fr.is_format_epoch(entries)


def test_logged_copies_the_rule_onto_the_failure_entry_only_when_present():
    logged = rules.logged(
        {"escalation_class": "failure"},
        [failure("part_over_budget", "check"), failure(None, "stall")],
        at="2026-11-21T10:00:00Z",
    )
    with_rule, without_rule = logged["pause_log"][:2]
    assert with_rule["rule"] == "part_over_budget"
    assert "rule" not in without_rule


def test_a_schema_or_check_epoch_that_carries_a_rule_is_keyed_by_its_reason_and_is_external():
    for reason in fr.FORMAT_REASONS:
        entries = epoch(failure("schema_invalid", reason))
        state: Any = {"pause_log": entries}
        assert fr.is_format_epoch(entries)
        assert rules.auto_resumable(state) == reason  # GH-1019 part 2: the poller retakes it
        assert rules.pause_kind(entries) == "external"


STATE: Any = {
    "format_retakes": {"schema_invalid": 1},
    "format_hold": {"rule": "schema_invalid", "cause": "rule_spent", "at": "2026-11-21T10:00:00Z"},
}


def test_resumed_clears_both_keys_for_a_human_and_keeps_them_for_the_poller():
    human = rules.resumed(STATE, "ship it", at="2026-11-21T11:00:00Z")
    assert human["format_retakes"] is None
    assert human["format_hold"] is None
    poller = rules.resumed(STATE, None, at="2026-11-21T11:00:00Z", by_poller=True)
    assert "format_retakes" not in poller
    assert "format_hold" not in poller


def test_resumed_writes_neither_key_when_the_state_has_neither():
    patch = rules.resumed({}, "ship it", at="2026-11-21T11:00:00Z")
    assert "format_retakes" not in patch
    assert "format_hold" not in patch


AT = "2026-11-21T12:00:00Z"


def test_count_patch_accumulates_over_two_applications_and_writes_only_its_key():
    entries = epoch(failure("schema_invalid"), failure("part_over_budget", "check", "b"))
    state: Any = {"title": "t"}
    first = fr.count_patch(state, entries)
    assert first == {"format_retakes": {"schema_invalid": 1, "part_over_budget": 1}}
    second = fr.count_patch({**state, **first}, entries)
    assert second == {"format_retakes": {"schema_invalid": 2, "part_over_budget": 2}}
    assert state == {"title": "t"}


def test_count_patch_counts_nothing_for_an_epoch_that_is_not_a_format_one():
    assert fr.count_patch({}, epoch(failure(None))) == {}
    assert fr.count_patch({}, []) == {}


def test_hold_patch_writes_a_whole_hold_with_its_issue_url_only_when_there_is_one():
    entries = epoch(failure("schema_invalid"))
    state: Any = {"format_retakes": {"schema_invalid": 2}}
    rules_ = fr.format_rules(entries)
    assert fr.hold_patch(state, rules_, None, AT, entries) == {
        "format_hold": {"rule": "schema_invalid", "cause": "rule_spent", "at": AT}
    }
    url = "https://example.test/issues/9"
    assert fr.hold_patch(state, rules_, url, AT, entries) == {
        "format_hold": {"rule": "schema_invalid", "cause": "rule_spent", "at": AT, "issue_url": url}
    }


def test_hold_patch_is_idempotent_and_empty_when_nothing_is_due():
    entries = epoch(failure("schema_invalid"))
    rules_ = fr.format_rules(entries)
    held: Any = {
        "format_retakes": {"schema_invalid": 2},
        "format_hold": {"rule": "unmapped", "cause": "rule_spent", "at": "earlier"},
    }
    assert fr.hold_patch(held, rules_, None, AT, entries) == {}
    assert fr.hold_patch({"format_retakes": {"schema_invalid": 1}}, rules_, None, AT, entries) == {}
    assert fr.hold_patch({}, rules_, None, AT, entries) == {}
    assert fr.hold_patch({"format_retakes": {"schema_invalid": 2}}, (), None, AT, entries) == {}


def test_hold_patch_under_task_spent_names_the_last_failure_rule_and_never_a_partial_hold():
    entries = epoch(failure("part_over_budget", "check", "a"), failure("unmapped", "schema", "b"))
    counts: Any = {
        "preview_slice_missing": 1,
        "visible_without_drawing_check": 1,
        "drawing_path_undeclared": 1,
        "parts_when_fits": 1,
        "estimate_does_not_fit": 1,
        "output_missing": 1,
    }
    state: Any = {"format_retakes": counts}
    patch = fr.hold_patch(state, fr.format_rules(entries), None, AT, entries)
    assert patch == {"format_hold": {"rule": "unmapped", "cause": "task_spent", "at": AT}}
    assert set(patch["format_hold"]) >= {"rule", "cause", "at"}


def test_noted_patch_is_empty_without_a_hold_and_keeps_every_field_with_one():
    assert fr.noted_patch({}, AT) == {}
    hold: Any = {"rule": "schema_invalid", "cause": "rule_spent", "at": "earlier", "issue_url": "u"}
    noted = fr.noted_patch({"format_hold": hold}, AT)
    assert noted == {"format_hold": {**hold, "noted_at": AT}}
    assert hold == {
        "rule": "schema_invalid",
        "cause": "rule_spent",
        "at": "earlier",
        "issue_url": "u",
    }


def test_noted_patch_replaces_an_earlier_noted_at_instead_of_failing():
    hold: Any = {"rule": "schema_invalid", "cause": "rule_spent", "at": "a", "noted_at": "earlier"}
    assert fr.noted_patch({"format_hold": hold}, AT) == {"format_hold": {**hold, "noted_at": AT}}


def test_release_patch_clears_only_the_keys_the_state_carries():
    assert fr.release_patch({}) == {}
    assert fr.release_patch({"format_retakes": {"unmapped": 1}}) == {"format_retakes": None}
    assert fr.release_patch(STATE) == {"format_retakes": None, "format_hold": None}
    unset: Any = {"title": "t", "format_hold": None}
    assert fr.release_patch(unset) == {}


# GH-1020: the release of a hold and the record of it.

URL = "https://github.com/acme/shop/issues/9"
SHA = "d" * 40
AT = "2026-11-22T10:00:00Z"


def _held(rule: RefusalRule, cause: str, counts: dict[str, int], **more: Any) -> Any:
    hold = {"rule": rule, "cause": cause, "at": AT, "issue_url": URL}
    return {"id": "GH-1", "format_hold": hold, "format_retakes": counts, **more}


def _cases() -> list[tuple[RefusalRule, str, dict[str, int]]]:
    rules_ = get_args(RefusalRule)
    held, others = rules_[0], rules_[1:4]
    cases: list[tuple[RefusalRule, str, dict[str, int]]] = []
    for values in itertools.product(range(3), repeat=4):
        counts = dict(zip((held, *others), values, strict=True))
        if values[0] == 2:
            cases.append((held, "rule_spent", counts))
        elif sum(values) >= 6:
            cases.append((held, "task_spent", counts))
    return cases


@pytest.mark.parametrize(("held", "cause", "counts"), _cases())
def test_after_a_fixed_release_the_held_rule_has_a_retake_left(held, cause, counts):
    state = _held(held, cause, counts)
    after = task.apply(state, fr.fixed_release_patch(state, AT, SHA))
    assert fr.limit(after.get("format_retakes") or {}, [held]) >= 1
    assert after["format_hold"] is None
    others = {rule: n for rule, n in counts.items() if rule != held}
    if cause == "rule_spent" and sum(others.values()) < 6:
        assert after["format_retakes"] == {r: n for r, n in others.items()}


def test_the_property_covers_both_causes_and_the_emptied_case():
    causes = {cause for _, cause, _ in _cases()}
    assert causes == {"rule_spent", "task_spent"}
    assert any(sum(c.values()) - c[get_args(RefusalRule)[0]] >= 5 for _, _, c in _cases())


def test_fixed_release_patch_never_mutates_the_state():
    state = _held("schema_invalid", "rule_spent", {"schema_invalid": 2, "unmapped": 1})
    before = copy.deepcopy(state)
    fr.fixed_release_patch(state, AT, SHA)
    assert state == before


def test_fixed_release_patch_is_empty_without_a_hold_or_an_issue_url():
    assert fr.fixed_release_patch({"id": "GH-1"}, AT, SHA) == {}
    assert fr.fixed_release_patch({"id": "GH-1", "format_hold": None}, AT, SHA) == {}
    state = _held("schema_invalid", "rule_spent", {"schema_invalid": 2})
    del state["format_hold"]["issue_url"]
    assert fr.fixed_release_patch(state, AT, SHA) == {}


def test_format_released_carries_the_previous_entries_plus_the_new_one():
    earlier = {
        "issue_url": "https://github.com/acme/shop/issues/3",
        "closing_commit": None,
        "at": AT,
    }
    state = _held("schema_invalid", "rule_spent", {"schema_invalid": 2}, format_released=[earlier])
    patch = fr.fixed_release_patch(state, AT, None)
    assert patch["format_released"] == [
        earlier,
        {"issue_url": URL, "closing_commit": None, "at": AT},
    ]
    assert patch["format_hold"] is None
    assert patch["format_retakes"] is None


def test_a_state_without_counts_leaves_format_retakes_out_of_the_patch():
    state = _held("schema_invalid", "rule_spent", {})
    del state["format_retakes"]
    assert "format_retakes" not in fr.fixed_release_patch(state, AT, SHA)


def test_released_before_compares_the_exact_pair():
    state: Any = {"format_released": [{"issue_url": URL, "closing_commit": None, "at": AT}]}
    assert not fr.released_before(state, URL, SHA)
    assert fr.released_before(state, URL, None)
    state = {"format_released": [{"issue_url": URL, "closing_commit": SHA, "at": AT}]}
    assert fr.released_before(state, URL, SHA)
    assert not fr.released_before(state, "https://github.com/acme/shop/issues/4", SHA)
    assert not fr.released_before({"id": "GH-1"}, URL, SHA)
    none_in_memory: Any = {"id": "GH-1", "format_released": None}
    assert not fr.released_before(none_in_memory, URL, SHA)


def test_release_patch_does_not_touch_format_released():
    state = _held("schema_invalid", "rule_spent", {"schema_invalid": 2}, format_released=[])
    assert "format_released" not in fr.release_patch(state)


def test_a_count_after_a_release_does_not_raise_on_cleared_keys():
    state = _held("schema_invalid", "rule_spent", {"schema_invalid": 2})
    entries = [
        {"kind": "failure", "value": "schema", "section": "S", "rule": "schema_invalid"},
        {"kind": "escalation", "value": "failure"},
    ]
    cleared = task.apply(state, fr.release_patch(state))
    counted = task.apply(cleared, fr.count_patch(cleared, entries))
    assert counted["format_retakes"] == {"schema_invalid": 1}
    emptied = _held(
        "schema_invalid",
        "task_spent",
        {"unmapped": 2, "output_missing": 2, "part_over_budget": 2, "schema_invalid": 0},
    )
    after = task.apply(emptied, fr.fixed_release_patch(emptied, AT, SHA))
    assert after["format_retakes"] is None
    again = task.apply(after, fr.count_patch(after, entries))
    assert fr.limit(again["format_retakes"], ["schema_invalid"]) == 1
    assert fr.hold_patch(after, ["schema_invalid"], URL, AT, entries) == {}
