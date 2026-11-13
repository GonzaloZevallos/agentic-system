import pytest

from agentic.core.knowledge.domain.spec import SECTIONS, validate
from agentic.core.process.domain.spec_delivery import (
    SPEC_FENCE,
    SPEC_MAX_BYTES,
    body_sha256,
    render_approved_front_matter,
    render_draft_front_matter,
)

OWNER = "Owner"
AT = "2026-11-12T10:00:00Z"
PATH = "docs/specs/x.md"
COMMIT = "c" * 40

PARTS = {
    "Goal": "Do the thing.",
    "Acceptance criteria": "- It works.",
    "Out of scope": "- Other things.",
    "Decisions": "- Use X\n  - Reason: it is simple.",
    "Screens": "### Home\nSlices: all\n- Save\n- Cancel",
    "Risks": "Some risk.",
    "Slices": "### one\n#### Acceptance criteria\n- One works.\n#### Risks\n- data-loss",
}


def body_of(**override: str | None) -> str:
    parts = {**PARTS, **{k.replace("_", " "): v for k, v in override.items()}}
    out = "# Title\n\n"
    for name in SECTIONS:
        if parts[name] is not None:
            out += f"## {name}\n\n{parts[name]}\n\n"
    return out


def approved(body: str, by: str = OWNER) -> str:
    sha = body_sha256(render_draft_front_matter() + body)
    assert sha is not None
    return render_approved_front_matter(by, AT, sha) + body


def check(text: str, slice_id: str | None = None, owner: str | None = OWNER) -> object:
    return validate(text, PATH, COMMIT, slice_id, owner)


def front_text(front: str, body: str | None = None) -> str:
    body = body or body_of()
    return f"---\n{front}\n---\n{body}"


def sha_of(body: str) -> str:
    sha = body_sha256(render_draft_front_matter() + body)
    assert sha is not None
    return sha


def two_slices() -> str:
    second = "\n### two\n#### Acceptance criteria\n- Two works.\n#### Risks\n- none"
    return PARTS["Slices"] + "\n" + second


def test_a_valid_spec_gives_its_contract() -> None:
    text = approved(body_of())
    assert check(text) == {
        "path": PATH,
        "slice": "one",
        "commit": COMMIT,
        "sha256": body_sha256(text),
        "criteria": ["One works."],
        "screens": [{"name": "Home", "copy": ["Save", "Cancel"]}],
        "risks": ["data-loss"],
        "touches_ui": True,
        "touches_copy": True,
    }


def test_round_trip_without_screens_has_no_flags() -> None:
    result = check(approved(body_of(Screens="None.")))
    assert isinstance(result, dict)
    assert (result["screens"], result["touches_ui"], result["touches_copy"]) == ([], False, False)


def test_a_screen_without_copy_touches_ui_only() -> None:
    result = check(approved(body_of(Screens="### Home\nSlices: all")))
    assert isinstance(result, dict)
    assert (result["touches_ui"], result["touches_copy"]) == (True, False)


def test_none_risk_is_an_empty_list() -> None:
    slices = PARTS["Slices"].replace("- data-loss", "- none")
    result = check(approved(body_of(Slices=slices)))
    assert isinstance(result, dict) and result["risks"] == []


@pytest.mark.parametrize("section", SECTIONS)
def test_a_missing_section_is_invalid(section: str) -> None:
    assert check(approved(body_of(**{section: None}))) == "spec_invalid"


@pytest.mark.parametrize("section", SECTIONS)
@pytest.mark.parametrize("empty", ["", "  \n\n \t"])
def test_an_empty_section_is_invalid(section: str, empty: str) -> None:
    assert check(approved(body_of(**{section: empty}))) == "spec_invalid"


def test_a_spec_without_acceptance_criteria_is_invalid() -> None:
    assert check(approved(body_of(Acceptance_criteria="Works, no bullets."))) == "spec_invalid"
    assert check(approved(body_of(Acceptance_criteria="-\n- "))) == "spec_invalid"


def test_the_section_order_and_name_case_are_free() -> None:
    text = approved("## SLICES\n" + PARTS["Slices"] + "\n" + body_of().replace("# Title\n", ""))
    assert check(text) == "spec_invalid"  # twice the Slices section
    reordered = "\n".join(f"## {n.upper()}\n{PARTS[n]}\n" for n in reversed(SECTIONS))
    assert isinstance(check(approved(reordered)), dict)


