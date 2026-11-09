"""GH-544, the chain: an `escalate_adr` question, an owner's decision on the issue, then a `land`
that dispatches `apply_adr_decision` and runs it (`landing_adr`) on the memory ports, with a
stub Developer that records its brief and commits a one-line edit. The collision is a real
one (`adr_collision` over the memory workspace): the branch's `0049-mine` against master's
`0049-theirs`, and a line of code that cites it in a way the grammar cannot rewrite."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from agentic.core.process.application import landing, landing_plan
from agentic.core.process.application.landing_adr import within_spots
from agentic.core.process.application.landing_plan import entries as plan_entries
from agentic.core.process.application.ports import (
    CommandResult,
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
from tests.core.process.application.test_poll_merge import _HeldLocks
from tests.core.process.domain.test_landing import _facts
from tests.fakes import MemoryCommands, MemoryStack, ScriptedRunner

CODE = "app/main.py"
CITES_CODE = 'MSG = "see (docs/adr/0049)"'
LIVE = frozenset({"escalate_adr", "apply_adr_decision", "re_review"})
ASKED = "2026-10-14T00:00:00+00:00"
ESCALATED_COMMENT = "2026-10-14T00:00:10+00:00"
DECIDED = "2026-10-14T01:00:00+00:00"
DECISION = "renumber to the next free number and point app/main.py to it"


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


def _round(tmp_path, ports, live=LIVE):
    lines: list[str] = []
    ports.workspace.diff_paths = (MINE, NOTES, CODE)  # the pull request's own paths
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
    """The path is a spot's, so only the line-level guard (`within_spots`) can refuse it: a
    non-spot line changed, or a line added, in the file that has a spot."""
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


def test_within_spots_accepts_the_spot_lines_alone_and_refuses_any_other_change():
    spots = [(CODE, 1, CITES_CODE)]
    before = {CODE: CITES_CODE + "\nOTHER = 1\n"}

    assert within_spots(before, {CODE: 'MSG = "x"\nOTHER = 1\n'}, spots)
    assert not within_spots(before, {CODE: 'MSG = "x"\nOTHER = 2\n'}, spots)
    assert not within_spots(before, {CODE: CITES_CODE + "\n"}, spots)
    assert not within_spots(before, {CODE: CITES_CODE + "\nOTHER = 1\nMORE = 1\n"}, spots)
    assert not within_spots(before, {"other.py": "x\n"}, spots)


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
    )
    entries = _gh_407_entries()
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
        {"name": "tests", "tail": ["collected 3", "FAILED test_a - boom"], "recovered": False}
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
        {"name": "lint", "tail": ["SECOND run: lint red"], "recovered": False}
    ]


def test_an_apply_that_does_not_end_checks_red_records_no_red_detail(tmp_path):
    ports, _ = _asked(tmp_path, _edit_elsewhere)

    _round(tmp_path, ports)

    entry = _apply_entry(ports)
    assert entry["outcome"] == "out_of_bounds"
    assert "red_checks" not in entry and "red_more" not in entry
