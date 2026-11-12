"""GH-544, the chain: an `escalate_adr` question, an owner's decision on the issue, then a `land`
that dispatches `apply_adr_decision` and runs it (`landing_adr`) on the memory ports, with a
stub Developer that records its brief and commits a one-line edit. The collision is a real
one (`adr_collision` over the memory workspace): the branch's `0049-mine` against master's
`0049-theirs`, and a line of code that cites it in a way the grammar cannot rewrite."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from agentic.core.process.application import landing, landing_adr, landing_plan
from agentic.core.process.application.adr_collision import adr_collision, adr_decided
from agentic.core.process.application.checkouts import SetupFailed
from agentic.core.process.application.landing_adr import brief
from agentic.core.process.application.landing_plan import entries as plan_entries
from agentic.core.process.application.ports import (
    CommandResult,
    GitError,
    IssueComment,
    IssueState,
    RawResult,
    Upstream,
)
from agentic.core.process.domain import adr_numbers, rules
from agentic.core.process.domain import landing as landing_rules
from tests.core.process.application.test_landing_renumber import (
    BRANCH,
    MINE,
    NOTES,
    _pushed,
    _world,
)
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import URL, _HeldLocks, _pr
from tests.core.process.domain.test_landing import _facts
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryTracker,
    ScriptedRunner,
    memory_ports,
)

CODE = "app/main.py"
CITES_CODE = 'MSG = "see (docs/adr/0049)"'
LIVE = frozenset({"escalate_adr", "apply_adr_decision", "re_review"})
ASKED = "2026-10-14T00:00:00+00:00"
ESCALATED_COMMENT = "2026-10-14T00:00:10+00:00"
DECIDED = "2026-10-14T01:00:00+00:00"
DECISION = "renumber to the next free number and point app/main.py to it"


@pytest.fixture(autouse=True)
def _owner_decides(monkeypatch):
    """The chain below is the owner's: the question is asked first. The rule's own apply
    (D-be76583a) is switched on only by the tests that exercise it."""
    monkeypatch.setattr(landing_rules, "ADR_AUTO_APPLY", False)


class StubDeveloper(ScriptedRunner):
    """The Developer of the landing: records the prompt it was given, then runs `edit` in the
    checkout (a write and a commit), as the model would, and delivers."""

    def __init__(self, ports, edit) -> None:
        super().__init__()
        self.ports, self.edit = ports, edit

    async def run_once(self, role, prompt: str, **kwargs: Any) -> RawResult:
        self.calls.append({"slug": role.slug, "prompt": prompt, "role": role, **kwargs})
        self.edit(self.ports, kwargs["cwd"])
        presubmit = kwargs.get("presubmit")
        if presubmit is not None:
            presubmit()
        return RawResult(
            {"markdown": "### Done", "summary": "s", "assumptions": [], "coverage": []},
            0.5,
            3,
            "session",
        )


def _point_at_the_new_number(ports, cwd):
    (cwd / CODE).write_text('MSG = "see (docs/adr/0050)"\n', encoding="utf-8")
    ports.workspace.commit_paths(cwd, [CODE], "Point the reference at the new number")
    ports.workspace.diff_paths = (CODE,)  # what the Developer's commit changes


def _edit_elsewhere(ports, cwd):
    (cwd / NOTES).write_text("something else entirely\n", encoding="utf-8")
    ports.workspace.commit_paths(cwd, [NOTES], "Edit outside the spots")
    ports.workspace.diff_paths = (NOTES,)


def _change_a_line_beside_the_spot(ports, cwd):
    """Points the spot at the new number, and also rewrites a line that is not a spot."""
    _write_code(ports, cwd, 'MSG = "see (docs/adr/0050)"\nOTHER = 2\n')


def _add_a_line_after_the_spot(ports, cwd):
    _write_code(ports, cwd, 'MSG = "see (docs/adr/0050)"\nOTHER = 1\nEXTRA = 3\n')


def _write_code(ports, cwd, text):
    (cwd / CODE).write_text(text, encoding="utf-8")
    ports.workspace.commit_paths(cwd, [CODE], "Edit the spot and more")
    ports.workspace.diff_paths = (CODE,)


def _boom(ports, cwd):
    raise RuntimeError("the harness broke")


def _round(tmp_path, ports, live=LIVE, paths=(MINE, NOTES, CODE)):
    lines: list[str] = []
    ports.workspace.diff_paths = paths  # the pull request's own paths
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lines.append,
        clock=lambda: ASKED,
        live=live,
    )
    return result, lines


def _asked(tmp_path, edit=_point_at_the_new_number, *, comments=None, stamps=None, labels=()):
    """The question asked on the collision, then the owner's decision on the issue. `stamps` is
    the `(at, resolved_at)` the question's entry carries, when a test pins them."""
    ports, head = _world(tmp_path, extra=((CODE, CITES_CODE),))
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    ports.workspace.diff_paths = (MINE, NOTES, CODE)
    (tmp_path / "GH-5" / "app").mkdir(parents=True)
    first, _ = _round(tmp_path, ports)
    assert (first.kind, first.step) == ("dispatched", "escalate_adr"), first
    if stamps is not None:
        at, resolved_at = stamps
        stamped = [{**e, "at": at, "resolved_at": resolved_at} for e in _entries(ports)]
        ports.documents.record_closure("GH-5", {"landing": stamped})
    ports.tracker.timestamped["GH-5"] = [
        IssueComment(body, at)
        for body, at in (
            comments or [("Escalated: ADR collision", ESCALATED_COMMENT), (DECISION, DECIDED)]
        )
    ]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, labels)
    ports.documents.record_closure(
        "GH-5",
        {"title": "t", "description": "d", "sections": {"Human": f"### Decision\n\n{DECISION}"}},
    )
    return ports, head


def _entries(ports):
    return plan_entries(ports.documents.load("GH-5"))


def test_a_decision_on_an_escalated_adr_collision_is_applied_pushed_and_reviewed_again(tmp_path):
    ports, head = _asked(tmp_path)
    [asked] = _entries(ports)
    assert asked["moves"] == [[MINE, "docs/adr/0050-mine.md"]]

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert result.reason != "not_live"
    assert ports.workspace.pushes == [(BRANCH, head)]
    applied = _entries(ports)[-1]
    assert applied["step"] == "apply_adr_decision" and applied["outcome"] == "applied"
    new = ports.workspace.branch_commits[-1].sha
    assert applied["head_after"] == new != head
    [prompt] = ports.runner.prompts("developer")
    assert DECISION in prompt and f"{CODE}:1: {CITES_CODE}" in prompt
    assert "Never choose an ADR number" in prompt
    assert ports.workspace.show(new, CODE) == 'MSG = "see (docs/adr/0050)"\n'
    assert ports.workspace.show(new, "docs/adr/0050-mine.md") == "# ADR-0050: mine\n"
    unit = ports.documents.load("GH-5")["subtasks"]["only"]
    assert unit["reviewed_head"] == head  # a code edit is not carried

    # The next round: the new head goes through the table, and the review it owes is next.
    _pushed(ports)
    again, _ = _round(tmp_path, ports)
    assert again.step == "re_review"


