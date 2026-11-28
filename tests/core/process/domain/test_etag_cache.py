"""The pure rule of the GitHub response cache: the key, the shape an entry must have to be
read back, when it has expired and what is worth storing."""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from agentic.core.process.domain import etag_cache
from agentic.core.process.domain.etag_cache import CacheEntry

REPO = "owner/name"
KEY = "a" * 64
DAY = 86400.0

BASE = CacheEntry(
    v=etag_cache.VERSION,
    repo=REPO,
    key=KEY,
    etag='W/"abc"',
    last_modified=None,
    body="[]",
    fetched_at=1000.0,
)


def _entry(**over: object) -> CacheEntry:
    return dataclasses.replace(BASE, **over)


def _raw(**over: object) -> str:
    return json.dumps({**dataclasses.asdict(BASE), **over})


# --- entry_key ---------------------------------------------------------------------------


def test_entry_key_is_64_lowercase_hex():
    key = etag_cache.entry_key(REPO, "/repos/x/issues", (("state", "open"),), 1)
    assert re.fullmatch(r"[0-9a-f]{64}", key)


def test_entry_key_is_stable_for_the_same_tuple():
    first = etag_cache.entry_key(REPO, "/repos/x/issues", (("a", "1"), ("b", "2")), 2)
    again = etag_cache.entry_key(REPO, "/repos/x/issues", (("a", "1"), ("b", "2")), 2)
    assert first == again


@pytest.mark.parametrize(
    "other",
    [
        ("o/n", "/repos/x", (("a", "1"), ("a", "2")), 1),  # repo
        (REPO, "/repos/x", (("a", "2"), ("a", "1")), 1),  # same pairs, another order
        (REPO, "/repos/x", (("a", "1"), ("a", "1")), 1),  # another value
        (REPO, "/repos/x", (("a", "1"),), 1),  # a repeated pair is not collapsed
        (REPO, "repos/x", (("a", "1"), ("a", "2")), 1),  # endpoint is not normalized
        (REPO, "/repos/x", (("a", "1"), ("a", "2")), 2),  # page
    ],
)
def test_entry_key_tells_apart_what_differs(other):
    base = etag_cache.entry_key(REPO, "/repos/x", (("a", "1"), ("a", "2")), 1)
    assert etag_cache.entry_key(*other) != base


def test_entry_key_is_total_over_any_str():
    keys = {
        etag_cache.entry_key(REPO, "/repos/\ud800", (("q", "\ud800"),), 1),
        etag_cache.entry_key(REPO, "/repos/ñ", (("q", "ñ"),), 1),
        etag_cache.entry_key(REPO, "/repos/\udfff", (("q", "\udfff"),), 1),
    }
    assert len(keys) == 3
    assert all(re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)


# --- parse -------------------------------------------------------------------------------


def test_parse_reads_back_what_to_json_wrote():
    entry = _entry(etag=None, last_modified="Tue, 01 Jan 2030 00:00:00 GMT", fetched_at=12)
    assert etag_cache.parse(etag_cache.to_json(entry), KEY, REPO) == entry


def test_parse_round_trips_a_lone_surrogate_body():
    entry = _entry(body="\ud800 ñ")
    assert etag_cache.parse(etag_cache.to_json(entry), KEY, REPO) == entry


def test_parse_accepts_a_negative_fetched_at():
    assert etag_cache.parse(_raw(fetched_at=-5), KEY, REPO) is not None


def test_parse_accepts_one_validator_only():
    assert etag_cache.parse(_raw(etag=None, last_modified="x"), KEY, REPO) is not None


_NAN = (
    f'{{"v":1,"repo":"owner/name","key":"{KEY}","etag":"e","last_modified":null,"body":"",'
    '"fetched_at":%s}'
)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        '{"v": 1, "repo": "owner/na',  # torn
        "[]",
        "null",
        "42",
        '"text"',
        "[" * 100000,  # deep enough to exhaust the parser's recursion
        _raw(v=2),
        _raw(v=True),
        _raw(v="1"),
        _raw(v=1.0),
        _raw(repo="other/name"),
        _raw(repo=3),
        _raw(key="b" * 64),
        _raw(key=None),
        _raw(etag=5),
        _raw(last_modified=["x"]),
        _raw(etag=None, last_modified=None),
        _raw(body=None),
        _raw(body=[]),
        _raw(fetched_at="1000"),
        _raw(fetched_at=True),
        _raw(fetched_at=None),
        _NAN % "NaN",
        _NAN % "Infinity",
        _NAN % "-Infinity",
        _NAN % "1e999",
    ],
)
def test_parse_makes_anything_invalid_a_miss(raw):
    assert etag_cache.parse(raw, KEY, REPO) is None


def test_parse_misses_a_missing_field():
    data = json.loads(_raw())
    del data["body"]
    assert etag_cache.parse(json.dumps(data), KEY, REPO) is None


# --- expired -----------------------------------------------------------------------------


def test_expired_at_exactly_the_ttl():
    assert etag_cache.expired(0.0, etag_cache.READ_TTL_S) is True


def test_not_expired_just_under_the_ttl():
    assert etag_cache.expired(0.0, etag_cache.READ_TTL_S - 1) is False


def test_a_slightly_future_mtime_is_not_expired():
    assert etag_cache.expired(1060.0, 1000.0) is False


def test_a_mtime_at_the_skew_edge_is_not_expired_and_beyond_it_is():
    assert etag_cache.expired(1000.0 + etag_cache.SKEW_S, 1000.0) is False
    assert etag_cache.expired(1000.0 + etag_cache.SKEW_S + 1, 1000.0) is True


def test_a_mtime_a_day_ahead_is_expired():
    assert etag_cache.expired(1000.0 + DAY, 1000.0) is True


# --- storable ----------------------------------------------------------------------------


def test_an_entry_with_a_validator_is_storable():
    assert etag_cache.storable(_entry()) is True
    assert etag_cache.storable(_entry(etag=None, last_modified="x")) is True


def test_an_entry_without_a_validator_is_not_storable():
    assert etag_cache.storable(_entry(etag=None, last_modified=None)) is False


def test_a_body_over_the_limit_is_not_storable_and_at_the_limit_is():
    at = "x" * etag_cache.BODY_MAX_BYTES
    assert etag_cache.storable(_entry(body=at)) is True
    assert etag_cache.storable(_entry(body=at + "x")) is False


def test_the_limit_counts_utf8_bytes_not_characters():
    body = "ñ" * (etag_cache.BODY_MAX_BYTES // 2 + 1)
    assert len(body) < etag_cache.BODY_MAX_BYTES
    assert etag_cache.storable(_entry(body=body)) is False


def test_a_body_with_a_lone_surrogate_does_not_raise():
    assert etag_cache.storable(_entry(body="\ud800")) is True
