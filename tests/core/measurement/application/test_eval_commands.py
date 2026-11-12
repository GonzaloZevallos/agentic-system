"""`agentic eval compare` split by arm, with the time figures and `--experiment`:
`agentic.core.measurement.application.eval_commands` (GH-470)."""

from pathlib import Path

import pytest
import yaml

from agentic.core.measurement.application import eval_commands
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import reflect_run
from agentic.shared.configuration import Configuration
from agentic.shared.errors import CannotStart
from tests.fakes import memory_ports

LIGHT = {"review_mode": "light", "skip_ephemeral": "False"}
LIGHT_LABEL = "review_mode=light,skip_ephemeral=False,plan_review=interview"


def resolve(sha):
    raise AssertionError("every document here carries its own baseline")


def run(started, ended):
    return {
        "role": "Developer",
        "slug": "developer",
        "section": "Developer: a",
        "version": 1,
        "session_id": "x",
        "model": "m",
        "cost_usd": 1.0,
        "cost_by_model": {"m": 1.0},
        "turns": 4,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "started_at": f"2026-10-20T{started}.000Z",
        "ended_at": f"2026-10-20T{ended}.000Z",
    }


def write(root: Path, task_id: str, baseline: str, *, arm: str | None = None, **extra) -> None:
    front = {
        "id": task_id,
        "title": "t",
        "status": "ready_for_review",
        "cost_usd": 1.0,
        "system_baseline": baseline,
        "runs": [run("10:00:00", "11:00:00")],
        **extra,
    }
    if arm is not None:
        front["assignment"] = {
            "experiment": "ex-1",
            "arm": arm,
            "switches": LIGHT if arm == "fast" else {},
        }
    tasks = root / ".agentic" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.md").write_text(
        f"---\n{yaml.safe_dump(front)}---\n\n## Original request\n\nd\n"
    )


@pytest.fixture
def root(tmp_path):
    (tmp_path / "p" / ".agentic").mkdir(parents=True)
    return tmp_path / "p"


async def compare(root, frm, to, capsys):
    await eval_commands._eval_compare(
        Configuration(product_dir=root), memory_ports(), frm, to, resolve
    )
    return capsys.readouterr().out


CAVEAT = (
    "  caveat: `sdk` counts as ours on both sides; a run before ADR-0029 recorded its 429s as "
    "`sdk` and is never reclassified -- a delta here is a quality delta only if both sides show "
    "the post-ADR-0029 vocabulary\n"
)

# `eval compare`'s two scorecards exactly as they printed before the time rows existed.
TODAYS_SCORECARDS = (
    "-- product / bl-a --\n"
    "autonomy:          n/a (1 judged, 0 excluded)\n"
    "own failures/task: 0.0000 (1 terminated, 0 excluded)\n" + CAVEAT + "  by reason: {}\n"
    "cost/delivery:     1.0000 (1 judged, 0 excluded)\n"
    "rework/task:       0.0000 (1 terminated, 0 excluded)\n"
    "  time to merge known for 0 of 1 judged deliveries\n"
    "actual/estimate cost:  n/a (median, n=0, 1 excluded)\n"
    "actual/estimate files: n/a (median, n=0, 1 excluded)\n"
    "-- product / bl-b --\n"
    "autonomy:          n/a (0 judged, 1 excluded)\n"
    "own failures/task: 0.0000 (1 terminated, 0 excluded)\n" + CAVEAT + "  by reason: {}\n"
    "cost/delivery:     n/a (0 judged, 1 excluded)\n"
    "rework/task:       0.0000 (1 terminated, 0 excluded)\n"
    "  time to merge known for 0 of 0 judged deliveries\n"
    "actual/estimate cost:  n/a (median, n=0, 1 excluded)\n"
    "actual/estimate files: n/a (median, n=0, 1 excluded)\n"
)

# The GH-467 rows, in print order. The fixtures hold no `failures`, `ci_timing` or `pause_log`,
# which `cycle.time_split` reads as none: zero failed, waiting and paused hours, and the cycle
# left over (3 h for bl-a's closed 4 h cycle with 1 h of runs, none for bl-b's open one).
NEW_ROWS_A = (
    "failed_h:              0.0000 (median, n=1, 0 excluded)",
    "waiting_h:             0.0000 (median, n=1, 0 excluded)",
    "paused_h:              0.0000 (median, n=1, 0 excluded)",
    "paused_external_h:     0.0000 (median, n=1, 0 excluded)",
    "paused_human_h:        0.0000 (median, n=1, 0 excluded)",
    "unattributed_h:        3.0000 (median, n=1, 0 excluded)",
)
NEW_ROWS_B = tuple(row.replace("3.0000", "0.0000") for row in NEW_ROWS_A)

TIME_ROWS = (
    "cycle_h:               4.0000 (median, n=1; closed 1, open 0)",
    "active_h:              1.0000 (median, n=1; closed 1, open 0)",
    *NEW_ROWS_A,
    "cycle_h:               1.0000 (median, n=1; closed 0, open 1)",
    "active_h:              1.0000 (median, n=1; closed 0, open 1)",
    *NEW_ROWS_B,
)