def test_the_review_after_an_applied_decision_is_taken_when_the_passes_are_spent(tmp_path):
    ports, _ = _asked(tmp_path)
    _round(tmp_path, ports)
    _pushed(ports)
    document = ports.documents.load("GH-5")
    spent = [
        {"head": f"old{n}", "step": "fix", "refusal": "checks_failed", "resolved_at": ASKED}
        for n in range(landing_rules.LANDING_PASSES_MAX)
    ]
    ports.documents.record_closure("GH-5", {"landing": [*spent, *document["landing"]]})

    result, _ = _round(tmp_path, ports)

    assert result.step == "re_review"


def test_a_legacy_question_without_moves_is_still_applied(tmp_path):
    ports, head = _asked(tmp_path)
    legacy = [{k: v for k, v in e.items() if k != "moves"} for e in _entries(ports)]
    ports.documents.record_closure("GH-5", {"landing": legacy})

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == [(BRANCH, head)]


def test_an_edit_outside_the_spots_pushes_nothing_and_asks_again(tmp_path):
    ports, head = _asked(tmp_path, _edit_elsewhere)

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == []
    assert ports.workspace.branch_commits[-1].sha == head  # the checkout was put back
    steps = [(e["step"], e.get("outcome")) for e in _entries(ports)]
    assert steps == [
        ("escalate_adr", None),
        ("apply_adr_decision", "out_of_bounds"),
        ("escalate_adr", None),
    ]
    document = ports.documents.load("GH-5")
    assert document["escalated"] and document["escalation_class"] == "questions"
    assert ("GH-5", "needs-human") in ports.tracker.labels
    assert "out_of_bounds" in document["questions_for_human"][0]
    assert _entries(ports)[-1]["resolved_at"]


@pytest.mark.parametrize("edit", [_change_a_line_beside_the_spot, _add_a_line_after_the_spot])
def test_an_edit_of_a_spot_file_beyond_its_spot_lines_is_out_of_bounds(tmp_path, edit):
    """The path is a spot's, so only the line-level guard (`adr_numbers.outside_bounds`) can
    refuse it: a non-spot line changed, or a line added, in the file that has a spot."""
    ports, head = _asked(tmp_path, edit)
    ports.workspace.files[(head, CODE)] = CITES_CODE + "\nOTHER = 1\n"

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == []
    assert ports.workspace.branch_commits[-1].sha == head
    assert ports.workspace.resets[-1][1] == head
    assert [(e["step"], e.get("outcome")) for e in _entries(ports)][1] == (
        "apply_adr_decision",
        "out_of_bounds",
    )


def test_moves_that_differ_from_the_recorded_ones_are_stale_and_push_nothing(tmp_path):
    ports, _ = _asked(tmp_path)
    ports.workspace.advance_master("docs/adr/0050-busy.md")  # the rule would now choose 0051

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == []
    assert ports.runner.calls == []
    assert _entries(ports)[1]["outcome"] == "stale_moves"


def test_an_exception_in_the_role_runner_leaves_the_checkout_at_the_head(tmp_path):
    ports, head = _asked(tmp_path, _boom)

    with pytest.raises(RuntimeError):
        _round(tmp_path, ports)

    assert ports.workspace.pushes == []
    assert ports.workspace.branch_commits[-1].sha == head
    assert ports.workspace.resets and ports.workspace.resets[-1][1] == head


def test_a_comment_posted_before_the_escalation_does_not_release(tmp_path):
    ports, _ = _asked(
        tmp_path,
        comments=[(DECISION, "2026-10-13T23:00:00+00:00"), ("Escalated: ADR", ESCALATED_COMMENT)],
    )

    result, _ = _round(tmp_path, ports)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert ports.runner.calls == []
    assert rules.task_branch("GH-5") == BRANCH


# -- GH-561: the `Escalated:` comment is posted while the step runs, before `resolved_at` ------

# (at, resolved_at, the `Escalated:` comment's creation) of the two real documents, and the case
# where the comment shares its second with the entry's `at`.
GH_407 = ("2026-11-06T21:42:07+00:00", "2026-11-06T21:42:12+00:00", "2026-11-06T21:42:11+00:00")
GH_601 = ("2026-11-07T06:06:26+00:00", "2026-11-07T06:06:29+00:00", "2026-11-07T06:06:29+00:00")
EQUAL_SECOND = (
    "2026-11-07T08:00:00+00:00",
    "2026-11-07T08:00:04+00:00",
    "2026-11-07T08:00:00+00:00",
)
OWNER_ANSWERED = "2026-11-08T09:00:00+00:00"


def _answered(tmp_path, stamps, created, *, decision=True, labels=()):
    comments = [("Escalated: ADR collision", created)]
    if decision:
        comments.append((DECISION, OWNER_ANSWERED))
    return _asked(tmp_path, comments=comments, stamps=stamps, labels=labels)


@pytest.mark.parametrize(
    ("at", "resolved_at", "created"),
    [GH_407, GH_601, EQUAL_SECOND],
    ids=["gh-407", "gh-601", "equal-second"],
)
def test_the_decision_after_the_comment_the_step_posted_releases_the_escalation(
    tmp_path, at, resolved_at, created
):
    ports, _ = _answered(tmp_path, (at, resolved_at), created)

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert result.reason != "escalated_waiting_on_human"


def test_without_a_decision_comment_the_escalation_still_waits(tmp_path):
    ports, _ = _answered(tmp_path, GH_407[:2], GH_407[2], decision=False)

    result, _ = _round(tmp_path, ports)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert ports.runner.calls == []


def test_with_needs_human_still_on_the_issue_the_escalation_still_waits(tmp_path):
    ports, _ = _answered(tmp_path, GH_407[:2], GH_407[2], labels=("needs-human",))

    result, _ = _round(tmp_path, ports)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert ports.runner.calls == []


def test_an_escalated_comment_a_minute_before_the_step_started_does_not_release(tmp_path):
    at, resolved_at, _ = GH_407
    ports, _ = _answered(tmp_path, (at, resolved_at), "2026-11-06T21:41:07+00:00")

    result, _ = _round(tmp_path, ports)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert ports.runner.calls == []


# -- GH-570: the slots are counted per collision, so GH-407's second collision is not stuck -----

OLD_HEAD, NEW_HEAD = "e7b34747" + "0" * 32, "a21ed649" + "0" * 32
FIRST_RELEASE = "2026-11-08T05:00:00+00:00"
SECOND_RELEASE = "2026-11-08T14:52:30+00:00"
THIRD_RELEASE = "2026-11-08T16:30:00+00:00"
M1 = [["docs/adr/0070-x.md", "docs/adr/0075-x.md"]]
M2 = [["docs/adr/0070-x.md", "docs/adr/0076-x.md"]]


def _entry(at, head, step, **over) -> dict:
    return {
        "head": head,
        "step": step,
        "refusal": "adr_number_taken",
        "at": f"2026-11-{at}:00+00:00",
        **over,
    }


