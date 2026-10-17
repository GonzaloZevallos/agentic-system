"""`observability/README.md`'s score table names exactly the scores the code writes
(GH-273): a score added to `task_scores.SCORE_NAMES` without its row, or a row for a score
nothing writes, fails here."""

import re

import pytest

from agentic.core.measurement.domain import task_scores
from tests.readme_metrics import README

HEADING = "## Scores por tarea"
ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.M)


def documented_scores(text: str) -> list[str]:
    """The score names of the first table under the scores heading, in order."""
    if HEADING not in text:
        raise AssertionError(f"{HEADING!r} heading not found")
    section = re.split(r"\n## ", text.split(HEADING, 1)[1], maxsplit=1)[0]
    table = section.split("| Score |", 1)
    if len(table) != 2:
        raise AssertionError(f"no score table under {HEADING!r}")
    rows = re.split(r"\n\n", table[1], maxsplit=1)[0]
    return ROW.findall(rows)


def test_the_readme_score_table_equals_the_score_names():
    names = documented_scores(README.read_text(encoding="utf-8"))
    assert sorted(names) == sorted(task_scores.SCORE_NAMES)
    assert len(names) == len(set(names))


def test_a_table_missing_a_score_is_rejected():
    text = f"{HEADING}\n\n| Score | f |\n|---|---|\n| `cost_usd` | x |\n\nrest"
    assert sorted(documented_scores(text)) != sorted(task_scores.SCORE_NAMES)


def test_a_readme_without_the_section_is_rejected():
    with pytest.raises(AssertionError, match="heading not found"):
        documented_scores("# nothing here")
