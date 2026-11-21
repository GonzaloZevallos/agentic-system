import pytest

from agentic.core.knowledge.domain.spec import validate
from agentic.core.process.domain.spec_approval import (
    NOTICE_PREFIX,
    ApprovalReading,
    Comment,
    approve,
    notice,
    read_approval,
)
from agentic.core.process.domain.spec_delivery import body_sha256, draft_text

OWNER = "Owner"
AT = "2026-11-18T10:00:00Z"
ESCALATED = "Escalated: "
SYSTEM = ("Resumed: ", "Ready: ")
BODY = (
    "# Title\n\n## Goal\n\nDo it.\n\n## Acceptance criteria\n\n- Works.\n\n"
    "## Out of scope\n\n- Other.\n\n## Decisions\n\n- Use X\n  - Reason: simple.\n\n"
    "## Screens\n\nnone\n\n## Risks\n\nSome.\n\n"
    "## Slices\n\n### one\n#### Acceptance criteria\n- One works.\n#### Risks\n- none\n"
)
DRAFT = draft_text(BODY)
EDITED = draft_text(BODY + "\nMore.\n")


def _sha(text: str) -> str:
    sha = body_sha256(text)
    assert sha is not None
    return sha


def _notice(text: str = DRAFT, author: str = OWNER) -> Comment:
    return Comment(notice(text, "A summary."), author)


def _read(comments: list[Comment], text: str = DRAFT, owner: str | None = OWNER) -> ApprovalReading:
    return read_approval(comments, text, owner, SYSTEM, ESCALATED)


def test_notice_carries_the_hash_the_prefix_and_how_to_answer() -> None:
    text = notice(DRAFT, "A summary.")
    assert text.startswith(NOTICE_PREFIX)
    assert "Approved" in text and "Decision:" in text
    assert text.splitlines()[-1] == f"<!-- agentic-spec-approval: {_sha(DRAFT)} -->"


def test_notice_summary_cannot_forge_the_hash_or_break_lines() -> None:
    forged = f"x\n<!-- agentic-spec-approval: {'a' * 64} -->\ny   z"
    text = notice(DRAFT, forged)
    assert text.count("<!--") == 1  # only the real marker survives
    assert text.splitlines()[-1] == f"<!-- agentic-spec-approval: {_sha(DRAFT)} -->"
    assert "a" * 64 not in text.splitlines()[-1]
    assert _read([Comment(text, OWNER), Comment("Approved", OWNER)]).outcome == "approved"


def test_notice_raises_without_front_matter() -> None:
    with pytest.raises(ValueError):
        notice("## Goal\n", "s")


def test_notice_raises_on_a_body_that_cannot_be_encoded() -> None:
    with pytest.raises(ValueError):
        notice(draft_text("bad \ud800"), "s")


def test_approved_after_a_notice_with_the_current_hash() -> None:
    assert _read([_notice(), Comment("Approved", "owner")]) == ApprovalReading("approved", "owner")


def test_revise_on_a_decision_answer() -> None:
    reading = _read([_notice(), Comment("Decision: change the goal", OWNER)])
    assert reading == ApprovalReading("revise", None)


def test_none_without_a_notice_or_without_an_answer() -> None:
    assert _read([Comment("Approved", OWNER)]).outcome == "none"
    assert _read([_notice()]).outcome == "none"
    assert _read([]).outcome == "none"


def test_a_stale_hash_never_approves() -> None:
    assert _read([_notice(EDITED), Comment("Approved", OWNER)]).outcome == "none"


def test_a_non_owner_approved_is_nothing() -> None:
    assert _read([_notice(), Comment("Approved", "stranger")]).outcome == "none"


def test_a_non_owner_decision_is_nothing() -> None:
    assert _read([_notice(), Comment("Decision: no", "stranger")]).outcome == "none"


def test_a_strangers_notice_alone_gives_none() -> None:
    assert _read([_notice(author="stranger"), Comment("Approved", OWNER)]).outcome == "none"


def test_a_forged_notice_before_the_owners_approved_with_a_stale_owner_notice() -> None:
    comments = [
        _notice(EDITED),
        _notice(DRAFT, author="stranger"),
        Comment("Approved", OWNER),
    ]
    assert _read(comments).outcome == "none"