def _gh_407_entries(*, third_released=True) -> list[dict]:
    """GH-407's landing list (PR #618): the first collision settled by an `applied` on another
    head, the second one's decision ended `checks_red`, and the owner decided again."""
    third = _entry("08T16:06", NEW_HEAD, "escalate_adr", resolved_at="2026-11-08T16:06:05+00:00")
    if third_released:
        third["released_at"] = THIRD_RELEASE
    return [
        _entry(
            "07T21:42",
            OLD_HEAD,
            "escalate_adr",
            resolved_at="2026-11-07T21:42:05+00:00",
            released_at=FIRST_RELEASE,
            moves=M1,
        ),
        _entry(
            "08T06:41",
            OLD_HEAD,
            "apply_adr_decision",
            decision_at=FIRST_RELEASE,
            outcome="applied",
            head_after=NEW_HEAD[:8] + "1" * 32,
        ),
        _entry(
            "08T14:52",
            NEW_HEAD,
            "escalate_adr",
            resolved_at="2026-11-08T14:52:05+00:00",
            released_at=SECOND_RELEASE,
            moves=M2,
        ),
        _entry(
            "08T14:53",
            NEW_HEAD,
            "apply_adr_decision",
            decision_at=SECOND_RELEASE,
            outcome="checks_red",
        ),
        third,
    ]


def _second_collision() -> adr_numbers.Ambiguous:
    return adr_numbers.Ambiguous("unclassified_token", "app/main.py:1", tuple(map(tuple, M2)))


def _gh_407_facts() -> rules.MergeFacts:
    return _facts(pr_head=NEW_HEAD, adr_collision=_second_collision())


def _gh_407_document(entries) -> dict:
    return {"escalated": "adr_number_taken", "status": "waiting_on_human", "landing": entries}


def test_gh_407_a_fresh_decision_after_the_second_escalation_is_due(tmp_path):
    raw = _gh_407_document(_gh_407_entries())
    facts = _gh_407_facts()

    step = landing_plan.step_for(raw, "adr_number_taken", facts)

    assert step.kind == "apply_adr_decision"


def test_gh_407_the_escalated_task_is_released_for_the_decision(tmp_path):
    raw = _gh_407_document(_gh_407_entries())

    assert landing_plan.release_asked(
        raw, "apply_adr_decision", refusal="adr_number_taken", facts=_gh_407_facts()
    )


def test_gh_407_one_more_spent_apply_on_the_same_key_is_not_due(tmp_path):
    more = _entry(
        "08T15:30",
        NEW_HEAD,
        "apply_adr_decision",
        decision_at=SECOND_RELEASE,
        outcome="still_colliding",
        bounds=landing_rules.ADR_EDIT_BOUNDS,
    )
    entries = _gh_407_entries()
    entries[3]["bounds"] = landing_rules.ADR_EDIT_BOUNDS  # the checks_red apply of the same key
    entries[3]["gate_tier"] = "delivery"  # judged on a tier, so its slot stays spent (GH-585)
    entries.insert(4, more)
    raw = _gh_407_document(entries)
    facts = _gh_407_facts()

    assert landing_plan.step_for(raw, "adr_number_taken", facts).kind != "apply_adr_decision"
    assert not landing_plan.release_asked(
        raw, "apply_adr_decision", refusal="adr_number_taken", facts=facts
    )


def test_gh_407_land_dispatches_the_apply_not_not_live(tmp_path):
    comments = [
        ("Escalated: ADR collision", "2026-11-08T16:06:03+00:00"),
        (DECISION, "2026-11-08T17:00:00+00:00"),
    ]
    stamps = ("2026-11-08T16:06:00+00:00", "2026-11-08T16:06:05+00:00")
    ports, head = _asked(tmp_path, comments=comments, stamps=stamps)
    [asked] = _entries(ports)
    assert asked["moves"] == [[MINE, "docs/adr/0050-mine.md"]]
    second = _gh_407_entries(third_released=False)
    second[2] = {**second[2], "head": head, "moves": asked["moves"]}
    second[3] = {**second[3], "head": head}
    second[4] = {**asked}  # the 16:06 question, as the step recorded it
    ports.documents.record_closure("GH-5", {"landing": second})

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert result.reason != "not_live"
    assert _entries(ports)[-1]["step"] == "apply_adr_decision"


# --- GH-570: the red check is recorded on the entry that ended `checks_red` -----------------


def _red_world(tmp_path, commands, developer=None):
    """The asked collision whose product declares two checks, run on `commands`."""
    ports, head = _asked(tmp_path)
    ports = dataclasses.replace(
        ports,
        stack=MemoryStack(checks={"tests": "run tests", "lint": "run lint"}),
        commands=commands,
    )
    ports = dataclasses.replace(
        ports, runner=(developer or StubDeveloper)(ports, _point_at_the_new_number)
    )
    return ports, head


def _apply_entry(ports) -> dict:
    return [e for e in _entries(ports) if e["step"] == "apply_adr_decision"][0]


def test_a_checks_red_apply_records_which_check_was_red_and_its_last_lines(tmp_path):
    commands = MemoryCommands(
        **{"run tests": [CommandResult(1, "collected 3\nFAILED test_a - boom\n")] * 5}
    )
    ports, _ = _red_world(tmp_path, commands)

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red"
    assert entry["red_checks"] == [
        {
            "name": "tests",
            "tail": ["collected 3", "FAILED test_a - boom"],
            "recovered": False,
            "timed_out": False,
        }
    ]
    assert "red_more" not in entry
    assert ports.workspace.pushes == []


def test_only_the_last_gate_run_is_reported_when_the_gate_ran_twice(tmp_path):
    commands = MemoryCommands(
        **{
            "run tests": [CommandResult(1, "FIRST run: tests red\n"), CommandResult(0, "ok\n")],
            "run lint": [
                CommandResult(0, "clean\n"),
                *[CommandResult(1, "SECOND run: lint red\n")] * 4,
            ],
        }
    )

    class TwiceDeveloper(StubDeveloper):
        async def run_once(self, role, prompt, **kwargs):
            result = await super().run_once(role, prompt, **kwargs)
            kwargs["presubmit"]()  # the gate runs again on the same checkout
            return result

    ports, _ = _red_world(tmp_path, commands, TwiceDeveloper)

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red"
    assert entry["red_checks"] == [
        {"name": "lint", "tail": ["SECOND run: lint red"], "recovered": False, "timed_out": False}
    ]


def test_an_apply_that_does_not_end_checks_red_records_no_red_detail(tmp_path):
    ports, _ = _asked(tmp_path, _edit_elsewhere)

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "out_of_bounds"
    assert "red_checks" not in entry and "red_more" not in entry


# --- GH-576: the lines the renumbering itself moves are inside the bounds ----------------------

INDEX = "docs/architecture/decisiones.md"
ROW_MINE = "| [0049](../adr/0049-mine.md) | mine |"
ROW_THEIRS = "| [0049](../adr/0049-theirs.md) | theirs |"
ROW_OLD = "| [0048](../adr/0048-old.md) | old |"
ROW_MINE_NEW = "| [0050](../adr/0050-mine.md) | mine |"
# What a merge left: the branch's row before master's, so the renumbered row is out of order.
MERGED_INDEX = "\n".join([ROW_OLD, ROW_MINE, ROW_THEIRS]) + "\n"
SORTED_INDEX = "\n".join([ROW_OLD, ROW_THEIRS, ROW_MINE_NEW]) + "\n"


class IndexChecks(MemoryCommands):
    """The `docs` check as a function of the checkout: red while an index row still carries the
    old label or the rows are out of order, green otherwise."""

    def run(self, cwd, command, timeout_s, *, env=None) -> CommandResult:
        result = super().run(cwd, command, timeout_s, env=env)
        if command != "run docs":
            return result
        index = cwd / INDEX
        rows = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
        labels = [row.split("]")[0] for row in rows]
        if any("0049-mine" in row for row in rows) or labels != sorted(labels):
            return CommandResult(1, "docs stale: the ADR index is out of date\n")
        return CommandResult(0, "ok\n")


