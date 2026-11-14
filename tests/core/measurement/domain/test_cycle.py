"""Cycle and active time of a task document: `agentic.core.measurement.domain.cycle`."""

import ast
import random
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.measurement.domain import cycle, scorecard
from agentic.core.process.domain import task_path
from agentic.core.process.domain.task import Status

START = "2026-10-20T10:00:00.000Z"
MID = "2026-10-20T10:30:00.000Z"
END = "2026-10-20T11:00:00.000Z"
CLOSED = "2026-10-20T14:00:00.000Z"

NOT_A_DELIVERY = {"split", "discarded", "tracker_only"}


def run(started=START, ended=END):
    record = {"role": "Developer", "version": 1, "cost_usd": 0.1, "session_id": "s", "model": "m"}
    if started is not None:
        record["started_at"] = started
    if ended is not None:
        record["ended_at"] = ended
    return record


def doc(**over):
    return {"status": "ready_for_review", "runs": [run()], **over}


@pytest.mark.parametrize("status", get_args(Status))
def test_cycle_class_of_every_status(status):
    expected_open = "excluded" if status in NOT_A_DELIVERY else "open"
    expected_closed = "excluded" if status in NOT_A_DELIVERY else "closed"
    assert cycle.cycle_class(doc(status=status)) == expected_open
    assert cycle.cycle_class(doc(status=status, closed_at=CLOSED)) == expected_closed


@pytest.mark.parametrize(
    ("label", "document", "expected"),
    [
        ("merged", doc(closed_at=CLOSED, delivery_outcome="merged"), "closed"),
        (
            "merged_with_changes",
            doc(closed_at=CLOSED, delivery_outcome="merged_with_changes"),
            "closed",
        ),
        ("auto_merged", doc(closed_at=CLOSED, delivery_outcome="auto_merged"), "closed"),
        ("closed unmerged", doc(closed_at=CLOSED, delivery_outcome="closed_unmerged"), "closed"),
        ("ready_for_review open", doc(), "open"),
        ("waiting_on_human", doc(status="waiting_on_human"), "open"),
        ("split", doc(status="split", closed_at=CLOSED), "excluded"),
        ("discarded", doc(status="discarded"), "excluded"),
        ("tracker_only", doc(status="tracker_only"), "excluded"),
        ("closed by slices", doc(closed_at=CLOSED, closed_by_slices="1,2"), "excluded"),
        ("no runs", doc(runs=[], closed_at=CLOSED), "excluded"),
        ("no runs key", {"status": "open", "closed_at": CLOSED}, "excluded"),
        ("run never started", doc(runs=[run(started=None)], closed_at=CLOSED), "excluded"),
        ("open and still running", doc(runs=[run(ended=None)]), "excluded"),
        ("closed whose run never ended", doc(runs=[run(ended=None)], closed_at=CLOSED), "closed"),
    ],
)
def test_cycle_class_cases(label, document, expected):
    assert cycle.cycle_class(document) == expected, label


def test_cycle_seconds_of_a_closed_task_runs_to_closed_at():
    document = doc(runs=[run(MID, END), run(START, MID)], closed_at=CLOSED)
    assert cycle.cycle_seconds(document) == 4 * 3600


def test_cycle_seconds_of_an_open_task_runs_to_the_last_ended_at():
    document = doc(runs=[run(START, MID), run(MID, END), run(END, None)])
    assert cycle.cycle_seconds(document) == 3600


def test_cycle_seconds_is_none_for_an_excluded_task_or_an_unreadable_bound():
    assert cycle.cycle_seconds(doc(status="discarded")) is None
    assert cycle.cycle_seconds(doc(runs=[run("not a date", END)], closed_at=CLOSED)) is None
    assert cycle.cycle_seconds(doc(closed_at="garbage")) is None
    # A close recorded before the first run is a corrupt document, not a negative time.
    assert cycle.cycle_seconds(doc(closed_at="2026-10-20T09:00:00.000Z")) is None


def test_active_seconds_is_the_union_of_overlapping_intervals():
    runs = [
        run("2026-10-20T10:00:00.000Z", "2026-10-20T10:10:00.000Z"),
        run("2026-10-20T10:05:00.000Z", "2026-10-20T10:15:00.000Z"),
        run("2026-10-20T11:00:00.000Z", "2026-10-20T11:05:00.000Z"),
    ]
    assert cycle.active_seconds({"runs": runs}) == 20 * 60


