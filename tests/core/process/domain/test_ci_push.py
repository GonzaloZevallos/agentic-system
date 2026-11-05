"""The push policy of the CI path (GH-487): what git said decides how often a push is tried,
and no credential or line break of it reaches a document."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci_push
from agentic.core.process.domain.ci_push import (
    GIT_SAID_LIMIT,
    PUSH_ATTEMPTS,
    PUSH_UNKNOWN_ATTEMPTS,
    attempts_for,
    classify,
    scrub,
)

PERMANENT = [
    "remote: Permission to o/r.git denied to workflow",
    "! [remote rejected] HEAD -> agentic-ci/GH-1 (refusing to allow an OAuth App to create "
    "or update workflow `.github/workflows/ci.yml` without `workflow` scope)",
    "remote: error: GH006: Protected branch update failed for refs/heads/main.",
    "! [remote rejected] HEAD -> x (hook declined)",
    "remote: error: pre-receive hook declined",
    "! [rejected] HEAD -> x (non-fast-forward)",
    "fatal: Authentication failed for 'https://github.com/o/r.git/'",
    "fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 403",
    "fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 404",
    "git@github.com: Permission denied (publickey).",
]

TRANSIENT = [
    "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com",
    "fatal: unable to access 'x': Connection timed out after 300000 milliseconds",
    "fatal: unable to access 'x': Connection reset by peer",
    "fatal: unable to access 'x': Failed to connect to github.com port 443",
    "ssh: connect to host github.com port 22: Connection refused",
    "fatal: unable to access 'x': Operation timed out",
    "fatal: the remote end hung up unexpectedly",
    "error: RPC failed; curl 56 GnuTLS recv error (-54)",
    "fatal: early EOF",
    "fatal: unable to access 'x': The requested URL returned error: 502",
    "fatal: unable to access 'x': SSL_ERROR_SYSCALL in connection to github.com:443",
]


@pytest.mark.parametrize("stderr", PERMANENT)
def test_a_refusal_git_states_as_permanent_is_permanent(stderr):
    assert classify(stderr) == "permanent"


@pytest.mark.parametrize("stderr", TRANSIENT)
def test_a_network_failure_is_transient(stderr):
    assert classify(stderr) == "transient"


def test_a_text_in_both_lists_is_permanent():
    both = "fatal: the remote end hung up unexpectedly\nremote: Permission to o/r denied"
    assert classify(both) == "permanent"


@pytest.mark.parametrize("stderr", ["", "   ", "push refused", "something odd"])
def test_a_text_no_list_knows_is_unknown(stderr):
    assert classify(stderr) == "unknown"


def test_the_attempts_follow_the_kind():
    assert attempts_for("transient") == PUSH_ATTEMPTS
    assert attempts_for("unknown") == PUSH_UNKNOWN_ATTEMPTS
    assert attempts_for("permanent") == 1


def test_a_backoff_is_declared_for_every_wait_between_attempts():
    assert len(ci_push.PUSH_BACKOFF_S) == PUSH_ATTEMPTS - 1


def test_scrub_removes_the_user_and_the_token_of_a_url():
    said = "fatal: unable to access 'https://x-access-token:ghs_abc@github.com/o/r': nope"
    scrubbed = scrub(said)
    assert "ghs_abc" not in scrubbed
    assert "x-access-token" not in scrubbed
    assert "github.com/o/r" in scrubbed


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "gho_abcDEF123456",
        "ghu_abcDEF123456",
        "ghs_abcDEF123456",
        "ghr_abcDEF123456",
        "github_pat_11ABCDEF0_abcdefXYZ",
    ],
)
def test_scrub_removes_a_github_token_wherever_it_stands(secret):
    assert secret not in scrub(f"remote: bad token {secret} given")


@pytest.mark.parametrize(
    ("header", "secret"),
    [
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("Authorization: bearer abcdef123", "abcdef123"),
        ("http.extraheader=AUTHORIZATION: basic c2VjcmV0", "c2VjcmV0"),
    ],
)
def test_scrub_removes_what_an_authorization_header_carries(header, secret):
    assert secret not in scrub(f"fatal: sent {header} and failed")


def test_scrub_leaves_braces_alone_and_never_formats():
    said = "remote: {0} {name} } { rejected %s"
    assert scrub(said) == said


def test_scrub_puts_a_heading_on_one_line():
    scrubbed = scrub("remote: no\n## Full checks (v99)\nmore\r\ntext")
    assert "\n" not in scrubbed
    assert "\r" not in scrubbed
    assert "## Full checks (v99)" in scrubbed


def test_scrub_removes_backticks_and_collapses_spaces():
    assert scrub("a `workflow`   scope\t\tmissing") == "a workflow scope missing"


def test_scrub_trims_with_an_ellipsis_and_keeps_the_case():
    scrubbed = scrub("Permission " + "x" * 1000, limit=20)
    assert len(scrubbed) == 20
    assert scrubbed.endswith("…")
    assert scrubbed.startswith("Permission ")


def test_scrub_trims_to_the_declared_limit_by_default():
    assert len(scrub("y" * (GIT_SAID_LIMIT * 3))) == GIT_SAID_LIMIT


def test_scrub_of_a_short_text_changes_nothing_else():
    assert scrub("  remote: Permission to o/r.git denied to workflow.\n") == (
        "remote: Permission to o/r.git denied to workflow."
    )


def test_the_failure_section_quotes_git_through_scrub_and_names_the_attempts():
    said = "remote: Permission to o/r denied to workflow `x` {y} https://u:tok@github.com/o/r"
    text = ci_push.push_failure_markdown("publish_failed", "agentic-ci/GH-1", 1, "permanent", said)
    assert "agentic-ci/GH-1" in text and "publish_failed" in text
    assert "1 attempt(s)" in text and "permanent" in text
    quoted = [line for line in text.splitlines() if line.startswith("> ")]
    assert quoted == [f"> {scrub(said)}"]
    assert "tok" not in text and "`x`" not in text


def test_the_failure_section_scrubs_what_it_is_given_even_when_unscrubbed():
    text = ci_push.push_failure_markdown(
        "publish_failed", "agentic-ci/GH-1", 2, "unknown", "line one\nline two ghp_abc123"
    )
    assert "> line one line two ***" in text