def _spend_two_slots(ports, head) -> None:
    """GH-407's document: two applies on this head and moves, both written before `bounds`."""
    [asked] = _entries(ports)
    first = {**asked, "at": "2026-10-13T00:00:00+00:00", "released_at": "2026-10-13T01:00:00+00:00"}
    second = {
        **first,
        "at": "2026-10-13T02:00:00+00:00",
        "released_at": "2026-10-13T03:00:00+00:00",
    }
    applies = [
        {"head": head, "step": "apply_adr_decision", "decision_at": first["released_at"]},
        {"head": head, "step": "apply_adr_decision", "decision_at": second["released_at"]},
    ]
    applies[0].update(outcome="checks_red", at="2026-10-13T01:10:00+00:00")
    applies[1].update(outcome="out_of_bounds", at="2026-10-13T03:10:00+00:00")
    for entry in (first, second, *applies):
        entry["resolved_at"] = "2026-10-13T04:00:00+00:00"
    ports.documents.record_closure(
        "GH-5", {"landing": [first, applies[0], second, applies[1], asked]}
    )


def _gh_407_world(tmp_path, edit, *, own_row=False, commands=None):
    extra = ((CODE, CITES_CODE), (INDEX, ROW_MINE)) if own_row else ((CODE, CITES_CODE),)
    ports, head = _asked_with(tmp_path, edit, extra)
    ports = dataclasses.replace(
        ports,
        stack=MemoryStack(checks={"docs": "run docs"}),
        commands=commands or IndexChecks(),
    )
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    if not own_row:  # a merge brought the row in: no own commit added it
        ports.workspace.files[(head, INDEX)] = MERGED_INDEX
    else:
        ports.workspace.files[(head, INDEX)] = ROW_MINE + "\n"
    _spend_two_slots(ports, head)
    return ports, head


def _asked_with(tmp_path, edit, extra, *, prepare=None, paths=(MINE, NOTES, CODE)):
    ports, head = _world(tmp_path, extra=extra)
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    if prepare is not None:
        prepare(ports, head)
    (tmp_path / "GH-5" / "app").mkdir(parents=True)
    first, _ = _round(tmp_path, ports, paths=paths)
    assert (first.kind, first.step) == ("dispatched", "escalate_adr"), first
    ports.tracker.timestamped["GH-5"] = [
        IssueComment("Escalated: ADR collision", ESCALATED_COMMENT),
        IssueComment(DECISION, DECIDED),
    ]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    ports.documents.record_closure(
        "GH-5",
        {"title": "t", "description": "d", "sections": {"Human": f"### Decision\n\n{DECISION}"}},
    )
    return ports, head


def _spot_and_index(index_text):
    def edit(ports, cwd):
        (cwd / CODE).write_text('MSG = "see (docs/adr/0050)"\n', encoding="utf-8")
        (cwd / INDEX).parent.mkdir(parents=True, exist_ok=True)
        (cwd / INDEX).write_text(index_text, encoding="utf-8")
        ports.workspace.commit_paths(cwd, [CODE, INDEX], "Point the spot, sort the index")
        ports.workspace.diff_paths = (CODE, INDEX)

    return edit


def test_gh_407_the_index_row_a_merge_brought_in_follows_the_move_and_the_apply_pushes(tmp_path):
    ports, head = _gh_407_world(tmp_path, _spot_and_index(SORTED_INDEX))

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert ports.workspace.pushes == [(BRANCH, head)]
    entry = _entries(ports)[-1]
    assert entry["outcome"] == "applied" and entry["bounds"] == landing_rules.ADR_EDIT_BOUNDS
    pushed = entry["head_after"]
    rows = (ports.workspace.show(pushed, INDEX) or "").splitlines()
    assert rows == [ROW_OLD, ROW_THEIRS, ROW_MINE_NEW]
    assert ports.workspace.show(pushed, CODE) == 'MSG = "see (docs/adr/0050)"\n'


def test_gh_407_the_index_row_the_branch_added_follows_the_move_and_the_apply_pushes(tmp_path):
    ports, head = _gh_407_world(tmp_path, _spot_and_index(ROW_MINE_NEW + "\n"), own_row=True)

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert ports.workspace.pushes == [(BRANCH, head)]
    assert _entries(ports)[-1]["outcome"] == "applied"


def test_a_pass_that_leaves_the_index_unsorted_ends_checks_red_not_pushed(tmp_path):
    unsorted = "\n".join([ROW_OLD, ROW_MINE_NEW, ROW_THEIRS]) + "\n"
    ports, _ = _gh_407_world(tmp_path, _spot_and_index(unsorted))

    _round(tmp_path, ports)

    entry = [e for e in _entries(ports) if e["step"] == "apply_adr_decision"][-1]
    assert entry["outcome"] == "checks_red" and entry["bounds"] == landing_rules.ADR_EDIT_BOUNDS
    assert entry["red_checks"][0]["name"] == "docs"
    assert ports.workspace.pushes == []


def test_a_pass_that_edits_the_text_of_a_foreign_line_is_out_of_bounds_and_names_it(tmp_path):
    ports, head = _asked(tmp_path, _change_a_line_beside_the_spot)
    ports.workspace.files[(head, CODE)] = CITES_CODE + "\nOTHER = 1\n"

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "out_of_bounds" and entry["bounds"] == landing_rules.ADR_EDIT_BOUNDS
    assert {"path": CODE, "line": 2} in entry["out_of_bounds_lines"]
    assert "out_of_bounds_more" not in entry
    assert ports.workspace.pushes == []


def test_a_pass_that_edits_the_text_of_a_bound_row_is_out_of_bounds(tmp_path):
    changed = SORTED_INDEX.replace("| mine |", "| mine, reworded |")
    ports, _ = _gh_407_world(tmp_path, _spot_and_index(changed))

    _round(tmp_path, ports)

    entry = [e for e in _entries(ports) if e["step"] == "apply_adr_decision"][-1]
    assert entry["outcome"] == "out_of_bounds"
    assert entry["out_of_bounds_lines"]
    assert all(row["path"] == INDEX for row in entry["out_of_bounds_lines"])
    assert ports.workspace.pushes == []


def test_the_brief_lists_the_bound_lines_the_pass_may_move_but_not_change():
    text = brief(
        DECISION,
        [(MINE, "docs/adr/0050-mine.md")],
        [(CODE, 1, CITES_CODE)],
        [(INDEX, 3, ROW_MINE_NEW)],
    )

    assert f"{CODE}:1: {CITES_CODE}" in text
    assert f"{INDEX}:3: {ROW_MINE_NEW}" in text
    assert "move" in text and "must not change their text" in text
    assert "no other line may change" in text


# -- GH-579: apostrophe GH-407's shape, the keep lines and the upstream drift -----------------

G_ADR = "docs/adr/0076-payment-methods.md"
G_MOVED = "docs/adr/0078-payment-methods.md"
G_MAIN = "app/main.py"
G_SALES = "docs/product/sales.md"
G_MAIN_LINE = "x = 1  # (docs/adr/0076)"
G_SALES_TEXT = (
    "# Ventas\n\npuerto OnTheSpotPayments (ADR-0076)\n(ADR-0007, ADR-0076, payments.md)\n"
)
G_BASE = "# Ventas\n\n"
KEEP_SALES = "keep docs/product/sales.md:3\nkeep docs/product/sales.md:4"