def test_active_seconds_ignores_incomplete_runs_and_is_none_without_any_complete_one():
    assert cycle.active_seconds({"runs": [run(ended=None), run(started=None)]}) is None
    assert cycle.active_seconds({}) is None
    assert cycle.active_seconds({"runs": [run(START, MID), run(ended=None)]}) == 1800


def test_the_scorecard_counts_closed_and_open_and_drops_a_task_missing_a_bound():
    documents = [
        doc(closed_at=CLOSED),  # 4 h cycle, 1 h active
        doc(runs=[run(START, MID)]),  # open: 0.5 h cycle, 0.5 h active
        doc(status="discarded"),  # excluded
        doc(runs=[run(ended=None)]),  # open with no end: excluded, not a zero
        doc(runs=[run(ended=None)], closed_at=CLOSED),  # closed: cycle 4 h, no active
    ]
    sc = scorecard.build([scorecard.task_figures(d) for d in documents])
    assert (sc.cycle_h.value, sc.cycle_h.denominator, sc.cycle_h.excluded) == (4.0, 3, 2)
    assert (sc.active_h.value, sc.active_h.denominator, sc.active_h.excluded) == (0.75, 2, 3)
    assert (sc.cycle_closed, sc.cycle_open) == (2, 1)
    empty = scorecard.build([])
    assert empty.cycle_h.value is None and empty.cycle_h.denominator == 0
    assert empty.active_h.value is None


