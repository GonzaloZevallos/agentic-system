"""GH-1142: why an eval set could not be read, and the one sentence for local commits."""

import pytest

from agentic.core.process.domain import rules


def test_the_local_commits_sentence_names_the_count_and_the_shas() -> None:
    assert rules.local_commits_note(["abc1234"]) == (
        "the product checkout has 1 local commit not on the upstream: abc1234, left out of the base"
    )
    assert rules.local_commits_note(["a1", "b2"]) == (
        "the product checkout has 2 local commits not on the upstream: a1, b2, left out of the base"
    )


def test_the_local_commits_sentence_shows_a_few_and_counts_the_rest() -> None:
    shas = [f"{n:07x}" for n in range(13)]
    note = rules.local_commits_note(shas)
    assert note.startswith("the product checkout has 13 local commits not on the upstream: ")
    assert shas[9] in note and shas[10] not in note
    assert note.endswith(" and 3 more, left out of the base")


@pytest.mark.parametrize(
    ("sha_known", "left_out", "failure", "parsed", "cause"),
    [
        (False, (), False, True, "no_sha"),
        (False, ("a1",), False, True, "no_sha"),
        (True, (), True, True, "upstream_note"),
        (True, ("a1",), True, True, "upstream_note"),
        (True, ("a1",), False, True, "local_commits"),
        (True, ("a1",), False, False, "local_commits"),
        (True, (), False, False, "unparsable"),
        (True, (), False, True, None),
    ],
)
def test_the_cause_follows_one_precedence(
    sha_known: bool, left_out: tuple[str, ...], failure: bool, parsed: bool, cause: str | None
) -> None:
    assert rules.eval_set_unread_cause(sha_known, left_out, failure, parsed) == cause