def _asked_407(tmp_path, edit):
    """Branch ADR 0076 against master's 0076/0077; `app/main.py` cites it in an own commit and
    `docs/product/sales.md` only through a merge (no own line). The decision is on the issue."""
    ports = memory_ports(tracker=MemoryTracker())
    workspace = ports.workspace
    workspace.ROOT, workspace.track_commits, workspace.full_shas = tmp_path, True, True
    base = workspace.master_sha
    workspace.advance_master("docs/adr/0076-other.md", "docs/adr/0077-another.md")
    head = workspace.add_branch_commit(
        adds=(G_ADR,),
        lines=((G_ADR, "# ADR-0076: payment methods"), (G_MAIN, G_MAIN_LINE)),
        master=base,
    )
    files = {G_ADR: "# ADR-0076: payment methods\n", G_MAIN: G_MAIN_LINE + "\n"}
    files[G_SALES] = G_SALES_TEXT
    workspace.files.update({(head, path): text for path, text in files.items()})
    for ref in (base, workspace.master_sha):
        workspace.files[(ref, G_SALES)] = G_BASE
    workspace.merge_bases[(workspace.master_sha, head)] = base
    url = URL.format(5)
    ports.tracker.prs[url] = dataclasses.replace(_pr(), head_oid=head)
    unit = {"id": "only", "status": "approved", "reviewed_head": head}
    ports.documents.save(
        {
            "id": "GH-5",
            "status": "ready_for_review",
            "pull_request": url,
            "subtasks": {"only": unit},
        }
    )
    workspace.remote_tips[BRANCH] = head
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    (tmp_path / "GH-5" / "app").mkdir(parents=True)
    (tmp_path / "GH-5" / "docs" / "product").mkdir(parents=True)
    paths = (G_ADR, G_MAIN, G_SALES)
    first, _ = _round(tmp_path, ports, paths=paths)
    assert (first.kind, first.step) == ("dispatched", "escalate_adr"), first
    ports.tracker.timestamped["GH-5"] = [
        IssueComment("Escalated: ADR collision", ESCALATED_COMMENT),
        IssueComment(DECISION, DECIDED),
    ]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    ports.documents.record_closure(
        "GH-5",
        {"title": "t", "description": "d", "sections": {"Human": f"### Decision\n\n{DECISION}"}},
    )
    return ports, head, paths


def _commit_407(ports, cwd, files: dict[str, str], message: str):
    for relative, text in files.items():
        (cwd / relative).write_text(text, encoding="utf-8")
    ports.workspace.commit_paths(cwd, list(files), message)
    ports.workspace.diff_paths = tuple(files)


MAIN_AFTER = "x = 1  # (docs/adr/0078)\n"
SALES_AFTER = G_SALES_TEXT.replace("ADR-0076", "ADR-0078")
SALES_ONE_LINE = G_SALES_TEXT.replace("ADR-0076)\n(", "ADR-0078)\n(")


def _rewrites_all_three(ports, cwd):
    _commit_407(ports, cwd, {G_MAIN: MAIN_AFTER, G_SALES: SALES_AFTER}, "Point at 0078")


def _edits_main_keeps_sales(ports, cwd):
    _commit_407(ports, cwd, {G_MAIN: MAIN_AFTER}, f"Point main.py at 0078\n\n{KEEP_SALES}")


def _keeps_everything(ports, cwd):
    _commit_407(ports, cwd, {}, f"Nothing to edit\n\nkeep app/main.py:1\n{KEEP_SALES}")


def _edits_one_and_skips_another(ports, cwd):
    _commit_407(
        ports, cwd, {G_MAIN: MAIN_AFTER}, "Point main.py at 0078\n\nkeep docs/product/sales.md:3"
    )


def _edits_sales_line_three_and_keeps_four(ports, cwd):
    _commit_407(
        ports,
        cwd,
        {G_MAIN: MAIN_AFTER, G_SALES: SALES_ONE_LINE},
        "Point at 0078\n\nkeep docs/product/sales.md:4",
    )


def _round_407(tmp_path, ports, paths):
    return _round(tmp_path, ports, paths=paths)


@pytest.mark.parametrize(
    "edit",
    [
        _rewrites_all_three,
        _edits_main_keeps_sales,
        _keeps_everything,
        _edits_sales_line_three_and_keeps_four,
    ],
    ids=["rewrites-all", "keeps-sales", "keeps-everything", "edits-one-keeps-other"],
)
def test_gh_579_the_decision_on_gh_407s_collision_lands_without_out_of_bounds(tmp_path, edit):
    ports, head, paths = _asked_407(tmp_path, edit)
    [asked] = _entries(ports)
    assert asked["moves"] == [[G_ADR, G_MOVED]]

    result, lines = _round_407(tmp_path, ports, paths)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    applied = _entries(ports)[-1]
    assert applied["outcome"] == "applied", applied
    assert "out_of_bounds_lines" not in applied
    assert ports.workspace.pushes == [(BRANCH, head)]
    [prompt] = ports.runner.prompts("developer")
    for listed in (f"{G_MAIN}:1:", f"{G_SALES}:3:", f"{G_SALES}:4:"):
        assert listed in prompt
    assert "keep <path>:<line>" in prompt and "--allow-empty" in prompt


@pytest.mark.parametrize("edit", [_edits_one_and_skips_another])
def test_gh_579_a_spot_neither_edited_nor_kept_is_a_role_failure_and_pushes_nothing(tmp_path, edit):
    ports, head, paths = _asked_407(tmp_path, edit)

    result, _ = _round_407(tmp_path, ports, paths)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == []
    assert [(e["step"], e.get("outcome")) for e in _entries(ports)][1] == (
        "apply_adr_decision",
        "role_failed",
    )
    assert ports.workspace.branch_commits[-1].sha == head


def test_gh_579_a_kept_line_the_pass_did_not_name_with_the_right_number_does_not_count(tmp_path):
    def wrong_numbers(ports, cwd):
        _commit_407(ports, cwd, {G_MAIN: MAIN_AFTER}, "Point\n\nkeep docs/product/sales.md:30\n")

    ports, _, paths = _asked_407(tmp_path, wrong_numbers)

    _round_407(tmp_path, ports, paths)

    assert _entries(ports)[1]["outcome"] == "role_failed"


def test_gh_579_a_different_upstream_than_the_one_asked_about_is_stale_and_runs_nothing(tmp_path):
    ports, _, paths = _asked_407(tmp_path, _rewrites_all_three)
    [asked] = _entries(ports)
    assert asked["upstream"] == ports.workspace.master_sha
    ports.workspace.advance_master()  # master moved, the moves are the same

    _round_407(tmp_path, ports, paths)

    assert ports.workspace.pushes == [] and ports.runner.calls == []
    assert _entries(ports)[1]["outcome"] == "stale_moves"


def test_gh_579_an_entry_without_upstream_is_applied_as_before(tmp_path):
    ports, head, paths = _asked_407(tmp_path, _rewrites_all_three)
    old = [{k: v for k, v in e.items() if k != "upstream"} for e in _entries(ports)]
    ports.documents.record_closure("GH-5", {"landing": old})
    ports.workspace.advance_master()

    _round_407(tmp_path, ports, paths)

    assert ports.workspace.pushes == [(BRANCH, head)]


