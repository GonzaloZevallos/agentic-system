"""The size budget is stated only by rendering it from `size_budget` (GH-592, plan invariant 9):
no prompt and no description of a plan's or a part's size fields retypes 15, 400 or 600.

The guard reads the prompt files as text and the schema module as a syntax tree, so a literal
inside an f-string's fixed pieces is seen and the interpolated constant is not."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agentic.core.process.domain import schemas, size_budget

DOMAIN = Path(__file__).resolve().parents[4] / "agentic" / "core" / "process" / "domain"
PROMPTS = DOMAIN / "prompts"
SCHEMAS = DOMAIN / "schemas.py"

LIMITS = (size_budget.HAND_FILES, size_budget.HAND_LINES, size_budget.TEST_LINES)
SIZE_FIELDS = ("hand_files", "hand_lines", "test_lines")
SIZE_CLASSES = ("Part", "TechPlan")


def retyped(text: str) -> list[str]:
    """The limits `text` writes as a number: each of them as a whole word."""
    return [str(n) for n in LIMITS if re.search(rf"(?<![\w.]){n}(?![\w.])", text)]


def prompt_problems(root: Path) -> list[str]:
    """One line per prompt file that retypes a limit."""
    return [
        f"{path.relative_to(root)}: {', '.join(found)}"
        for path in sorted(root.rglob("*"))
        if path.is_file() and (found := retyped(path.read_text(encoding="utf-8")))
    ]


def description_problems(source: str) -> list[str]:
    """One line per size field of `Part` or `TechPlan` whose `description=` writes a limit as a
    literal. Only the fixed text counts: `{size_budget.HAND_FILES}` in an f-string is not one."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or node.name not in SIZE_CLASSES:
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if stmt.target.id not in SIZE_FIELDS or not isinstance(stmt.value, ast.Call):
                continue
            for keyword in stmt.value.keywords:
                if keyword.arg != "description":
                    continue
                fixed = " ".join(
                    piece.value
                    for piece in ast.walk(keyword.value)
                    if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
                )
                if numbers := retyped(fixed):
                    found.append(f"{node.name}.{stmt.target.id}: {', '.join(numbers)}")
    return found


def test_no_prompt_retypes_a_limit_of_the_budget():
    assert prompt_problems(PROMPTS) == []


def test_no_size_field_description_retypes_a_limit_in_its_source():
    assert description_problems(SCHEMAS.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("model", [schemas.Part, schemas.TechPlan])
def test_the_rendered_descriptions_carry_the_limits_from_the_constants(model):
    properties = model.model_json_schema()["properties"]
    assert str(size_budget.HAND_FILES) in properties["hand_files"]["description"]
    assert str(size_budget.HAND_LINES) in properties["hand_lines"]["description"]
    assert str(size_budget.TEST_LINES) in properties["test_lines"]["description"]


def test_a_prompt_that_writes_a_limit_is_reported(tmp_path):
    (tmp_path / "clean.md").write_text("Declare the size honestly.", encoding="utf-8")
    (tmp_path / "typed.md").write_text("A task has at most 15 files.", encoding="utf-8")
    nested = tmp_path / "more"
    nested.mkdir()
    (nested / "lines.md").write_text("Up to 400 lines, 600 of tests.", encoding="utf-8")
    assert prompt_problems(tmp_path) == ["more/lines.md: 400, 600", "typed.md: 15"]


def test_a_number_that_only_contains_a_limit_is_not_reported():
    assert retyped("1500 files, 4000 lines, v0.15, 60000") == []


def test_a_description_that_writes_a_limit_in_its_fixed_text_is_reported():
    source = """
class Part:
    hand_files: int = Field(description="at most 15 files")
    hand_lines: int = Field(description=f"at most {size_budget.HAND_LINES}, not 400 more")
    test_lines: int = Field(description=f"at most {size_budget.TEST_LINES}")
    other: int = Field(description="600 is fine on a field that is no size")
"""
    assert description_problems(source) == ["Part.hand_files: 15", "Part.hand_lines: 400"]
