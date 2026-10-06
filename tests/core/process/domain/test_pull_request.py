"""The pull request body's bound (GH-203): GitHub refuses a body over 65,536 UTF-16 code
units, so the body `deliver` sends is cut to whole sections, and only the first (the PM's
definition) is ever trimmed, at a blank line outside a fenced block."""

from agentic.core.process.domain.pull_request import (
    PR_BODY_MAX,
    bounded_body,
    fence_open,
    github_length,
)

GITHUB_LIMIT = 65_536


def pointer(omitted: tuple[str, ...]) -> str:
    return f"See the task document for: {', '.join(omitted)}."


def join(parts):
    return "\n\n".join(f"## {heading}\n\n{text}" for heading, text in parts)


def test_github_length_counts_utf16_code_units_not_characters():
    assert github_length("abc") == 3
    assert github_length("é") == 1
    assert github_length("😀") == 2  # one character, a surrogate pair for GitHub
    assert len("😀" * 10) == 10 and github_length("😀" * 10) == 20


def test_fence_open_toggles_on_backtick_and_tilde_fences_after_leading_spaces():
    assert not fence_open(["text", ""])
    assert fence_open(["text", "```python", "x = 1"])
    assert not fence_open(["```", "x", "```"])
    assert fence_open(["  ~~~", "x"])
    assert not fence_open(["~~~", "x", "~~~", "y"])


def test_under_the_limit_is_exactly_todays_join_and_not_bounded():
    parts = [("PM", "Six numbers."), ("Tech Lead", "The plan.")]
    result = bounded_body(parts, "Title", pointer)
    assert result.text == "## PM\n\nSix numbers.\n\n## Tech Lead\n\nThe plan."
    assert result.bounded is False
    assert result.omitted == ()


def test_no_parts_is_the_fallback():
    result = bounded_body([], "Dashboard", pointer)
    assert result.text == "Dashboard"
    assert not result.bounded


def test_a_body_exactly_at_the_limit_is_untouched_and_one_over_is_bounded():
    head = "## PM\n\n"
    at = [("PM", "x" * (PR_BODY_MAX - len(head)))]
    assert github_length(join(at)) == PR_BODY_MAX
    assert bounded_body(at, "T", pointer).text == join(at)
    assert not bounded_body(at, "T", pointer).bounded

    over = [("PM", "x" * (PR_BODY_MAX - len(head) + 1))]
    result = bounded_body(over, "T", pointer)
    assert result.bounded
    assert github_length(result.text) <= PR_BODY_MAX


def test_an_astral_plane_body_is_measured_in_utf16_units():
    # 20,000 emoji: 20,000 characters but 40,000 units; two sections of that is over.
    parts = [("PM", "😀" * 20_000), ("Tech Lead", "😀" * 20_000)]
    assert len(join(parts)) < PR_BODY_MAX < github_length(join(parts))
    result = bounded_body(parts, "T", pointer)
    assert result.bounded
    assert github_length(result.text) <= PR_BODY_MAX
    assert "## PM" in result.text
    assert result.omitted == ("Tech Lead",)
    assert "## Tech Lead\n" not in result.text


def test_later_sections_are_dropped_whole_and_named_in_the_pointer():
    parts = [("PM", "short"), ("Tech Lead", "y" * 70_000), ("Observations on the design", "z")]
    result = bounded_body(parts, "T", pointer)
    assert result.text.startswith("## PM\n\nshort\n\n")
    # Whole or nothing: the small last section does not jump over the big one.
    assert result.omitted == ("Tech Lead", "Observations on the design")
    assert result.text.endswith(
        "## Task document\n\nSee the task document for: Tech Lead, Observations on the design."
    )
    assert "y" * 10 not in result.text


def test_the_first_section_is_trimmed_at_a_blank_line_with_a_closing_line():
    paragraphs = [f"paragraph {i} " + "w" * 990 for i in range(100)]
    parts = [("PM", "\n\n".join(paragraphs)), ("Tech Lead", "plan")]
    result = bounded_body(parts, "T", pointer)
    assert github_length(result.text) <= PR_BODY_MAX
    assert result.omitted == ("PM", "Tech Lead")
    body, task_document = result.text.split("\n\n## Task document\n\n")
    assert body.endswith("\n\n(truncated; full text in the task document)")
    kept = body.removesuffix("\n\n(truncated; full text in the task document)")
    # The cut is at a paragraph boundary: the last kept paragraph is whole.
    assert kept.rsplit("\n\n", 1)[1] in paragraphs
    assert "PM" in task_document


def test_the_cut_never_lands_inside_a_fence_it_moves_before_it():
    prose = "\n\n".join(f"paragraph {i} " + "w" * 990 for i in range(40))
    code = "```\n" + "\n\n".join("c" * 990 for _ in range(40)) + "\n```"
    parts = [("PM", f"{prose}\n\n{code}")]
    result = bounded_body(parts, "T", pointer)
    assert github_length(result.text) <= PR_BODY_MAX
    assert "```" not in result.text
    assert not fence_open(result.text.splitlines())
    assert "paragraph 39" in result.text


def test_a_first_section_that_is_one_long_fence_is_dropped_whole():
    parts = [("PM", "```\n" + "\n\n".join("c" * 990 for _ in range(100)) + "\n```")]
    result = bounded_body(parts, "T", pointer)
    assert result.text == "## Task document\n\nSee the task document for: PM."
    assert result.omitted == ("PM",)
    assert result.bounded


def test_the_result_leaves_room_for_the_closes_prefix_even_with_a_long_pointer():
    def long_pointer(omitted):
        return "p" * 1_000 + ", ".join(omitted)

    parts = [("PM", "\n\n".join("a" * 500 for _ in range(200))), ("Tech Lead", "b" * 70_000)]
    result = bounded_body(parts, "T", long_pointer)
    assert github_length("Closes #1234567890\n\n" + result.text) < GITHUB_LIMIT
