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

_STAMP = "2026-11-06T12:00:00+00:00"


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
    due = state(task_head=HEAD, owner_rules_seen=HEAD)
    assert rules.full_checks_due(due)
    assert rules.after_integrate(due) == "verify"
    # Before the copy review, the final validation and QA Performance, whatever is convened.
    convened = state(task_head=HEAD, owner_rules_seen=HEAD, performance=[{"name": "x"}])
    assert rules.after_integrate(convened) == "verify"


# --- ci_unavailable (GH-462) ------------------------------------------------------------------


def test_ci_unavailable_escalates_with_class_ci_and_writes_no_record_and_no_return():
    patch = rules.ci_unavailable(
        "no_run", HEAD, "no CI run on agentic-ci/GH-1 after 25 min", at=_STAMP
    )
    assert patch["escalation_class"] == "ci"
    assert patch["status"] == "waiting_on_human"
    assert patch["current_role"] == roles.DEVELOPER.name
    assert patch["pause_log"] == [{"kind": "escalation", "value": "ci", "at": _STAMP}]
    assert "(no_run)" in patch["escalated"] and HEAD[:7] in patch["escalated"]
    assert HEAD not in patch["escalated"]
    assert "no CI run on agentic-ci/GH-1 after 25 min" in patch["escalated"]
    assert "full_checks" not in patch and "subtasks" not in patch and "findings" not in patch


def test_a_state_escalated_ci_with_no_passed_record_is_due_again_so_a_resume_re_enters():
    stopped = apply(
        state(task_head=HEAD, owner_rules_seen=HEAD),
        rules.ci_unavailable("publish_failed", HEAD, "push failed", at=_STAMP),
    )
    assert stopped["escalation_class"] == "ci"
    assert rules.full_checks_due(stopped)
    # Rejected input: a passed record of the same head is the one thing that ends the node.
    assert not rules.full_checks_due({**stopped, "full_checks": record()})


def test_the_fan_in_still_dispatches_and_stops_before_it_considers_the_node():
    open_unit = state(task_head=HEAD, subtasks={"api": {"status": "returned"}})
    assert rules.after_integrate(open_unit) == "pipeline"
    delivered_unit = state(task_head=HEAD, subtasks={"api": {"status": "delivered"}})
    assert rules.after_integrate(delivered_unit) == "pipeline"
    assert rules.after_integrate(state(task_head=HEAD, escalated="stop")) is None


def test_a_passed_record_of_the_same_head_is_not_due_and_deliver_follows():
    done = state(task_head=HEAD, owner_rules_seen=HEAD, full_checks=record())
    assert not rules.full_checks_due(done)
    assert rules.after_integrate(done) == "deliver"
    # The same commit at another length is the same head.
    assert not rules.full_checks_due(state(task_head=HEAD[:12], full_checks=record()))


def test_a_red_record_or_one_of_another_head_is_due_again():
    assert rules.full_checks_due(state(task_head=HEAD, full_checks=record(passed=False)))
    assert rules.full_checks_due(state(task_head=MOVED, full_checks=record(HEAD)))


def test_a_head_moved_by_a_landing_pass_is_due_again():
    landing = [{"head": HEAD, "step": "sync", "by": "landing"}]
    moved = state(
        task_head=MOVED, owner_rules_seen=MOVED, full_checks=record(HEAD), landing=landing
    )
    assert rules.after_integrate(moved) == "verify"


def test_after_verify_always_returns_to_the_fan_in_unless_escalated():
    assert rules.after_verify(state()) == "integrate"
    assert rules.after_verify(state(escalated="stuck")) is None


def test_files_owed_to_the_docs_keep_the_route_in_verify_and_a_resume_goes_through_the_fan_in():
    owing = state(
        task_head=HEAD,
        full_checks={"head": HEAD, "passed": True},
        owner_rules_seen=HEAD,
        pending_close_docs={"parent": HEAD, "definitions": []},
    )
    assert rules.verifiers_due(owing) == ()
    assert rules.after_integrate(owing) == "verify"
    assert rules.resume_target(owing) == "integrate"
    assert rules.after_integrate({**owing, "pending_close_docs": None}) == "deliver"


# --- the red run returns the integration ------------------------------------------------------


RED = record(passed=False, version=2, failed=("types",))


def test_a_red_run_reopens_the_integration_with_one_blocking_row_per_failed_check():
    patch = rules.full_checks_return(state(task_head=HEAD), RED, 3, at=_STAMP)
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
    patch = rules.full_checks_return(only, RED, 3, at=_STAMP)
    assert patch["subtasks"]["api"]["returned_by"] == "full_checks"
    assert patch["findings"][0]["subtask"] == "api"
    assert patch["changes_requested"] == {roles.pair_for("api").key: 1}


def test_a_run_that_failed_before_any_check_names_the_step_it_stopped_at():
    stopped = record(passed=False, failed_step="setup")
    stopped["results"] = []
    patch = rules.full_checks_return(state(), stopped, 3, at=_STAMP)
    assert [f["location"] for f in patch["findings"]] == ["full_checks:setup"]


def test_the_return_counts_on_the_pair_and_escalates_at_its_limit():
    spent = state(changes_requested={roles.pair_for("integration").key: 2})
    patch = rules.full_checks_return(spent, RED, 2, at=_STAMP)
    assert patch["escalation_class"] == "returns"
    assert "full_checks" in patch["escalated"]
    assert patch["status"] == "waiting_on_human"
    assert "changes_requested" not in patch
    # The rows and the reopening are still recorded: a return is never without its rows.
    assert patch["findings"] and patch["subtasks"]["integration"]["returned_by"] == "full_checks"


