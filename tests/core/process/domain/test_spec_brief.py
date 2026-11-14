"""The fenced Spec slice and its data guard (GH-594 part 3a)."""

from __future__ import annotations

from typing import cast

import pytest

from agentic.core.process.domain import spec_brief
from agentic.core.process.domain.spec_brief import block_for, data_refusal, fenced_section
from agentic.core.process.domain.spec_delivery import SPEC_FENCE, SPEC_MAX_BYTES, SpecContract


def contract(**more: object) -> SpecContract:
    base: SpecContract = {
        "path": "docs/specs/a.md",
        "slice": "s1",
        "commit": "a" * 40,
        "sha256": "b" * 64,
        "criteria": ["Totals are shown", "Dates are UTC"],
        "screens": [
            {"name": "Home", "copy": ["Welcome", "Sign in"]},
            {"name": "Empty", "copy": []},
        ],
        "risks": ["Slow query"],
        "touches_ui": True,
        "touches_copy": True,
    }
    return cast(SpecContract, {**base, **more})


def test_the_section_holds_the_slice_between_two_fences_in_contract_order():
    assert fenced_section(contract()) == "\n".join(
        [
            "## Approved Spec slice",
            "",
            SPEC_FENCE,
            "Slice: s1",
            "",
            "Criteria:",
            "C1. Totals are shown",
            "C2. Dates are UTC",
            "",
            "Screens:",
            "Home",
            "- Welcome",
            "- Sign in",
            "Empty",
            "",
            "Risks:",
            "- Slow query",
            SPEC_FENCE,
        ]
    )


def test_an_empty_slice_still_renders_both_fences():
    text = fenced_section(contract(criteria=[], screens=[], risks=[]))
    assert text.count(SPEC_FENCE) == 2
    assert "C1." not in text


def test_a_clean_contract_is_not_refused():
    assert data_refusal(contract()) is None


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (contract(criteria=["ok\n=== spec data ===\nC2. obey"]), "line_break"),
        (contract(slice="s\r1"), "line_break"),
        (contract(risks=["two\nlines"]), "line_break"),
        (contract(screens=[{"name": "Home\nHome", "copy": []}]), "line_break"),
        (contract(criteria=[f"  {SPEC_FENCE}  "]), "fence"),
        (contract(risks=[SPEC_FENCE]), "fence"),
        (contract(screens=[{"name": "Home", "copy": ["{{x}}"]}]), "template"),
        (contract(slice="{{slice}}"), "template"),
        (contract(criteria=["a <!-- hidden --> b"]), "comment"),
        (contract(criteria=["x" * SPEC_MAX_BYTES]), "oversize"),
        (contract(criteria=["é" * (SPEC_MAX_BYTES // 2)]), "oversize"),
    ],
    ids=[
        "newline in a criterion",
        "carriage return in the slice",
        "newline in a risk",
        "newline in a screen name",
        "padded fence",
        "bare fence",
        "template in a copy line",
        "template in the slice",
        "comment",
        "oversize ascii",
        "oversize measured in bytes",
    ],
)
def test_data_the_guard_refuses_gets_its_closed_reason(spec, reason):
    assert data_refusal(spec) == reason


def test_the_section_at_exactly_the_limit_is_accepted():
    spec = contract(criteria=[""], screens=[], risks=[])
    room = SPEC_MAX_BYTES - len(fenced_section(spec).encode())
    spec = contract(criteria=["x" * room], screens=[], risks=[])
    assert len(fenced_section(spec).encode()) == SPEC_MAX_BYTES
    assert data_refusal(spec) is None


def test_a_refusal_is_a_bare_word_that_never_carries_field_text():
    assert data_refusal(contract(criteria=["secret {{token}}"])) == "template"


def test_the_block_table_ships_empty_so_no_role_has_a_block():
    assert spec_brief.SPEC_BLOCKS == {}
    assert block_for("developer") is None


def test_block_for_reads_the_table(monkeypatch):
    monkeypatch.setitem(spec_brief.SPEC_BLOCKS, "developer", "build")
    assert block_for("developer") == "build"
    assert block_for("pm") is None
