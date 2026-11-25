import re
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agentic.core.process.domain import escalation_prompts, notify_message
from agentic.core.process.domain.notify import EventKey, PartFact, ReleaseEvent
from agentic.core.process.domain.notify_message import (
    BODY_MAX_BYTES,
    AcceptedDeparture,
    DigestRow,
    DigestView,
    Message,
    WindowedSection,
    clean,
    digest_message,
    release_message,
    repo_url,
    waiting_message,
)
from agentic.core.process.domain.task import DepartureRecord

REPO = "https://github.com/acme/shop"
AT = datetime(2026, 11, 20, 11, tzinfo=UTC)


def _row(number: int, answered: bool = False) -> DepartureRecord:
    answer: Any = {"verdict": "accept", "by": "owner", "at": "x"} if answered else None
    return DepartureRecord(
        number=number,
        key=f"k{number}",
        drawing="d",
        element=f"el{number}",
        shows="<b>shows</b>",
        would_ship="ships",
        recommendation="accept",
        reason="why",
        raised_by="dev",
        raised_at="x",
        answer=answer,
        fix_passes=0,
    )


def _waiting(**more: Any) -> Message:
    args: dict[str, Any] = {
        "product": "shop",
        "issue": 7,
        "title": "Title",
        "cls": "failure",
        "cause": None,
        "reason": "because",
        "open_rows": [],
        "usd": None,
        "repo": REPO,
    }
    return waiting_message(**{**args, **more})


def _event(*leaves: PartFact, issue: int = 1) -> ReleaseEvent:
    return ReleaseEvent(EventKey("release", "shop", issue, AT), "send", tuple(leaves))


def _html_paragraphs(message: Message) -> int:
    return message.html.count("<p>")


def _text_lines(message: Message) -> int:
    return len([line for line in message.text.split("\n") if line])


def test_clean_strips_line_breaks_controls_and_direction_marks() -> None:
    assert clean("a\r\nb\x00c d e‮f‎g⁦h") == "abcdefgh"


def test_clean_keeps_zwj_emoji_and_does_not_normalize() -> None:
    family = "👨‍👩‍👧"
    assert clean(family) == family
    assert clean("é") == "é"


def test_clean_counts_the_ellipsis_inside_the_limit() -> None:
    assert clean("x" * 500) == "x" * 500
    cut = clean("x" * 501)
    assert cut == "x" * 499 + "…" and len(cut) == 500
    emoji = clean("😀" * 600)
    assert len(emoji) == 500 and emoji.endswith("😀…")


def test_message_rejects_a_bad_subject() -> None:
    assert Message("ok", "t", "h").subject == "ok"
    for bad in ("a\nb", "a\rb", "a\x07b", "a b", "x" * 201):
        with pytest.raises(ValueError):
            Message(bad, "t", "h")
    assert Message("x" * 200, "t", "h")


@pytest.mark.parametrize(
    "value",
    [
        REPO + "\n",
        "https://github.com/../shop",
        "https://github.com/acme/..",
        "https://github.com/./x",
        "https://github.com/acmé/shop",
        "http://github.com/acme/shop",
        REPO + "/",
        "https://github.com/a",
    ],
)
def test_repo_url_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        repo_url(value)


def test_repo_url_accepts_a_repository() -> None:
    assert repo_url(REPO) == REPO
    assert repo_url("https://github.com/a_b/c.d-e") == "https://github.com/a_b/c.d-e"


def test_a_hostile_title_never_reaches_the_subject_and_is_escaped_in_html() -> None:
    title = "Fix\r\nBcc: evil@example.com <script>alert(1)</script> & co‮"
    message = _waiting(title=title)
    assert "\r" not in message.subject and "\n" not in message.subject
    assert "‮" not in message.text + message.html
    assert "<script>" not in message.html and "&lt;script&gt;" in message.html
    assert "&amp; co" in message.html
    assert "<script>" in message.text


def test_a_long_title_is_cut_in_every_field_and_the_subject_stays_in_bounds() -> None:
    message = _waiting(title="t" * 5000, reason="r" * 5000)
    assert len(message.subject) <= 200
    assert "t" * 500 not in message.text.replace("t" * 499 + "…", "")
    assert "t" * 499 + "…" in message.text and "r" * 499 + "…" in message.text