@pytest.mark.anyio
async def test_an_all_default_baseline_prints_todays_scorecard_plus_the_time_rows(root, capsys):
    write(root, "GH-1", "bl-a", closed_at="2026-10-20T14:00:00.000Z")
    write(root, "GH-2", "bl-b")
    out = await compare(root, "bl-a", "bl-b", capsys)

    lines = out.split("verdict:")[0].splitlines(keepends=True)
    timed = (
        "cycle_h",
        "active_h",
        "failed_h",
        "waiting_h",
        "paused_h",
        "paused_external_h",
        "paused_human_h",
        "unattributed_h",
    )
    assert tuple(ln.rstrip("\n") for ln in lines if ln.startswith(timed)) == TIME_ROWS
    assert "".join(ln for ln in lines if not ln.startswith(timed)) == TODAYS_SCORECARDS
    assert "/ arm" not in out
    assert out.count("verdict:") == 1


@pytest.mark.anyio
async def test_a_baseline_with_two_arms_is_split_and_compared_arm_against_itself(root, capsys):
    write(root, "GH-1", "bl-a", arm="control")
    write(root, "GH-2", "bl-a", arm="fast")
    write(root, "GH-3", "bl-b", arm="fast")
    write(root, "GH-4", "bl-b", arm="control")
    write(root, "GH-5", "bl-b", arm="control", runs=[run("10:00:00", "12:00:00")])
    out = await compare(root, "bl-a", "bl-b", capsys)

    for heading in (
        "-- product / bl-a / arm default --",
        f"-- product / bl-a / arm {LIGHT_LABEL} --",
        "-- product / bl-b / arm default --",
        f"-- product / bl-b / arm {LIGHT_LABEL} --",
    ):
        assert heading in out
    assert "-- product / bl-a --" not in out
    # The arm's name is never a heading or a label.
    assert "control" not in out and "fast" not in out
    # bl-b's default arm holds two tasks (1 h and 2 h cycle): a median of 1.5.
    default_b = out.split("-- product / bl-b / arm default --")[1].split("\n-- ")[0]
    assert "cycle_h:               1.5000 (median, n=2; closed 0, open 2)" in default_b
    assert "verdict [default]:" in out and f"verdict [{LIGHT_LABEL}]:" in out
    assert out.count("parallel factor --") == 4


@pytest.mark.anyio
async def test_a_task_missing_a_bound_is_left_out_of_the_time_rows_not_shown_as_zero(root, capsys):
    write(root, "GH-1", "bl-a")
    write(root, "GH-2", "bl-a", runs=[{**run("10:00:00", "11:00:00"), "ended_at": None}])
    write(root, "GH-3", "bl-b")
    out = await compare(root, "bl-a", "bl-b", capsys)

    first = out.split("-- product / bl-b --")[0]
    assert "cycle_h:               1.0000 (median, n=1; closed 0, open 1)" in first
    # The new rows leave the same task out and count it, never as a zero.
    assert "failed_h:              0.0000 (median, n=1, 1 excluded)" in first
    assert "unattributed_h:        0.0000 (median, n=1, 1 excluded)" in first


def write_experiment(root, per_arm: int, *, baselines=("bl-a", "bl-a")):
    for i in range(per_arm):
        write(root, f"GH-{i + 1}", baselines[0], arm="control")
        write(root, f"GH-{100 + i}", baselines[1], arm="fast")


@pytest.mark.anyio
async def test_experiment_below_the_minimum_per_arm_is_insufficient(root, capsys):
    write_experiment(root, 9)
    write(root, "GH-900", "bl-a")  # not in the experiment
    await eval_commands._eval_compare_experiment(
        Configuration(product_dir=root), memory_ports(), "ex-1", resolve
    )
    out = capsys.readouterr().out
    assert "experiment ex-1: 18 task(s), 2 arm(s)" in out
    assert "verdict: insufficient (9/10)" in out
    assert "arm default --" in out and f"arm {LIGHT_LABEL} --" in out


@pytest.mark.anyio
async def test_experiment_with_enough_tasks_per_arm_compares_the_arms(root, capsys):
    write_experiment(root, 10)
    await eval_commands._eval_compare_experiment(
        Configuration(product_dir=root), memory_ports(), "ex-1", resolve
    )
    out = capsys.readouterr().out
    assert "insufficient" not in out
    assert f"verdict [default -> {LIGHT_LABEL}]:" in out


@pytest.mark.anyio
async def test_experiment_spanning_two_baselines_is_insufficient_with_each_baselines_n(
    root, capsys
):
    write_experiment(root, 3, baselines=("bl-a", "bl-b"))
    await eval_commands._eval_compare_experiment(
        Configuration(product_dir=root), memory_ports(), "ex-1", resolve
    )
    out = capsys.readouterr().out
    assert "verdict: insufficient (mixed baselines)" in out
    assert f"  bl-a: default n=3, {LIGHT_LABEL} n=0" in out
    assert f"  bl-b: default n=0, {LIGHT_LABEL} n=3" in out
    assert "-- product" not in out  # no scorecard over mixed baselines


