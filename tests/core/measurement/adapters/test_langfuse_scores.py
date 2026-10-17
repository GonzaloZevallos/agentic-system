"""`LangfuseScores.write` with the HTTP replaced by a recording transport."""

import base64
import json
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from agentic.core.measurement.adapters.langfuse.scores import LangfuseScores, Transport
from agentic.core.measurement.domain import task_scores
from agentic.core.measurement.domain.task_scores import Dimensions, TaskTime

START = datetime(2026, 10, 9, 8, 53, 20, tzinfo=UTC)
END = datetime(2026, 10, 9, 9, 53, 20, 250000, tzinfo=UTC)
TIME = TaskTime(START, END)
DIMENSIONS = Dimensions(release="v1.2", baseline="bl-1", arm="default")
DOCUMENT = {"cost_usd": 1.5, "type": "feature", "title": "Add a thing"}


class Recorder:
    """A transport that records every post and answers 200 (or what `status` says)."""

    def __init__(self, status: int = 200, body: bytes = b'{"successes":[],"errors":[]}') -> None:
        self.posts: list[tuple[str, bytes, dict[str, str], float]] = []
        self.status = status
        self.body = body

    def __call__(
        self, url: str, body: bytes, headers: dict[str, str], timeout_s: float
    ) -> tuple[int, bytes]:
        self.posts.append((url, body, headers, timeout_s))
        return self.status, self.body


def _writer(transport: Transport, **kwargs: float) -> LangfuseScores:
    return LangfuseScores(
        "http://lf:3000", "pk-lf-pub", "sk-lf-secret", transport=transport, **kwargs
    )


def _scores() -> list[task_scores.Score]:
    return task_scores.task_scores(DOCUMENT)


def _write(writer: LangfuseScores) -> bool:
    return writer.write("apostrophe", "GH-12", DOCUMENT, _scores(), TIME, DIMENSIONS)


def test_the_span_is_sent_first_as_protobuf_and_the_scores_second_as_one_batch() -> None:
    transport = Recorder()
    assert _write(_writer(transport)) is True
    [(span_url, _, span_headers, _), (score_url, _, score_headers, _)] = transport.posts
    assert span_url == "http://lf:3000/api/public/otel/v1/traces"
    assert score_url == "http://lf:3000/api/public/ingestion"
    assert span_headers["Content-Type"] == "application/x-protobuf"
    assert score_headers["Content-Type"] == "application/json"


def test_the_carrier_span_has_the_derived_ids_the_task_times_and_the_dimensions() -> None:
    transport = Recorder()
    _write(_writer(transport))
    request = ExportTraceServiceRequest()
    request.ParseFromString(transport.posts[0][1])
    [span] = [s for rs in request.resource_spans for ss in rs.scope_spans for s in ss.spans]
    ids = task_scores.identity("apostrophe", "GH-12", START)
    assert span.trace_id.hex() == ids.trace_id
    assert span.span_id.hex() == ids.span_id
    assert span.name == "agentic.task.scores GH-12"
    assert span.start_time_unix_nano == int(START.timestamp()) * 1_000_000_000
    assert span.end_time_unix_nano == int(END.timestamp()) * 1_000_000_000 + 250_000_000
    attributes = {a.key: a.value.string_value for a in span.attributes}
    assert attributes == {
        "langfuse.release": "v1.2",
        "langfuse.environment": "apostrophe",
        "agentic.task.id": "GH-12",
        "agentic.task.arm": "default",
        "agentic.task.baseline": "bl-1",
    }


def test_each_score_carries_the_derived_ids_the_task_end_the_environment_and_the_metadata() -> None:
    transport = Recorder()
    _write(_writer(transport))
    batch = json.loads(transport.posts[1][1])["batch"]
    ids = task_scores.identity("apostrophe", "GH-12", START)
    assert [e["body"]["name"] for e in batch] == [s.name for s in _scores()]
    for event in batch:
        body = event["body"]
        assert event["type"] == "score-create"
        assert body["id"] == ids.score_id(body["name"])
        assert event["id"] == ids.event_id(body["id"])
        assert body["traceId"] == ids.trace_id
        assert body["timestamp"] == event["timestamp"] == "2026-10-09T09:53:20.250Z"
        assert body["environment"] == "apostrophe"
        assert body["metadata"] == {"release": "v1.2", "arm": "default", "baseline": "bl-1"}
    by_name = {e["body"]["name"]: e["body"] for e in batch}
    assert by_name["cost_usd"]["value"] == 1.5 and by_name["cost_usd"]["dataType"] == "NUMERIC"
    assert by_name["type"]["value"] == "feature" and by_name["type"]["dataType"] == "CATEGORICAL"


def test_sending_twice_yields_identical_ids() -> None:
    transport = Recorder()
    writer = _writer(transport)
    _write(writer)
    _write(writer)
    first, second = transport.posts[1][1], transport.posts[3][1]
    assert first == second
    assert transport.posts[0][1] == transport.posts[2][1]


def test_a_transport_that_hangs_returns_false_within_the_timeout_and_a_small_margin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    release = threading.Event()

    def hang(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, bytes]:
        release.wait()
        return 200, b""

    writer = LangfuseScores("http://lf:3000", "pk", "sk", transport=hang, timeout_s=0.3)
    started = time.monotonic()
    try:
        assert _write(writer) is False
        assert time.monotonic() - started < 0.3 + 0.5
    finally:
        release.set()
    assert capsys.readouterr().out.count("\n") == 1


@pytest.fixture
def silent_server() -> Iterator[str]:
    """A listener that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()
    for connection in held:
        connection.close()


def test_the_default_transport_gives_up_on_a_server_that_never_answers(silent_server: str) -> None:
    writer = LangfuseScores(silent_server, "pk", "sk", timeout_s=0.3)
    started = time.monotonic()
    assert _write(writer) is False
    assert time.monotonic() - started < 0.3 + 0.5


def test_a_transport_that_raises_with_the_secret_in_its_message_leaks_it_nowhere(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def leak(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, bytes]:
        raise ConnectionError(f"refused {headers['Authorization']} sk-lf-secret")

    assert _write(_writer(leak)) is False
    out = capsys.readouterr()
    shown = out.out + out.err
    expected_auth = "Basic " + base64.b64encode(b"pk-lf-pub:sk-lf-secret").decode()
    assert "sk-lf-secret" not in shown and expected_auth not in shown
    assert shown.strip() == "Langfuse span write failed for GH-12: ConnectionError"


def test_a_failed_span_post_sends_no_scores_and_a_rejected_status_names_only_the_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = Recorder(status=401, body=b"sk-lf-secret in a response body")
    assert _write(_writer(transport)) is False
    assert len(transport.posts) == 1
    out = capsys.readouterr().out
    assert out.strip() == "Langfuse span write failed for GH-12: Rejected (HTTP 401)"
    assert "response body" not in out


def test_a_batch_that_comes_back_with_errors_is_not_a_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = Recorder(status=207, body=b'{"successes":[],"errors":[{"id":"x","status":400}]}')
    assert _write(_writer(transport)) is False
    assert capsys.readouterr().out.strip() == (
        "Langfuse scores write failed for GH-12: Rejected (1 score(s) refused)"
    )