BAD_BODIES = {
    "mustache": body_of(Goal="Say {{name}}."),
    "comment": body_of(Goal="<!-- hidden -->"),
    "fence line": body_of(Goal=f"before\n  {SPEC_FENCE}  \nafter"),
    "unknown section": body_of() + "## Extra\n\ntext\n",
    "duplicate section": body_of() + "## Goal\n\nagain\n",
    "text before sections": "Intro.\n\n" + body_of(),
    "two titles": "# One\n\n" + body_of(),
    "level 1 later": body_of() + "# Late\n",
    "level 5 heading": body_of(Goal="##### deep"),
    "bare hashes": body_of(Goal="##"),
    "heading in a plain section": body_of(Goal="### sub\ntext"),
    "decision without reason": body_of(Decisions="- Use X"),
    "decision with empty reason": body_of(Decisions="- Use X\n  - Reason: "),
    "decision with two reasons": body_of(Decisions="- Use X\n  - Reason: a\n  - Reason: b"),
    "decision with another label": body_of(Decisions="- Use X\n  - Because: a"),
    "decision with loose text": body_of(Decisions="- Use X\n  - Reason: a\nstray"),
    "reason without decision": body_of(Decisions="  - Reason: a"),
    "screen without Slices line": body_of(Screens="### Home\n- Save"),
    "screen with unknown slice": body_of(Screens="### Home\nSlices: nope\n- Save"),
    "screen with all mixed": body_of(Screens="### Home\nSlices: all, one\n- Save"),
    "screen copy not a bullet": body_of(Screens="### Home\nSlices: all\nSave"),
    "duplicate screens": body_of(Screens="### Home\nSlices: all\n### HOME\nSlices: all"),
    "no slices": body_of(Slices="Nothing."),
    "slice id not a slug": body_of(Slices=PARTS["Slices"].replace("### one", "### One")),
    "duplicate slice": body_of(Slices=PARTS["Slices"] + "\n" + PARTS["Slices"]),
    "slice without criteria": body_of(Slices="### one\n#### Risks\n- none"),
    "slice with empty criteria": body_of(
        Slices="### one\n#### Acceptance criteria\n#### Risks\n- none"
    ),
    "slice without risks": body_of(Slices="### one\n#### Acceptance criteria\n- A"),
    "slice risk not a slug": body_of(Slices=PARTS["Slices"].replace("data-loss", "Data Loss")),
    "slice risk repeated": body_of(Slices=PARTS["Slices"] + "\n- data-loss"),
    "none mixed with risks": body_of(Slices=PARTS["Slices"] + "\n- none"),
    "slice with extra block": body_of(Slices=PARTS["Slices"] + "\n#### Notes\n- x"),
    "slice with loose text": body_of(Slices=PARTS["Slices"].replace("### one", "### one\ntext")),
    "setext heading": body_of(Goal="Title\n=====\ntext").replace("## Goal", "Goal\n-----", 1),
    "open backtick fence": body_of(Goal="```\ncode"),
    "open tilde fence": body_of(Goal="~~~\ncode"),
    "closer shorter than opener": body_of(Goal="````\ncode\n```"),
    "closer of another char": body_of(Goal="```\ncode\n~~~"),
    "closer with text": body_of(Goal="```\ncode\n``` x"),
}


def test_the_valid_baseline_is_accepted() -> None:
    assert isinstance(check(approved(body_of())), dict)


@pytest.mark.parametrize("name", BAD_BODIES)
def test_a_structure_failure_is_invalid(name: str) -> None:
    assert check(approved(BAD_BODIES[name])) == "spec_invalid"


def test_a_setext_underline_is_not_a_heading_so_its_section_is_missing() -> None:
    body = body_of().replace("## Goal", "Goal\n-----")
    assert check(approved(body)) == "spec_invalid"


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "   ```"])
def test_a_heading_inside_a_fence_is_not_counted(fence: str) -> None:
    close = fence.strip()
    goal = f"Do it.\n{fence}\n## Fake\n# Fake\n{close}"
    assert isinstance(check(approved(body_of(Goal=goal))), dict)