# --- The cycle split (GH-467): pauses, failed, waiting, unattributed -------------------------

ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def m(minutes):
    """The instant `minutes` past 10:00:00Z, as the ISO string a document holds."""
    return (ORIGIN + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def at(minutes):
    return ORIGIN + timedelta(minutes=minutes)


def failure(start, end, section="Developer: a", reason="upstream", **over):
    record = {"role": "Developer", "attempt": 1, "error": "x", "cost_usd": 0.0, "session_id": "s"}
    record.update(section=section, reason=reason)
    if start is not None:
        record["started_at"] = m(start)
    if end is not None:
        record["ended_at"] = m(end)
    return {**record, **over}


def timing(pushed, node, ended, verdict="green"):
    return {
        "pushed_at": m(pushed),
        "node_started_at": m(node),
        "run_started_at": None if ended is None else m(node),
        "run_ended_at": None if ended is None else m(ended),
        "verdict": verdict,
    }


def resumed(minutes):
    return {"kind": "resumed", "at": m(minutes)}


def escalation(minutes, value, **over):
    return {"kind": "escalation", "value": value, "at": m(minutes), **over}


def failed_entry(minutes, reason="account_limit", section="Developer: a"):
    return {"kind": "failure", "value": reason, "section": section, "at": m(minutes)}


def timed_run(start, end, role="Developer", section="Developer: a"):
    return {**run(m(start), m(end)), "role": role, "section": section}


def closed(*runs, close_at, **over):
    return {"status": "merged", "runs": list(runs), "closed_at": m(close_at), **over}


def seconds(minutes):
    return minutes * 60.0


def split(document):
    return cycle.time_split(document)


def test_a_pause_that_starts_before_the_first_run_counts_only_inside_the_cycle():
    document = closed(
        timed_run(0, 60),
        close_at=120,
        pause_log=[escalation(-30, "returns"), resumed(10)],
    )
    pauses = cycle.pause_intervals(document)
    assert [(p.start, p.end, p.kind) for p in pauses.closed] == [(at(-30), at(10), "human")]
    got = split(document)
    # 0-10 is inside the first run, so it is active; the rest of the pause is outside the cycle.
    assert got.paused_human_s == 0.0
    assert got.unattributed_s == seconds(60)


def test_a_pause_after_the_runs_counts_until_the_next_resumed():
    document = closed(
        timed_run(0, 60),
        close_at=240,
        pause_log=[escalation(70, "returns"), resumed(130)],
    )
    got = split(document)
    assert got.paused_human_s == seconds(60)
    assert got.paused_external_s == 0.0
    assert got.unattributed_s == seconds(240 - 60 - 60)


def test_a_ci_wait_that_ends_after_the_last_run_is_waiting_and_a_clamped_one_is_not():
    document = closed(
        timed_run(0, 60),
        close_at=120,
        full_checks={"ci_timing": timing(60, 62, 90)},
    )
    assert split(document).waiting_s == seconds(28)
    clamped = closed(
        timed_run(0, 60),
        close_at=120,
        full_checks={"ci_timing": timing(60, 62, 61)},
    )
    assert split(clamped).waiting_s == seconds(0)
    assert split(clamped).unattributed_s == seconds(60)


def test_a_ci_wait_past_the_end_of_an_open_cycle_is_cut_to_it():
    document = {
        "status": "ready_for_review",
        "runs": [timed_run(0, 60), timed_run(70, 80)],
        "merge_ci": [{"ci_timing": timing(60, 60, 200)}],
    }
    # The cycle ends at 80; the wait 60-200 is cut to 60-80 and loses the 70-80 run.
    assert split(document).waiting_s == seconds(10)


def test_the_wait_clamp_is_ci_waited_seconds_not_a_second_copy():
    # Node started after the push: the wait runs from the node's start, not from the push.
    document = closed(
        timed_run(0, 10),
        close_at=120,
        full_checks={"ci_timing": timing(15, 40, 70)},
    )
    assert split(document).waiting_s == seconds(30)


def test_a_failure_overlapping_a_ci_wait_counts_as_failed_and_the_rest_as_waiting():
    document = closed(
        timed_run(0, 10),
        close_at=100,
        failures=[failure(20, 40)],
        full_checks={"ci_timing": timing(30, 30, 50)},
    )
    got = split(document)
    assert got.failed_s == seconds(20)
    assert got.waiting_s == seconds(10)


def test_a_ci_wait_overlapped_by_a_tech_lead_review_counts_once_and_as_active():
    document = closed(
        timed_run(0, 20),
        timed_run(20, 50, role="Tech Lead", section="Tech Lead: review a"),
        close_at=60,
        merge_ci=[{"ci_timing": timing(30, 30, 60)}],
    )
    got = split(document)
    assert got.waiting_s == seconds(10)
    assert got.unattributed_s == 0.0
    assert cycle.clipped_active_seconds(document) == seconds(50)


def test_a_ci_pause_entry_waits_up_to_its_own_at_when_the_timing_has_no_run_end():
    document = closed(
        timed_run(0, 10),
        close_at=100,
        pause_log=[
            escalation(50, "ci", ci=timing(20, 20, None, verdict="no_verdict")),
            resumed(60),
        ],
    )
    got = split(document)
    assert got.waiting_s == seconds(30)
    assert got.paused_human_s == seconds(10)  # 50-60: the wait 20-50 sits before the pause


def test_a_ci_timing_whose_wait_is_unknown_makes_waiting_none():
    document = closed(
        timed_run(0, 10),
        close_at=100,
        full_checks={"ci_timing": timing(20, 20, None, verdict="no_verdict")},
    )
    got = split(document)
    assert got.waiting_s is None
    assert got.unattributed_s is None
    assert got.failed_s == 0.0


def test_a_failure_that_retried_and_recovered_never_opens_the_pause():
    document = closed(
        timed_run(0, 5),
        close_at=400,
        failures=[failure(65, 80)],
        pause_log=[resumed(60), escalation(240, "ci"), resumed(300)],
    )
    pauses = cycle.pause_intervals(document)
    assert [(p.start, p.end) for p in pauses.closed] == [(at(240), at(300))]
    got = split(document)
    assert got.failed_s == seconds(15)
    assert got.unattributed_s == seconds(400 - 5 - 15 - 60)


def test_a_failure_pause_starts_at_the_end_of_the_latest_matching_failure():
    document = closed(
        timed_run(0, 5),
        close_at=400,
        failures=[failure(10, 20), failure(30, 40), failure(35, 45, reason="other")],
        pause_log=[failed_entry(41, reason="upstream"), resumed(100)],
    )
    pauses = cycle.pause_intervals(document)
    # Same section and reason as the entry: the failure that ended at 40, not the `other` one.
    assert [(p.start, p.end, p.kind) for p in pauses.closed] == [(at(40), at(100), "external")]


def test_a_failure_without_reason_reads_as_other():
    bare = failure(30, 40)
    del bare["reason"]
    document = closed(
        timed_run(0, 5),
        close_at=200,
        failures=[bare],
        pause_log=[failed_entry(41, reason="other"), resumed(100)],
    )
    assert cycle.pause_intervals(document).closed[0].start == at(40)


def test_two_failures_of_one_section_in_two_epochs_each_open_their_own_pause():
    document = closed(
        timed_run(0, 5),
        close_at=400,
        failures=[
            failure(20, 30, reason="account_limit"),
            failure(120, 130, reason="account_limit"),
        ],
        pause_log=[failed_entry(31), resumed(100), failed_entry(131), resumed(200)],
    )
    starts = [(p.start, p.end) for p in cycle.pause_intervals(document).closed]
    assert starts == [(at(30), at(100)), (at(130), at(200))]


def test_a_failure_that_ended_before_the_previous_resumed_cannot_open_a_later_epoch():
    document = closed(
        timed_run(0, 5),
        close_at=400,
        failures=[failure(20, 30, reason="account_limit")],
        pause_log=[failed_entry(31), resumed(100), failed_entry(131), resumed(200)],
    )
    starts = [p.start for p in cycle.pause_intervals(document).closed]
    assert starts == [at(30), at(131)]


def test_a_role_run_never_closes_a_pause():
    document = closed(
        timed_run(0, 5),
        timed_run(20, 25),
        close_at=100,
        pause_log=[escalation(10, "returns"), resumed(60)],
    )
    assert [(p.start, p.end) for p in cycle.pause_intervals(document).closed] == [(at(10), at(60))]


def test_an_epoch_of_only_lapse_entries_is_no_pause():
    document = closed(
        timed_run(0, 5),
        close_at=100,
        pause_log=[{"kind": "lapse", "value": "step: Boom"}, resumed(60)],
    )
    pauses = cycle.pause_intervals(document)
    assert pauses.closed == ()
    assert pauses.complete


@pytest.mark.parametrize(
    ("label", "entries", "kind"),
    [
        ("external failure alone", [failed_entry(20)], "external"),
        (
            "external failure and a failure escalation",
            [failed_entry(20), escalation(21, "failure")],
            "external",
        ),
        ("external failure then returns", [failed_entry(20), escalation(21, "returns")], "human"),
        ("ci alone", [escalation(20, "ci")], "human"),
        ("failure then ci", [failed_entry(20), escalation(21, "ci")], "human"),
        ("an internal failure reason", [failed_entry(20, reason="max_turns")], "human"),
        (
            "an external and an internal reason",
            [failed_entry(20), failed_entry(21, reason="other")],
            "human",
        ),
        (
            "two external reasons",
            [failed_entry(20), failed_entry(21, reason="network")],
            "external",
        ),
        ("a returns escalation alone", [escalation(20, "returns")], "human"),
    ],
)
def test_an_epoch_is_external_only_when_every_failure_is_external_and_nothing_else_escalated(
    label, entries, kind
):
    document = closed(timed_run(0, 5), close_at=100, pause_log=[*entries, resumed(60)])
    assert [p.kind for p in cycle.pause_intervals(document).closed] == [kind], label
    got = split(document)
    assert (got.paused_external_s or 0) > 0 if kind == "external" else got.paused_external_s == 0.0
    assert (got.paused_human_s or 0) > 0 if kind == "human" else got.paused_human_s == 0.0


def test_a_task_whose_last_epoch_has_no_resumed_has_unknown_pauses_but_known_failed_and_waiting():
    document = {
        "status": "waiting_on_human",
        "runs": [timed_run(0, 60)],
        "failures": [failure(10, 20)],
        "full_checks": {"ci_timing": timing(20, 20, 40)},
        "pause_log": [escalation(70, "returns")],
    }
    assert not cycle.pause_intervals(document).complete
    got = split(document)
    assert got.paused_external_s is None
    assert got.paused_human_s is None
    assert got.unattributed_s is None
    assert got.failed_s == seconds(0)  # 10-20 sits inside the run 0-60: it counts as active
    assert got.waiting_s == seconds(0)


def test_a_pause_entry_with_no_at_leaves_the_paused_figures_unknown_never_zero():
    document = closed(
        timed_run(0, 5),
        close_at=100,
        pause_log=[{"kind": "escalation", "value": "returns"}, resumed(60)],
    )
    got = split(document)
    assert got.paused_human_s is None
    assert got.paused_external_s is None
    assert got.unattributed_s is None


def test_an_unreadable_resumed_leaves_the_pause_unknown_and_the_next_epoch_borrows_none():
    document = closed(
        timed_run(0, 5),
        close_at=400,
        failures=[failure(20, 30, reason="account_limit")],
        pause_log=[
            escalation(10, "returns"),
            {"kind": "resumed"},
            failed_entry(131),
            resumed(200),
        ],
    )
    pauses = cycle.pause_intervals(document)
    assert not pauses.complete
    assert [(p.start, p.end) for p in pauses.closed] == [(at(131), at(200))]


def test_a_naive_run_and_an_aware_run_in_one_document_are_ordered_as_utc():
    naive = {**run("2026-10-20T10:00:00", "2026-10-20T10:20:00")}
    aware = {**run(m(30), m(50))}
    document = closed(naive, aware, close_at=60)
    got = split(document)
    assert got.unattributed_s == seconds(20)
    assert cycle.clipped_active_seconds(document) == seconds(40)


def test_a_run_that_ends_after_closed_at_keeps_active_unclipped_and_the_split_clips_it():
    document = closed(timed_run(0, 90), close_at=60)
    assert cycle.active_seconds(document) == seconds(90)
    assert cycle.clipped_active_seconds(document) == seconds(60)
    assert split(document).unattributed_s == 0.0


def test_a_document_with_old_records_reads_unknown_never_zero():
    document = closed(
        timed_run(0, 5),
        close_at=100,
        failures=[failure(None, None)],
        pause_log=[{"kind": "escalation", "value": "ci"}],
    )
    # waiting is known on its own, but it is subtracted after failed, which is unknown here.
    assert split(document) == cycle.TimeSplit(None, None, None, None, None)
    assert (
        split(closed(timed_run(0, 5), close_at=100, failures=[failure(None, 30)])).failed_s is None
    )
    # A ci entry with no `ci` and a record with no `ci_timing` add nothing to the wait.
    bare = closed(
        timed_run(0, 5),
        close_at=100,
        full_checks={"passed": True},
        merge_ci=[{"head": "h"}],
        pause_log=[escalation(10, "ci"), resumed(20)],
    )
    assert split(bare).waiting_s == 0.0


def test_a_task_with_no_failures_has_failed_zero():
    assert split(closed(timed_run(0, 5), close_at=100)).failed_s == 0.0


def test_a_task_without_a_cycle_has_every_figure_unknown():
    assert split({"status": "discarded", "runs": [timed_run(0, 5)]}) == cycle.TimeSplit(
        None, None, None, None, None
    )
    assert cycle.clipped_active_seconds({"status": "open", "runs": []}) is None


def test_an_unreadable_timing_instant_makes_waiting_none():
    bad = {**timing(20, 20, 40), "pushed_at": "not a date"}
    document = closed(timed_run(0, 5), close_at=100, full_checks={"ci_timing": bad})
    assert split(document).waiting_s is None


def test_a_figure_whose_instants_cannot_be_ordered_is_none(monkeypatch):
    def refuse(*_args):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")

    monkeypatch.setattr(cycle.parallel_factor, "difference", refuse)
    got = split(closed(timed_run(0, 5), close_at=100, failures=[failure(10, 20)]))
    assert got == cycle.TimeSplit(None, None, None, None, None)


def test_an_unknown_failed_class_leaves_the_later_classes_unknown():
    """A failure missing a bound cannot be stood in for by an empty set: a wait the failure
    overlaps would otherwise count seconds the failure covers."""
    document = closed(
        timed_run(0, 5),
        close_at=100,
        failures=[failure(10, None)],
        full_checks={"ci_timing": timing(20, 20, 40)},
        pause_log=[escalation(50, "returns"), resumed(60)],
    )
    assert split(document) == cycle.TimeSplit(None, None, None, None, None)


def _rules_names(tree):
    """The local names bound by an import of `core.process.domain.rules` or of something in it."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module.endswith("rules") or f"{module}.{alias.name}".endswith(".rules"):
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("rules"):
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def rules_violations(source, helpers=None):
    """Where `source` reaches `core.process.domain.rules`. With `helpers` None, any import of it
    counts; with helper names, only an import inside one of those functions, or a use of a name
    any import bound (at module level too) inside one, counts: the module's older imports stay."""
    tree = ast.parse(source)
    bound = _rules_names(tree)
    if helpers is None:
        return [f"line {n.lineno}" for n in ast.walk(tree) if _imports_rules(n)]
    found = []
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in helpers):
            continue
        for node in ast.walk(fn):
            if _imports_rules(node):
                found.append(f"{fn.name}: import at line {node.lineno}")
            elif isinstance(node, ast.Name) and node.id in bound:
                found.append(f"{fn.name}: uses {node.id} at line {node.lineno}")
    return found


