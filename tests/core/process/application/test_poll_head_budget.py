"""GH-621 part 2: the poller reads the head-budget facts, writes each turn through one writer
(`_record_turn`) and says each yield once. The writer is exercised on real documents."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from datetime import UTC, datetime, timedelta

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import ci, rules
from tests.core.process.application.test_poll import _Locks
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _ready,
)
from tests.core.process.domain.test_rules import ESCALATED_0105, RESET_0540
from tests.fakes import MemoryStack, MemoryTracker, memory_ports

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
STUCK = {"merge_checks": {"head": HEAD, "master": MASTER, "passed": False}}


def _ports(tmp_path, *numbers: int, stuck: tuple[int, ...] = (), checks: bool = True):
    ports = memory_ports(documents=FilesystemTaskDocuments(tmp_path), tracker=MemoryTracker())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    for number in numbers:
        _ready(ports, number, checks=checks, title=f"t{number}", description="d")
        ports.documents.record_closure(
            f"GH-{number}",
            {"landing_queued_at": (T0 - timedelta(hours=10 - number)).isoformat()},
        )
        if number in stuck:
            ports.documents.record_closure(f"GH-{number}", {"merge_checks": STUCK["merge_checks"]})
    return ports


def _round(ports, minutes: float, **more):
    lines: list[str] = []
    found = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), lines, now=T0 + timedelta(minutes=minutes), **more
    )
    return found, lines


def _doc(ports, number: int):
    return ports.documents.load(f"GH-{number}")


def test_a_new_head_is_recorded_once_and_the_next_round_inside_five_minutes_writes_nothing(
    tmp_path,
):
    ports = _ports(tmp_path, 5, 6)

    _round(ports, 0)
    first = _doc(ports, 5)
    assert first["head_seen_at"] == T0.isoformat()
    assert first["head_turn_at"] == T0.isoformat()
    assert first["head_since_head"] == HEAD
    assert "head_seen_at" not in _doc(ports, 6)

    before = (tmp_path / ".agentic/tasks/GH-5.md").read_text()
    _round(ports, 2)
    assert (tmp_path / ".agentic/tasks/GH-5.md").read_text() == before


def test_a_stuck_head_yields_after_one_budget_of_observed_rounds_and_the_second_leads(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5,))
    said: list[str] = []
    found: list[int] = []
    for minute in range(0, 60, 5):
        found, lines = _round(ports, minute)
        said += [line for line in lines if "yielded" in line]

    assert said == ["agentic: poll: landing order #5 -> yielded (budget_time) to #6"]
    first = _doc(ports, 5)
    assert first["head_yielded_at"] is not None
    assert [(y["head"], y["reason"]) for y in first["head_yields"]] == [(HEAD, "budget_time")]
    assert "head_seen_at" not in first
    assert "head_turn_at" not in first
    assert found == [6]
    assert _doc(ports, 6)["head_seen_at"] is not None


def test_a_full_save_between_two_yields_does_not_break_the_next_record(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5, 6))
    for minute in range(0, 50, 5):
        _round(ports, minute)
    state = _doc(ports, 5)
    assert len(state["head_yields"]) == 1
    ports.documents.save(state)  # a child's full save rewrites the document whole

    lines: list[str] = []
    for minute in range(50, 130, 5):
        lines += _round(ports, minute)[1]

    assert len([line for line in lines if "yielded" in line]) >= 1
    assert len(_doc(ports, 5)["head_yields"]) >= 2 or len(_doc(ports, 6)["head_yields"]) >= 1


class _Failing(FilesystemTaskDocuments):
    """Documents whose closure writes fail once armed (a full disk, a read-only file)."""

    armed = False

    def record_closure(self, task_id, values):
        if self.armed:
            raise OSError("disk full")
        return super().record_closure(task_id, values)


def test_a_failing_write_prints_no_yield_line(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5,))
    documents = _Failing(tmp_path)
    ports = dataclasses.replace(ports, documents=documents)
    for minute in range(0, 40, 5):
        _round(ports, minute)
    documents.armed = True

    lines: list[str] = []
    for minute in range(40, 60, 5):
        lines += _round(ports, minute)[1]

    assert not [line for line in lines if "yielded" in line]
    assert "head_yielded_at" not in _doc(ports, 5)


def test_a_held_round_writes_no_head_key_and_still_says_its_order(tmp_path):
    ports = _ports(tmp_path, 5, 6)
    found, lines = _round(ports, 0, held=True)

    assert found == [5]
    assert lines == ["agentic: poll: auto-merge #6 -> waiting (clean) behind #5"]
    for number in (5, 6):
        assert not [k for k in _doc(ports, number) if k.startswith("head_")]


def test_member_of_a_busy_task_with_a_running_ci_does_not_wait_on_ci(tmp_path):
    ports = _ports(tmp_path, 5)
    raw = _doc(ports, 5)
    assessed = poll.assess_merge(ports, "GH-5", raw, 5, frozenset(), MASTER)
    assert not isinstance(assessed, poll.Abandoned)
    waiting = dataclasses.replace(
        assessed, decision=dataclasses.replace(assessed.decision, kind="wait")
    )
    running = dataclasses.replace(
        assessed.report if hasattr(assessed.report, "running_runs") else _report(),
        head_sha=assessed.facts.pr_head,
        running_runs=frozenset({"1"}),
    )
    waiting = dataclasses.replace(waiting, report=running)

    idle = poll._member(ports, None, _Locks(), "GH-5", 5, raw, waiting)
    busy = poll._member(ports, None, _Locks(), "GH-5", 5, raw, waiting, busy=True)

    assert idle.waits_on_ci is True
    assert busy.waits_on_ci is False
    assert busy.busy is True


def _report():
    from agentic.core.process.domain import ci

    return ci.CiReport("x", None, None, None, ())


def test_a_busy_head_left_unobserved_for_forty_minutes_keeps_the_head(tmp_path):
    ports = _ports(tmp_path, 5, 6)
    seen = (T0 - timedelta(minutes=40)).isoformat()
    ports.documents.record_closure(
        "GH-6", {"head_seen_at": seen, "head_turn_at": seen, "head_charged_s": 600.0}
    )
    round_ = poll.evaluate_landing_order(
        ports, frozenset(), MASTER, _Locks(), frozenset({"GH-6"}), None, T0
    )
    assert round_.head == 6
    turned = next(e.turned for e in round_.entries if e.number == 6)
    assert turned is not None and turned.charged_s == 600.0


def _yield_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if "yielded" in line]


def test_a_yield_already_recorded_for_this_turn_is_neither_appended_nor_said_again(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5,))
    turn = (T0 - timedelta(minutes=50)).isoformat()
    recorded = {
        "at": (T0 - timedelta(minutes=10)).isoformat(),
        "head": HEAD,
        "reason": "budget_time",
    }
    ports.documents.record_closure(
        "GH-5",
        {
            "head_turn_at": turn,
            "head_charged_s": 2700.0,
            "head_seen_at": (T0 - timedelta(minutes=1)).isoformat(),
            "head_yields": [recorded],
        },
    )

    found, lines = _round(ports, 0)

    assert _yield_lines(lines) == []
    first = _doc(ports, 5)
    assert first["head_yields"] == [recorded]
    assert first["head_yielded_at"] == T0.isoformat()  # the state is still cleared
    assert found == [6]


def test_a_pull_request_that_yields_and_returns_with_the_same_sha_is_anchored_again(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5, 6))
    anchors: list[tuple[int, object]] = []
    yielded_at = None
    for minute in range(0, 200, 5):
        _, lines = _round(ports, minute)
        state = _doc(ports, 5)
        if yielded_at is None and any("#5 -> yielded" in line for line in lines):
            yielded_at = minute
            assert not state.get("head_since_at")  # the yield cleared the anchor
        elif yielded_at is not None and state.get("head_since_at"):
            anchors.append((minute, state["head_since_at"]))
            break

    assert yielded_at is not None and anchors
    minute, anchor = anchors[0]
    assert anchor == (T0 + timedelta(minutes=minute)).isoformat()
    assert _doc(ports, 5)["head_since_head"] == HEAD


def _ci_world(tmp_path):
    ports = _ports(tmp_path, 5, 6, checks=False)
    ports = dataclasses.replace(
        ports, stack=MemoryStack(checks={"tests": "run tests"}, ci=("web",))
    )
    ports.tracker.ci_reports = {
        URL.format(5): _ci(running=False),
        URL.format(6): _ci(running=False),
    }
    return ports


def _ci(*, running: bool) -> ci.CiReport:
    check = ci.CiCheck(
        "web",
        "IN_PROGRESS" if running else "COMPLETED",
        None if running else "SUCCESS",
        "https://x/runs/1",
    )
    return ci.CiReport(
        HEAD,
        "master",
        MASTER,
        datetime.now(UTC).isoformat(),
        (check,),
        running_runs=frozenset({"1"}) if running else frozenset(),
    )


def test_a_head_that_waits_on_a_running_ci_hands_the_turn_on_and_a_free_other_gives_it_back(
    tmp_path,
):
    ports = _ci_world(tmp_path)
    assert _round(ports, 0)[0] == [5]
    ports.tracker.ci_reports[URL.format(5)] = _ci(running=True)

    assert _round(ports, 10)[0] == [6]
    waiting = _doc(ports, 5)
    assert waiting["head_charged_s"] == 600.0  # folded, the turn and the charge kept
    assert "head_seen_at" not in waiting and "head_turn_at" in waiting
    assert _doc(ports, 6)["head_seen_at"] == (T0 + timedelta(minutes=10)).isoformat()

    ports.tracker.ci_reports[URL.format(5)] = _ci(running=False)
    assert _round(ports, 12)[0] == [5]

    assert _doc(ports, 5)["head_charged_s"] == 600.0
    assert _doc(ports, 5)["head_seen_at"] == (T0 + timedelta(minutes=12)).isoformat()
    other = _doc(ports, 6)
    assert other["head_charged_s"] == 120.0
    assert "head_seen_at" not in other


def test_a_head_that_waits_on_a_running_ci_is_not_taken_back_while_the_other_is_busy(tmp_path):
    ports = _ci_world(tmp_path)
    _round(ports, 0)
    ports.tracker.ci_reports[URL.format(5)] = _ci(running=True)
    _round(ports, 10)
    ports.tracker.ci_reports[URL.format(5)] = _ci(running=False)

    found, lines = _round(ports, 12, busy=frozenset({"GH-6"}))

    assert found == []  # the busy one keeps the head, its child is at work
    assert "agentic: poll: auto-merge #5 -> waiting (clean) behind #6" in lines
    assert "head_seen_at" not in _doc(ports, 5)
    assert _doc(ports, 6)["head_seen_at"] == (T0 + timedelta(minutes=10)).isoformat()


def test_a_member_of_a_product_with_an_account_limit_pause_is_paused_unless_busy(tmp_path):
    ports = _ports(tmp_path, 5)
    raw = _doc(ports, 5)
    paused = [
        rules.ResumeCandidate(9, "account_limit", ESCALATED_0105, 0, 24, 0, resets_at=RESET_0540)
    ]
    assessed = poll.assess_merge(ports, "GH-5", raw, 5, frozenset(), MASTER)
    assert not isinstance(assessed, poll.Abandoned)
    now = ESCALATED_0105 + timedelta(minutes=16)

    idle = poll._member(ports, None, _Locks(), "GH-5", 5, raw, assessed, now=now, resumes=paused)
    busy = poll._member(
        ports, None, _Locks(), "GH-5", 5, raw, assessed, busy=True, now=now, resumes=paused
    )
    free = poll._member(ports, None, _Locks(), "GH-5", 5, raw, assessed, now=now)

    assert idle.account_paused is True
    assert busy.account_paused is False
    assert free.account_paused is False


def test_the_poller_passes_the_products_pauses_to_its_merge_round():
    source = inspect.getsource(poll.run_once)
    call = source[source.index("merges = gather_merges(") :]
    assert "resumes," in call[: call.index(")\n")]


_MEMBER = re.compile(r"member|turned|^m$|^before$|^after$")


class _Offences(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: set[str] = set()
        self.inside: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.inside.append(node.name)
        self.generic_visit(node)
        self.inside.pop()

    def visit_Call(self, node: ast.Call) -> None:
        text = ast.unparse(node.func)
        if re.fullmatch(r"landing_order\.(observed|left_head|yielded)", text):
            self.found.add(text)
        elif text in ("replace", "dataclasses.replace") and node.args:
            if _MEMBER.search(ast.unparse(node.args[0])):
                self.found.add(f"{text}({ast.unparse(node.args[0])})")
        elif text == "landing_order.Member" and "_member" not in self.inside:
            self.found.add(text)
        self.generic_visit(node)


def _charge_offences(source: str) -> list[str]:
    """What breaks the rule that the charge fields are the domain's alone: a call to `observed`,
    `left_head` or `yielded` of `landing_order`, a `replace` of something that is a member, and a
    `Member(` built anywhere but in `_member`."""
    visitor = _Offences()
    visitor.visit(ast.parse(source))
    return sorted(visitor.found)


def test_nothing_outside_landing_order_builds_or_charges_a_member():
    from agentic.core.process.application import landing, landing_fix

    for module in (poll, landing, landing_fix):
        assert _charge_offences(inspect.getsource(module)) == [], module.__name__


def test_the_charge_check_rejects_each_way_of_breaking_the_rule():
    bad = (
        "def f(m, entry, now):\n"
        "    a = landing_order.observed(m, now)\n"
        "    b = landing_order.left_head(m, now)\n"
        "    c = landing_order.yielded(m, 'budget_time', now)\n"
        "    d = replace(entry.member, charged_s=0.0)\n"
        "    e = dataclasses.replace(m, seen_at=None)\n"
        "    g = landing_order.Member(1, None, False, False)\n"
    )
    assert _charge_offences(bad) == [
        "dataclasses.replace(m)",
        "landing_order.Member",
        "landing_order.left_head",
        "landing_order.observed",
        "landing_order.yielded",
        "replace(entry.member)",
    ]
    fine = (
        "def _member(x):\n    return landing_order.Member(1, None, False, False)\n"
        "replace(facts, a=1)\n"
    )
    assert _charge_offences(fine) == []