def test_a_fence_closes_only_with_its_own_kind() -> None:
    goal = "```\n## Fake\n~~~\n## Still fake\n```"
    assert isinstance(check(approved(body_of(Goal=goal))), dict)


def test_a_backtick_opener_with_a_backtick_in_its_info_is_not_a_fence() -> None:
    assert isinstance(check(approved(body_of(Goal="``` a`b\ntext"))), dict)


def test_closing_hashes_are_not_part_of_the_name() -> None:
    assert isinstance(check(approved(body_of().replace("## Goal", "## Goal ##"))), dict)
    assert isinstance(check(approved(body_of().replace("## Goal", "##   Goal   "))), dict)


@pytest.mark.parametrize(
    "front",
    [
        "",
        "- a\n- b",
        "just text",
        "status: [draft]",
        "status: {a: b}",
        "status: draft\nstatus: draft",
        "status: draft\nowner: me",
        "status: draft\nextra: 1",
        "status: &a draft",
        "status: *a",
        "&a status: draft",
        "status: archived",
        "status: Draft",
        "approved_by: x",
        "status: draft\n---\nstatus: draft",
        "status: draft\napproved_by: Owner",
        "status: draft\napproved_at: 2026-11-12T10:00:00Z",
        "status: draft\napproved_sha256: " + "a" * 64,
        "status: draft\n\tbad: [",
        "<<: {}\nstatus: draft",
        "status: !!python/object/apply:os.system [ls]",
        "[" * 30000,
        "a: " + "[" * 30000,
        "a: " + "[" * 500 + "]" * 500,
        "status: draft\n" + "# padding\n" * 120,
        "? [a]\n: b",
    ],
)
def test_a_front_matter_failure_is_invalid(front: str) -> None:
    assert check(front_text(front)) == "spec_invalid"


def test_a_draft_with_empty_approval_values_is_a_draft() -> None:
    assert check(front_text("status: draft\napproved_by:\napproved_at: ''")) == "spec_unapproved"


def test_a_missing_or_open_front_matter_is_invalid() -> None:
    assert check(body_of()) == "spec_invalid"
    assert check("---\nstatus: draft\n" + body_of()) == "spec_invalid"
    assert check("") == "spec_invalid"


