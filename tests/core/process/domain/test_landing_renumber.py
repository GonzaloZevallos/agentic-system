"""The `renumber` landing step (GH-229, ADR-0049): where `adr_number_taken` leads once the
collision is a clean renaming, its own cap, and the carry rule for the rewrite it makes.
Pure: facts in, a step out."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import adr_numbers, landing
from agentic.core.process.domain.landing import LandingSpent, LandingStep, LineChange
from agentic.core.process.domain.rules import MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
RENAMING = adr_numbers.Renaming((("docs/adr/0049-x.md", "docs/adr/0050-x.md"),), {})
AMBIGUOUS = adr_numbers.Ambiguous("unclassified_token", "docs/a.md:3: 0049")


def _facts(collision) -> MergeFacts:
    return MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("docs/adr/0049-x.md",),
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
        adr_collision=collision,
    )


def _spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


def _step(collision, spent=None) -> LandingStep:
    return landing.landing_step("adr_number_taken", _facts(collision), spent or _spent(), ())


def test_a_clean_renaming_is_a_renumber_step():
    assert _step(RENAMING) == LandingStep("renumber", "adr_number_taken")


def test_an_ambiguous_result_goes_to_diagnose_never_to_a_pass():
    assert _step(AMBIGUOUS) == LandingStep("diagnose", "adr_number_taken")
    assert _step(AMBIGUOUS, _spent(renumbers=0)).units == ()


def test_the_cap_is_three_and_a_spent_cap_diagnoses():
    assert landing.LANDING_RENUMBERS_MAX == 3
    assert _step(RENAMING, _spent(renumbers=2)).kind == "renumber"
    assert _step(RENAMING, _spent(renumbers=3)).kind == "diagnose"


def test_a_renumber_is_not_charged_to_passes_or_syncs():
    spent = _spent(passes=landing.LANDING_PASSES_MAX, syncs=landing.LANDING_SYNCS_MAX)
    assert _step(RENAMING, spent).kind == "renumber"


def test_a_diagnosed_task_has_no_renumber_left():
    assert _step(RENAMING, _spent(diagnoses=1)).kind == "none"


DOC = LineChange("docs/adr/0050-x.md", "see ADR-0049", "see ADR-0050")
COMMENT = LineChange("agentic/x.py", "    # ADR-0049 says", "    # ADR-0050 says")


def test_a_docs_only_rewrite_carries_the_review():
    assert landing.renumber_carries([DOC, LineChange("docs/y.md", "a", "b")])


def test_a_comment_line_carries_whatever_the_path():
    assert landing.renumber_carries([DOC, COMMENT])


@pytest.mark.parametrize(
    "change",
    [
        LineChange("agentic/x.py", 'raise ValueError("ADR-0049")', 'raise ValueError("ADR-0050")'),
        LineChange("agentic/x.py", '    """ADR-0049."""', '    """ADR-0050."""'),
        LineChange("agentic/x.py", "X = '0049'", "X = '0050'"),
        LineChange("agentic/x.py", "x = 1  # ADR-0049", "x = 1  # ADR-0050"),
    ],
    ids=["string", "docstring", "literal", "trailing-comment"],
)
def test_code_outside_docs_does_not_carry(change):
    assert not landing.renumber_carries([DOC, change])


def test_the_comment_test_looks_at_the_new_line_as_well():
    assert not landing.renumber_carries([LineChange("a.py", "# old", "code()")])
    assert not landing.renumber_carries([LineChange("a.py", "code()", "# new")])


def test_a_rewrite_that_changes_nothing_carries():
    assert landing.renumber_carries([])


def test_it_agrees_with_review_carries_on_a_docs_only_change():
    # A docs-only sync merge and a docs-only renumber are both carried; a code change is
    # carried by neither unless the paths are disjoint (the sync's own criterion).
    assert landing.review_carries(False, ["docs/adr/0050-x.md"], ["agentic/a.py"])
    assert landing.renumber_carries([DOC])
    assert not landing.review_carries(True, ["docs/adr/0050-x.md"], ["agentic/a.py"])
    assert not landing.renumber_carries([LineChange("agentic/a.py", "x = 1", "x = 2")])


def test_line_changes_pairs_the_lines_that_differ_and_leaves_the_rest():
    found = landing.line_changes("docs/a.md", "a\nsee ADR-0049\nz\n", "a\nsee ADR-0050\nz\nnew\n")
    assert found == [
        LineChange("docs/a.md", "see ADR-0049", "see ADR-0050"),
        LineChange("docs/a.md", "", "new"),
    ]
    assert landing.line_changes("docs/a.md", "same\n", "same\n") == []


def test_the_renumber_record_renders_only_when_present_and_a_document_without_it_is_unchanged():
    from agentic.core.process.domain import document

    state = {"id": "GH-9", "title": "t", "description": "d"}
    record = {"from": "0049", "to": "0050", "head_before": "a", "head_after": "b", "carried": True}
    without = document.render(state)
    assert "adr_renumbered" not in without
    assert document.render({**state, "adr_renumbered": []}) == without
    assert "adr_renumbered" in document.render({**state, "adr_renumbered": [record]})


def test_the_numbers_a_renumbering_says_come_from_the_adr_rule():
    moves = {"docs/adr/0049-a.md": "docs/adr/0050-a.md", "docs/adr/0051-b.md": "docs/adr/0052-b.md"}
    assert adr_numbers.old_numbers(moves) == ["0049", "0051"]
    assert adr_numbers.new_numbers(moves) == ["0050", "0052"]