def _imports_rules(node):
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module.endswith("rules") or any(
            f"{module}.{a.name}".endswith(".rules") for a in node.names
        )
    return isinstance(node, ast.Import) and any(a.name.endswith("rules") for a in node.names)


def _module_import(node):
    """`from agentic.core.process.domain import ..., rules`, the one other way `cycle` reaches
    `rules` (GH-757): it calls `rules.terminal_failures` through the module, so the reasons that
    subdivide a pause come from the filter `pause_kind` reads and from no copy of it."""
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "agentic.core.process.domain"
        and ("rules", None) in [(a.name, a.asname) for a in node.names]
    )


def only_pause_kind_violations(source):
    """Where `source` reaches `core.process.domain.rules` other than by exactly one
    `from ...rules import pause_kind` with no alias; a missing import is a violation too."""
    nodes = [n for n in ast.walk(ast.parse(source)) if _imports_rules(n) and not _module_import(n)]
    found = [
        f"line {n.lineno}"
        for n in nodes
        if not isinstance(n, ast.ImportFrom)
        or [(a.name, a.asname) for a in n.names] != [("pause_kind", None)]
    ]
    if len(nodes) != 1:
        found.append(f"{len(nodes)} imports of rules, exactly one expected")
    return found


PAUSE_KIND_IMPORT = "from agentic.core.process.domain.rules import pause_kind\n"


