"""GH-371, subtask landing-escalate: a required check red on the head and also red on the base
branch ends the landing in an escalation, with no model step. Pure: `landing.landing_step`
and `ci.red_on_base`."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.domain import adr_numbers, ci, landing
from tests.core.process.domain.test_landing import _Comment, _facts, _spent


def _red(name: str) -> ci.CiCheck:
    return ci.CiCheck(name, "COMPLETED", "FAILURE", f"https://ci/{name}")


def _verdict(*names: str) -> ci.CiVerdict:
    return ci.CiVerdict("red", tuple(_red(n) for n in names), ())


def _step(on_base: tuple[str, ...], failing: tuple[str, ...], **spent):
    facts = _facts(
        ci_required=("web", "e2e"), ci_verdict=_verdict(*failing), ci_red_on_base=on_base
    )
    return landing.landing_step("ci_failed", facts, _spent(**spent), ("api",))


def test_every_failing_name_red_on_the_base_escalates():
    assert _step(("web", "e2e"), ("web", "e2e")).kind == "escalate"


def test_a_name_red_on_the_base_only_does_not_keep_the_escalation_from_the_failing_ones():
    assert _step(("web", "e2e"), ("web",)).kind == "escalate"


@pytest.mark.parametrize(
    ("spent", "expected"),
    [({}, "rerun"), ({"reruns_on_head": 1}, "fix")],
)
def test_only_some_failing_names_red_on_the_base_goes_the_old_way(spent, expected):
    assert _step(("web",), ("web", "e2e"), **spent).kind == expected


@pytest.mark.parametrize(
    ("spent", "expected"),
    [({}, "rerun"), ({"reruns_on_head": 1}, "fix")],
)
def test_nothing_red_on_the_base_goes_the_old_way(spent, expected):
    assert _step((), ("web",), **spent).kind == expected


def test_a_red_on_the_base_without_a_verdict_to_cover_goes_the_old_way():
    facts = _facts(ci_required=("web",), ci_red_on_base=("web",))
    assert landing.landing_step("ci_failed", facts, _spent(), ()).kind == "rerun"


def _matrix_verdict(expansion: str, declared: str) -> ci.CiVerdict:
    run = _red(expansion)
    return ci.CiVerdict("red", (run,), (), red=((declared, (run,)),))


def test_a_matrix_red_on_the_head_and_its_id_red_on_the_base_escalates():
    facts = _facts(
        ci_required=("pytest",),
        ci_verdict=_matrix_verdict("pytest (a)", "pytest"),
        ci_red_on_base=("pytest",),
    )

    step = landing.landing_step("ci_failed", facts, _spent(), ("api",))

    assert step.kind == "escalate"


def test_an_expanded_run_name_on_the_base_list_is_not_the_declared_name():
    facts = _facts(
        ci_required=("pytest",),
        ci_verdict=_matrix_verdict("pytest (a)", "pytest"),
        ci_red_on_base=("pytest (a)",),
    )

    assert landing.landing_step("ci_failed", facts, _spent(), ("api",)).kind == "rerun"


def test_the_escalation_spends_no_model_and_its_child_is_a_checks_one():
    assert "escalate" in landing.LandingStepKind.__args__  # type: ignore[attr-defined]
    assert landing.run_kind_of("escalate", "ci_failed") == "land_checks"
    assert landing.run_kind_admits("land_checks", "escalate", "ci_failed")


def test_a_spent_budget_does_not_turn_the_escalation_into_a_pass():
    step = _step(("web",), ("web",), passes=landing.LANDING_PASSES_MAX, reruns_on_head=1)
    assert step.kind == "escalate" and step.refusal == "ci_failed"


def _report(*checks: ci.CiCheck) -> ci.CiReport:
    return ci.CiReport("a" * 40, "master", "b" * 40, None, checks)


def test_red_on_base_names_the_failing_head_names_that_also_fail_on_the_base():
    head = _report(_red("web"), _red("e2e"), ci.CiCheck("test", "COMPLETED", "SUCCESS", "u"))
    base = _report(_red("web"), ci.CiCheck("e2e", "COMPLETED", "SUCCESS", "u"), _red("test"))
    assert ci.red_on_base(head, base, ("web", "e2e", "test")) == ("web",)


def test_red_on_base_without_a_base_report_is_empty():
    assert ci.red_on_base(_report(_red("web")), None, ("web",)) == ()


def test_red_on_base_ignores_a_pending_base_run_and_an_undeclared_name():
    pending = ci.CiCheck("web", "IN_PROGRESS", None, "u")
    assert ci.red_on_base(_report(_red("web")), _report(pending), ("web",)) == ()
    assert ci.red_on_base(_report(_red("web")), _report(_red("web")), ("e2e",)) == ()


# -- GH-544: an owner's decision on an `escalate_adr` escalation ------------------------------

H1, H2 = "a" * 40, "c" * 40
T0, T1, T2, T3 = (f"2026-11-07T0{n}:00:00+00:00" for n in range(4))
REFUSAL = "adr_number_taken"
MOVES = (("docs/adr/0070-x.md", "docs/adr/0074-x.md"),)
AMBIGUOUS = adr_numbers.Ambiguous("unclassified_token", "app/main.py:228: (docs/adr/0070)", MOVES)
NO_MOVES = adr_numbers.Ambiguous("duplicate_own_number", "0070 twice")


def _asked(head=H1, **over) -> dict:
    return {"head": head, "step": "escalate_adr", "refusal": REFUSAL, "at": T0, **over}


def _asked_resolved(head=H1, **over) -> dict:
    return _asked(head, resolved_at=T1, **over)


def _released(head=H1, at=T2, **over) -> dict:
    return _asked_resolved(head, released_at=at, **over)


def _applied(outcome="applied", head=H1, decision_at=T2, **over) -> dict:
    return {
        "head": head,
        "step": "apply_adr_decision",
        "refusal": REFUSAL,
        "at": T3,
        "decision_at": decision_at,
        "outcome": outcome,
        "moves": [list(m) for m in MOVES],
        "bounds": landing.ADR_EDIT_BOUNDS,
        **over,
    }


def _adr_step(collision=AMBIGUOUS, spent=None, **keywords):
    facts = _facts(adr_collision=collision)
    return landing.landing_step(REFUSAL, facts, spent or _spent(), (), **keywords)


def test_the_step_is_a_model_step_that_a_checks_child_does_not_admit():
    assert "apply_adr_decision" in get_args(landing.LandingStepKind)
    assert landing.run_kind_of("apply_adr_decision", REFUSAL) == "land_model"
    assert not landing.run_kind_admits("land_checks", "apply_adr_decision", REFUSAL)
    assert landing.run_kind_admits("land_model", "apply_adr_decision", REFUSAL)


def test_without_a_decision_an_ambiguous_collision_is_asked_once_then_none():
    assert (
        landing.adr_decision_due([_asked_resolved()], H1, AMBIGUOUS, [_asked_resolved()]) is False
    )
    assert _adr_step(adr_decision_due=False).kind == "escalate_adr"
    assert _adr_step(adr_decision_due=False, adr_escalated=True).kind == "none"


def test_a_released_escalation_gives_the_apply_step_never_a_pass_or_a_restart():
    step = _adr_step(adr_decision_due=True, adr_escalated=True)
    assert step == landing.LandingStep("apply_adr_decision", REFUSAL)
    spent = _spent(passes=landing.LANDING_PASSES_MAX, syncs=landing.LANDING_SYNCS_MAX)
    assert _adr_step(spent=spent, adr_decision_due=True).kind == "apply_adr_decision"


def test_a_clean_renaming_under_its_cap_is_still_a_renumber_before_a_decision():
    renaming = adr_numbers.Renaming(MOVES, {})
    assert _adr_step(renaming, adr_decision_due=True).kind == "renumber"
    capped = _spent(renumbers=landing.LANDING_RENUMBERS_MAX)
    assert _adr_step(renaming, capped, adr_decision_due=True).kind == "apply_adr_decision"


def test_a_legacy_released_entry_without_moves_is_a_decision_due():
    legacy = _released()  # a GH-412 entry: no moves, no outcome, no decision_at
    assert "moves" not in legacy
    assert landing.adr_decision_due([legacy], H1, AMBIGUOUS, [legacy]) is True


@pytest.mark.parametrize(
    ("entries", "collision"),
    [
        ([_asked_resolved()], AMBIGUOUS),
        ([_released()], NO_MOVES),
        ([_released()], adr_numbers.Renaming(MOVES, {})),
        ([_released()], None),
        ([_released(), _applied()], AMBIGUOUS),
        ([_released(head=H2)], AMBIGUOUS),
    ],
    ids=[
        "not-released",
        "no-moves",
        "clean-renaming",
        "no-collision",
        "already-applied",
        "other-head",
    ],
)
def test_no_decision_is_due_without_a_release_or_moves(entries, collision):
    assert landing.adr_decision_due(entries, H1, collision, entries) is False


def _question(head, released_at, moves=MOVES) -> dict:
    return _released(head, released_at, moves=[list(m) for m in moves])


def _archived_spent(head, key_moves, *outcomes) -> list[dict]:
    """The question of `key_moves` on `head`, then one apply per outcome, all archived."""
    asked = _question(head, T0, key_moves)
    return [asked, *(_applied(o, head, decision_at=T0) for o in outcomes)]


def test_the_cap_is_spent_per_collision_key_not_per_task():
    live = [_question(H1, T2)]
    other = (("docs/adr/0071-y.md", "docs/adr/0075-y.md"),)
    spent = _archived_spent(H1, MOVES, "checks_red", "applied")
    one = _archived_spent(H1, MOVES, "checks_red")
    due = landing.adr_decision_due
    # The key's own slots: two spent is the cap, one is not.
    assert due(live, H1, AMBIGUOUS, [*spent, *live]) is False
    assert due(live, H1, AMBIGUOUS, [*one, *live]) is True
    # The same two on another head or on other moves leave this key free.
    assert due(live, H1, AMBIGUOUS, [*_archived_spent(H2, MOVES, "applied", "applied"), *live])
    elsewhere = _archived_spent(H1, other, "applied", "applied")
    assert due(live, H1, AMBIGUOUS, [*elsewhere, *live]) is True


def test_adr_asked_is_true_only_for_a_resolved_question_after_the_last_decision():
    assert not landing.adr_asked([], H1, REFUSAL)
    assert not landing.adr_asked([_asked()], H1, REFUSAL)
    assert landing.adr_asked([_asked_resolved()], H1, REFUSAL)
    assert not landing.adr_asked([_asked_resolved()], H2, REFUSAL)
    assert not landing.adr_asked([_asked_resolved()], H1, "merge_conflict")
    after = [_released(), _applied("role_failed")]
    assert not landing.adr_asked(after, H1, REFUSAL)
    assert not landing.adr_asked([*after, _asked()], H1, REFUSAL)
    assert landing.adr_asked([*after, _asked_resolved(at=T3)], H1, REFUSAL)


def test_a_decision_on_another_head_does_not_unask_this_one():
    assert landing.adr_asked([_applied(head=H2), _asked_resolved()], H1, REFUSAL)


def test_a_failed_decision_then_its_new_question_resolved_is_none_again():
    entries = [_released(), _applied("out_of_bounds"), _asked_resolved()]
    asked = landing.adr_asked(entries, H1, REFUSAL)
    assert _adr_step(adr_decision_due=False, adr_escalated=asked).kind == "none"
    unresolved = [*entries[:-1], _asked()]
    asked = landing.adr_asked(unresolved, H1, REFUSAL)
    assert _adr_step(adr_decision_due=False, adr_escalated=asked).kind == "escalate_adr"


@pytest.mark.parametrize(
    "outcome",
    [
        "out_of_bounds",
        "role_failed",
        "checks_red",
        "still_colliding",
        "stale_moves",
        "git_failed",
        "ambiguous",
    ],
)
def test_each_failed_outcome_followed_by_its_question_asks_again(outcome):
    entries = [_released(), _applied(outcome)]
    due = landing.adr_decision_due(entries, H1, AMBIGUOUS, entries)
    asked = landing.adr_asked(entries, H1, REFUSAL)
    assert due is False and asked is False
    assert _adr_step(adr_decision_due=due, adr_escalated=asked).kind == "escalate_adr"


def test_an_applied_decision_with_a_still_ambiguous_new_head_asks_then_waits():
    entries = [_released(), _applied(head_after=H2)]
    asked = landing.adr_asked(entries, H2, REFUSAL)
    assert _adr_step(adr_decision_due=False, adr_escalated=asked).kind == "escalate_adr"
    entries.append(_asked_resolved(H2))
    asked = landing.adr_asked(entries, H2, REFUSAL)
    assert _adr_step(adr_decision_due=False, adr_escalated=asked).kind == "none"


def test_the_escalate_adr_case_of_already_dispatched_reads_adr_asked():
    assert not landing.already_dispatched([_asked()], H1, "escalate_adr", None)
    assert landing.already_dispatched([_asked_resolved()], H1, "escalate_adr", None)
    after = [_released(), _applied("checks_red")]
    assert not landing.already_dispatched(after, H1, "escalate_adr", None)
    assert landing.already_dispatched([*after, _asked_resolved()], H1, "escalate_adr", None)


def test_the_apply_step_is_taken_only_by_the_same_head_and_decision():
    entries = [_released(), _applied(decision_at=T2)]
    taken = landing.already_dispatched
    assert taken(entries, H1, "apply_adr_decision", None, decision_at=T2)
    assert not taken(entries, H1, "apply_adr_decision", None, decision_at=T3)
    assert not taken(entries, H2, "apply_adr_decision", None, decision_at=T2)
    assert not taken(entries, H1, "apply_adr_decision", None)
    legacy = [{"head": H1, "step": "apply_adr_decision"}]
    assert not taken(legacy, H1, "apply_adr_decision", None, decision_at=T2)


def test_a_new_decision_on_the_same_head_is_due_again_after_a_stale_one():
    asked_again = _released(at="2026-11-07T05:00:00+00:00")
    entries = [_released(at=T2), _applied("stale_moves", decision_at=T2), asked_again]
    assert landing.adr_decision_due(entries, H1, AMBIGUOUS, entries) is True


def _escalated_doc(*entries, escalated="adr_number_taken") -> dict:
    return {"escalated": escalated, "status": "waiting_on_human", "landing": list(entries)}


def test_the_escalation_entry_is_the_last_live_resolved_escalate_adr_of_an_escalated_task():
    entry = _asked_resolved()
    assert landing.adr_escalation_entry(_escalated_doc(entry)) == entry
    assert landing.adr_escalation_entry(_escalated_doc(entry, escalated=None)) is None
    assert landing.adr_escalation_entry(_escalated_doc(_asked())) is None
    assert landing.adr_escalation_entry(_escalated_doc(entry, _applied())) is None
    assert landing.adr_escalation_entry(_escalated_doc()) is None
    legacy = {"head": H1, "step": "escalate_adr", "resolved_at": T1}
    assert landing.adr_escalation_entry(_escalated_doc(legacy)) == legacy


def test_the_release_baseline_is_the_start_of_the_question_else_the_last_diagnose():
    asked = _escalated_doc(_asked_resolved())
    assert landing.release_baseline(asked) == datetime.fromisoformat(T0)
    diagnosed = _escalated_doc({"head": H1, "step": "diagnose", "at": T0}, escalated="failure")
    assert landing.release_baseline(diagnosed) == datetime.fromisoformat(T0)
    assert landing.release_baseline(_escalated_doc(escalated="failure")) is None


# -- GH-561: the owner is told by a comment posted while the step ran, before `resolved_at` ----

ESCALATED_PREFIX = "Escalated: "


def _told_doc(at, resolved_at) -> dict:
    entry = {"head": H1, "step": "escalate_adr", "refusal": REFUSAL, "at": at}
    if resolved_at is not None:
        entry["resolved_at"] = resolved_at
    return _escalated_doc(entry)


def _escalated_comment(created_at: str) -> _Comment:
    return _Comment(ESCALATED_PREFIX + "ADR collision", created_at)


def _iso(base: str, delta: timedelta) -> str:
    return (datetime.fromisoformat(base) + delta).isoformat()


# The two documents of the report: the comment lands before (GH-407) or with (GH-601) `resolved_at`.
GH_407 = ("2026-11-06T21:42:07+00:00", "2026-11-06T21:42:12+00:00", "2026-11-06T21:42:11+00:00")
GH_601 = ("2026-11-07T06:06:26+00:00", "2026-11-07T06:06:29+00:00", "2026-11-07T06:06:29+00:00")
EQUAL_SECOND = (T1, T2, T1)


@pytest.mark.parametrize(
    ("at", "resolved_at", "created"),
    [GH_407, GH_601, EQUAL_SECOND],
    ids=["gh-407", "gh-601", "equal-second"],
)
def test_the_comment_the_step_posted_tells_the_owner(at, resolved_at, created):
    raw = _told_doc(at, resolved_at)

    assert landing.release_baseline(raw) == datetime.fromisoformat(at)
    assert landing.owner_told(raw, [_escalated_comment(created)], ESCALATED_PREFIX) is True


def test_a_comment_a_second_before_the_step_started_is_clock_skew_and_tells_the_owner():
    raw = _told_doc(T1, T2)
    comment = _escalated_comment(_iso(T1, -timedelta(seconds=1)))
    assert landing.owner_told(raw, [comment], ESCALATED_PREFIX) is True


def test_the_tolerance_is_the_named_skew_and_not_a_minute():
    assert landing.ADR_RELEASE_SKEW == timedelta(seconds=2)
    raw = _told_doc(T1, T2)
    stale = _escalated_comment(_iso(T1, -timedelta(minutes=1)))
    assert landing.owner_told(raw, [stale], ESCALATED_PREFIX) is False
    edge = _escalated_comment(_iso(T1, -landing.ADR_RELEASE_SKEW))
    assert landing.owner_told(raw, [edge], ESCALATED_PREFIX) is False


def test_without_an_escalated_comment_the_owner_is_not_told():
    raw = _told_doc(T1, T2)
    assert landing.owner_told(raw, [_Comment("Decision: renumber", T3)], ESCALATED_PREFIX) is False


@pytest.mark.parametrize(
    "at", [None, "garbage", "2026-11-07T01:00:00"], ids=["none", "bad", "naive"]
)
def test_an_unreadable_start_never_falls_back_to_the_resolution(at):
    raw = _told_doc(at, T3)

    assert landing.release_baseline(raw) is None
    comments = [_escalated_comment(T1)]
    assert landing.owner_told(raw, comments, ESCALATED_PREFIX) is True


def test_a_document_that_is_not_the_adr_one_keeps_the_strict_comparison_on_the_diagnose():
    raw = _escalated_doc({"head": H1, "step": "diagnose", "at": T1}, escalated="failure")
    same = _escalated_comment(T1)
    later = _escalated_comment(T2)
    assert landing.owner_told(raw, [same], ESCALATED_PREFIX) is False
    assert landing.owner_told(raw, [later], ESCALATED_PREFIX) is True


def test_released_stamps_the_escalate_adr_entry_and_leaves_an_older_diagnose_alone():
    diagnose = {"head": H1, "step": "diagnose", "at": T0, "delivered": True}
    raw = _escalated_doc(diagnose, _asked_resolved())
    patch = landing.released(raw, "renumber to the next free number", T2)
    assert patch["landing"][0] == diagnose
    assert patch["landing"][1]["released_at"] == T2
    assert patch["escalated"] is None
    assert "renumber to the next free number" in patch["sections"]["Human"]


def test_released_keeps_the_apply_keys_of_every_other_entry():
    applied = _applied(head_after=H2)
    raw = _escalated_doc(_released(at=T1), applied, _asked_resolved(H2))
    written = landing.released(raw, "ok", T3)["landing"]
    assert written[1] == applied
    assert written[2]["released_at"] == T3


def test_a_released_escalation_that_is_not_the_adr_one_still_stamps_the_diagnose():
    raw = _escalated_doc({"head": H1, "step": "diagnose", "at": T0}, escalated="failure")
    assert landing.released(raw, "retry", T2)["landing"][0]["released_at"] == T2