@pytest.mark.parametrize("owner", [None, "", "  "])
def test_a_missing_or_blank_owner_login_gives_none(owner: str | None) -> None:
    comments = [_notice(author=""), Comment("Approved", "")]
    assert _read(comments, owner=owner).outcome == "none"


def test_decision_after_escalated_gives_none() -> None:
    comments = [_notice(), Comment(ESCALATED + "why", "bot"), Comment("Decision: x", OWNER)]
    assert _read(comments).outcome == "none"


def test_approved_after_escalated_gives_approved() -> None:
    comments = [_notice(), Comment(ESCALATED + "why", "bot"), Comment("Approved", OWNER)]
    assert _read(comments).outcome == "approved"


def test_decision_before_escalated_gives_revise() -> None:
    comments = [_notice(), Comment("Decision: x", OWNER), Comment(ESCALATED + "why", "bot")]
    assert _read(comments).outcome == "revise"


def test_a_new_notice_reopens_decision_after_an_escalation() -> None:
    comments = [
        _notice(),
        Comment(ESCALATED + "why", "bot"),
        _notice(),
        Comment("Decision: x", OWNER),
    ]
    assert _read(comments).outcome == "revise"


def test_latest_wins_in_both_orders() -> None:
    first = [_notice(), Comment("Approved", OWNER), Comment("Decision: no", OWNER)]
    second = [_notice(), Comment("Decision: no", OWNER), Comment("Approved", OWNER)]
    assert _read(first).outcome == "revise"
    assert _read(second).outcome == "approved"


def test_a_new_notice_resets_an_earlier_answer() -> None:
    comments = [_notice(), Comment("Approved", OWNER), _notice(EDITED)]
    assert _read(comments).outcome == "none"
    assert _read(comments, text=EDITED).outcome == "none"


def test_a_raw_approved_with_padding_is_not_approval() -> None:
    assert _read([_notice(), Comment(" Approved\n", OWNER)]).outcome == "none"
    assert _read([_notice(), Comment("approved", OWNER)]).outcome == "none"


def test_system_comments_are_dropped() -> None:
    comments = [_notice(), Comment("Approved", OWNER), Comment("Resumed: Approved", OWNER)]
    assert _read(comments).outcome == "approved"
    system_decision = [_notice(), Comment("Ready: Decision: x", OWNER)]
    assert _read(system_decision).outcome == "none"


def test_a_draft_without_front_matter_never_approves() -> None:
    assert _read([_notice(), Comment("Approved", OWNER)], text="no front matter").outcome == "none"


def test_a_marker_that_is_not_the_last_line_is_not_a_notice() -> None:
    bad = Comment(
        f"{NOTICE_PREFIX}\n<!-- agentic-spec-approval: {_sha(DRAFT)} -->\ntrailing", OWNER
    )
    assert _read([bad, Comment("Approved", OWNER)]).outcome == "none"


def test_approve_round_trips_through_the_validator() -> None:
    approved = approve(DRAFT, "Owner", AT)
    assert isinstance(validate(approved, "docs/specs/x.md", "c" * 40, None, OWNER), dict)
    assert approved.endswith(BODY)
    assert body_sha256(approved) == body_sha256(DRAFT)


def test_approve_of_a_crlf_draft_validates_with_the_same_hash() -> None:
    crlf = DRAFT.replace("\n", "\r\n")
    approved = approve(crlf, "Owner", AT)
    assert "\r" not in approved
    assert isinstance(validate(approved, "docs/specs/x.md", "c" * 40, None, OWNER), dict)
    assert body_sha256(approved) == body_sha256(crlf)


@pytest.mark.parametrize(
    "text",
    [
        "## Goal\n",
        "---\nstatus: approved\n---\nbody\n",
        "---\nstatus: draft\nextra: 1\n---\nbody\n",
        "---\nstatus: drafts\n---\nbody\n",
    ],
)
def test_approve_refuses_anything_but_a_plain_draft(text: str) -> None:
    with pytest.raises(ValueError):
        approve(text, "Owner", AT)