def test_a_text_over_the_size_cap_is_invalid() -> None:
    padding = "x" * SPEC_MAX_BYTES
    assert check(approved(body_of(Goal=padding))) == "spec_invalid"
    assert len(approved(body_of(Goal="é" * (SPEC_MAX_BYTES // 2))).encode()) > SPEC_MAX_BYTES
    assert check(approved(body_of(Goal="é" * (SPEC_MAX_BYTES // 2)))) == "spec_invalid"


def test_a_text_that_cannot_be_encoded_is_invalid() -> None:
    assert (
        check(front_text("status: draft", body_of(Goal="lone \ud800 surrogate"))) == "spec_invalid"
    )


def test_the_fence_line_in_the_front_matter_is_invalid() -> None:
    assert check(front_text(f"status: draft\n{SPEC_FENCE}")) == "spec_invalid"


def test_a_draft_is_unapproved() -> None:
    assert check(render_draft_front_matter() + body_of()) == "spec_unapproved"


def test_a_hash_mismatch_is_unapproved() -> None:
    text = approved(body_of()).replace("Do the thing.", "Do another thing.")
    assert check(text) == "spec_unapproved"


def test_a_changed_final_newline_is_unapproved() -> None:
    assert check(approved(body_of()).rstrip("\n")) == "spec_unapproved"


def test_a_non_owner_is_unapproved() -> None:
    assert check(approved(body_of(), by="intruder")) == "spec_unapproved"


@pytest.mark.parametrize("owner", [None, "", "   "])
def test_a_missing_owner_login_approves_nothing(owner: str | None) -> None:
    assert check(approved(body_of()), owner=owner) == "spec_unapproved"


def test_the_login_is_compared_with_casefold() -> None:
    assert isinstance(check(approved(body_of(), by="oWNER")), dict)
    assert isinstance(check(approved(body_of()), owner="OWNER"), dict)


@pytest.mark.parametrize(
    "front",
    [
        "status: approved",
        "status: approved\napproved_by: Owner\napproved_at: {at}",
        "status: approved\napproved_by: Owner\napproved_at: {at}\napproved_sha256: {upper}",
        "status: approved\napproved_by: Owner\napproved_at: {at}\napproved_sha256: abc",
        "status: approved\napproved_by: Owner\napproved_at: today\napproved_sha256: {sha}",
        "status: approved\napproved_by: Owner\napproved_at: 2026-11-12\napproved_sha256: {sha}",
        "status: approved\napproved_by: Owner\napproved_at: 2026-11-12T10:00:00\n"
        "approved_sha256: {sha}",
        "status: approved\napproved_by: Owner\napproved_at: 2026-11-12T10:00:00+02:00\n"
        "approved_sha256: {sha}",
        "status: approved\napproved_by: Owner\napproved_sha256: {sha}",
        "status: approved\napproved_at: {at}\napproved_sha256: {sha}",
    ],
)
def test_incomplete_or_malformed_approval_is_unapproved(front: str) -> None:
    sha = sha_of(body_of())
    text = front_text(front.format(at=AT, sha=sha, upper=sha.upper()))
    assert check(text) == "spec_unapproved"


def test_a_zero_offset_other_than_z_is_approved() -> None:
    sha = sha_of(body_of())
    front = (
        "status: approved\napproved_by: Owner\napproved_at: 2026-11-12T10:00:00+00:00\n"
        f"approved_sha256: {sha}"
    )
    assert isinstance(check(front_text(front)), dict)


def test_crlf_and_bom_do_not_move_the_approval() -> None:
    text = approved(body_of())
    assert isinstance(check("﻿" + text.replace("\n", "\r\n")), dict)


def test_structure_failures_beat_approval_failures() -> None:
    text = approved(body_of(Goal="{{x}}"), by="intruder")
    assert check(text) == "spec_invalid"
    assert check(front_text("status: draft", body_of(Goal="")), "nope") == "spec_invalid"


def test_approval_failures_beat_slice_failures() -> None:
    assert check(approved(body_of(Slices=two_slices()), by="intruder")) == "spec_unapproved"
    assert check(approved(body_of(), by="intruder"), "nope") == "spec_unapproved"
    assert check(approved(body_of()), "nope", owner=None) == "spec_unapproved"


def test_slice_missing_comes_before_slice_unknown() -> None:
    text = approved(body_of(Slices=two_slices()))
    assert check(text) == "slice_missing"
    assert check(text, "three") == "slice_unknown"


def test_the_slice_suffix_is_optional_with_one_slice() -> None:
    text = approved(body_of())
    assert isinstance(check(text), dict)
    assert isinstance(check(text, "one"), dict)
    assert check(text, "two") == "slice_unknown"


def test_the_slice_suffix_selects_among_several() -> None:
    text = approved(body_of(Slices=two_slices()))
    result = check(text, "two")
    assert isinstance(result, dict)
    assert (result["slice"], result["criteria"], result["risks"]) == ("two", ["Two works."], [])


@pytest.mark.parametrize("slice_id", ["", "Slice-1", " one", "one ", "ONE", "o ne", "one\n"])
def test_a_slice_that_is_not_a_slice_id_is_unknown(slice_id: str) -> None:
    assert check(approved(body_of()), slice_id) == "slice_unknown"
    assert check(approved(body_of(Slices=two_slices())), slice_id) == "slice_unknown"


def test_screens_follow_the_slice() -> None:
    screens = (
        "### Both\nSlices: all\n- Shared\n"
        "### Only one\nSlices: one\n- First\n"
        "### Only two\nSlices: two\n- Second\n"
        "### Silent\nSlices: two\n"
    )
    text = approved(body_of(Screens=screens, Slices=two_slices()))
    one, two = check(text, "one"), check(text, "two")
    assert isinstance(one, dict) and isinstance(two, dict)
    assert [s["name"] for s in one["screens"]] == ["Both", "Only one"]
    assert [s["name"] for s in two["screens"]] == ["Both", "Only two", "Silent"]
    assert (two["touches_ui"], two["touches_copy"]) == (True, True)


def test_a_slice_without_matching_screens_touches_nothing() -> None:
    screens = "### Only two\nSlices: two\n- Second"
    text = approved(body_of(Screens=screens, Slices=two_slices()))
    result = check(text, "one")
    assert isinstance(result, dict)
    assert (result["screens"], result["touches_ui"], result["touches_copy"]) == ([], False, False)
