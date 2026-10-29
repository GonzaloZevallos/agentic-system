"""`landing_plan.spent` (GH-239, GH-255): the budgets counted off the document's own
`landing` list. A `sync_master` that answered a conflict is a model pass; every other one is
a clean sync. Pure: a document in, the counts out."""

from datetime import datetime

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.application.ports import IssueComment, IssueState
from agentic.core.process.domain import adr_numbers, ci, landing, rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application import gh374_shape as gh374
from tests.fakes import MemoryTracker, memory_ports

HEAD = "a" * 40


def _sync(refusal: str) -> dict:
    return {"head": HEAD, "step": "sync_master", "refusal": refusal}


@pytest.mark.parametrize("refusal", landing.CONFLICTING_SYNC_REFUSALS)
def test_a_conflicting_sync_is_a_sync_of_its_own_and_never_a_pass(refusal):
    spent = landing_plan.spent({"landing": [_sync(refusal)]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (0, 1)


def test_a_sync_answering_anything_else_is_a_clean_one():
    spent = landing_plan.spent({"landing": [_sync("attempts_exhausted")]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (0, 1)


def test_the_loop_of_two_conflicting_syncs_for_one_sha_reaches_the_diagnose():
    entries = [
        {**_sync("merge_conflict"), "upstream": "b" * 40},
        {**_sync("checks_failed"), "upstream": "b" * 40},
    ]
    raw = {"landing": entries}
    spent = landing_plan.spent(raw, HEAD, None, "b" * 40)
    assert (spent.passes, spent.syncs, spent.conflicting_syncs_for_upstream) == (0, 2, 2)
    facts = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        conflicts=True,
    )
    step = landing_plan.step_for(
        {"id": "GH-9", "title": "", "description": "", **raw}, "merge_conflict", facts
    )
    assert step.kind == "diagnose"


def _diagnose(**more) -> dict:
    return {"head": HEAD, "step": "diagnose", "refusal": "merge_failed", **more}


def test_only_a_delivered_diagnosis_counts_as_diagnosed():
    attempt = _diagnose(failure="account_limit")
    assert landing_plan.spent({"landing": [attempt]}, HEAD, None).diagnoses == 0
    both = {"landing": [attempt, _diagnose(delivered=True)]}
    assert landing_plan.spent(both, HEAD, None).diagnoses == 1


def test_a_redelivery_keeps_the_diagnosis_counted():
    resolved = landing.resolve_open([_diagnose(delivered=True)], "2026-10-15T13:00:00+00:00", 3.0)
    assert landing_plan.spent({"landing": resolved}, HEAD, None).diagnoses == 1


def test_an_undelivered_diagnose_entry_is_not_a_dispatched_step():
    assert not landing.already_dispatched([_diagnose(failure="sdk")], HEAD, "diagnose", None)
    assert landing.already_dispatched([_diagnose(delivered=True)], HEAD, "diagnose", None)
    assert landing.already_dispatched([{**_sync("x"), "step": "fix"}], HEAD, "fix", None)


def test_the_gate_skips_a_retry_that_is_not_due_and_an_escalation_already_made():
    from datetime import UTC, datetime, timedelta

    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    raw = {"landing": [_diagnose(failure="account_limit", at=at.isoformat())]}
    wait = timedelta(minutes=rules.ACCOUNT_LIMIT_SCHEDULE[0])
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait - timedelta(seconds=1)) == "skip"
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait) == "go"
    assert landing_plan.diagnose_gate(raw, "diagnose", None) == "skip"
    assert landing_plan.diagnose_gate(raw, "fix", None) == "go"
    sdk = {"landing": [_diagnose(failure="sdk", at=at.isoformat())]}
    assert landing_plan.diagnose_gate(sdk, "diagnose", at) == "escalate"
    assert landing_plan.diagnose_gate({**sdk, "escalated": "x"}, "diagnose", at) == "skip"


# --- GH-328: releasing an escalated landing diagnose --------------------------------------------

ATTEMPT_AT = "2026-10-23T06:00:00+00:00"
ESCALATED_AT = "2026-10-23T06:05:00+00:00"
ANSWERED_AT = "2026-10-23T06:47:00+00:00"


def _escalated(**over) -> dict:
    raw = {
        "id": "GH-5",
        "status": "waiting_on_human",
        "escalated": "Landing Diagnoser did not deliver after 1 attempt: max_turns",
        "escalation_class": "failure",
        "pull_request": "https://github.com/o/r/pull/5",
        "landing": [
            {
                "head": HEAD,
                "step": "diagnose",
                "refusal": "merge_failed",
                "at": ATTEMPT_AT,
                "failure": "max_turns",
            }
        ],
        "cost_usd": 4.0,
    }
    return {**raw, **over}


class _ReadingTracker(MemoryTracker):
    """A tracker that remembers what was read from it."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        self.reads.append(task_id)
        return super().timestamped_comments(task_id)


def _world(
    comments=("Escalated: x", "merge master in"),
    *,
    labels: tuple[str, ...] | None = (),
    state="OPEN",
):
    ports = memory_ports(tracker=_ReadingTracker())
    ports.tracker.timestamped["GH-5"] = [
        IssueComment(body, (ESCALATED_AT, ANSWERED_AT)[min(i, 1)])
        for i, body in enumerate(comments)
    ]
    ports.tracker.states[5] = IssueState(state, "", (), None, labels)
    return ports


def _release(ports, raw=None, cfg=None):
    cfg = cfg or Configuration(product_dir="/p")
    return landing_plan.release(ports, cfg, "GH-5", raw or _escalated(), 5)


def test_a_human_answer_after_the_escalation_releases_it():
    found = _release(_world())
    assert (found.decision, found.why) == ("merge master in", "released")


def test_without_a_human_comment_after_the_escalation_it_waits_on_a_human():
    found = _release(_world(("Escalated: x", "Auto-merge refused: ci_missing")))
    assert (found.decision, found.why) == (None, "waiting_on_human")


@pytest.mark.parametrize(
    "world",
    [
        dict(labels=("needs-human",)),
        dict(labels=None),
        dict(state="CLOSED"),
    ],
    ids=["label_still_there", "labels_unreadable", "issue_closed"],
)
def test_a_comment_does_not_release_while_a_condition_fails(world):
    found = _release(_world(**world))
    assert (found.decision, found.why) == (None, "waiting_on_human")


def test_a_label_that_was_never_added_counts_as_removed():
    assert _release(_world(labels=("agentic",))).why == "released"


def test_a_tracker_that_cannot_answer_does_not_release():
    ports = _world()
    del ports.tracker.states[5]  # `issue_state` raises
    ports.tracker.state_errors.add(5)
    assert _release(ports).why == "waiting_on_human"


def test_a_spent_cap_does_not_release_and_is_its_own_reason():
    cfg = Configuration(product_dir="/p", task_max_budget_usd=10.0)
    raw = _escalated(cost_usd=6.0, landing_cost_carried=4.0)
    found = _release(_world(), raw, cfg)
    assert (found.decision, found.why) == (None, "task_cap")
    under = _release(_world(), _escalated(cost_usd=6.0, landing_cost_carried=3.0), cfg)
    assert under.why == "released"


def test_an_escalation_after_a_delivered_diagnosis_is_the_humans_to_restart():
    delivered = {**_escalated()["landing"][0], "delivered": True}
    ports = _world()
    found = _release(ports, _escalated(landing=[delivered]))
    assert (found.decision, found.why) == (None, "needs_restart")
    # Nothing was read for it: the shape alone says no.
    assert ports.tracker.reads == []


def test_only_a_failure_class_escalation_is_releasable():
    assert _release(_world(), _escalated(escalation_class="questions")).why == "needs_restart"


def test_pending_counts_a_released_diagnose_as_go_and_still_skips_an_unreleased_one():
    facts = rules.MergeFacts(
        task_id="GH-5",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        merge_failures=3,
        merge_failure="gh_rejected",
    )
    live = landing_plan.LIVE_STEPS
    now = datetime.fromisoformat("2026-10-23T07:00:00+00:00")
    raw = _escalated()
    assert landing_plan.pending(raw, "merge_failed", facts, live, now) is None
    step = landing_plan.pending(raw, "merge_failed", facts, live, now, released=True)
    assert step is not None and step.kind == "diagnose"
    # Released has no effect on any other step.
    other = landing_plan.pending({}, "master_moved", facts, live, now, released=True)
    assert other is None


def _checks(head: str, *entries: dict) -> dict:
    return {"merge_checks": {"head": head, "passed": False, "checks": list(entries)}}


@pytest.mark.parametrize(
    ("raw", "reran"),
    [
        (_checks(HEAD, {"name": "tests", "passed": False, "tail": "x", "rerun": True}), True),
        (_checks(HEAD, {"name": "tests", "passed": False, "tail": "x"}), False),
        (_checks("c" * 40, {"name": "tests", "passed": False, "tail": "x", "rerun": True}), False),
        (_checks(HEAD, {"name": "tests", "passed": True, "flake": True}), False),
        ({}, False),
        ({"merge_checks": "garbage"}, False),
        ({"merge_checks": {"head": HEAD, "checks": "garbage"}}, False),
    ],
    ids=[
        "rerun_row",
        "before_the_change",
        "other_head",
        "flake_only",
        "absent",
        "text",
        "bad_list",
    ],
)
def test_a_red_check_with_a_rerun_row_on_the_head_is_a_fact_of_the_plan(raw, reran):
    assert landing_plan.spent(raw, HEAD, None).checks_rerun_on_head is reran


def test_a_pass_written_before_the_pull_request_spends_the_important_budget_and_no_model_pass():
    raw = {"landing": [{"head": "h", "step": "fix_important", "before_pr": True, "at": "t"}]}
    spent = landing_plan.spent(raw, "h", None)
    assert spent.important_passes == landing.LANDING_IMPORTANT_PASSES_MAX
    assert spent.passes == 0 and spent.syncs == 0 and spent.reruns_on_head == 0
    assert landing_plan.spent({"landing": [{"step": "fix"}]}, "h", None).important_passes == 0


def _asked(step: str, *, head: str = HEAD, refusal: str = "adr_number_taken", **more) -> dict:
    return {"step": step, "head": head, "refusal": refusal, **more}


def test_an_open_escalate_adr_entry_does_not_count_as_asked_but_a_resolved_one_does():
    open_ = {"landing": [_asked("escalate_adr")]}
    done = {"landing": [_asked("escalate_adr", resolved_at="2026-10-28T10:00:00Z")]}
    recorded = landing_plan.escalation_recorded
    assert not recorded(open_, HEAD, "adr_number_taken", "escalate_adr", resolved_only=True)
    assert recorded(done, HEAD, "adr_number_taken", "escalate_adr", resolved_only=True)


def test_another_head_refusal_or_step_is_not_the_question_asked():
    raw = {"landing": [_asked("escalate_adr", resolved_at="t")]}
    recorded = landing_plan.escalation_recorded
    assert not recorded(raw, "b" * 40, "adr_number_taken", "escalate_adr", resolved_only=True)
    assert not recorded(raw, HEAD, "merge_failed", "escalate_adr", resolved_only=True)
    assert not recorded(raw, HEAD, "adr_number_taken", "escalate_ledger", resolved_only=True)


def test_escalate_ledger_keeps_counting_an_entry_without_resolved_at():
    raw = {"landing": [_asked("escalate_ledger", refusal="open_findings_blocking")]}
    assert landing_plan.escalation_recorded(raw, HEAD, "open_findings_blocking", "escalate_ledger")


def test_escalate_adr_is_a_live_step_but_not_an_always_taken_one():
    assert "escalate_adr" in landing_plan.LIVE_STEPS
    assert "escalate_adr" not in landing_plan.ALWAYS_TAKEN
    assert landing_plan.takes_action("escalate_adr", landing_plan.LIVE_STEPS)
    assert not landing_plan.takes_action("escalate_adr", frozenset())


def _adr_step(raw: dict) -> landing.LandingStep:
    facts = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=1,
        checks_head=HEAD,
        checks_master="b" * 40,
        checks_passed=True,
        current_master="b" * 40,
        adr_collision=adr_numbers.Ambiguous("unclassified_token", "docs/a.md:3: 0054"),
    )
    return landing_plan.step_for(raw, "adr_number_taken", facts)


def test_step_for_asks_until_the_escalation_is_resolved_then_waits():
    assert _adr_step({}).kind == "escalate_adr"
    assert _adr_step({"landing": [_asked("escalate_adr")]}).kind == "escalate_adr"
    assert _adr_step({"landing": [_asked("escalate_adr", resolved_at="t")]}).kind == "none"
    other_head = _asked("escalate_adr", head="c" * 40, resolved_at="t")
    assert _adr_step({"landing": [other_head]}).kind == "escalate_adr"


# --- GH-442: a landing escalation only CI caused is released by the table, never by a restart ---


def _gh374(**over) -> dict:
    return gh374.document(**over)


def _step(raw: dict, ancestry=None, facts=None) -> landing.LandingStep:
    return landing_plan.step_for(raw, "ci_failed", facts or gh374.facts(), ancestry=ancestry)


def test_the_recorded_deadlock_shape_is_the_diagnose_before_any_release():
    """Red first: the table sends the exhausted `ci_failed` of GH-374 to a restart's path."""
    step = _step(_gh374())
    assert step.kind == "diagnose"


def test_a_ci_escalation_is_an_escalated_ci_failed_on_the_head_with_at_most_a_rerun_after_it():
    raw = _gh374()
    assert landing.ci_escalation(raw, gh374.HEAD)
    assert landing.ci_escalation(
        _gh374(landing=gh374.document()["landing"][:5]), gh374.HEAD
    )  # no rerun
    assert not landing.ci_escalation(raw, "f" * 40)
    assert not landing.ci_escalation(_gh374(escalated=None), gh374.HEAD)


@pytest.mark.parametrize(
    "after",
    [
        {"head": gh374.HEAD, "step": "escalate_ledger", "refusal": "ci_failed"},
        {"head": gh374.HEAD, "step": "escalate_adr", "refusal": "ci_failed"},
        {"head": gh374.HEAD, "step": "diagnose", "refusal": "ci_failed", "delivered": True},
        {"head": gh374.HEAD, "step": "rerun", "refusal": "ci_failed"},
        {"head": "f" * 40, "step": "rerun", "refusal": "ci_failed"},
    ],
    ids=["ledger", "adr", "delivered_diagnose", "second_rerun", "rerun_on_another_head"],
)
def test_an_entry_after_the_escalation_that_is_not_its_one_rerun_makes_it_no_ci_escalation(after):
    assert not landing.ci_escalation(
        _gh374(landing=[*gh374.document()["landing"], after]), gh374.HEAD
    )


@pytest.mark.parametrize(
    "escalate",
    [
        {**gh374.ESCALATE, "refusal": "open_findings_blocking"},
        {**gh374.ESCALATE, "step": "escalate_ledger"},
        {**gh374.ESCALATE, "head": "f" * 40},
    ],
    ids=["other_refusal", "ledger_step", "other_head"],
)
def test_only_an_escalate_entry_for_ci_failed_on_the_head_is_a_ci_escalation(escalate):
    raw = _gh374(landing=[*gh374.document()["landing"][:4], escalate])
    assert not landing.ci_escalation(raw, gh374.HEAD)


def test_the_entries_of_an_archived_run_are_not_the_escalation():
    archived = {**gh374.ESCALATE, "at": "2026-10-19T00:00:00+00:00"}
    raw = _gh374(landing=[archived, *gh374.document()["landing"][:4]])
    assert not landing.ci_escalation(raw, gh374.HEAD)


def test_an_old_escalate_entry_has_no_checks_to_read():
    entry = landing.ci_escalation_entry(_gh374(), gh374.HEAD)
    assert entry is not None
    assert landing.escalation_checks(entry, ("web", "api")) == ("web", "api")
    recorded = {**entry, "checks": ["web"]}
    assert landing.escalation_checks(recorded, ("web", "api")) == ("web",)


def test_released_by_ci_clears_the_escalation_and_stamps_the_pair_and_nothing_else():
    raw = _gh374()
    patch = landing.released_by_ci(raw, gh374.NEW_MASTER, gh374.RELEASED_AT)
    assert patch["escalated"] is None and patch["escalation_class"] is None
    assert "sections" not in patch
    released = apply(raw, patch)
    assert released["sections"] == raw["sections"]
    assert released["sections"]["Human"].encode() == gh374.HUMAN.encode()
    stamped = [e for e in released["landing"] if e["step"] == "escalate"]
    assert [(e["ci_released"], e["released_at"]) for e in stamped] == [
        (gh374.NEW_MASTER, gh374.RELEASED_AT)
    ]
    assert len(released["landing"]) == len(raw["landing"])


def test_a_second_release_of_the_same_pair_writes_nothing():
    raw = _gh374()
    released = apply(raw, landing.released_by_ci(raw, gh374.NEW_MASTER, gh374.RELEASED_AT))
    assert landing.released_by_ci(released, gh374.NEW_MASTER, gh374.RELEASED_AT) == {}
    again = {**released, "escalated": "again"}
    assert landing.released_by_ci(again, gh374.NEW_MASTER, "2026-10-30T00:00:00+00:00") == {}


def _released(upstream: str = gh374.NEW_MASTER) -> dict:
    raw = _gh374()
    return apply(raw, landing.released_by_ci(raw, upstream, gh374.RELEASED_AT))


def test_the_marker_survives_a_rewrite_of_the_landing_list():
    rewritten = landing.resolve_open(_released()["landing"], "2026-10-30T00:00:00+00:00", 1.0)
    assert any(e.get("ci_released") == gh374.NEW_MASTER for e in rewritten)


@pytest.mark.parametrize(
    ("ancestry", "kind"),
    [(False, "sync_master"), (True, "rerun"), (None, "wait")],
    ids=["head_lacks_upstream", "head_has_upstream", "ancestry_unreadable"],
)
def test_after_a_ci_release_the_table_syncs_reruns_or_waits_by_the_ancestry(ancestry, kind):
    assert _step(_released(), lambda: ancestry).kind == kind


def test_a_marker_for_another_upstream_changes_nothing():
    assert _step(_released(gh374.MASTER), lambda: False).kind == "diagnose"


def test_the_sync_and_the_rerun_of_a_release_are_each_owed_once():
    synced = {"head": gh374.HEAD, "step": "sync_master", "refusal": "ci_failed"}
    raw = _released()
    after_sync = {**raw, "landing": [*raw["landing"], synced]}
    assert _step(after_sync, lambda: False).kind == "diagnose"
    rerun = {**gh374.RERUN, "at": "2026-10-29T09:00:01+00:00"}
    after_rerun = {**raw, "landing": [*raw["landing"], rerun]}
    assert _step(after_rerun, lambda: True).kind == "diagnose"


def test_the_rerun_before_the_release_does_not_spend_the_one_the_release_owes():
    assert [e["step"] for e in _released()["landing"]][-1] == "rerun"
    assert _step(_released(), lambda: True).kind == "rerun"


def test_the_marker_never_changes_a_refusal_that_is_not_ci_failed():
    raw = _released()
    step = landing_plan.step_for(raw, "merge_conflict", gh374.facts(), ancestry=lambda: True)
    assert step.kind == landing_plan.step_for(_gh374(), "merge_conflict", gh374.facts()).kind


def test_the_ancestry_is_not_read_without_the_marker():
    def boom() -> bool:
        raise AssertionError("read")

    assert _step(_gh374(), boom).kind == "diagnose"


def test_a_rerun_recorded_before_the_release_is_not_a_dispatch_of_the_one_it_owes():
    entries = landing_plan.live_entries(_released())
    assert not landing.already_dispatched(entries, gh374.HEAD, "rerun", gh374.NEW_MASTER)
    plain = landing_plan.live_entries(_gh374())
    assert landing.already_dispatched(plain, gh374.HEAD, "rerun", gh374.NEW_MASTER)
    new = [*entries, {**gh374.RERUN, "at": "2026-10-29T09:00:01+00:00"}]
    assert landing.already_dispatched(new, gh374.HEAD, "rerun", gh374.NEW_MASTER)


def _report(*checks: ci.CiCheck) -> ci.CiReport:
    return gh374.report(*checks)


@pytest.mark.parametrize(
    ("base", "red", "green"),
    [
        (_report(gh374.web("FAILURE")), ("web",), False),
        (_report(gh374.web("SUCCESS")), (), True),
        (_report(gh374.web(None)), (), False),
        (_report(), (), False),
        (ci.CiUnreadable("gh_unavailable"), (), False),
        (None, (), False),
    ],
    ids=["red", "green", "running", "check_missing", "unreadable", "not_read"],
)
def test_the_base_is_green_only_for_a_readable_complete_green_report(base, red, green):
    assert ci.red_names_on_base(base, ("web",)) == red
    assert ci.base_green(base, ("web",)) is green


def test_red_names_on_base_compared_with_a_head_keeps_only_the_names_red_on_both():
    head = _report(gh374.web("FAILURE"), ci.CiCheck("api", "COMPLETED", "FAILURE", "x"))
    base = _report(gh374.web("FAILURE"), ci.CiCheck("api", "COMPLETED", "SUCCESS", "y"))
    assert ci.red_names_on_base(base, ("web", "api"), head) == ("web",)
