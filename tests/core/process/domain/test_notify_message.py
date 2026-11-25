import re
from datetime import UTC, datetime
from typing import Any

import pytest

from agentic.core.process.domain import escalation_prompts, notify_message
from agentic.core.process.domain.notify import EventKey, PartFact, ReleaseEvent
from agentic.core.process.domain.notify_message import (
    BODY_MAX_BYTES,
    AcceptedDeparture,
    Message,
    clean,
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