def test_gh_579_the_question_escalate_adr_posts_lists_every_spot_of_the_collision(tmp_path):
    """The wiring: `escalate_adr` feeds its one `adr_decided` result into the posted question."""
    ports, _, _ = _asked_407(tmp_path, _rewrites_all_three)

    [question] = ports.documents.load("GH-5")["questions_for_human"]

    for row in (f"`{G_MAIN}:1`", f"`{G_SALES}:3`", f"`{G_SALES}:4`"):
        assert row in question
    assert "puerto OnTheSpotPayments" not in question  # never the text of a line


# -- GH-582: apostrophe GH-652's shape, a decision the rule settles with no spot left ----------

ROW_BARE = "| 0049 | mine, said bare |"


def _gh_652_world(tmp_path, *, bare=False, edit=_boom, commands=None, stack=None):
    """Branch ADR 0049 against master's 0049. `docs/architecture/decisiones.md` holds master's
    own row for its 0049 and, besides it, the branch's row `| [0049](../adr/0049-mine.md) |`, an
    own line. The question
    is asked with `renumber` (the row's label is an `unclassified_token`), the decision is
    recomputed with `renumber_decided` (rule (d) rewrites the row: no spot is left)."""
    extra = ((INDEX, ROW_MINE),)
    extra += ((INDEX, ROW_BARE),) if bare else ()

    def prepare(ports, head):
        workspace = ports.workspace
        tail = ROW_BARE + "\n" if bare else ""
        workspace.files[(head, INDEX)] = f"{ROW_THEIRS}\n{ROW_MINE}\n{tail}"
        workspace.files[(workspace.master_sha, INDEX)] = ROW_THEIRS + "\n"

    paths = (MINE, NOTES, INDEX)
    ports, head = _asked_with(tmp_path, edit, extra, prepare=prepare, paths=paths)
    ports = dataclasses.replace(
        ports,
        stack=stack or MemoryStack(checks={"docs": "run docs"}),
        commands=commands or WhereChecksRan(ports.workspace),
    )
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    ports.workspace.diff_paths = paths
    return ports, head, paths


class WhereChecksRan(MemoryCommands):
    """Green checks that note the head of the checkout each command ran on."""

    def __init__(self, workspace, **queues) -> None:
        super().__init__(**queues)
        self.workspace, self.heads = workspace, []

    def run(self, cwd, command, timeout_s, *, env=None) -> CommandResult:
        self.heads.append(self.workspace.full_head(cwd))
        return super().run(cwd, command, timeout_s, env=env)


def test_gh_652_the_question_calls_the_row_ambiguous_and_the_decision_finds_no_spot(tmp_path):
    ports, head, _ = _gh_652_world(tmp_path)

    [asked] = _entries(ports)

    assert asked["moves"] == [[MINE, "docs/adr/0050-mine.md"]]
    found = adr_collision(ports, ports.workspace.master_sha, head)
    assert found == adr_numbers.Ambiguous("unclassified_token", found.detail, found.moves)
    assert found.detail.startswith(f"{INDEX}:")
    found = adr_decided(ports, ports.workspace.master_sha, head)
    assert isinstance(found, adr_numbers.DecidedRenaming) and found.spots == ()
    assert any(path == INDEX for path, _, _ in found.bound)


def test_gh_652_a_decision_with_no_spot_is_applied_without_a_model_pass(tmp_path):
    ports, head, paths = _gh_652_world(tmp_path)

    result, lines = _round(tmp_path, ports, paths=paths)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    entry = _apply_entry(ports)
    assert entry["outcome"] == "applied", (entry, lines)
    assert ports.workspace.pushes == [(BRANCH, head)]
    assert ports.runner.calls == []
    pushed = entry["head_after"]
    mechanical = ports.workspace.branch_commits[-1].sha
    assert pushed == mechanical != head
    assert ports.workspace.show(pushed, "docs/adr/0050-mine.md") == "# ADR-0050: mine\n"
    assert ports.workspace.show(pushed, MINE) is None
    rows = (ports.workspace.show(pushed, INDEX) or "").splitlines()
    assert rows == [ROW_THEIRS, ROW_MINE_NEW]
    assert ports.commands.heads == [mechanical]  # the stack's checks ran on the mechanical head
    assert [command for _, command in ports.commands.ran] == ["run docs"]
    unit = ports.documents.load("GH-5")["subtasks"]["only"]
    assert unit["reviewed_head"] == pushed  # only names and numbers changed: the review is carried


def test_gh_652_a_bare_number_beside_the_row_still_goes_through_the_developer(tmp_path):
    def sort_the_bare_row(ports, cwd):
        text = f"{ROW_THEIRS}\n{ROW_MINE_NEW}\n| 0050 | mine, said bare |\n"
        _commit_407(ports, cwd, {INDEX: text}, f"Point the bare row\n\nkeep {NOTES}:0")

    ports, head, paths = _gh_652_world(tmp_path, bare=True, edit=sort_the_bare_row)

    _round(tmp_path, ports, paths=paths)

    [prompt] = ports.runner.prompts("developer")
    assert f"{INDEX}:3: {ROW_BARE}" in prompt
    entry = _apply_entry(ports)
    assert entry["outcome"] == "applied", entry


def test_gh_652_a_red_check_ends_checks_red_and_pushes_nothing(tmp_path):
    commands = WhereChecksRan(None, **{"run docs": [CommandResult(1, "docs stale\n")] * 5})
    ports, head, paths = _gh_652_world(tmp_path, commands=commands)
    commands.workspace = ports.workspace

    _round(tmp_path, ports, paths=paths)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red", entry
    assert entry["red_checks"][0]["name"] == "docs"
    assert ports.workspace.pushes == [] and ports.runner.calls == []
    assert ports.workspace.branch_commits[-1].sha == head  # the checkout was put back


def test_gh_652_a_witness_that_disagrees_ends_checks_red_not_applied(tmp_path):
    commands = WhereChecksRan(None, witness=[CommandResult(1, "not this checkout\n")] * 5)
    stack = MemoryStack(checks={"docs": "run docs"}, witness="witness")
    ports, head, paths = _gh_652_world(tmp_path, commands=commands, stack=stack)
    commands.workspace = ports.workspace

    _, lines = _round(tmp_path, ports, paths=paths)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red", (entry, lines)
    assert "red_checks" not in entry
    assert ports.workspace.pushes == []


def _decided_as(monkeypatch, answer):
    def decided(ports, master, head):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(landing_adr, "adr_decided", decided)


def test_gh_582_a_git_failure_reading_the_collision_is_git_failed_and_asks_again(
    tmp_path, monkeypatch
):
    ports, head, paths = _gh_652_world(tmp_path)
    _decided_as(monkeypatch, GitError("git grep failed"))

    _, lines = _round(tmp_path, ports, paths=paths)

    assert [(e["step"], e.get("outcome")) for e in _entries(ports)][1:] == [
        ("apply_adr_decision", "git_failed"),
        ("escalate_adr", None),
    ]
    assert any("adr_decided could not read git" in line for line in lines)
    assert ports.workspace.pushes == [] and ports.runner.calls == []
    assert "A git operation failed" in ports.documents.load("GH-5")["questions_for_human"][0]


def test_gh_582_a_failed_push_is_git_failed_not_stale_moves(tmp_path):
    ports, head, paths = _gh_652_world(tmp_path)
    ports.workspace.push_failures = 1

    _round(tmp_path, ports, paths=paths)

    assert _apply_entry(ports)["outcome"] == "git_failed"
    assert ports.workspace.branch_commits[-1].sha == head


