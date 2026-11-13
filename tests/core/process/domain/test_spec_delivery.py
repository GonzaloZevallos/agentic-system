import hashlib

import pytest

from agentic.core.process.domain.spec_delivery import (
    Screen,
    body_sha256,
    derive_flags,
    render_approved_front_matter,
    render_draft_front_matter,
    split_front_matter,
)

BODY = "## Goal\n\nText.\n"


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def test_hash_is_of_the_body_with_lf_endings() -> None:
    assert body_sha256("---\nstatus: draft\n---\n" + BODY) == _sha(BODY)
    assert body_sha256("---\r\nstatus: draft\r\n---\r\n## Goal\r\n\r\nText.\r\n") == _sha(BODY)
    assert body_sha256("---\rstatus: draft\r---\r## Goal\r\rText.\r") == _sha(BODY)


def test_hash_ignores_a_bom() -> None:
    plain = "---\nstatus: draft\n---\n" + BODY
    assert body_sha256("﻿" + plain) == body_sha256(plain)


def test_hash_counts_the_final_newline_and_trailing_spaces() -> None:
    head = "---\nstatus: draft\n---\n"
    assert body_sha256(head + BODY) != body_sha256(head + BODY.rstrip("\n"))
    assert body_sha256(head + BODY) != body_sha256(head + BODY.replace("Text.", "Text. "))


def test_hash_does_not_move_with_the_status() -> None:
    draft = render_draft_front_matter() + BODY
    approved = render_approved_front_matter("owner", "2026-11-12T10:00:00Z", "a" * 64) + BODY
    assert body_sha256(draft) == body_sha256(approved) == _sha(BODY)


def test_hash_is_none_without_front_matter() -> None:
    assert body_sha256(BODY) is None
    assert body_sha256("---\nstatus: draft\n") is None


def test_split_returns_none_without_a_closing_line() -> None:
    assert split_front_matter("---\nstatus: draft\n") is None
    assert split_front_matter("---\nstatus: draft\n--- \n") is None
    assert split_front_matter(" ---\nstatus: draft\n---\n") is None
    assert split_front_matter("") is None


def test_split_returns_front_matter_and_body() -> None:
    assert split_front_matter("---\na: b\n---\nx\ny") == ("a: b", "x\ny")
    assert split_front_matter("---\n---") == ("", "")


def test_flags_without_screens() -> None:
    assert derive_flags([]) == (False, False)


def test_flags_with_a_screen_without_copy() -> None:
    assert derive_flags([Screen(name="Home", copy=[])]) == (True, False)


def test_flags_with_blank_copy_only() -> None:
    assert derive_flags([Screen(name="Home", copy=["  ", ""])]) == (True, False)


def test_flags_with_copy() -> None:
    screens = [Screen(name="A", copy=[]), Screen(name="B", copy=["Save"])]
    assert derive_flags(screens) == (True, True)


def test_draft_render() -> None:
    assert render_draft_front_matter() == "---\nstatus: draft\n---\n"


def test_approved_render() -> None:
    sha = "0123456789abcdef" * 4
    assert render_approved_front_matter("Some-User1", "2026-11-12T10:00:00Z", sha) == (
        f"---\nstatus: approved\napproved_by: Some-User1\napproved_at: 2026-11-12T10:00:00Z\n"
        f"approved_sha256: {sha}\n---\n"
    )


@pytest.mark.parametrize(
    ("by", "at", "sha"),
    [
        ("", "2026-11-12T10:00:00Z", "a" * 64),
        ("bad login", "2026-11-12T10:00:00Z", "a" * 64),
        ("-lead", "2026-11-12T10:00:00Z", "a" * 64),
        ("a--b", "2026-11-12T10:00:00Z", "a" * 64),
        ("x\nstatus: draft", "2026-11-12T10:00:00Z", "a" * 64),
        ("owner", "2026-11-12", "a" * 64),
        ("owner", "2026-11-12T10:00:00+00:00", "a" * 64),
        ("owner", "2026-11-12T10:00:00Z\n", "a" * 64),
        ("owner", "2026-11-12T10:00:00Z", "A" * 64),
        ("owner", "2026-11-12T10:00:00Z", "a" * 63),
        ("owner", "2026-11-12T10:00:00Z", "a" * 64 + "\n"),
    ],
)
def test_approved_render_rejects_bad_values(by: str, at: str, sha: str) -> None:
    with pytest.raises(ValueError):
        render_approved_front_matter(by, at, sha)
