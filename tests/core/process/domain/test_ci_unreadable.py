"""GH-1151: `CiUnreadable.of`, the one way to attach what `gh` said to an unreadable CI, and the
verdict of a run cut at its job limit. Pure; the sanitiser's inputs it must scrub sit beside the
ones it must leave alone."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable

HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def run(name: str, conclusion: str | None, status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/runs/{name}")


def test_a_job_cancelled_at_its_limit_with_ci_ok_failed_is_red_on_the_first_read() -> None:
    """The GH-1133 shape: apostrophe declares `ci: [ci-ok]`; `e2e-shard (1)` was cancelled, `e2e`
    and `ci-ok` failed, every run completed. One readable report is a verdict at elapsed 0."""
    checks = (
        run("e2e-shard (1)", "CANCELLED"),
        run("e2e", "FAILURE"),
        run("ci-ok", "FAILURE"),
    )
    read = CiReport(HEAD, None, None, None, checks, runs_completed=True)
    got = ci.full_tier_verdict(read, HEAD, ("ci-ok",), 0.0, 1500.0, 120.0)
    assert got.state == "red"
    assert [check.name for check in got.failing] == ["ci-ok"]


def test_the_detail_is_empty_unless_of_filled_it_and_equality_ignores_it() -> None:
    assert CiUnreadable("gh_unreadable").detail == ""
    assert CiUnreadable.of("gh_unreadable", "boom") == CiUnreadable("gh_unreadable")
    assert CiUnreadable.of("gh_unreadable", "boom").cause == "gh_unreadable"


def test_the_detail_survives_dataclasses_replace() -> None:
    kept = replace(CiUnreadable.of("gh_unreadable", "boom"), cause="gh_unavailable")
    assert (kept.cause, kept.detail) == ("gh_unavailable", "boom")


def test_an_empty_or_blank_raw_text_is_no_detail() -> None:
    assert CiUnreadable.of("gh_unreadable", "").detail == ""
    assert CiUnreadable.of("gh_unreadable", " \n\t ").detail == ""


def test_markers_split_around_another_marker_do_not_reassemble() -> None:
    assert CiUnreadable.of("gh_unreadable", "<!<!---->--x").detail == "x"
    assert CiUnreadable.of("gh_unreadable", "gh<!---->p_" + "a" * 20).detail == "[redacted]"


def test_the_detail_is_cut_to_two_hundred_characters() -> None:
    assert CiUnreadable.of("gh_unreadable", "x" * 500).detail == "x" * 200


def test_control_characters_and_newlines_are_dropped() -> None:
    raw = "a\x00b\x1b[31mred\r\nline2\t\x7fend"
    assert CiUnreadable.of("gh_unreadable", raw).detail == "ab[31mredline2end"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("see `rm -rf`", "see rm -rf"),
        ("a <!-- hidden --> b", "a  hidden  b"),
        ("{{ secrets.X }}", "secrets.X }}"),
    ],
    ids=["backtick", "html-comment", "template-marker"],
)
def test_markup_that_would_act_in_a_comment_is_dropped(raw: str, expected: str) -> None:
    assert CiUnreadable.of("gh_unreadable", raw).detail == expected


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "A1" * 18,
        "gho_abcDEF123",
        "ghu_abcDEF123",
        "ghs_abcDEF123",
        "ghr_abcDEF123",
        "github_pat_11ABC_def456_GHI",
    ],
)
def test_a_github_token_is_replaced(token: str) -> None:
    got = CiUnreadable.of("gh_unreadable", f"bad credentials {token} for user").detail
    assert got == "bad credentials [redacted] for user"


def test_a_credential_in_a_url_is_replaced() -> None:
    got = CiUnreadable.of("gh_unreadable", "fetch https://bob:s3cret@github.com/o/r failed").detail
    assert "s3cret" not in got and "bob" not in got
    assert got == "fetch https://[redacted]@github.com/o/r failed"


def test_a_token_cut_by_the_length_bound_is_still_never_leaked() -> None:
    """The token starts before character 200 and ends after it: redaction runs first, so no
    prefix of it is left behind."""
    token = "ghp_" + "Z" * 60
    detail = CiUnreadable.of("gh_unreadable", "x" * 190 + token).detail
    assert "ghp_" not in detail and detail == "x" * 190 + "[redacted]"


def test_plain_text_is_left_alone() -> None:
    assert CiUnreadable.of("gh_unreadable", "HTTP 502: Bad Gateway").detail == (
        "HTTP 502: Bad Gateway"
    )