def test_gh_582_a_collision_that_is_gone_is_no_collision_and_does_not_ask_again(
    tmp_path, monkeypatch
):
    ports, head, paths = _gh_652_world(tmp_path)
    _decided_as(monkeypatch, None)

    _, lines = _round(tmp_path, ports, paths=paths)

    assert [(e["step"], e.get("outcome")) for e in _entries(ports)][1:] == [
        ("apply_adr_decision", "no_collision")
    ]
    assert any("no longer collides" in line for line in lines)
    assert ports.workspace.pushes == []


def test_gh_582_a_collision_the_rule_cannot_decide_is_ambiguous_and_asks_again(
    tmp_path, monkeypatch
):
    ports, _, paths = _gh_652_world(tmp_path)
    _decided_as(monkeypatch, adr_numbers.Ambiguous("duplicate_own_number", "x", ()))

    _round(tmp_path, ports, paths=paths)

    assert _apply_entry(ports)["outcome"] == "ambiguous"
    assert _entries(ports)[-1]["step"] == "escalate_adr"
    assert ports.workspace.pushes == []


def test_gh_582_a_changed_upstream_wins_over_a_collision_that_is_gone(tmp_path, monkeypatch):
    ports, _, paths = _gh_652_world(tmp_path)
    ports.workspace.advance_master()
    _decided_as(monkeypatch, None)

    _round(tmp_path, ports, paths=paths)

    assert _apply_entry(ports)["outcome"] == "stale_moves"


def test_gh_582_the_question_says_the_rule_rewrites_the_row_when_no_spot_is_left(tmp_path):
    ports, _, _ = _gh_652_world(tmp_path)

    # The question above is the one asked before the fix of the text: `escalate_adr` itself
    # computes it from `adr_decided`, so it names the rewrite and not "Fix by hand".
    [question] = ports.documents.load("GH-5")["questions_for_human"]

    assert "The rule rewrites these lines itself" in question
    assert f"`{INDEX}:2`" in question
    assert "Fix by hand" not in question and "no model pass" in question


def test_gh_582_a_setup_failure_without_spots_is_git_failed_and_spends_no_slot(
    tmp_path, monkeypatch
):
    ports, _, paths = _gh_652_world(tmp_path)

    def broken(*_):
        raise SetupFailed("agentic/GH-5", "install", "make install", "boom")

    monkeypatch.setattr(landing_adr, "task_checkout", broken)

    _, lines = _round(tmp_path, ports, paths=paths)

    assert _apply_entry(ports)["outcome"] == "git_failed"
    assert any("the task checkout's setup failed (install) -> git_failed" in x for x in lines)
    assert ports.workspace.pushes == [] and ports.runner.calls == []
    entry = _apply_entry(ports)
    assert landing_rules.adr_decisions_spent(_entries(ports), entry["head"], []) == 0


# --- GH-585: the apply gate runs the delivery tier, a timeout is no red and spends no slot -----


def _tiered_world(tmp_path, commands, delivery=("tests",), runner=StubDeveloper):
    """The asked collision whose product declares `tests` and a slow `e2e` outside its tier."""
    ports, head = _asked(tmp_path)
    ports = dataclasses.replace(
        ports,
        stack=MemoryStack(
            checks={"tests": "run tests", "e2e": "run e2e"}, delivery_checks=delivery
        ),
        commands=commands,
    )
    ports = dataclasses.replace(ports, runner=runner(ports, _point_at_the_new_number))
    return ports, head


def test_gh_585_the_apply_gate_runs_only_the_delivery_tier_and_records_it(tmp_path):
    commands = MemoryCommands(**{"run e2e": [CommandResult(1, "e2e is red\n")] * 5})
    ports, head = _tiered_world(tmp_path, commands)

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "applied", entry
    assert entry["gate_tier"] == "delivery"
    assert [command for _, command in ports.commands.ran] == ["run tests"]
    assert ports.workspace.pushes == [(BRANCH, head)]


def test_gh_585_a_product_with_no_tier_runs_everything_and_records_all(tmp_path):
    commands = MemoryCommands(**{"run e2e": [CommandResult(1, "e2e is red\n")] * 5})
    ports, _ = _tiered_world(tmp_path, commands, delivery=())

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red" and entry["gate_tier"] == "all"
    assert {command for _, command in ports.commands.ran} == {"run tests", "run e2e"}


def test_gh_585_a_red_check_inside_the_tier_is_checks_red_and_spends_a_slot(tmp_path):
    commands = MemoryCommands(**{"run tests": [CommandResult(1, "FAILED a\n")] * 5})
    ports, _ = _tiered_world(tmp_path, commands)

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_red" and entry["gate_tier"] == "delivery"
    assert (
        landing_rules.adr_decisions_spent(
            _entries(ports), entry["head"], _entries(ports)[0]["moves"]
        )
        == 1
    )


def _timeout_world(tmp_path, runner=StubDeveloper):
    slow = CommandResult(124, "tests were slow\n", timed_out=True)
    return _tiered_world(tmp_path, MemoryCommands(**{"run tests": [slow] * 5}), runner=runner)


def test_gh_585_a_check_that_timed_out_is_checks_timeout_pushes_nothing_and_spends_no_slot(
    tmp_path,
):
    ports, head = _timeout_world(tmp_path)

    _, lines = _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_timeout", (entry, lines)
    assert entry["gate_tier"] == "delivery"
    [check] = entry["red_checks"]
    assert check["name"] == "tests" and check["timed_out"] is True
    assert ports.workspace.pushes == []
    assert ports.workspace.branch_commits[-1].sha == head  # the checkout was put back
    assert (
        landing_rules.adr_decisions_spent(_entries(ports), head, _entries(ports)[0]["moves"]) == 0
    )
    assert any("checks_timeout" in line for line in lines)
    # It asks the owner again, as every refusal does.
    assert _entries(ports)[-1]["step"] == "escalate_adr"


def test_gh_585_a_timeout_without_a_developer_pass_is_checks_timeout_too(tmp_path):
    ports, head, paths = _gh_652_world(
        tmp_path,
        commands=WhereChecksRan(None, **{"run docs": [CommandResult(124, "slow\n", True)] * 5}),
    )
    ports.commands.workspace = ports.workspace

    _round(tmp_path, ports, paths=paths)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "checks_timeout", entry
    assert entry["red_checks"][0]["timed_out"] is True
    assert ports.workspace.pushes == [] and ports.runner.calls == []


def test_gh_585_a_real_red_beside_a_timeout_stays_checks_red(tmp_path):
    slow = CommandResult(124, "slow\n", timed_out=True)
    commands = MemoryCommands(
        **{"run tests": [slow] * 5, "run e2e": [CommandResult(1, "FAILED x\n")] * 5}
    )
    ports, _ = _tiered_world(tmp_path, commands, delivery=())

    _round(tmp_path, ports)

    assert _apply_entry(ports)["outcome"] == "checks_red"