@pytest.mark.parametrize(
    "source",
    [
        "from agentic.core.process.domain.rules import pause_kind, EXTERNAL_REASONS\n",
        "from agentic.core.process.domain.rules import pause_kind as k\n",
        "from agentic.core.process.domain.rules import EXTERNAL_REASONS\n",
        "from agentic.core.process.domain import rules\n",
        "import agentic.core.process.domain.rules\n",
        PAUSE_KIND_IMPORT + "from agentic.core.process.domain.rules import EXTERNAL_REASONS\n",
        PAUSE_KIND_IMPORT * 2,
        "import os\n",
    ],
)
def test_the_pause_kind_guard_rejects_anything_but_one_plain_import_of_pause_kind(source):
    assert only_pause_kind_violations(source) != []


def test_the_pause_kind_guard_accepts_exactly_one_plain_import_of_pause_kind():
    assert only_pause_kind_violations(PAUSE_KIND_IMPORT + "import os\n") == []


def test_the_time_split_modules_import_only_pause_kind_from_process_rules():
    from pathlib import Path

    def source(name):
        return Path(cycle.__file__).with_name(f"{name}.py").read_text()

    # GH-593: whether a pause is mechanical is `rules.pause_kind`'s call alone, so `cycle` takes
    # that one name and nothing else from `rules`.
    assert only_pause_kind_violations(source("cycle")) == []
    # parallel_factor keeps its older `rules` import; only the new helpers must not reach it.
    assert rules_violations(source("parallel_factor"), {"clip", "difference"}) == []


