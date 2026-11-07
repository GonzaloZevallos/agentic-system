"""`CheckoutPresubmit` reruns a red check once before it decides (ADR-0066), over fakes:
the three outcomes (pass, flake, fail), the checks that are never rerun, which command the
rerun uses, what is stored, and the timeout the harness is told."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult, RunStopped
from agentic.core.process.application.presubmit import CheckoutPresubmit, Reuse
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import (
    FAILED_IDS_TAIL_BYTES,
    AffectedPlan,
    FlakeGate,
    RerunForm,
    failed_ids,
    flake_verdict,
    outcomes,
)
from tests.fakes import (
    FakeCheckScheduler,
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

FIXTURES = Path(__file__).parents[3] / "fixtures" / "presubmit"
PATH = Path("/memory/worktrees/GH-360.gate")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
FORM = RerunForm(command="pytest --lf", test_id=r"(?m)^FAILED (\S+)")
PYTEST_RED = CommandResult(1, "FAILED tests/test_a.py::test_one\nFAILED tests/test_b.py::t2\n")
PLAN = AffectedPlan(base="abc", commands={"tests": "pytest --since {base}"}, fallback=())
AFFECTED = "pytest --since abc"


def gate(
    commands: MemoryCommands,
    *,
    forms: dict[str, RerunForm] | None = None,
    flake_gate: FlakeGate = "delivery",
    parallel: bool = False,
    plan: AffectedPlan | None = None,
    checks: dict[str, str] | None = None,
    timeouts: dict[str, float] | None = None,
) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.diff_paths = ("src/a.py",)
    workspace.moved.add(PATH)
    ports = type(
        "Ports",
        (),
        {"workspace": workspace, "commands": commands, "check_scheduler": FakeCheckScheduler()},
    )()
    return CheckoutPresubmit(
        ports,
        PATH,
        "base",
        checks or CHECKS,
        30.0,
        parallel=parallel,
        affected=plan,
        rerun_forms=forms,
        flake_gate=flake_gate,
        timeouts=timeouts,
        priority=CheckPriority.SUBTASK,
    )


def ran(commands: MemoryCommands) -> list[str]:
    return [c for _, c in commands.ran]


@pytest.mark.parametrize("parallel", [False, True])
def test_a_green_check_is_not_rerun(parallel):
    commands = MemoryCommands()
    g = gate(commands, parallel=parallel)
    assert g() is None
    assert sorted(ran(commands)) == sorted(CHECKS.values())
    assert g.flakes() == []
    assert g.results is not None and not any(r.get("rerun") for r in g.results)


@pytest.mark.parametrize("parallel", [False, True])
def test_red_then_green_passes_the_delivery_as_a_flake_with_one_record(parallel):
    commands = MemoryCommands(pytest=[PYTEST_RED, CommandResult(0, "ok")])
    g = gate(commands, parallel=parallel)
    assert g() is None  # the gate lets it through
    assert g.results is not None
    rows = [(r["name"], r["passed"], r.get("rerun", False)) for r in g.results]
    assert rows == [("tests", False, False), ("tests", True, True), ("lint", True, False)]
    assert g.results[1]["command"] == "pytest" and g.results[1]["duration_s"] >= 0.0
    markdown = g.markdown() or ""
    assert "flake" in markdown and "| `pytest` | passed |" not in markdown
    assert g.flakes() == [{"check": "tests", "head": g.head, "gate": "delivery", "tests": []}]


def test_red_twice_refuses_with_the_reruns_output():
    commands = MemoryCommands(
        pytest=[CommandResult(1, "first output"), CommandResult(1, "second output")]
    )
    g = gate(commands)
    reason = g()
    assert reason is not None and "second output" in reason and "first output" not in reason
    assert g.flakes() == []
    assert ran(commands).count("pytest") == 2  # one rerun, never a third run


def test_a_timed_out_check_is_refused_without_a_rerun():
    commands = MemoryCommands(pytest=[CommandResult(-9, "partial", timed_out=True)])
    g = gate(commands, forms={"tests": FORM})
    reason = g()
    assert reason is not None and "timed out" in reason
    assert ran(commands).count("pytest") == 1 and "pytest --lf" not in ran(commands)
    assert g.results is not None and g.results[0]["timed_out"] is True
    assert g.flakes() == []


def test_the_declared_form_runs_when_ids_were_parsed_at_the_delivery_gate():
    commands = MemoryCommands(pytest=[PYTEST_RED], **{"pytest --lf": [CommandResult(0, "ok")]})
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]
    assert g.results is not None and g.results[1]["command"] == "pytest --lf"
    (flake,) = g.flakes()
    assert flake["tests"] == ["tests/test_a.py::test_one", "tests/test_b.py::t2"]


def test_the_full_command_reruns_when_the_form_parsed_no_ids():
    commands = MemoryCommands(pytest=[CommandResult(1, "boom, no ids"), CommandResult(0, "ok")])
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert "pytest --lf" not in ran(commands)
    assert g.flakes()[0]["tests"] == []


@pytest.mark.parametrize("flake_gate", ["merge", "full_checks"])
def test_gates_other_than_delivery_rerun_the_form_too(flake_gate):
    commands = MemoryCommands(pytest=[PYTEST_RED], **{"pytest --lf": [CommandResult(0, "ok")]})
    g = gate(commands, forms={"tests": FORM}, flake_gate=flake_gate)
    assert g() is None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]
    assert g.flakes()[0]["gate"] == flake_gate


def test_sixty_failing_ids_rerun_the_whole_command():
    sixty = CommandResult(1, "".join(f"FAILED tests/test_a.py::t{n}\n" for n in range(60)))
    commands = MemoryCommands(pytest=[sixty, CommandResult(0, "ok")])
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest", "ruff check ."]


def test_an_output_past_the_tail_window_reruns_the_whole_command():
    real = (FIXTURES / "pytest_noisy_failures.txt").read_text()
    assert len(real.encode()) > FAILED_IDS_TAIL_BYTES
    assert failed_ids(real, FORM.test_id)  # the tail names ids, yet they are not trusted
    commands = MemoryCommands(pytest=[CommandResult(1, real), CommandResult(0, "ok")])
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest", "ruff check ."]


def test_three_ids_in_a_small_output_rerun_the_form():
    three = red("a.py::t1", "a.py::t2", "a.py::t3")
    commands = MemoryCommands(pytest=[three], **{"pytest --lf": [CommandResult(0, "ok")]})
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]


def test_an_uncaptured_error_line_beside_a_named_id_still_reruns_the_form_and_flakes():
    """The documented residual risk: the pattern names one id, the collection error beside it
    is not captured, the form reruns only that id and its green makes the check a flake."""
    output = "FAILED tests/test_a.py::t1\nERROR tests/test_b.py - ImportError\n"
    commands = MemoryCommands(
        pytest=[CommandResult(1, output)], **{"pytest --lf": [CommandResult(0, "ok")]}
    )
    g = gate(commands, forms={"tests": FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]
    assert g.results is not None
    assert outcomes(g.results)["tests"] == "flake"


@pytest.mark.parametrize(
    "form_output",
    ["no tests ran\n", "FAILED tests/test_a.py::t[a\n", "error: unrecognized arguments: --lf\n"],
)
@pytest.mark.parametrize("whole_green", [True, False])
def test_an_unresolved_red_form_falls_back_once_to_the_whole_command(form_output, whole_green):
    whole = CommandResult(0, "ok") if whole_green else CommandResult(1, "still red")
    commands = MemoryCommands(
        pytest=[red("tests/test_a.py::t1"), whole],
        **{"pytest --lf": [CommandResult(1, form_output)]},
    )
    g = gate(commands, forms={"tests": FORM})
    reason = g()
    assert ran(commands) == ["pytest", "pytest --lf", "pytest", "ruff check ."]
    assert g.results is not None
    tests_rows = [r for r in g.results if r["name"] == "tests"]
    assert [(r["command"], r.get("rerun", False)) for r in tests_rows] == [
        ("pytest", False),
        ("pytest", True),
    ]  # the form row is dropped
    assert outcomes(g.results)["tests"] == flake_verdict(tests_rows[0], tests_rows[1])
    assert (reason is None) is whole_green  # never a fail by itself
    assert bool(g.flakes()) is whole_green


def test_a_form_that_stays_red_on_one_of_its_ids_is_a_fail_with_no_third_run():
    commands = MemoryCommands(
        pytest=[red("tests/test_a.py::t1")],
        **{"pytest --lf": [red("tests/test_a.py::t1")]},
    )
    g = gate(commands, forms={"tests": FORM})
    assert g() is not None
    assert ran(commands) == ["pytest", "pytest --lf", "ruff check ."]


def test_an_affected_red_is_followed_by_the_full_form_and_only_the_full_reds_rerun():
    commands = MemoryCommands(
        **{AFFECTED: [CommandResult(1, "affected red")]},
        pytest=[CommandResult(1, "full red"), CommandResult(0, "ok")],
    )
    g = gate(commands, plan=PLAN)
    assert g() is None
    assert ran(commands) == [AFFECTED, "pytest", "pytest", "ruff check ."]
    assert g.results is not None
    assert [(r["command"], r.get("mode"), r.get("rerun", False)) for r in g.results[:3]] == [
        (AFFECTED, "affected-only", False),
        ("pytest", "full", False),
        ("pytest", "full", True),
    ]


def test_a_green_full_form_after_a_red_affected_one_is_not_rerun():
    commands = MemoryCommands(**{AFFECTED: [CommandResult(1, "stale")]})
    g = gate(commands, plan=PLAN)
    assert g() is None
    assert ran(commands) == [AFFECTED, "pytest", "ruff check ."]
    assert g.flakes() == []


def test_a_flaked_check_stores_nothing_but_a_green_one_still_does():
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.trees[PATH] = "tree:one"
    workspace.record_setup_tree(PATH)
    store = MemoryCheckResults()
    ports = memory_ports(
        commands=MemoryCommands(pytest=[PYTEST_RED, CommandResult(0, "ok")]),
        workspace=workspace,
        check_results=store,
    )
    g = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        CHECKS,
        30.0,
        reuse=Reuse("p", ("uv sync",), "a" * 40),
        priority=CheckPriority.SUBTASK,
    )
    assert g() is None
    # lint only: the flaked check's green rerun is never stored
    assert [row["name"] for _, row, _ in store.puts] == ["lint"]


@pytest.mark.parametrize(
    ("parallel", "plan", "expected"),
    [
        (False, None, 30.0 * 2 + 30.0 * 2),  # one run per check, plus one rerun per check
        (True, None, 30.0 + 30.0),  # the slowest check, then its rerun
        (False, PLAN, 30.0 * 3 + 30.0 * 2),
        (True, PLAN, 30.0 * 2 + 30.0),  # affected, full, rerun in one worker
    ],
)
def test_timeout_s_includes_the_possible_rerun(parallel, plan, expected):
    assert gate(MemoryCommands(), parallel=parallel, plan=plan).timeout_s == expected


@pytest.mark.parametrize(
    ("parallel", "forms", "expected"),
    [
        (False, None, 30.0 * 2 + 30.0 * 2),  # today's: a run and a rerun per check
        (True, None, 30.0 * 2),
        (False, {"tests": FORM}, 30.0 * 2 + 30.0 * 3),  # tests: form, then whole, as reruns
        (True, {"tests": FORM}, 30.0 * 3),  # first, form, whole in the slowest worker
    ],
)
def test_timeout_s_covers_a_form_then_a_whole_rerun(parallel, forms, expected):
    assert gate(MemoryCommands(), parallel=parallel, forms=forms).timeout_s == expected


def test_timeout_s_with_a_plan_and_a_form_adds_the_affected_run():
    serial = gate(MemoryCommands(), plan=PLAN, forms={"tests": FORM}).timeout_s
    parallel = gate(MemoryCommands(), parallel=True, plan=PLAN, forms={"tests": FORM}).timeout_s
    assert serial == 30.0 * 3 + 30.0 * 3
    assert parallel == 30.0 * 4


@pytest.mark.parametrize(
    ("forms", "expected"),
    [(None, 100.0 * 2), ({"tests": FORM}, 100.0 * 3), ({"lint": FORM}, 100.0 * 2)],
)
def test_a_declared_wall_is_multiplied_by_that_checks_runs(forms, expected):
    g = gate(MemoryCommands(), forms=forms, timeouts={"tests": 100.0})
    assert g.timeout_s == expected


def red(*ids: str) -> CommandResult:
    return CommandResult(1, "".join(f"FAILED {i}\n" for i in ids))


def refusing_gate(
    attempts: list[tuple[str, ...]], *, forms: bool = True
) -> tuple[CheckoutPresubmit, MemoryCommands]:
    """A gate whose `tests` check is red on each call with the ids of `attempts[n]`, then green
    from the call after the last one. `lint` is always green."""
    commands = MemoryCommands(
        pytest=[red(*ids) for ids in attempts],
        **{"pytest --lf": [red(*ids) for ids in attempts]},
    )
    return gate(commands, forms={"tests": FORM} if forms else None), commands


def verdicts(g: CheckoutPresubmit, calls: int) -> list[bool]:
    """For each of `calls` deliveries, whether the gate refused it."""
    return [g() is not None for _ in range(calls)]


def test_without_failed_ids_the_gate_stops_after_two_refusals_as_before():
    g, _ = refusing_gate([("a",), ("b",), ("c",), ("d",)], forms=False)
    assert verdicts(g, 4) == [True, True, False, False]


def test_the_same_failed_ids_stop_the_gate_after_two_refusals():
    g, _ = refusing_gate([("a", "b")] * 4)
    assert verdicts(g, 4) == [True, True, False, False]


def test_changed_failed_ids_earn_a_third_refusal_and_never_a_fourth():
    g, _ = refusing_gate([("a", "b"), ("a",), ("a",), ("b",), ("c",)])
    refused = [g() for _ in range(4)]
    assert [r is not None for r in refused] == [True, True, True, False]
    assert refused[2] is not None and "(3 of 3)" in refused[2]


def test_a_third_refusal_is_not_earned_when_the_ids_only_change_after_the_second():
    g, _ = refusing_gate([("a",), ("a",), ("b",), ("c",)])
    assert verdicts(g, 4) == [True, True, False, False]


def test_a_timeout_spends_a_refusal_and_its_reason_says_it_timed_out():
    commands = MemoryCommands(pytest=[CommandResult(-9, "partial", timed_out=True)] * 3)
    g = gate(commands, forms={"tests": FORM})
    first, second, third = g(), g(), g()
    assert first is not None and "`tests` timed out" in first and "(1 of 2)" in first
    assert second is not None and "(2 of 2)" in second
    assert third is None  # no ids parsed from a timeout: the budget is today's


class _StopsOnTheRerun(MemoryCommands):
    """The first run of `pytest` is red; the stop lands during its flake rerun."""

    def run(self, cwd, command, timeout_s, *, env=None):
        if command == "pytest" and any(c == "pytest" for _, c in self.ran):
            raise RunStopped
        return super().run(cwd, command, timeout_s, env=env)


@pytest.mark.parametrize("parallel", [False, True])
def test_a_stop_during_the_rerun_leaves_no_flake_no_refusal_and_no_row(parallel):
    """`RunStopped` is not an `Exception`: the rerun, the thread pool and the gate let it
    through, and nothing of the interrupted look is recorded as a verdict."""
    commands = _StopsOnTheRerun(pytest=[PYTEST_RED])
    g = gate(commands, parallel=parallel)
    with pytest.raises(RunStopped):
        g()
    assert g.flakes() == []
    assert g.refusals == 0
    assert g.results is None and g.invocations == [] and g.markdown() is None


def test_a_stop_that_is_not_raised_still_leaves_the_red_check_as_a_refusal():
    """The control: the same red check without the stop is a verdict, so the test above
    proves the stop is what kept it from being one."""
    commands = MemoryCommands(pytest=[PYTEST_RED, PYTEST_RED])
    g = gate(commands)
    assert g() is not None and g.refusals == 1


# --- the `{failed_ids}` token (GH-530) ---------------------------------------------------

TOKEN_FORM = RerunForm(command="pytest {failed_ids} -n {workers}", test_id=r"(?m)^FAILED (.+)$")


def test_the_executed_rerun_is_the_expanded_form_with_its_workers_sized():
    commands = MemoryCommands(
        pytest=[red("tests/a.py::t[a b]", "tests/a.py::it's")],
        **{"pytest 'tests/a.py::t[a b]' 'tests/a.py::it'\"'\"'s' -n 4": [CommandResult(0, "ok")]},
    )
    g = gate(commands, forms={"tests": TOKEN_FORM})
    assert g() is None
    assert ran(commands) == [
        "pytest",
        "pytest 'tests/a.py::t[a b]' 'tests/a.py::it'\"'\"'s' -n 4",
        "ruff check .",
    ]
    assert g.results is not None and g.results[1].get("rerun") is True
    assert outcomes(g.results)["tests"] == "flake"


def test_an_id_that_cannot_be_inserted_reruns_the_whole_command_unexpanded():
    commands = MemoryCommands(
        pytest=[red("tests/a.py::t{workers}"), CommandResult(0, "ok")],
    )
    g = gate(commands, forms={"tests": TOKEN_FORM})
    assert g() is None
    assert ran(commands) == ["pytest", "pytest", "ruff check ."]


def test_a_red_token_form_naming_none_of_its_ids_falls_back_once_to_the_whole_command():
    expanded = "pytest tests/a.py::t1 -n 4"
    commands = MemoryCommands(
        pytest=[red("tests/a.py::t1"), CommandResult(0, "ok")],
        **{expanded: [CommandResult(1, "error: no such option\n")]},
    )
    g = gate(commands, forms={"tests": TOKEN_FORM})
    assert g() is None
    assert ran(commands) == ["pytest", expanded, "pytest", "ruff check ."]
    assert g.results is not None
    assert [r["command"] for r in g.results if r["name"] == "tests"] == ["pytest", "pytest"]


def test_a_token_form_that_stays_red_on_its_own_id_is_judged_with_those_ids_and_fails():
    expanded = "pytest tests/a.py::t1 -n 4"
    commands = MemoryCommands(pytest=[red("tests/a.py::t1")], **{expanded: [red("tests/a.py::t1")]})
    g = gate(commands, forms={"tests": TOKEN_FORM})
    assert g() is not None
    assert ran(commands) == ["pytest", expanded, "ruff check ."]
