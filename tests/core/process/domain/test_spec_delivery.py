import hashlib
from typing import Any

import pytest

from agentic.core.process.domain.spec_delivery import (
    SLICE_SLUG,
    Screen,
    SpecLine,
    SpecLineMalformed,
    SpecRefusal,
    body_sha256,
    derive_flags,
    normalize_text,
    parse_spec_line,
    render_approved_front_matter,
    render_draft_front_matter,
    spec_commit_ok,
    spec_path_ok,
    spec_state_patch,
    spec_unchanged,
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


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "docs/specs/../x.md",
        "/docs/specs/a.md",
        "docs\\specs\\a.md",
        "docs/specs/a\x00.md",
        "docs/specs/a.md\n",
        "docs/specs/a\x1f.md",
        "docs/specs/sub/a.md",
        "docs/specs/a.txt",
        "docs/other/a.md",
        "docs/specs/.md",
        "docs/specs/.hidden.md",
        "docs/specs/" + "a" * 253 + ".md",
    ],
)
def test_spec_path_refuses_every_other_form(path: str) -> None:
    assert not spec_path_ok(path)


@pytest.mark.parametrize(
    "path", ["docs/specs/GH-594.md", "docs/specs/a_b-c.1.md", "docs/specs/" + "a" * 240 + ".md"]
)
def test_spec_path_accepts_its_one_form(path: str) -> None:
    assert spec_path_ok(path)


def test_spec_path_limit_is_255_characters() -> None:
    edge = "docs/specs/" + "a" * (255 - len("docs/specs/") - len(".md")) + ".md"
    assert len(edge) == 255
    assert spec_path_ok(edge)
    assert not spec_path_ok(edge[:-3] + "a.md")


@pytest.mark.parametrize("commit", ["a" * 40, "0123456789abcdef" * 4])
def test_spec_commit_accepts_a_full_lowercase_id(commit: str) -> None:
    assert spec_commit_ok(commit)


@pytest.mark.parametrize(
    "commit", ["abc1234", "A" * 40, "HEAD", "-x", "a" * 41, "a" * 40 + "\n", "", "g" * 40]
)
def test_spec_commit_refuses_everything_else(commit: str) -> None:
    assert not spec_commit_ok(commit)


# --- GH-594 part 4a: the pure delivery rules ---------------------------------------------

SPEC = "docs/specs/a.md"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", None),
        ("No line here\nat all", None),
        (f"Intro\nSpec: {SPEC}\nMore", SpecLine(SPEC, None)),
        (f"Spec: {SPEC}#first-slice", SpecLine(SPEC, "first-slice")),
        (f"Intro\r\nSpec: {SPEC}#s1\r\nEnd", SpecLine(SPEC, "s1")),
        (f"Intro\rSpec: {SPEC}\rEnd", SpecLine(SPEC, None)),
        (f"Spec: {SPEC} ", SpecLineMalformed()),
        (f"Spec: {SPEC}#", SpecLineMalformed()),
        (f"Spec: {SPEC}#Bad_Slug", SpecLineMalformed()),
        (f"Spec: {SPEC}#a--b", SpecLineMalformed()),
        (f"Spec: {SPEC}#s ", SpecLineMalformed()),
        ("Spec: docs/specs/../a.md", SpecLineMalformed()),
        ("Spec: README.md", SpecLineMalformed()),
        ("Spec: ", SpecLineMalformed()),
        ("Spec:", SpecLineMalformed()),
        (f"Spec:  {SPEC}", SpecLineMalformed()),
        (f"Spec:{SPEC}", SpecLineMalformed()),
        (f"spec: {SPEC}", None),
        (f" Spec: {SPEC}", None),
        (f"Spec: {SPEC}\nSpec: {SPEC}", SpecLineMalformed()),
        (f"Spec: {SPEC}\nSpec: docs/specs/b.md", SpecLineMalformed()),
        (f"```\nSpec: {SPEC}\n```", SpecLine(SPEC, None)),
        (f"<!--\nSpec: {SPEC}\n-->", SpecLine(SPEC, None)),
        (f"Spec: {SPEC} ", SpecLineMalformed()),
        (f"Spec: {SPEC}\x0b", SpecLineMalformed()),
        (f"Spec: {SPEC}\x85", SpecLineMalformed()),
        (f"Spec: {SPEC} Spec: {SPEC}", SpecLineMalformed()),
        (f"Spec: {SPEC}\x85#s", SpecLineMalformed()),
    ],
)
def test_parse_spec_line(body: str, expected: object) -> None:
    assert parse_spec_line(body) == expected