MODULE_IMPORT = "from agentic.core.process.domain.rules import REVIEW_PREFIX as X\n"


@pytest.mark.parametrize(
    "source",
    [
        # the existing `rules.X` attribute use
        "from agentic.core.process.domain import rules\ndef clip(a):\n    return rules.X\n",
        # a name bound by a module-level from-import and used in the helper
        MODULE_IMPORT + "def difference(a):\n    return X\n",
        # a from-import inside the helper
        "def clip(a):\n    from agentic.core.process.domain.rules import X\n    return X\n",
        "def difference(a):\n    import agentic.core.process.domain.rules\n    return 1\n",
    ],
)
def test_the_rules_guard_rejects_every_way_a_helper_reaches_rules(source):
    assert rules_violations(source, {"clip", "difference"}) != []


def test_the_rules_guard_accepts_an_older_import_the_helpers_do_not_use():
    source = MODULE_IMPORT + "def other():\n    return X\ndef clip(a):\n    return a\n"
    assert rules_violations(source, {"clip", "difference"}) == []


def test_the_rules_guard_rejects_any_import_of_rules_in_a_module_that_takes_none():
    assert rules_violations(MODULE_IMPORT) != []
    assert rules_violations("import os\nfrom a import b\n") == []


def _random_document(rng):
    """A synthetic task: overlapping runs, failures, CI timings and pause epochs, at random
    second offsets, some of them open, clamped or unreadable."""
    minute = lambda: rng.randint(-30, 700) / 2  # noqa: E731 -- half minutes, some before 0
    runs = []
    for _ in range(rng.randint(1, 4)):
        start = rng.randint(0, 400) / 2
        runs.append(timed_run(start, start + rng.randint(1, 200) / 2))
    document = {"status": "merged", "runs": runs}
    if rng.random() < 0.6:
        document["closed_at"] = m(rng.randint(100, 800) / 2)
    else:
        document["status"] = "ready_for_review"
    failures = []
    for _ in range(rng.randint(0, 3)):
        start = minute()
        failures.append(
            failure(
                start, start + rng.randint(1, 100) / 2, reason=rng.choice(["upstream", "other"])
            )
        )
    document["failures"] = failures
    if rng.random() < 0.7:
        pushed = minute()
        document["full_checks"] = {
            "ci_timing": timing(pushed, pushed + rng.randint(-5, 5), pushed + rng.randint(-5, 60))
        }
    if rng.random() < 0.5:
        pushed = minute()
        document["merge_ci"] = [
            {"ci_timing": timing(pushed, pushed + rng.randint(0, 5), pushed + rng.randint(0, 60))}
        ]
    log = []
    cursor = rng.randint(-20, 50) / 2
    for _ in range(rng.randint(0, 3)):
        kind = rng.choice(["failure", "returns", "ci"])
        if kind == "failure":
            log.append(failed_entry(cursor, reason=rng.choice(["account_limit", "other"])))
        elif kind == "ci":
            pushed = cursor - rng.randint(1, 30)
            log.append(escalation(cursor, "ci", ci=timing(pushed, pushed, None, "no_verdict")))
        else:
            log.append(escalation(cursor, "returns"))
        cursor += rng.randint(1, 100) / 2
        if rng.random() < 0.9:
            log.append(resumed(cursor))
        cursor += rng.randint(1, 50) / 2
    document["pause_log"] = log
    return document


