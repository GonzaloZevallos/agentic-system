"""`AccountKeys` and `read_rate_limit` (GH-1071): the account key the budget is filed under,
from a token that is never stored, logged or raised."""

import hashlib
import logging
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.budget_store import FilesystemBudgetStore
from agentic.core.process.adapters.github.account import AccountKeys, read_rate_limit
from agentic.core.process.domain.github_budget import UNKNOWN

T = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
SENTINEL = "ghp_SENTINEL0123456789abcdefghijklmnop"
_now = [T]
_calls: list[list[str]] = []
_answer: dict[str, object] = {}


def _fake_run(argv, **kwargs):
    _calls.append(list(argv))
    if "returncode" in _answer:
        raise subprocess.CalledProcessError(
            int(str(_answer["returncode"])), argv, output="", stderr=f"bad credentials {SENTINEL}"
        )
    return subprocess.CompletedProcess(argv, 0, stdout=f"{_answer['stdout']}\n", stderr="")


@pytest.fixture(autouse=True)
def _reset():
    _now[0] = T
    _calls.clear()
    _answer.clear()
    _answer["stdout"] = SENTINEL


def _keys(environ: dict[str, str]) -> AccountKeys:
    return AccountKeys(_fake_run, Path("."), environ, lambda: _now[0])


def _digest(token: str) -> str:
    return "github.com-" + hashlib.sha256(token.encode()).hexdigest()[:16]


def test_gh_token_wins_over_github_token_and_the_key_has_its_format():
    key = _keys({"GH_TOKEN": "aaa", "GITHUB_TOKEN": "bbb"}).key()
    assert key == _digest("aaa")
    assert re.fullmatch(r"github\.com-[0-9a-f]{16}", key)
    assert _keys({"GITHUB_TOKEN": "bbb"}).key() == _digest("bbb")
    assert _calls == []


def test_a_token_in_the_environment_is_read_on_every_call():
    environ = {"GH_TOKEN": "aaa"}
    keys = _keys(environ)
    assert keys.key() == _digest("aaa")
    environ["GH_TOKEN"] = "ccc"
    assert keys.key() == _digest("ccc")


def test_another_host_gives_none_and_never_runs_gh():
    assert _keys({"GH_HOST": "ghe.example.com", "GH_TOKEN": "aaa"}).key() is None
    assert _keys({"GH_HOST": "ghe.example.com"}).key() is None
    assert _calls == []
    assert _keys({"GH_HOST": "github.com", "GH_TOKEN": "aaa"}).key() == _digest("aaa")


def test_the_gh_token_comes_from_gh_for_github_com():
    assert _keys({}).key() == _digest(SENTINEL)
    assert _calls == [["gh", "auth", "token", "--hostname", "github.com"]]


@pytest.mark.parametrize("token", ["two words", "tab\there", " "])
def test_a_token_with_whitespace_is_none_from_the_environment_and_from_gh(token):
    assert _keys({"GH_TOKEN": token}).key() is None
    _answer["stdout"] = token
    assert _keys({}).key() is None


def test_an_empty_gh_token_is_none():
    _answer["stdout"] = ""
    assert _keys({}).key() is None


def test_a_failing_gh_gives_none():
    _answer["returncode"] = 1
    assert _keys({}).key() is None


def test_the_gh_key_is_remembered_for_the_ttl_only():
    keys = _keys({})
    first = keys.key()
    _now[0] = T + timedelta(seconds=59)
    assert keys.key() == first
    assert len(_calls) == 1
    _now[0] = T + timedelta(seconds=61)
    assert keys.key() == first
    assert len(_calls) == 2


def test_a_failed_gh_read_is_remembered_too():
    _answer["returncode"] = 1
    keys = _keys({})
    assert keys.key() is None
    assert keys.key() is None
    assert len(_calls) == 1


@pytest.mark.parametrize("where", ["stdout", "stderr"])
def test_the_token_never_leaks(where, tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    if where == "stderr":
        _answer["returncode"] = 1
    keys = _keys({})
    store = FilesystemBudgetStore(tmp_path)
    key = keys.key()
    if key is not None:
        claim = store.claim(key, T)
        assert claim is not None
        store.put(key, claim, UNKNOWN)
    read_rate_limit(_fake_run, Path("."))
    shown = [key or "", caplog.text, repr(vars(keys)), *(str(p) for p in tmp_path.iterdir())]
    shown += [p.read_text() for p in tmp_path.iterdir()]
    captured = capsys.readouterr()
    shown += [captured.out, captured.err]
    assert (key is None) == (where == "stderr")
    assert all(SENTINEL not in text for text in shown)


def test_read_rate_limit_runs_gh_api_rate_limit_and_parses_it():
    _answer["stdout"] = '{"resources": {}}'
    assert read_rate_limit(_fake_run, Path(".")) == {"resources": {}}
    assert _calls == [["gh", "api", "rate_limit"]]


def test_read_rate_limit_is_none_on_a_failure_and_on_bad_json():
    _answer["stdout"] = "not json"
    assert read_rate_limit(_fake_run, Path(".")) is None
    _answer["returncode"] = 1
    assert read_rate_limit(_fake_run, Path(".")) is None