@pytest.mark.parametrize("key", sorted(escalation_prompts.ESCALATION_PROMPTS, key=str))
@pytest.mark.parametrize("usd", [None, 12.5])
def test_every_prompt_renders_with_no_unfilled_placeholder(
    key: tuple[Any, Any], usd: float | None
) -> None:
    cls, cause = key
    rows = [_row(1), _row(2), _row(3, answered=True)]
    message = _waiting(cls=cls, cause=cause, usd=usd, open_rows=rows)
    for part in (message.subject, message.text, message.html):
        assert not re.search(r"[{}]|<usd>|<n>|&lt;usd&gt;|&lt;n&gt;", part)
    assert f"{REPO}/issues/7" in message.text
    question = escalation_prompts.prompt_for(cls, cause)[0]
    if "{" not in question and "<n>" not in question:
        assert question in message.text


def test_commands_are_filled_from_the_issue() -> None:
    message = _waiting(cls="failure")
    assert "agentic resume --issue 7" in message.text
    assert "gh issue close 7 --reason 'not planned'" in message.text


def test_estimate_and_cap_with_and_without_a_value() -> None:
    estimate = _waiting(cls="estimate", usd=12.5)
    assert "agentic resume --issue 7 --estimate 12.5" in estimate.text
    cap = _waiting(cls="cap", usd=40.0)
    assert "agentic resume --issue 7 --task-budget 40.0" in cap.text
    for cls, flag in (("estimate", "--estimate"), ("cap", "--task-budget")):
        bare = _waiting(cls=cls, usd=None)
        assert flag not in bare.text
        assert "Set the amount from the escalation comment on the issue." in bare.text


def test_estimate_without_a_value_keeps_the_options_with_no_amount() -> None:
    assert "agentic resume --issue 7 (" not in _waiting(cls="estimate", usd=None).text
    assert "- agentic resume --issue 7\n" in _waiting(cls="estimate", usd=None).text


def test_a_bad_amount_is_refused() -> None:
    for usd in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            _waiting(cls="estimate", usd=usd)


def test_api_break_missing_is_a_fixed_line() -> None:
    message = _waiting(cls="questions", cause="api_break_missing")
    assert "See the escalation comment on the issue." in message.text
    assert "{step}" not in message.text


def test_drawing_cause_carries_the_per_number_commands_and_departures() -> None:
    message = _waiting(
        cls="questions", cause="drawing_departure", open_rows=[_row(2), _row(1), _row(3, True)]
    )
    assert "agentic resume --issue 7 --drawing 1=accept" in message.text
    assert message.text.index("--drawing 1=reject") < message.text.index("--drawing 2=accept")
    assert "--drawing 3=" not in message.text
    assert "&lt;b&gt;shows&lt;/b&gt;" in message.html and "<b>shows" not in message.html


def test_waiting_order_is_asked_commands_reason() -> None:
    text = _waiting().text
    assert text.index("Question:") < text.index("Recommended:") < text.index("Reason: because")


def test_release_groups_leaves_under_each_intermediate_parent() -> None:
    leaves = (
        PartFact(issue=12, merged=True, pr=112, parent_of=11),
        PartFact(issue=13, merged=True, pr=None, parent_of=11),
        PartFact(issue=21, merged=True, pr=121, parent_of=20),
        PartFact(issue=10, merged=True, pr=110, parent_of=1),
    )
    titles = {10: "Ten", 12: "Twelve <b>", 13: "Thirteen\r\nX", 21: "Twenty-one", 1: "Root"}
    message = release_message(
        product="shop",
        event=_event(*leaves),
        titles=titles,
        departures=[AcceptedDeparture(issue=12, number=2, text="Button & <i>icon</i>")],
        repo=REPO,
    )
    text = message.text
    assert text.index("Parts of #1 ") < text.index("Parts of #11 ") < text.index("Parts of #20 ")
    block = text[text.index("Parts of #11 ") : text.index("Parts of #20 ")]
    assert block.index("#12") < block.index("#13")
    assert f"{REPO}/pull/112" in text and f"{REPO}/pull/121" in text
    assert "no pull request" in text and "ThirteenX" in text
    assert "Accepted drawing departures:" in text and "departure 2: Button & <i>icon</i>" in text
    assert f'<a href="{REPO}/pull/112">#112</a>' in message.html
    assert "Twelve &lt;b&gt;" in message.html and "Button &amp; &lt;i&gt;icon" in message.html
    assert message.subject == "[shop] Released #1: Root"


def test_release_of_a_single_task_lists_the_issue_itself() -> None:
    message = release_message(
        product="shop", event=_event(issue=5), titles={5: "Solo"}, departures=[], repo=REPO
    )
    assert f"#5 ({REPO}/issues/5) Solo" in message.text
    assert "Parts of" not in message.text