def test_the_classes_and_the_remainder_add_up_to_the_cycle():
    rng = random.Random(467)
    known = 0
    for _ in range(300):
        document = _random_document(rng)
        got = split(document)
        cycle_s = cycle.cycle_seconds(document)
        parts = [
            cycle.clipped_active_seconds(document),
            got.failed_s,
            got.waiting_s,
            got.paused_external_s,
            got.paused_human_s,
            got.unattributed_s,
        ]
        if cycle_s is None or None in parts:
            continue
        known += 1
        assert all(part >= 0 for part in parts)
        assert sum(parts) == pytest.approx(cycle_s)
    assert known >= 200


# --- Lead time and the per-path report (GH-599) ---------------------------------------------

LABELED = "2026-10-20T10:00:00Z"
MERGED = "2026-10-20T12:00:00Z"
KNOWN = {"labeled_at": LABELED, "merged_at": MERGED}


def routed(initial="full", **over):
    """A closed document the router classified (the `path_recorded` cut-off holds)."""
    return {
        "status": "ready_for_review",
        "closed_at": CLOSED,
        "initial_path": initial,
        "path": initial,
        "path_routed": True,
        "lead_time": dict(KNOWN),
        **over,
    }


def test_lead_time_seconds_is_the_difference_of_the_two_instants():
    assert cycle.lead_time_seconds(routed()) == 7200.0


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"unknown": "no_merge"},
        {"unknown": "unreadable"},
        {"labeled_at": LABELED},
        {"labeled_at": "not a time", "merged_at": MERGED},
        {"labeled_at": MERGED, "merged_at": LABELED},
        "garbage",
    ],
)
def test_lead_time_seconds_is_unknown_never_zero(value):
    document = routed()
    document["lead_time"] = value
    assert cycle.lead_time_seconds(document) is None
    del document["lead_time"]
    assert cycle.lead_time_seconds(document) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dict(KNOWN), "known"),
        ({"unknown": "no_merge"}, "no_merge"),
        ({"unknown": "unreadable"}, "unreadable"),
        ({"labeled_at": MERGED, "merged_at": LABELED}, "negative"),
        ({"labeled_at": "x", "merged_at": MERGED}, "missing"),
        ({"unknown": "something else"}, "missing"),
        ("garbage", "missing"),
    ],
)
def test_lead_time_class_of_every_shape(value, expected):
    assert cycle.lead_time_class(routed(lead_time=value)) == expected