def test_gh_585_a_check_that_failed_then_passed_in_a_second_gate_run_is_not_red(tmp_path):
    commands = MemoryCommands(
        **{"run tests": [CommandResult(1, "flaky\n"), CommandResult(0, "ok\n")]}
    )

    class TwiceDeveloper(StubDeveloper):
        async def run_once(self, role, prompt, **kwargs):
            result = await super().run_once(role, prompt, **kwargs)
            kwargs["presubmit"]()  # the gate runs again: the last row of `tests` passes
            return result

    ports, _ = _tiered_world(tmp_path, commands, runner=TwiceDeveloper)

    _round(tmp_path, ports)

    assert _apply_entry(ports)["outcome"] == "applied"


# The GH-652 document of apostrophe: two decisions on one collision ended `checks_red` on the
# whole suite (no `gate_tier`), the owner then asked a third time.
G652_HEAD = "b" * 40
G652_MOVES = [["docs/adr/0078-x.md", "docs/adr/0079-x.md"]]
G652_ASKS = ("2026-11-10T09:00:00+00:00", "2026-11-10T09:45:00+00:00", "2026-11-10T10:30:00+00:00")


def _g652_entries(tier, *, released=False) -> list[dict]:
    asks = [
        _entry(
            f"10T0{n}:00",
            G652_HEAD,
            "escalate_adr",
            resolved_at=f"2026-11-10T0{n}:00:05+00:00",
            released_at=at,
            moves=G652_MOVES,
        )
        for n, at in zip((9, 9), G652_ASKS, strict=False)
    ]
    applies = [
        _entry(
            f"10T0{n}:30",
            G652_HEAD,
            "apply_adr_decision",
            decision_at=at,
            outcome="checks_red",
            moves=G652_MOVES,
            bounds=3,
            **({} if tier is None else {"gate_tier": tier}),
        )
        for n, at in zip((9, 9), G652_ASKS, strict=False)
    ]
    third = _entry(
        "10T10:30",
        G652_HEAD,
        "escalate_adr",
        resolved_at="2026-11-10T10:30:05+00:00",
        moves=G652_MOVES,
    )
    if released:
        third["released_at"] = G652_ASKS[2]
    return [asks[0], applies[0], asks[1], applies[1], third]


def _g652_facts():
    ambiguous = adr_numbers.Ambiguous(
        "unclassified_token", "web/src/Inicio.tsx:16", tuple(map(tuple, G652_MOVES))
    )
    return _facts(pr_head=G652_HEAD, adr_collision=ambiguous)


def _g652_raw(entries) -> dict:
    return {"escalated": "adr_number_taken", "status": "waiting_on_human", "landing": entries}


def test_gh_585_the_gh_652_slots_spent_on_the_whole_suite_are_given_back(tmp_path):
    entries = _g652_entries(None)
    raw = _g652_raw(entries)
    facts = _g652_facts()

    assert landing_rules.adr_decisions_spent(entries, G652_HEAD, tuple(map(tuple, G652_MOVES))) == 0
    assert landing_plan.release_asked(
        raw, "apply_adr_decision", refusal="adr_number_taken", facts=facts
    )
    assert not landing_rules.adr_decision_due(entries[-1:], G652_HEAD, facts.adr_collision, entries)
    assert landing_plan.step_for(raw, "adr_number_taken", facts).kind != "apply_adr_decision"


def test_gh_585_a_new_owner_comment_after_the_refund_makes_the_decision_due(tmp_path):
    entries = _g652_entries(None, released=True)
    facts = _g652_facts()

    assert landing_rules.adr_decision_due(entries[-1:], G652_HEAD, facts.adr_collision, entries)
    step = landing_plan.step_for(_g652_raw(entries), "adr_number_taken", facts)
    assert step.kind == "apply_adr_decision"


def test_gh_585_the_same_document_with_a_gate_tier_keeps_both_slots_spent(tmp_path):
    entries = _g652_entries("delivery", released=True)
    raw = _g652_raw(entries)
    facts = _g652_facts()

    assert landing_rules.adr_decisions_spent(entries, G652_HEAD, tuple(map(tuple, G652_MOVES))) == 2
    assert not landing_plan.release_asked(
        raw, "apply_adr_decision", refusal="adr_number_taken", facts=facts
    )
    assert landing_plan.step_for(raw, "adr_number_taken", facts).kind != "apply_adr_decision"


# --- D-be76583a: the rule applies a collision it settles, with no question ------------------


@pytest.fixture
def _rule_decides(monkeypatch):
    monkeypatch.setattr(landing_rules, "ADR_AUTO_APPLY", True)


def _unasked(tmp_path, edit=_point_at_the_new_number):
    """The collision of `_asked`, before anybody was asked: the first round is the rule's."""
    ports, head = _world(tmp_path, extra=((CODE, CITES_CODE),))
    ports = dataclasses.replace(ports, runner=StubDeveloper(ports, edit))
    (tmp_path / "GH-5" / "app").mkdir(parents=True)
    ports.documents.record_closure("GH-5", {"title": "t", "description": "d"})
    return ports, head


def test_d_be76583a_a_collision_with_spots_in_bounds_is_applied_without_asking(
    tmp_path, _rule_decides
):
    ports, head = _unasked(tmp_path)

    result, lines = _round(tmp_path, ports)

    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert ports.workspace.pushes == [(BRANCH, head)]
    [applied] = _entries(ports)
    assert applied["outcome"] == "applied" and applied["decided_by"] == "rule"
    assert "decision_at" not in applied
    [prompt] = ports.runner.prompts("developer")
    assert landing_adr.AUTO_DECISION in prompt and "Never choose an ADR number" in prompt
    document = ports.documents.load("GH-5")
    assert not document.get("escalated")
    # The slot is the owner's too: one spent for this (head, moves).
    entries = landing_plan.all_entries(document)
    assert landing_rules.adr_decisions_spent(entries, head, [(MINE, "docs/adr/0050-mine.md")]) == 1


def test_d_be76583a_a_red_result_goes_back_to_the_question_and_is_not_retried(
    tmp_path, _rule_decides
):
    ports, head = _unasked(tmp_path, _edit_elsewhere)  # out of bounds

    result, _ = _round(tmp_path, ports)

    assert result.step == "apply_adr_decision"
    assert ports.workspace.pushes == []
    assert [(e["step"], e.get("outcome")) for e in _entries(ports)] == [
        ("apply_adr_decision", "out_of_bounds"),
        ("escalate_adr", None),
    ]
    document = ports.documents.load("GH-5")
    assert document["escalated"] and document["escalation_class"] == "questions"
    assert "tried to apply the renumbering on its own" in document["questions_for_human"][0]
    developer_runs = len(ports.runner.calls)

    again, _ = _round(tmp_path, ports)

    assert again.step != "apply_adr_decision" and len(ports.runner.calls) == developer_runs
    assert [e["step"] for e in _entries(ports)].count("apply_adr_decision") == 1


def test_d_be76583a_more_spots_than_the_rule_applies_are_asked_with_no_model_run(
    tmp_path, _rule_decides, monkeypatch
):
    monkeypatch.setattr(landing_rules, "ADR_AUTO_SPOTS_MAX", 0)
    ports, head = _unasked(tmp_path)

    _round(tmp_path, ports)

    assert ports.workspace.pushes == [] and ports.runner.calls == []
    assert [(e["step"], e.get("outcome")) for e in _entries(ports)] == [
        ("apply_adr_decision", "ambiguous"),
        ("escalate_adr", None),
    ]
    entries = landing_plan.all_entries(ports.documents.load("GH-5"))
    assert landing_rules.adr_decisions_spent(entries, head, [(MINE, "docs/adr/0050-mine.md")]) == 0
