"""The pure rules of the full-tier run (GH-358): when the `full_checks` node is due, where it
sends the task, how a red run returns the integration, when a green one closes its rows, what
`deliver` may push and how a refusal heals or escalates."""

import pytest

from agentic.core.process.domain import document, roles, rules
from agentic.core.process.domain.presubmit import tier_section
from agentic.core.process.domain.task import apply

HEAD = "a" * 40
MOVED = "b" * 40
LATER = "c" * 40


def entry(name, passed=True):
    if passed:
        return {"name": name, "passed": True}
    return {"name": name, "passed": False}


def record(head=HEAD, passed=True, version=1, failed=(), failed_step=None):
    names = dict.fromkeys(["tests", "lint", *failed])
    out = {
        "version": version,
        "head": head,
        "passed": passed,
        "duration_s": 12.5,
        "declared": {"tests": "pytest", "lint": "ruff"},
        "results": [entry(n, n not in failed) for n in names],
        "witness": {"passed": True},
    }
    if failed_step:
        out["failed_step"] = failed_step
    return out


def state(**more):
    base = {
        "type": "feature",
        "flags": {},
        "subtasks": {"integration": {"status": "approved"}, "api": {"status": "approved"}},
        "runs": [],
    }
    return {**base, **more}


# --- full_checks_due and the order after the fan-in -----------------------------------------


def test_a_state_without_task_head_never_visits_the_node():
    assert not rules.full_checks_due(state())
    assert not rules.full_checks_due(state(full_checks=record(passed=False)))
    assert rules.after_integrate(state()) == "deliver"


def test_with_task_head_and_no_record_the_node_comes_before_everything_after_the_fan_in():
    due = state(task_head=HEAD)
    assert rules.full_checks_due(due)
    assert rules.after_integrate(due) == "full_checks"
    # Before the copy review, the final validation and QA Performance, whatever is convened.
    convened = state(task_head=HEAD, performance=[{"name": "x"}])
    assert rules.after_integrate(convened) == "full_checks"


def test_the_fan_in_still_dispatches_and_stops_before_it_considers_the_node():
    open_unit = state(task_head=HEAD, subtasks={"api": {"status": "returned"}})
    assert rules.after_integrate(open_unit) == "pipeline"
    delivered_unit = state(task_head=HEAD, subtasks={"api": {"status": "delivered"}})
    assert rules.after_integrate(delivered_unit) == "pipeline"
    assert rules.after_integrate(state(task_head=HEAD, escalated="stop")) is None


def test_a_passed_record_of_the_same_head_is_not_due_and_deliver_follows():
    done = state(task_head=HEAD, full_checks=record())
    assert not rules.full_checks_due(done)
    assert rules.after_integrate(done) == "deliver"
    # The same commit at another length is the same head.
    assert not rules.full_checks_due(state(task_head=HEAD[:12], full_checks=record()))


def test_a_red_record_or_one_of_another_head_is_due_again():
    assert rules.full_checks_due(state(task_head=HEAD, full_checks=record(passed=False)))
    assert rules.full_checks_due(state(task_head=MOVED, full_checks=record(HEAD)))


def test_a_head_moved_by_a_landing_pass_is_due_again():
    landing = [{"head": HEAD, "step": "sync", "by": "landing"}]
    moved = state(task_head=MOVED, full_checks=record(HEAD), landing=landing)
    assert rules.after_integrate(moved) == "full_checks"


def test_after_full_checks_always_returns_to_the_fan_in_unless_escalated():
    assert rules.after_full_checks(state()) == "integrate"
    assert rules.after_full_checks(state(escalated="stuck")) is None


# --- the red run returns the integration ------------------------------------------------------


RED = record(passed=False, version=2, failed=("types",))