def test_a_closed_document_without_the_key_or_not_routed_is_unrecorded():
    without_key = routed()
    del without_key["lead_time"]
    not_routed = routed()
    del not_routed["path_routed"]
    assert cycle.lead_time_class(without_key) == "unrecorded"
    assert cycle.lead_time_class(not_routed) == "unrecorded"


def test_path_recorded_needs_both_the_initial_path_and_the_marker():
    assert cycle.path_recorded(routed())
    assert not cycle.path_recorded({"initial_path": "light", "path": "light"})
    assert not cycle.path_recorded({"path_routed": True})
    assert not cycle.path_recorded({})


def lead_at(hours, initial="light", **over):
    return routed(
        initial,
        lead_time={"labeled_at": LABELED, "merged_at": f"2026-10-20T{10 + hours:02d}:00:00Z"},
        **over,
    )


def report(*documents):
    return cycle.path_report([cycle.path_facts(d) for d in documents])


def test_the_median_is_per_initial_path_with_n_of_m():
    result = report(
        lead_at(1),
        lead_at(3),
        lead_at(5, "full"),
        routed("light", lead_time={"unknown": "unreadable"}),
        # promoted light -> full: still in the light bucket, by its initial path
        lead_at(2, "light", path="full", promotion={"kind": "owner", "text": "t"}),
    )
    light, full = result.paths["light"], result.paths["full"]
    assert (light.median_s, light.n, light.m) == (2 * 3600.0, 3, 4)
    assert (full.median_s, full.n, full.m) == (5 * 3600.0, 1, 1)


def test_an_unrecorded_document_is_outside_every_median_and_counted():
    old = {"status": "ready_for_review", "closed_at": CLOSED, "initial_path": "light"}
    result = report(old, lead_at(1), {"status": "ready_for_review", "closed_at": CLOSED})
    assert result.paths["light"].m == 1
    assert dict(result.exclusions)["unrecorded"] == 2


def test_open_documents_are_not_in_the_report():
    result = report({"status": "developing", "initial_path": "full", "path_routed": True})
    assert result.paths == {} and not any(result.promotions.values())
    assert all(count == 0 for _, count in result.exclusions)


def test_exclusions_are_global_by_reason_in_a_fixed_order():
    result = report(
        lead_at(1),
        routed(lead_time={"unknown": "no_merge"}),
        routed(lead_time={"unknown": "no_merge"}),
        routed(lead_time={"unknown": "unreadable"}),
        routed(lead_time={"labeled_at": MERGED, "merged_at": LABELED}),
        routed(lead_time="garbage"),
        {"status": "discarded", "initial_path": "full", "path_routed": True},
    )
    assert result.exclusions == (
        ("unrecorded", 1),
        ("no_merge", 2),
        ("unreadable", 1),
        ("negative", 1),
        ("missing", 1),
    )


def test_no_delivery_endings_are_closed_documents():
    ended = routed(status="discarded", lead_time={"unknown": "no_merge"})
    del ended["closed_at"]
    result = report(ended)
    assert dict(result.exclusions)["no_merge"] == 1 and result.paths["full"].m == 1


def test_promotions_are_counted_by_kind_among_recorded_tasks_only():
    promoted = {"from_role": "Developer", "at": "x"}
    old = {"status": "ready_for_review", "closed_at": CLOSED, "initial_path": "light"}
    result = report(
        lead_at(1, promotion={**promoted, "kind": "risk_area", "text": "t"}),
        lead_at(1, promotion={**promoted, "kind": "risk_area", "text": "t"}),
        lead_at(1, promotion={**promoted, "kind": "owner", "text": None}),
        lead_at(1, promotion={**promoted, "kind": "made-up"}),
        lead_at(1, promotion="garbage"),
        {**old, "promotion": {**promoted, "kind": "role"}},
        lead_at(1),
    )
    assert result.promotions == {
        "plan_over_cap": 0,
        "risk_area": 2,
        "owner": 1,
        "role": 0,
        "other": 2,
    }


def test_the_report_has_every_kind_even_with_no_promotion():
    assert tuple(report().promotions) == (*get_args(task_path.PromotionKind), "other")