def test_the_return_asks_the_decision_for_the_modes_pair(monkeypatch):
    asked = []
    monkeypatch.setattr(rules, "on_return_limit", lambda *a, **k: asked.append(a[1]) or "escalate")
    patch = rules.full_checks_return(state(), RED, 2, at=_STAMP, mode="single")
    assert patch["escalation_class"] == "returns" and "changes_requested" not in patch
    assert asked == [rules.full_checks_pair("integration", "single")]


def test_the_returned_section_is_the_full_checks_one():
    returned = state(subtasks={"integration": {"status": "returned", "returned_by": "full_checks"}})
    assert rules.return_section(returned, "integration") == "Full checks"


# --- the fold writer ---------------------------------------------------------------------------


def statuses(current):
    return [(row["version"], row["status"]) for row in rules.findings_rows(current)]


def test_red_then_green_closes_the_rows_and_a_later_red_reopens_them():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3, at=_STAMP))
    assert statuses(red) == [(2, "open")]
    green = apply(red, {"full_checks": record(MOVED, version=3)})
    assert rules.full_checks_green_version(green) == 3
    assert statuses(green) == [(2, "fixed")]
    later = apply(
        green,
        rules.full_checks_return(green, record(LATER, False, 4, ("types",)), 3, at=_STAMP),
    )
    assert rules.full_checks_green_version(later) == 0
    assert statuses(later) == [(2, "open"), (4, "open")]


def test_an_approving_review_alone_leaves_the_rows_open():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3, at=_STAMP))
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
    red = apply(red, rules.full_checks_return(red, RED, 3, at=_STAMP))
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
    patch = rules.full_checks_heal(state(task_head=HEAD), MOVED, "stale", at=_STAMP)
    assert patch["task_head"] == MOVED and patch["full_checks_healed"] == MOVED
    assert "escalated" not in patch
    healed = apply(state(task_head=HEAD, full_checks=record()), patch)
    assert rules.full_checks_due(healed)
    assert rules.after_deliver(healed) == "integrate"


def test_a_second_refusal_on_the_same_head_escalates_with_the_system_delivery_class():
    healed = apply(
        state(task_head=HEAD),
        rules.full_checks_heal(state(), MOVED, "missing", at=_STAMP),
    )
    patch = rules.full_checks_heal(healed, MOVED, "stale", at=_STAMP)
    assert patch["escalation_class"] == "system_delivery"
    assert "stale" in patch["escalated"] and MOVED[:7] in patch["escalated"]
    assert patch["status"] == "waiting_on_human"
    assert "full_checks_healed" not in patch
    assert rules.after_deliver(apply(healed, patch)) is None


def test_a_new_head_routes_again():
    healed = apply(state(), rules.full_checks_heal(state(), MOVED, "missing", at=_STAMP))
    again = rules.full_checks_heal(healed, LATER, "stale", at=_STAMP)
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


def test_tier_section_says_the_full_suite_reuses_a_pass_only_on_the_developers_own_tree():
    assert "reuses your pass only when the integrated tree is yours" in " ".join(
        tier_section(["tests"], ["e2e"]).split()
    )


# --- a deferred row is not a failure (GH-453) -----------------------------------------------------


def deferred_record(version=3, head=HEAD, failed=()):
    out = record(head, version=version)
    out["results"].append({"name": "web-e2e", "passed": False, "deferred": True})
    out["deferred"] = ["web-e2e"]
    out["passed"] = not failed
    for name in failed:
        out["results"].append({"name": name, "passed": False})
    return out


def test_a_record_whose_only_non_passing_row_is_deferred_returns_nothing():
    patch = rules.full_checks_return(state(task_head=HEAD), deferred_record(), 3, at=_STAMP)
    assert patch == {"full_checks": deferred_record()}


def test_a_deferred_row_next_to_a_red_one_returns_only_the_red_one():
    mixed = deferred_record(failed=("types",))
    patch = rules.full_checks_return(state(task_head=HEAD), mixed, 3, at=_STAMP)
    assert [f["location"] for f in patch["findings"]] == ["full_checks:types"]
    assert "web-e2e" not in patch["subtasks"]["integration"]["reason"]


def test_an_open_row_stays_open_under_a_record_with_a_deferred_row():
    red = state(task_head=HEAD)
    red = apply(red, rules.full_checks_return(red, RED, 3, at=_STAMP))
    assert statuses(red) == [(2, "open")]
    deferred = apply(
        red,
        rules.full_checks_return(red, deferred_record(version=3), 3, at=_STAMP),
    )
    assert rules.full_checks_green_version(deferred) == 0
    assert statuses(deferred) == [(2, "open")]
    # The same state under a plain green record does close it: the guard is the deferral.
    plain = apply(red, {"full_checks": record(MOVED, version=3)})
    assert statuses(plain) == [(2, "fixed")]


def test_next_section_version_is_one_without_a_kept_run():
    assert rules.next_section_version({}, "Full checks") == 1
    assert rules.next_section_version({"sections": {"Full checks": "x"}}, "Full checks") == 1


def test_next_section_version_goes_past_the_highest_key_and_ignores_gaps():
    sections = {"Full checks": "x", "Full checks (v1)": "a", "Full checks (v3)": "b"}
    assert rules.next_section_version({"sections": sections}, "Full checks") == 4


def test_next_section_version_ignores_other_sections_and_look_alike_keys():
    sections = {
        "QA (v7)": "a",
        "Full checks (v2) extra": "b",
        "Full checks (vX)": "c",
        "Full checks (v1)": "d",
    }
    assert rules.next_section_version({"sections": sections}, "Full checks") == 2


def test_next_section_version_reads_keys_never_contents():
    sections = {"Full checks": "## Full checks (v99)\n\nFull checks (v99)", "Full checks (v1)": "x"}
    assert rules.next_section_version({"sections": sections}, "Full checks") == 2