def test_a_red_run_reopens_the_integration_with_one_blocking_row_per_failed_check():
    patch = rules.full_checks_return(state(task_head=HEAD), RED, 3)
    assert patch["subtasks"]["integration"]["status"] == "returned"
    assert patch["subtasks"]["integration"]["returned_by"] == "full_checks"
    assert "types" in patch["subtasks"]["integration"]["reason"]
    assert patch["findings"] == [
        {
            "subtask": "integration",
            "version": 2,
            "location": "full_checks:types",
            "claim": f"types failed on the integrated head {HEAD[:7]}",
            "severity": "blocking",
            "scope": "plan",
            "category": "process-suite-red",
            "status": "open",
        }
    ]
    assert patch["changes_requested"] == {roles.pair_for("integration").key: 1}
    assert patch["change_history"][0]["category"] == "process-suite-red"
    assert patch["full_checks"] == RED
    assert "escalated" not in patch


def test_a_red_run_of_a_single_subtask_task_reopens_that_subtask():
    only = state(subtasks={"api": {"status": "approved"}})
    patch = rules.full_checks_return(only, RED, 3)
    assert patch["subtasks"]["api"]["returned_by"] == "full_checks"
    assert patch["findings"][0]["subtask"] == "api"
    assert patch["changes_requested"] == {roles.pair_for("api").key: 1}


def test_a_run_that_failed_before_any_check_names_the_step_it_stopped_at():
    stopped = record(passed=False, failed_step="setup")
    stopped["results"] = []
    patch = rules.full_checks_return(state(), stopped, 3)
    assert [f["location"] for f in patch["findings"]] == ["full_checks:setup"]


def test_the_return_counts_on_the_pair_and_escalates_at_its_limit():
    spent = state(changes_requested={roles.pair_for("integration").key: 2})
    patch = rules.full_checks_return(spent, RED, 2)
    assert patch["escalation_class"] == "returns"
    assert "full_checks" in patch["escalated"]
    assert patch["status"] == "waiting_on_human"
    assert "changes_requested" not in patch
    # The rows and the reopening are still recorded: a return is never without its rows.
    assert patch["findings"] and patch["subtasks"]["integration"]["returned_by"] == "full_checks"


def test_the_returned_section_is_the_full_checks_one():
    returned = state(subtasks={"integration": {"status": "returned", "returned_by": "full_checks"}})
    assert rules.return_section(returned, "integration") == "Full checks"


# --- the fold writer ---------------------------------------------------------------------------


def statuses(current):
    return [(row["version"], row["status"]) for row in rules.findings_rows(current)]


def test_red_then_green_closes_the_rows_and_a_later_red_reopens_them():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3))
    assert statuses(red) == [(2, "open")]
    green = apply(red, {"full_checks": record(MOVED, version=3)})
    assert rules.full_checks_green_version(green) == 3
    assert statuses(green) == [(2, "fixed")]
    later = apply(green, rules.full_checks_return(green, record(LATER, False, 4, ("types",)), 3))
    assert rules.full_checks_green_version(later) == 0
    assert statuses(later) == [(2, "open"), (4, "open")]


def test_an_approving_review_alone_leaves_the_rows_open():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3))
    approved = apply(
        red,
        {
            "approval_settled": [
                {
                    "subtask": "integration",
                    "location": "full_checks:types",
                    "claim": f"types failed on the integrated head {HEAD[:7]}",
                    "version": 9,
                }
            ],
            "subtasks": {"integration": {**red["subtasks"]["integration"], "status": "approved"}},
        },
    )
    assert statuses(approved) == [(2, "open")]


def test_a_reviewers_settled_list_never_closes_a_full_checks_row():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3))
    claimed = apply(red, {"settled_findings": ["full_checks:types"]})
    assert statuses(claimed) == [(2, "open")]
    green = apply(claimed, {"full_checks": record(MOVED, version=3)})
    assert statuses(green) == [(2, "fixed")]


def test_the_writer_settles_only_its_own_locations_and_only_from_a_lower_version():
    row = {"location": "Full_checks:  Types", "version": 2}
    assert rules.full_checks_settles(row, 3)
    assert not rules.full_checks_settles(row, 2)
    assert not rules.full_checks_settles({"location": "src/a.py:3", "version": 1}, 9)
    assert not rules.full_checks_settles({"location": "QA Performance", "version": 1}, 9)
    assert rules.full_checks_green_version(state(full_checks=record(passed=False))) == 0
    assert rules.full_checks_green_version(state()) == 0


# --- what deliver may push -------------------------------------------------------------------