@pytest.mark.anyio
async def test_experiment_nobody_ran_is_refused(root):
    write(root, "GH-1", "bl-a")
    with pytest.raises(CannotStart, match="ex-9"):
        await eval_commands._eval_compare_experiment(
            Configuration(product_dir=root), memory_ports(), "ex-9", resolve
        )


# --- evaluation documents (GH-470): `eval/docs/` is read by --experiment alone ---

EVALUATION = {"issue": 7, "base": "a" * 40, "ci": "local"}


def write_evaluation(root: Path, task_id: str, baseline: str, arm: str, **extra) -> None:
    write(root, task_id, baseline, arm=arm, evaluation=EVALUATION, **extra)
    source = root / ".agentic" / "tasks" / f"{task_id}.md"
    docs = root / ".agentic" / "eval" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    source.rename(docs / f"{task_id}.md")


def test_an_evaluation_document_is_invisible_to_every_scanner_of_tasks(root):
    write(root, "GH-1", "bl-a")
    write_evaluation(root, "EVAL-7-aaaaaaaa-control-1", "bl-a", "control")
    buckets, unreadable = eval_commands._bucket_documents(root, resolve)
    assert [task_id for _, tasks in buckets.items() for task_id, _ in tasks] == ["GH-1"]
    assert unreadable == 0
    assert FilesystemTaskDocuments(root).ids() == ["GH-1"]


def test_the_reflector_never_sees_an_evaluation_document(root):
    write_evaluation(
        root, "EVAL-7-aaaaaaaa-control-1", "bl-a", "control", status="ready_for_review"
    )
    ports = memory_ports(documents=FilesystemTaskDocuments(root))
    assert reflect_run.pending(Configuration(product_dir=root), ports) == []


@pytest.mark.anyio
async def test_experiment_counts_the_evaluation_documents_beside_the_tasks(root, capsys):
    write(root, "GH-1", "bl-a", arm="control")
    write_evaluation(root, "EVAL-7-aaaaaaaa-control-1", "bl-a", "control")
    write_evaluation(root, "EVAL-7-aaaaaaaa-fast-1", "bl-a", "fast")
    (root / ".agentic" / "eval" / "docs" / "stray.md").write_text(
        "---\nid: GH-5\ntitle: t\nstatus: open\nsystem_baseline: bl-a\n---\n\n## Original request\n"
    )
    await eval_commands._eval_compare_experiment(
        Configuration(product_dir=root), memory_ports(), "ex-1", resolve
    )
    out = capsys.readouterr().out
    assert "experiment ex-1: 3 task(s), 2 arm(s)" in out  # the stray non-evaluation is out


@pytest.mark.anyio
async def test_compare_by_baseline_ignores_the_evaluation_documents(root, capsys):
    write(root, "GH-1", "bl-a")
    write(root, "GH-2", "bl-b")
    write_evaluation(root, "EVAL-7-aaaaaaaa-control-1", "bl-c", "control")
    await compare(root, "bl-a", "bl-b", capsys)
    with pytest.raises(CannotStart, match="bl-c: unknown baseline"):
        await compare(root, "bl-a", "bl-c", capsys)


def _escalated(cause_or_class, resumed_at="12:30:00", *, cause=None):
    """A `pause_log` of one escalation at 11:30 that a resume closed at `resumed_at`."""
    entry = {"kind": "escalation", "value": cause_or_class, "at": "2026-10-20T11:30:00.000Z"}
    if cause is not None:
        entry["cause"] = cause
    resumed = {"kind": "resumed", "value": "resumed", "at": f"2026-10-20T{resumed_at}.000Z"}
    return [entry, resumed]


@pytest.mark.anyio
async def test_compare_prints_the_escalations_by_cause_and_the_human_wait_by_cause(root, capsys):
    done = {"closed_at": "2026-10-20T14:00:00.000Z"}
    write(root, "GH-1", "bl-a", pause_log=_escalated("returns"), **done)
    write(root, "GH-2", "bl-a", pause_log=_escalated("returns", "13:30:00"), **done)
    write(root, "GH-3", "bl-a", pause_log=_escalated("ci", cause="ci_run_unfinished"), **done)
    write(root, "GH-4", "bl-a", **done)
    write(root, "GH-5", "bl-b")
    out = await compare(root, "bl-a", "bl-b", capsys)

    first = out.split("-- product / bl-b --")[0]
    # Per cause and kind: the escalations, over how many of the bucket's tasks.
    assert "escalations [judgment] returns: 2 in 2 of 4 tasks" in first
    assert "escalations [mechanical] ci_run_unfinished: 1 in 1 of 4 tasks" in first
    # Only a judgment call is a human's wait: the median of 1 h and 2 h, never the CI one.
    assert "human_wait_h [returns]: 1.5000 (median, n=2 of 4 tasks)" in first
    assert "human_wait_h [ci_run_unfinished]" not in first
    # A bucket with none prints none of it.
    assert "escalations [" not in out.split("-- product / bl-b --")[1]