def test_slice_slug_is_the_one_literal() -> None:
    assert SLICE_SLUG == r"[a-z0-9]+(?:-[a-z0-9]+)*"


def test_normalize_text() -> None:
    assert normalize_text("a\r\nb\rc\nd") == "a\nb\nc\nd"
    assert normalize_text("﻿a\r\n") == "a\n"
    assert normalize_text("﻿﻿a") == "﻿a"
    assert normalize_text("a b\x0bc\x85d") == "a b\x0bc\x85d"
    assert normalize_text("") == ""


def _contract(**over: Any) -> Any:
    base: dict[str, Any] = {
        "path": SPEC,
        "slice": "first",
        "commit": "a" * 40,
        "sha256": "b" * 64,
        "criteria": ["It works"],
        "screens": [],
        "risks": [],
        "touches_ui": True,
        "touches_copy": True,
    }
    return {**base, **over}


@pytest.mark.parametrize(
    ("screens", "ui", "copy"),
    [
        ([], False, False),
        ([{"name": "Home", "copy": []}], True, False),
        ([{"name": "Home", "copy": ["  ", ""]}], True, False),
        ([{"name": "Home", "copy": ["Hello"]}], True, True),
        ([{"name": "A", "copy": []}, {"name": "B", "copy": ["x"]}], True, True),
    ],
)
def test_state_patch_derives_the_flags_over_contrary_ones(
    screens: list[Any], ui: bool, copy: bool
) -> None:
    contract = _contract(screens=screens, touches_ui=not ui, touches_copy=not copy)
    patch = spec_state_patch(contract)
    assert not isinstance(patch, SpecRefusal)
    assert patch["flags"] == {"touches_ui": ui, "touches_copy": copy}
    assert patch["spec"] == {**contract, "touches_ui": ui, "touches_copy": copy}
    assert set(patch) == {"spec", "flags"}
    assert contract["touches_ui"] is not ui  # the input is not mutated


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"path": "README.md"}, "path"),
        ({"path": 3}, "path"),
        ({"commit": "abc"}, "commit"),
        ({"commit": "A" * 40}, "commit"),
        ({"sha256": "b" * 63}, "sha256"),
        ({"sha256": "B" * 64}, "sha256"),
        ({"slice": "Bad_Slug"}, "slice"),
        ({"slice": ""}, "slice"),
        ({"screens": [{"name": "x"}]}, "screens"),
        ({"screens": "none"}, "screens"),
        ({"criteria": "x"}, "criteria"),
        ({"risks": [3]}, "risks"),
    ],
)
def test_state_patch_refuses_an_invalid_field_naming_it(over: dict[str, Any], field: str) -> None:
    assert spec_state_patch(_contract(**over)) == SpecRefusal("spec_invalid", (field,))


def test_state_patch_refuses_missing_keys_and_several_fields() -> None:
    contract = _contract(slice="BAD", commit="x")
    del contract["risks"]
    assert spec_state_patch(contract) == SpecRefusal("spec_invalid", ("slice", "commit", "risks"))
    assert spec_state_patch({}).cause == "spec_invalid"  # type: ignore[arg-type]


def test_spec_unchanged() -> None:
    text = "---\nstatus: draft\n---\n" + BODY
    assert spec_unchanged(_sha(BODY), text)
    assert spec_unchanged(_sha(BODY), text.replace("\n", "\r\n"))
    assert not spec_unchanged(_sha(BODY), text.replace("Text.", "Other."))
    assert not spec_unchanged(_sha(BODY), BODY)
    assert not spec_unchanged(_sha(BODY), "---\nstatus: draft\n---\n\ud800")
    assert not spec_unchanged("", text)
