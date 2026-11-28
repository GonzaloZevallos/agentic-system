"""`instant.parse_instant` (ADR-0111): the one reading of an ISO-8601 instant of the process
domain. An instant without an offset is unreadable, never UTC; the source zone is kept."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from agentic.core.process.domain.instant import parse_instant

PLUS_TWO = timezone(timedelta(hours=2))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param(123, None, id="not-text"),
        pytest.param(b"2026-11-01T10:00:00+00:00", None, id="bytes"),
        pytest.param("garbage", None, id="garbage"),
        pytest.param("2026-11-01T10:00:00", None, id="naive-datetime"),
        pytest.param("2026-11-01", None, id="naive-date"),
        pytest.param("2026-11-01T10:00:00Z", datetime(2026, 11, 1, 10, tzinfo=UTC), id="zulu"),
        pytest.param(
            "2026-11-01T10:00:00+02:00",
            datetime(2026, 11, 1, 10, tzinfo=PLUS_TWO),
            id="offset-kept",
        ),
    ],
)
def test_parse_instant(value: Any, expected: datetime | None) -> None:
    parsed = parse_instant(value)
    assert parsed == expected
    if expected is not None:
        assert parsed is not None
        assert parsed.utcoffset() == expected.utcoffset()


def test_an_offset_is_kept_as_written_not_converted_to_utc() -> None:
    parsed = parse_instant("2026-11-01T10:00:00+02:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=2)
    assert parsed.hour == 10
