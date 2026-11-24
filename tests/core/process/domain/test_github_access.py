"""`split_response`, `wait_from`, `HttpResponse.header` and `GithubAccess` (GH-1090), and the
shared `gh_response` fake. The fixtures are what `gh api -i` printed (captured) or shaped after
it (synthetic); their sidecars say which."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from agentic.core.process.domain.github_access import (
    GithubAccess,
    HttpResponse,
    NotAnHttpResponse,
    Turn,
    WriteGate,
    is_write,
    last_slot_of,
    split_response,
    wait_from,
    write_slot,
)
from tests.core.process.adapters.github.gh_fake import StubGate, gh_response

FIXTURES = Path(__file__).parent / "fixtures" / "github_access"
SIDECAR_KEYS = {"command", "gh_version", "origin", "derived_from"}
ORIGINS = {"captured", "synthetic"}


def _response(*headers: tuple[str, str]) -> HttpResponse:
    return HttpResponse(200, headers, "")


# --- split_response ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "status", "headers", "body"),
    [
        pytest.param("HTTP/2.0 200 OK\r\nA: 1\r\n\r\nbody", 200, (("a", "1"),), "body", id="crlf"),
        pytest.param("HTTP/2.0 200 OK\nA: 1\n\nbody", 200, (("a", "1"),), "body", id="lf"),
        pytest.param(
            "HTTP/2.0 200 OK\nA: 1\r\nB: 2\n\nbody",
            200,
            (("a", "1"), ("b", "2")),
            "body",
            id="mixed line ends as gh prints them",
        ),
        pytest.param("HTTP/1.1 201 Created\n\nx", 201, (), "x", id="http 1.1"),
        pytest.param("HTTP/2 202 Accepted\n\nx", 202, (), "x", id="http 2"),
        pytest.param("HTTP/2.0 203 Non-Authoritative\n\nx", 203, (), "x", id="http 2.0"),
        pytest.param("HTTP/2 204\n\n", 204, (), "", id="no reason phrase"),
        pytest.param("HTTP/2 204 \n\n", 204, (), "", id="empty reason phrase"),
        pytest.param(
            "HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nA: 1\r\n\r\nbody",
            200,
            (("a", "1"),),
            "body",
            id="100 Continue then 200: the last block counts",
        ),
        pytest.param(
            "HTTP/1.1 100 Continue\nX: 9\n\nHTTP/1.1 102 Processing\n\nHTTP/2 200\nA: 1\n\nb",
            200,
            (("a", "1"),),
            "b",
            id="two interim blocks",
        ),
        pytest.param(
            "HTTP/1.1 200 OK\n\nHTTP/1.1 500 Internal Server Error\nA: 1\n\nrest",
            200,
            (),
            "HTTP/1.1 500 Internal Server Error\nA: 1\n\nrest",
            id="a body line starting HTTP/ stays body of a 200",
        ),
        pytest.param(
            "HTTP/1.1 200 OK\n\nfirst\n\nthird\n\n",
            200,
            (),
            "first\n\nthird\n\n",
            id="blank lines of the body kept",
        ),
        pytest.param(
            'HTTP/2.0 304 Not Modified\r\nEtag: "x"\r\n\r\n',
            304,
            (("etag", '"x"'),),
            "",
            id="304 has an empty body",
        ),
        pytest.param(
            "HTTP/2.0 200 OK\nA: 1\nB: 2",
            200,
            (("a", "1"), ("b", "2")),
            "",
            id="no blank line after the headers: empty body",
        ),
        pytest.param("HTTP/2.0 200 OK", 200, (), "", id="status line only"),
        pytest.param(
            "HTTP/2.0 200 OK\nContent-Type:  text/x ;  v=1  \nX-GitHub-Request-Id:abc\n\n",
            200,
            (("content-type", "text/x ;  v=1"), ("x-github-request-id", "abc")),
            "",
            id="names lower-cased, values stripped",
        ),
        pytest.param(
            "HTTP/2.0 200 OK\nLink: <a>\nA: 1\nlink: <b>\nLINK: <c>\n\n",
            200,
            (("link", "<a>, <b>, <c>"), ("a", "1")),
            "",
            id="repeated header joined in order, first appearance keeps its place",
        ),
        pytest.param(
            "HTTP/2.0 200 OK\nLocation: http://x:80/y\n\n",
            200,
            (("location", "http://x:80/y"),),
            "",
            id="a colon in the value stays in it",
        ),
    ],
)
def test_split_response_reads(
    stdout: str, status: int, headers: tuple[tuple[str, str], ...], body: str
) -> None:
    assert split_response(stdout) == HttpResponse(status, headers, body)


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("", id="empty"),
        pytest.param("not http\n\n", id="does not start with HTTP/"),
        pytest.param("\nHTTP/2 200\n\n", id="blank first line"),
        pytest.param("HTTP/2.0 20 OK\n\n", id="two-digit status"),
        pytest.param("HTTP/2.0 2000 OK\n\n", id="four-digit status"),
        pytest.param("HTTP/2.0 abc OK\n\n", id="non-numeric status"),
        pytest.param("HTTP/2.0 ٢٠٠ OK\n\n", id="non-ASCII digits in the status"),
        pytest.param("HTTP/3 200 OK\n\n", id="unknown version"),
        pytest.param("HTTP/1.0 200 OK\n\n", id="HTTP/1.0 is not accepted"),
        pytest.param("HTTP/2.0 200 OK\nno colon here\n\nbody", id="header line without a colon"),
        pytest.param("HTTP/1.1 100 Continue\n\nHTTP/9 200\n\n", id="bad second status line"),
    ],
)
def test_split_response_refuses(stdout: str) -> None:
    with pytest.raises(NotAnHttpResponse):
        split_response(stdout)


def test_not_an_http_response_is_a_value_error() -> None:
    assert issubclass(NotAnHttpResponse, ValueError)


def test_header_is_case_insensitive_and_none_when_absent() -> None:
    response = split_response("HTTP/2.0 200 OK\nRetry-After: 7\n\n")
    assert response.header("retry-after") == "7"
    assert response.header("RETRY-AFTER") == "7"
    assert response.header("Retry-After") == "7"
    assert response.header("x-ratelimit-reset") is None


def test_response_and_access_are_frozen_values() -> None:
    # A frozen dataclass with `eq` is hashable; an unfrozen one is not.
    assert hash(_response(("a", "1"))) == hash(_response(("a", "1")))
    assert GithubAccess() == GithubAccess(gate=None, cache=None, budget=None)
    assert hash(GithubAccess(gate=StubGate())) == hash(GithubAccess(gate=StubGate()))


# --- wait_from --------------------------------------------------------------------------

NOW = 1000.0


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param((("retry-after", "30"),), 30.0, id="retry-after decides"),
        pytest.param((("retry-after", "0"),), 0.0, id="retry-after 0 gives 0"),
        pytest.param(
            (("retry-after", "30"), ("x-ratelimit-reset", "1500")),
            30.0,
            id="retry-after over the reset",
        ),
        pytest.param(
            (("retry-after", "0"), ("x-ratelimit-reset", "1500")),
            0.0,
            id="retry-after 0 over the reset",
        ),
        pytest.param((("x-ratelimit-reset", "1090"),), 90.0, id="future reset gives reset - now"),
        pytest.param((("x-ratelimit-reset", "1000"),), 7.0, id="reset equal to now: backoff"),
        pytest.param((("x-ratelimit-reset", "900"),), 7.0, id="reset in the past: backoff"),
        pytest.param((), 7.0, id="neither header: backoff"),
        pytest.param((("retry-after", "10"), ("retry-after", "20")), 7.0, id="comma-joined repeat"),
        pytest.param((("retry-after", "5, 7"),), 7.0, id="comma-joined value"),
        pytest.param(
            (("retry-after", "Wed, 21 Oct 2026 07:28:00 GMT"),), 7.0, id="retry-after HTTP-date"
        ),
        pytest.param((("retry-after", "-1"),), 7.0, id="negative retry-after"),
        pytest.param((("retry-after", "+5"),), 7.0, id="signed retry-after"),
        pytest.param((("retry-after", "5_0"),), 7.0, id="underscore retry-after"),
        pytest.param((("retry-after", "1.5"),), 7.0, id="fractional retry-after"),
        pytest.param((("retry-after", "٥"),), 7.0, id="non-ASCII digit retry-after"),
        pytest.param((("retry-after", ""),), 7.0, id="empty retry-after"),
        pytest.param((("retry-after", " "),), 7.0, id="blank retry-after"),
        pytest.param((("x-ratelimit-reset", "1090.5"),), 7.0, id="float epoch reset"),
        pytest.param((("x-ratelimit-reset", "-5"),), 7.0, id="negative reset"),
        pytest.param((("x-ratelimit-reset", "1100, 1200"),), 7.0, id="comma-joined reset"),
        pytest.param((("x-ratelimit-reset", "soon"),), 7.0, id="unreadable reset"),
        pytest.param(
            (("retry-after", "soon"), ("x-ratelimit-reset", "1090")),
            90.0,
            id="unreadable retry-after falls to the reset",
        ),
        pytest.param(
            (("retry-after", "-1"), ("x-ratelimit-reset", "900")),
            7.0,
            id="both unusable: backoff",
        ),
        pytest.param((("retry-after", "9" * 400),), 7.0, id="a number no float holds"),
        pytest.param((("retry-after", "86400"),), 86400.0, id="a long wait is never capped"),
    ],
)
def test_wait_from(headers: tuple[tuple[str, str], ...], expected: float) -> None:
    assert wait_from(_response(*headers), NOW, 7.0) == expected


def test_wait_from_reads_the_headers_of_a_parsed_response_not_its_body() -> None:
    response = split_response("HTTP/2.0 403 Forbidden\nX-Ratelimit-Reset: 1090\n\nretry-after: 5\n")
    assert wait_from(response, NOW, 7.0) == 90.0


# --- fixtures ---------------------------------------------------------------------------


FIXTURE_NOW = 1795439000.0


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.http").read_bytes().decode()


@pytest.mark.parametrize(
    ("name", "status", "wait", "body_start"),
    [
        pytest.param("ok_200", 200, 1009.0, '["2026-03-10"', id="200"),
        pytest.param("not_modified_304", 304, 1009.0, "", id="304 with no body"),
        pytest.param("not_found_404", 404, 1009.0, '{"message":"Not Found"', id="404 on stdout"),
        pytest.param(
            "rate_limited_403",
            403,
            1009.0,
            '{"message":"API rate limit',
            id="primary rate limit",
        ),
        pytest.param("bad_gateway_502", 502, 30.0, '{"message":"Server Error"', id="502"),
        pytest.param("repeated_header_200", 200, 1009.0, "[]", id="repeated header"),
    ],
)
def test_fixture_parses(name: str, status: int, wait: float, body_start: str) -> None:
    raw = _fixture(name)
    response = split_response(raw)
    assert response.status == status
    assert response.body.startswith(body_start)
    if status == 304:
        assert response.body == ""
    # Every captured response carries `x-ratelimit-reset: 1795440009`, 1009 s after FIXTURE_NOW.
    assert wait_from(response, FIXTURE_NOW, 7.0) == wait


def test_fixture_repeated_header_is_joined() -> None:
    link = split_response(_fixture("repeated_header_200")).header("link")
    assert link is not None
    assert link.count("rel=") == 2
    assert link.index('rel="next"') < link.index('rel="last"')


def test_fixtures_keep_their_crlf_bytes() -> None:
    paths = sorted(FIXTURES.glob("*.http"))
    assert len(paths) == 6
    for path in paths:
        assert b"\r\n" in path.read_bytes(), path.name


def sidecar_problems(directory: Path) -> list[str]:
    """What is wrong with the fixtures of `directory` and their `.meta` sidecars."""
    problems: list[str] = []
    fixtures = {path.stem for path in directory.glob("*.http")}
    sidecars = {path.stem for path in directory.glob("*.meta")}
    problems += [f"{name}: fixture without a sidecar" for name in sorted(fixtures - sidecars)]
    problems += [f"{name}: sidecar without a fixture" for name in sorted(sidecars - fixtures)]
    for name in sorted(fixtures & sidecars):
        try:
            meta = yaml.safe_load((directory / f"{name}.meta").read_text())
        except yaml.YAMLError:
            problems.append(f"{name}: sidecar does not parse")
            continue
        if not isinstance(meta, dict):
            problems.append(f"{name}: sidecar is not a mapping")
            continue
        if set(meta) != SIDECAR_KEYS:
            problems.append(f"{name}: keys are {sorted(meta)}, not {sorted(SIDECAR_KEYS)}")
            continue
        if meta["origin"] not in ORIGINS:
            problems.append(f"{name}: origin {meta['origin']!r} is not one of {sorted(ORIGINS)}")
            continue
        if meta["gh_version"] != "2.100.0":
            problems.append(f"{name}: gh_version {meta['gh_version']!r}")
        if not isinstance(meta["command"], str) or not meta["command"]:
            problems.append(f"{name}: command is empty")
        derived = meta["derived_from"]
        if (meta["origin"] == "synthetic") != (isinstance(derived, str) and derived != ""):
            problems.append(f"{name}: derived_from must be non-empty exactly when synthetic")
    return problems


def test_every_fixture_has_a_sidecar_that_parses() -> None:
    assert sidecar_problems(FIXTURES) == []
    synthetic = {
        path.stem
        for path in FIXTURES.glob("*.meta")
        if yaml.safe_load(path.read_text())["origin"] == "synthetic"
    }
    assert synthetic == {"rate_limited_403", "bad_gateway_502", "repeated_header_200"}


def _write(directory: Path, name: str, meta: str | None = "") -> None:
    (directory / f"{name}.http").write_bytes(b"HTTP/2.0 200 OK\r\n\r\n")
    if meta is not None:
        (directory / f"{name}.meta").write_text(meta)


GOOD = "command: gh api -i /x\ngh_version: 2.100.0\norigin: captured\nderived_from: ''\n"


@pytest.mark.parametrize(
    ("meta", "fragment"),
    [
        pytest.param(None, "fixture without a sidecar", id="no sidecar"),
        pytest.param("command: [\n", "does not parse", id="unparseable"),
        pytest.param("- a\n", "not a mapping", id="not a mapping"),
        pytest.param(GOOD.replace("derived_from: ''\n", ""), "keys are", id="missing key"),
        pytest.param(GOOD + "extra: 1\n", "keys are", id="extra key"),
        pytest.param(GOOD.replace("captured", "recorded"), "origin", id="origin outside the set"),
        pytest.param(GOOD.replace("''", "ok_200.http"), "derived_from", id="captured with source"),
        pytest.param(
            GOOD.replace("captured", "synthetic"), "derived_from", id="synthetic without source"
        ),
        pytest.param(GOOD.replace("2.100.0", "2.99.0"), "gh_version", id="other gh version"),
        pytest.param(GOOD.replace("gh api -i /x", "''"), "command", id="empty command"),
    ],
)
def test_sidecar_check_rejects(tmp_path: Path, meta: str | None, fragment: str) -> None:
    _write(tmp_path, "one", meta)
    problems = sidecar_problems(tmp_path)
    assert len(problems) == 1
    assert fragment in problems[0]


def test_sidecar_check_rejects_a_sidecar_without_a_fixture(tmp_path: Path) -> None:
    (tmp_path / "lonely.meta").write_text(GOOD)
    assert sidecar_problems(tmp_path) == ["lonely: sidecar without a fixture"]


def test_sidecar_check_accepts_a_good_pair(tmp_path: Path) -> None:
    _write(tmp_path, "one", GOOD)
    assert sidecar_problems(tmp_path) == []


# --- the shared fake --------------------------------------------------------------------


def test_gh_response_200_round_trips_through_split_response() -> None:
    run = gh_response(200, [("Retry-After", "3"), ("Link", "<a>"), ("Link", "<b>")], "a\n\nb")
    result = run(["gh", "api", "-i", "/x"], cwd=Path("."), capture_output=True, text=True)
    assert run.argv == [["gh", "api", "-i", "/x"]]
    assert run.kwargs == [{"cwd": Path("."), "capture_output": True, "text": True}]
    assert result.returncode == 0
    assert "\r\n" in result.stdout
    assert split_response(result.stdout) == HttpResponse(
        200, (("retry-after", "3"), ("link", "<a>, <b>")), "a\n\nb"
    )


def test_gh_response_404_raises_with_stdout_and_stderr() -> None:
    run = gh_response(404, [("X-A", "1")], '{"message":"Not Found"}', stderr="gh: Not Found")
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run(["gh", "api", "-i", "/x"], check=True)
    assert caught.value.returncode == 1
    assert caught.value.stderr == "gh: Not Found"
    assert split_response(caught.value.output).status == 404
    assert run.argv == [["gh", "api", "-i", "/x"]]


def test_gh_response_default_returncode_is_zero_for_2xx_and_304_and_explicit_wins() -> None:
    assert gh_response(304, [], "").returncode == 0
    assert gh_response(502, [], "").returncode == 1
    assert gh_response(200, [], "", returncode=3).returncode == 3
    assert gh_response(500, [], "", returncode=0).returncode == 0


# --- is_write ---------------------------------------------------------------------------

_GQL = ("api", "graphql", "-f", "query={ viewer { login } }")

# The literal arguments the adapters pass today. `tracker.py` reaches them through `_gh`,
# `_gh_read` and `_gh_retrying`; `commit_ci.py` goes through GhTracker.
_ADAPTER_CALLS = [
    # tracker.py
    pytest.param(("pr", "create", "--head", "b", "--title", "t", "--body-file", "p"), True),
    pytest.param(("issue", "create", "--title", "t", "--body-file", "p"), True),
    pytest.param(("issue", "comment", "7", "--body-file", "p"), True),
    pytest.param(("issue", "comment", "7", "--body", "text"), True),
    pytest.param(("issue", "edit", "7", "--body-file", "p"), True),
    pytest.param(("issue", "edit", "7", "--add-label", "x"), True),
    pytest.param(("issue", "close", "7", "--reason", "completed"), True),
    pytest.param(("pr", "close", "7"), True),
    pytest.param(("pr", "reopen", "7"), True),
    pytest.param(("pr", "edit", "7", "--body-file", "p"), True),
    pytest.param(("pr", "merge", "7", "--squash", "--match-head-commit", "abc"), True),
    pytest.param(("run", "rerun", "99", "--failed"), True),
    pytest.param(("workflow", "run", "ci.yml", "-f", "a=b"), True),
    pytest.param(("label", "create", "x"), True),
    # `label list` is a read, but every `label` subcommand counts as a write (conservative).
    pytest.param(("label", "list", "--limit", "1000", "--json", "name"), True),
    pytest.param(("run", "list", "--commit", "h", "--json", "url,conclusion"), False),
    pytest.param(("run", "list", "--workflow", "ci.yml", "--event", "workflow_dispatch"), False),
    pytest.param(("run", "view", "9", "--json", "jobs"), False),
    pytest.param(("pr", "view", "7", "--json", "changedFiles"), False),
    pytest.param(("pr", "list", "--state", "all", "--json", "number", "--limit", "500"), False),
    pytest.param(("issue", "view", "7", "--json", "comments"), False),
    pytest.param(("issue", "list", "--label", "agentic", "--state", "open"), False),
    pytest.param(("repo", "view", "--json", "mergeCommitAllowed"), False),
    pytest.param(
        ("api", "graphql", "-f", "query=query { x }", "-F", "owner={owner}", "-F", "number=7"),
        False,
        id="CI GraphQL read with -F fields",
    ),
    # inbox.py, format_fix_reads.py
    pytest.param(("issue", "view", "7", "--json", "title,body"), False),
    pytest.param(("issue", "list", "--state", "open", "--json", "url,body"), False),
    pytest.param(("repo", "view", "--json", "nameWithOwner"), False),
    # label_timeline.py
    pytest.param(("api", "graphql", "-f", "query=query($n: Int!) {}", "-F", "repo={repo}"), False),
    # board.py: no visible document, so it counts as unknown. Conservative: board's reads go
    # through `--input` too and are counted as writes until part 2 passes `write=` to the gate.
    pytest.param(("api", "graphql", "--input", "/tmp/board-1.json"), True),
    pytest.param(("issue", "list", "--state", "open", "--limit", "500", "--json", "id"), False),
    pytest.param(("issue", "view", "7", "--json", "id"), False),
    # ci_logs.py
    pytest.param(("run", "view", "9", "--job", "5", "--log-failed"), False),
    pytest.param(("run", "view", "9", "--job", "5", "--log"), False),
]


@pytest.mark.parametrize(("args", "expected"), _ADAPTER_CALLS)
def test_is_write_on_the_calls_the_adapters_make(args: tuple[str, ...], expected: bool) -> None:
    assert is_write(args) is expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(
            ("api", "graphql", "-f", "query=# c\n  # d\nmutation { x }"), True, id="comments"
        ),
        pytest.param(
            ("api", "graphql", "-f", "query=  \n# c\nquery { x }"), False, id="comment read"
        ),
        pytest.param(("api", "graphql", "-f", "query=# only a comment"), True, id="empty document"),
        pytest.param(("api", "graphql", "--field=query=mutation { x }"), True, id="joined field"),
        pytest.param(("api", "graphql", "-fquery=mutation { x }"), True, id="joined short"),
        pytest.param(("api", "graphql", "-fquery=query { x }"), False, id="joined short read"),
        pytest.param(("api", "graphql", "--raw-field", "query=query { x }"), False, id="raw"),
        pytest.param(("api", "graphql", "-f", "query=@q.graphql"), True, id="query from a file"),
        pytest.param(("api", "graphql", "-F", "query=@q.graphql"), True, id="query from a file F"),
        pytest.param(("api", "graphql", "--input", "-"), True, id="input stdin"),
        pytest.param(
            ("api", "graphql", "-f", "query=query { x }", "--input", "f"), True, id="input"
        ),
        pytest.param(("api", "graphql", "-F", "owner=o"), True, id="no query"),
        pytest.param(("api", "graphql"), True, id="bare graphql"),
        pytest.param(("api", "-X", "get", "/repos/o/r"), False, id="-X get"),
        pytest.param(("api", "--method", "GET", "/x"), False, id="--method GET"),
        pytest.param(("api", "-XPOST", "/x"), True, id="-XPOST"),
        pytest.param(("api", "--method=post", "/x"), True, id="--method=post"),
        pytest.param(("api", "-X", "DELETE", "/x"), True, id="delete"),
        pytest.param(("api", "-X", "GET", "-f", "a=b", "/x"), False, id="GET with fields"),
        pytest.param(("api", "-f", "a=b", "/x"), True, id="-f no method"),
        pytest.param(("api", "-F", "a=b", "/x"), True, id="-F no method"),
        pytest.param(("api", "--field", "a=b", "/x"), True, id="--field no method"),
        pytest.param(("api", "--raw-field=a=b", "/x"), True, id="--raw-field joined"),
        pytest.param(("api", "/x", "--input", "f"), True, id="--input no method"),
        pytest.param(("api", "rate_limit"), False, id="rate_limit"),
        pytest.param(("api", "/repos/o/r/issues", "--paginate", "-i"), False, id="bare GET"),
        pytest.param(("api", "-H", "Accept: x", "/x"), False, id="header value is not a path"),
        pytest.param(("frobnicate", "x"), True, id="unknown verb"),
        pytest.param(("pr", "frobnicate"), True, id="unknown pr form"),
        pytest.param(("pr",), True, id="bare group"),
        pytest.param(("auth", "status"), False, id="auth status"),
        pytest.param(("pr", "checks", "7"), False, id="pr checks"),
        pytest.param(("pr", "diff", "7"), False, id="pr diff"),
        pytest.param(("workflow", "list"), True, id="workflow list is not in the table"),
        pytest.param((), True, id="no arguments"),
    ],
)
def test_is_write_edge_cases(args: tuple[str, ...], expected: bool) -> None:
    assert is_write(args) is expected


# --- last_slot_of -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", 0.0, id="empty"),
        pytest.param("   \n", 0.0, id="blank"),
        pytest.param("1000.", 1000.0, id="trailing dot is a float"),
        pytest.param("12.5x", 0.0, id="torn"),
        pytest.param("garbage", 0.0, id="garbage"),
        pytest.param("nan", 0.0, id="nan"),
        pytest.param("inf", 0.0, id="inf"),
        pytest.param("-inf", 0.0, id="-inf"),
        pytest.param("-1", 0.0, id="negative"),
        pytest.param(" 1.5\n", 1.5, id="whitespace around"),
        pytest.param("1_0", 10.0, id="underscore parses as 10.0, an old slot"),
        pytest.param("1100.5", 1100.5, id="exact boundary"),
        pytest.param("1100.6", 0.0, id="beyond the horizon: the clock stepped back"),
        pytest.param("990.25", 990.25, id="valid"),
    ],
)
def test_last_slot_of(text: str, expected: float) -> None:
    assert last_slot_of(text, 1000.0, 100.5) == expected


# --- write_slot -------------------------------------------------------------------------


def test_write_slot_an_old_last_slot_gives_no_wait() -> None:
    assert write_slot(10.0, 1000.0, 1.0, 30.0) == 1000.0


def test_write_slot_back_to_back_calls_are_one_second_apart() -> None:
    first = write_slot(0.0, 1000.0, 1.0, 30.0)
    assert first == 1000.0
    assert first is not None
    assert write_slot(first, 1000.0, 1.0, 30.0) == 1001.0


def test_write_slot_a_turn_over_the_cap_is_refused() -> None:
    assert write_slot(1031.0, 1000.0, 1.0, 30.0) is None
    assert write_slot(1029.0, 1000.0, 1.0, 30.0) == 1030.0  # exactly the cap is granted


def test_write_slot_cap_zero_with_no_wait_is_granted() -> None:
    assert write_slot(0.0, 1000.0, 1.0, 0.0) == 1000.0
    assert write_slot(1000.0, 1000.0, 1.0, 0.0) is None


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param((float("nan"), 1000.0, 1.0, 30.0), id="nan last slot"),
        pytest.param((0.0, float("nan"), 1.0, 30.0), id="nan now"),
        pytest.param((0.0, float("inf"), 1.0, 30.0), id="inf now"),
        pytest.param((0.0, 1000.0, float("nan"), 30.0), id="nan spacing"),
        pytest.param((0.0, 1000.0, 1.0, float("inf")), id="inf cap"),
        pytest.param((float("-inf"), 1000.0, 1.0, 30.0), id="-inf last slot"),
        pytest.param((0.0, 1000.0, -1.0, 30.0), id="negative spacing"),
        pytest.param((0.0, 1000.0, 1.0, -1.0), id="negative cap"),
    ],
)
def test_write_slot_never_grants_on_an_unreadable_time(
    bad: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError):
        write_slot(*bad)


# --- Turn and WriteGate -----------------------------------------------------------------


def test_a_stub_gate_implements_write_gate_and_takes_a_turn() -> None:
    gate: WriteGate = StubGate()
    assert gate.cap_s == 30.0
    assert gate.take(5.0) == Turn("granted", 0.0)
    assert hash(Turn("refused", 1.0)) == hash(Turn("refused", 1.0))
    with pytest.raises(FrozenInstanceError):
        setattr(Turn("stopped", 0.0), "waited_s", 1.0)
