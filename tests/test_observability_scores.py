"""`observability/README.md`'s score table names exactly the scores the code writes
(GH-273): a score added to `task_scores.SCORE_NAMES` without its row, or a row for a score
nothing writes, fails here."""

import re

import pytest

from agentic.core.measurement.domain import task_scores
from tests.readme_metrics import README

HEADING = "## Scores por tarea"
ROW = re.compile(r"^\|\s*`([a-z_.]+?)(\.<[A-Za-z]+>)?`\s*\|", re.M)


def documented_scores(text: str) -> list[str]:
    """The score names of the first table under the scores heading, in order."""
    if HEADING not in text:
        raise AssertionError(f"{HEADING!r} heading not found")
    section = re.split(r"\n## ", text.split(HEADING, 1)[1], maxsplit=1)[0]
    table = section.split("| Score |", 1)
    if len(table) != 2:
        raise AssertionError(f"no score table under {HEADING!r}")
    rows = re.split(r"\n\n", table[1], maxsplit=1)[0]
    names: list[str] = []
    for name, family in ROW.findall(rows):
        if not family:
            names.append(name)
        elif name in task_scores.FAMILIES:
            names.extend(task_scores.FAMILIES[name])
        else:
            raise AssertionError(f"{name}{family} names a family nothing builds")
    return names


def test_the_readme_score_table_equals_the_score_names():
    names = documented_scores(README.read_text(encoding="utf-8"))
    assert sorted(names) == sorted(task_scores.SCORE_NAMES)
    assert len(names) == len(set(names))


def test_a_family_row_expands_to_every_member_of_its_closed_set():
    text = f"{HEADING}\n\n| Score | f |\n|---|---|\n| `returns.<ChangeCategory>` | x |\n\nrest"
    assert documented_scores(text) == list(task_scores.FAMILIES["returns"])


def test_a_family_row_nothing_builds_is_rejected():
    text = f"{HEADING}\n\n| Score | f |\n|---|---|\n| `made_up.<Nothing>` | x |\n\nrest"
    with pytest.raises(AssertionError, match="names a family nothing builds"):
        documented_scores(text)


def test_a_table_missing_a_score_is_rejected():
    text = f"{HEADING}\n\n| Score | f |\n|---|---|\n| `cost_usd` | x |\n\nrest"
    assert sorted(documented_scores(text)) != sorted(task_scores.SCORE_NAMES)


def test_a_readme_without_the_section_is_rejected():
    with pytest.raises(AssertionError, match="heading not found"):
        documented_scores("# nothing here")