def test_release_requires_a_send_event_and_valid_facts() -> None:
    covered = ReleaseEvent(EventKey("release", "shop", 1, AT), "covered", ())
    with pytest.raises(ValueError):
        release_message(product="shop", event=covered, titles={}, departures=[], repo=REPO)
    with pytest.raises(ValueError):
        release_message(product="shop", event=_event(), titles={}, departures=[], repo="http://x")
    with pytest.raises(ValueError):
        AcceptedDeparture(issue=0, number=1, text="t")
    flag: Any = True
    with pytest.raises(ValueError):
        AcceptedDeparture(issue=1, number=flag, text="t")


def _release_with(count: int, title: str) -> Message:
    leaves = tuple(PartFact(issue=100 + i, merged=True, pr=None, parent_of=1) for i in range(count))
    titles = {100 + i: title for i in range(count)}
    return release_message(
        product="shop", event=_event(*leaves), titles=titles, departures=[], repo=REPO
    )


def test_a_cut_body_keeps_the_header_and_shows_the_same_sections_in_both_parts() -> None:
    # Escaping makes the HTML larger than the text, so only the HTML is over the cap first.
    leaves = tuple(
        PartFact(issue=100 + i, merged=True, pr=None, parent_of=50 + i) for i in range(900)
    )
    titles = {100 + i: "<&>" * 150 for i in range(900)}
    message = release_message(
        product="shop", event=_event(*leaves), titles=titles, departures=[], repo=REPO
    )
    assert len(message.text.encode()) < BODY_MAX_BYTES
    assert len(message.html.encode()) <= BODY_MAX_BYTES
    assert "Released: #1" in message.text and "(truncated)" in message.text
    assert "(truncated)" in message.html
    assert message.text.count("Parts of") == message.html.count("Parts of")
    assert message.text.count("Parts of") < 900
    assert _text_lines(message) == _html_paragraphs(message)
    assert message.html.endswith("</p></body></html>")


def test_a_cut_waiting_body_keeps_the_commands() -> None:
    rows = [_row(n) for n in range(1, 400)]
    for row in rows:
        row["shows"] = "s" * 400
    message = _waiting(cls="questions", cause="drawing_cap", open_rows=rows, reason="r" * 400)
    assert "Reason: " in message.text
    assert "--drawing 399=reject" in message.text and "--drawing 399=reject" in message.html
    assert "gh issue close 7" in message.text
    assert max(len(message.text.encode()), len(message.html.encode())) <= BODY_MAX_BYTES
    assert "(truncated)" in message.text and "(truncated)" in message.html


def test_a_body_that_fits_is_not_cut() -> None:
    message = _release_with(40, "ok")
    assert "(truncated)" not in message.text + message.html


def test_a_body_exactly_at_the_cap_is_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _release_with(3, "a")
    size = max(len(base.text.encode()), len(base.html.encode()))
    monkeypatch.setattr(notify_message, "BODY_MAX_BYTES", size)
    assert _release_with(3, "a") == base
    monkeypatch.setattr(notify_message, "BODY_MAX_BYTES", size - 1)
    shorter = _release_with(3, "a")
    assert "(truncated)" in shorter.text and "(truncated)" in shorter.html


NO_ROWS = WindowedSection((), cut=False, single_group=False)


def _digest(**more: Any) -> DigestView:
    args: dict[str, Any] = {
        "product": "shop",
        "local_date": date(2026, 11, 24),
        "limit": 3,
        "waiting": (),
        "paused": (),
        "running": (),
        "merged": NO_ROWS,
        "closed": NO_ROWS,
    }
    return DigestView(**{**args, **more})


def _rows(start: int, count: int, title: str = "t") -> tuple[DigestRow, ...]:
    return tuple(DigestRow(start + i, title) for i in range(count))


def test_each_digest_section_renders_in_order_with_its_links() -> None:
    view = _digest(
        waiting=_rows(1, 1, "w"),
        paused=(DigestRow(2, "p", "no budget"),),
        running=_rows(3, 1, "r"),
        merged=WindowedSection(_rows(40, 1, "m"), cut=False, single_group=False),
        closed=WindowedSection(_rows(5, 1, "c"), cut=False, single_group=False),
    )
    message = digest_message(view, REPO)
    assert message.subject == "[shop] Daily digest 2026-11-24"
    order = [
        message.text.index(x)
        for x in ("Waiting for you (1)", "Paused (1)", "Running (1)", "Merged", "Closed")
    ]
    assert order == sorted(order)
    assert "#2 (" + REPO + "/issues/2) p - paused: no budget" in message.text
    assert f'<a href="{REPO}/pull/40">#40</a>' in message.html
    assert f'<a href="{REPO}/issues/5">#5</a>' in message.html
    assert "Merged pull requests (1)" in message.text and "Closed issues (1)" in message.text


