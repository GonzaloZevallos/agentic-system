"""The work scores of `task_scores` (GH-907, part 1): `work_h` beside the stage hours, and the
LLM / tools / outside groups only when generations are passed."""

import math
from datetime import UTC, datetime

from agentic.core.measurement.domain import task_scores as ts
from agentic.core.measurement.domain.stages import Generations, NoRows

ISSUE_AT = "2026-10-01T09:00:00+00:00"
PR_AT = "2026-10-01T12:00:00+00:00"
MERGED_AT = "2026-10-01T13:00:00+00:00"
TASK_TIME = ts.TaskTime(
    datetime(2026, 10, 1, 9, 30, tzinfo=UTC), datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
)
NEW = set(ts.WORK_SCORE_NAMES)


def at(hour, minute=0):
    return datetime(2026, 10, 1, hour, minute, tzinfo=UTC)


def document(session="s"):
    run = {
        "slug": "developer",
        "cost_usd": 1.0,
        "started_at": "2026-10-01T09:30:00+00:00",
        "ended_at": "2026-10-01T10:30:00+00:00",
    }
    if session is not None:
        run["session_id"] = session
    return {"id": "GH-1", "status": "ready_for_review", "cost_usd": 1.0, "runs": [run]}


def scores(doc=None, generations=None, merged=MERGED_AT):
    found = ts.task_scores(
        doc if doc is not None else document(),
        task_time=TASK_TIME,
        issue_created_at=ISSUE_AT,
        pr_created_at=PR_AT,
        pr_merged_at=merged,
        generations=generations,
    )
    return {s.name: s for s in found}


def test_without_generations_the_output_is_todays_plus_work_h():
    got = scores()
    assert set(got) & (NEW - {"work_h"}) == set()
    assert "work_h" in got
    assert got["work_h"].value == sum(
        got[f"stage_h.{stage}"].value
        for stage in ("definition", "plan", "development", "review", "checks", "landing", "other")
    )
    assert got["work_h"].data_type == "NUMERIC"
    assert got["work_h"].timestamp == at(13)
    # everything else is the output the generations do not touch
    base = [n for n in ts.SCORE_NAMES if n not in NEW]
    assert [n for n in got if n not in NEW] == [n for n in base if n in got]
    other = scores(generations={"s": NoRows()})
    assert {n: s.value for n, s in got.items() if n not in NEW} == {
        n: s.value for n, s in other.items() if n not in NEW
    }


def test_a_known_split_writes_three_groups_that_sum_to_work_h():
    generations = {"s": Generations(((at(9, 30), at(10)),))}
    got = scores(generations=generations)
    assert got["work_split_known"].value == 1.0
    assert got["work_split_known"].data_type == "BOOLEAN"
    assert got["work_llm_h"].value == 0.5
    assert got["work_tools_h"].value == 0.5
    groups = sum(got[n].value for n in ("work_llm_h", "work_tools_h", "work_outside_h"))
    assert math.isclose(groups, got["work_h"].value, abs_tol=1e-9)
    assert all(got[n].timestamp == at(13) for n in NEW)


def test_no_session_and_no_rows_write_only_work_split_known_zero():
    for doc, generations in (
        (document(session=None), {}),
        (document(), {"s": NoRows()}),
        (document(), {}),
    ):
        got = scores(doc, generations)
        assert got["work_split_known"].value == 0.0
        assert set(got) & NEW == {"work_h", "work_split_known"}


def test_no_merge_or_an_unknown_stage_split_writes_none_of_the_five():
    assert set(scores(generations={"s": NoRows()}, merged=None)) & NEW == set()
    broken = document()
    broken["runs"].append({"slug": "developer", "started_at": "garbage"})
    got = scores(broken, {"s": NoRows()})
    assert set(got) & NEW == set()


def test_the_new_names_close_the_list_and_are_merge_dated():
    assert ts.SCORE_NAMES[-5:] == ts.WORK_SCORE_NAMES
    assert all(ts.merge_dated(name) for name in ts.WORK_SCORE_NAMES)


def test_a_generations_value_with_unreadable_intervals_never_raises():
    got = scores(generations={"s": Generations(None)})
    assert got["work_split_known"].value == 1.0