def test_each_cover_verdict():
    assert rules.full_checks_cover(None, False) == "missing"
    assert rules.full_checks_cover(record(passed=False), False) == "missing"
    assert rules.full_checks_cover(record(passed=False), True) == "missing"
    assert rules.full_checks_cover(record(), True) == "stale"
    assert rules.full_checks_cover(record(), False) == "ok"


def test_the_first_refusal_on_a_head_heals_and_routes_to_the_fan_in():
    patch = rules.full_checks_heal(state(task_head=HEAD), MOVED, "stale")
    assert patch["task_head"] == MOVED and patch["full_checks_healed"] == MOVED
    assert "escalated" not in patch
    healed = apply(state(task_head=HEAD, full_checks=record()), patch)
    assert rules.full_checks_due(healed)
    assert rules.after_deliver(healed) == "integrate"


def test_a_second_refusal_on_the_same_head_escalates_with_the_system_delivery_class():
    healed = apply(state(task_head=HEAD), rules.full_checks_heal(state(), MOVED, "missing"))
    patch = rules.full_checks_heal(healed, MOVED, "stale")
    assert patch["escalation_class"] == "system_delivery"
    assert "stale" in patch["escalated"] and MOVED[:7] in patch["escalated"]
    assert patch["status"] == "waiting_on_human"
    assert "full_checks_healed" not in patch
    assert rules.after_deliver(apply(healed, patch)) is None


def test_a_new_head_routes_again():
    healed = apply(state(), rules.full_checks_heal(state(), MOVED, "missing"))
    again = rules.full_checks_heal(healed, LATER, "stale")
    assert "escalated" not in again and again["full_checks_healed"] == LATER


def test_after_deliver_is_none_when_nothing_is_due_or_nothing_was_healed():
    assert rules.after_deliver(state()) is None
    assert rules.after_deliver(state(task_head=HEAD, full_checks=record())) is None
    assert rules.after_deliver(state(task_head=HEAD)) is None  # due, but no refusal wrote it
    done = state(task_head=HEAD, full_checks=record(), full_checks_healed=HEAD)
    assert rules.after_deliver(done) is None


# --- the document ------------------------------------------------------------------------------


def _doc(**more):
    return {"id": "GH-1", "title": "t", "description": "d", "sections": {}, **more}


def test_the_three_keys_round_trip_through_the_document():
    keys = {
        "task_head": HEAD,
        "full_checks": record(MOVED, False, 2, ("types",)),
        "full_checks_healed": MOVED,
    }
    text = document.render(_doc(**keys))
    parsed = document.parse(text)
    for key, value in keys.items():
        assert parsed[key] == value
    assert len([ln for ln in text.split("\n") if ln.startswith("full_checks:")]) == 1


def test_a_document_without_them_renders_byte_identical():
    plain = document.render(_doc())
    assert "task_head" not in plain and "full_checks" not in plain
    assert plain == document.render(_doc(full_checks={}))


def test_the_record_replaces_in_place_through_record_closure():
    raw = document.render(_doc(task_head=HEAD, full_checks=record(HEAD, False, 1, ("types",))))
    again = document.record_closure(raw, {"full_checks": record(MOVED, True, 2)})
    assert document.parse(again)["full_checks"] == record(MOVED, True, 2)
    assert again.count("\nfull_checks:") == 1


# --- the tier section ---------------------------------------------------------------------------


def test_tier_section_names_what_runs_where():
    text = tier_section(["tests", "lint"], ["e2e", "vitest"])
    assert text.startswith("###")
    assert "tests, lint" in text and "e2e, vitest" in text
    assert "once" in text and "integrated head" in text
    assert "must not run the full suite" in text
    assert "supersedes" in text


def test_tier_section_without_a_rest_says_the_full_run_is_the_same_checks_without_affected_forms():
    text = tier_section(["tests", "lint"], [])
    assert "tests, lint" in text and "without affected forms" in text
    assert "None" not in text and "()" not in text


@pytest.mark.parametrize("fast", [[], ["tests"]])
def test_tier_section_is_deterministic(fast):
    assert tier_section(fast, ["e2e"]) == tier_section(list(fast), ["e2e"])