def test_empty_sections_are_omitted_and_an_empty_digest_says_so() -> None:
    message = digest_message(_digest(running=_rows(3, 1)), REPO)
    assert "Running (1)" in message.text
    assert "Waiting" not in message.text and "Merged" not in message.text
    assert "Nothing to report." in digest_message(_digest(), REPO).text
    assert "Nothing to report." not in message.text


def test_a_cut_shows_the_limit_with_a_plus_and_never_the_literal() -> None:
    cut = WindowedSection(_rows(10, 3), cut=True, single_group=False)
    message = digest_message(_digest(limit=3, merged=cut), REPO)
    assert "Merged pull requests (3+)" in message.text and "(3+)" in message.html
    assert "50+" not in message.text + message.html
    assert (
        "+"
        not in digest_message(
            _digest(merged=WindowedSection(_rows(10, 3), False, False)), REPO
        ).text
    )


def test_the_single_group_line_shows_only_with_single_group_and_in_its_section() -> None:
    line = "More than 3 items share one instant; some are not listed."
    one = WindowedSection(_rows(10, 2), cut=True, single_group=True)
    plain = WindowedSection(_rows(20, 2), cut=True, single_group=False)
    message = digest_message(_digest(merged=plain, closed=one), REPO)
    assert message.text.count(line) == 1 and message.html.count(line) == 1
    assert message.text.index(line) > message.text.index("Closed issues (3+)")
    assert line not in digest_message(_digest(merged=plain), REPO).text
    empty = WindowedSection((), cut=True, single_group=True)
    assert line in digest_message(_digest(closed=empty), REPO).text


def test_single_group_without_a_cut_is_rejected() -> None:
    with pytest.raises(ValueError, match="single_group"):
        WindowedSection(_rows(1, 1), cut=False, single_group=True)


@pytest.mark.parametrize(
    "more",
    [
        {"limit": 0},
        {"limit": True},
        {"limit": "3"},
        {"product": "Shop Now"},
        {"product": "a\nb"},
        {"local_date": datetime(2026, 11, 24, tzinfo=UTC)},
    ],
)
def test_a_bad_digest_view_is_rejected(more: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _digest(**more)


@pytest.mark.parametrize("number", [0, -1, True, "1"])
def test_a_bad_digest_row_number_is_rejected(number: Any) -> None:
    with pytest.raises(ValueError):
        DigestRow(number, "t")


def test_a_digest_refuses_a_bad_repo() -> None:
    with pytest.raises(ValueError):
        digest_message(_digest(), "https://evil.example/a/b")


def test_a_hostile_digest_title_and_reason_are_cleaned_and_escaped() -> None:
    row = DigestRow(4, "<script>x</script>\r\nBcc: a@b", "<i>why</i>\x00")
    message = digest_message(_digest(paused=(row,)), REPO)
    assert (
        "<script>" not in message.html and "&lt;script&gt;x&lt;/script&gt;Bcc: a@b" in message.html
    )
    assert "&lt;i&gt;why&lt;/i&gt;" in message.html
    assert "\r" not in message.text and "\x00" not in message.text


def test_a_large_digest_keeps_the_snapshot_and_shows_the_same_sections_in_both_parts() -> None:
    merged = WindowedSection(_rows(1000, 250, "<&>" * 150), cut=True, single_group=False)
    closed = WindowedSection(_rows(2000, 250, "<&>" * 150), cut=True, single_group=False)
    message = digest_message(
        _digest(waiting=_rows(1, 2, "w"), running=_rows(3, 2, "r"), merged=merged, closed=closed),
        REPO,
    )
    assert max(len(message.text.encode()), len(message.html.encode())) <= BODY_MAX_BYTES
    assert "Waiting for you (2)" in message.text and "Waiting for you (2)" in message.html
    assert "Running (2)" in message.text and "Running (2)" in message.html
    assert "(truncated)" in message.text and "(truncated)" in message.html
    for heading in ("Merged pull requests", "Closed issues"):
        assert (heading in message.text) == (heading in message.html)
    assert _text_lines(message) == _html_paragraphs(message)
