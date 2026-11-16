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

from agentic.core.measurement.adapters.langfuse.scores import (
    LangfuseScores,
    Transport,
    urllib_transport,
)
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
        "http://lf:3000",
        "pk-lf-pub",
        "sk-lf-secret",
        product="apostrophe",
        transport=transport,
        **kwargs,
    )


MERGED = "2026-10-12T15:30:00Z"


def _scores(**times: str) -> list[task_scores.Score]:
    return task_scores.task_scores(DOCUMENT, task_time=TIME, **times)


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


def test_each_score_is_stamped_with_its_own_timestamp_and_the_batch_computes_none() -> None:
    transport = Recorder()
    scores = _scores(issue_created_at="2026-10-09T08:00:00Z", pr_merged_at=MERGED)
    writer = _writer(transport)
    assert writer.write("apostrophe", "GH-12", DOCUMENT, scores, TIME, DIMENSIONS) is True
    batch = json.loads(transport.posts[1][1])["batch"]
    stamps = {e["body"]["name"]: e["body"]["timestamp"] for e in batch}
    assert stamps["cycle_h"] == "2026-10-12T15:30:00.000Z"
    assert stamps["cost_usd"] == "2026-10-09T09:53:20.250Z"
    assert all(e["timestamp"] == e["body"]["timestamp"] for e in batch)
    for score in scores:
        moment = score.timestamp
        assert stamps[score.name] == f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def test_the_carrier_span_keeps_the_task_time_when_the_scores_are_dated_at_the_merge() -> None:
    transport = Recorder()
    scores = _scores(issue_created_at="2026-10-09T08:00:00Z", pr_merged_at=MERGED)
    _writer(transport).write("apostrophe", "GH-12", DOCUMENT, scores, TIME, DIMENSIONS)
    request = ExportTraceServiceRequest()
    request.ParseFromString(transport.posts[0][1])
    [span] = [s for rs in request.resource_spans for ss in rs.scope_spans for s in ss.spans]
    assert span.end_time_unix_nano == int(END.timestamp()) * 1_000_000_000 + 250_000_000


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

    writer = LangfuseScores(
        "http://lf:3000", "pk", "sk", product="apostrophe", transport=hang, timeout_s=0.3
    )
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
    writer = LangfuseScores(
        silent_server, "pk", "sk", product="apostrophe", transport=urllib_transport, timeout_s=0.3
    )
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


def test_a_write_for_another_product_posts_nothing_and_prints_one_line_without_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = Recorder()
    writer = _writer(transport)
    assert writer.write("gym", "GH-12", DOCUMENT, _scores(), TIME, DIMENSIONS) is False
    assert transport.posts == []
    out = capsys.readouterr().out
    assert out.strip() == "Langfuse write refused for GH-12: product gym is not apostrophe"
    assert out.count("\n") == 1
    assert "sk-lf-secret" not in out and "pk-lf-pub" not in out


def test_the_span_and_every_score_carry_the_bound_product_as_their_environment() -> None:
    transport = Recorder()
    assert _write(_writer(transport)) is True
    request = ExportTraceServiceRequest()
    request.ParseFromString(transport.posts[0][1])
    [span] = [s for rs in request.resource_spans for ss in rs.scope_spans for s in ss.spans]
    attributes = {a.key: a.value.string_value for a in span.attributes}
    assert attributes["langfuse.environment"] == "apostrophe"
    batch = json.loads(transport.posts[1][1])["batch"]
    assert batch and {e["body"]["environment"] for e in batch} == {"apostrophe"}


def test_a_writer_built_without_a_transport_cannot_reach_the_real_one_inside_the_suite(
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer = LangfuseScores("http://127.0.0.1:9", "pk", "sk", product="apostrophe")
    assert _write(writer) is False
    assert capsys.readouterr().out.strip() == "Langfuse span write failed for GH-12: AssertionError"


# --- retire: a re-post keeps the first timestamp, so a re-dated score is deleted first (GH-819) ---


class Requests:
    """A requester that records `(method, path)` and answers each from a script; the clock
    only moves when `sleep` is called, so a 60 s bound runs instantly."""

    def __init__(self, deleted_after: int = 0, delete_status: int = 200) -> None:
        self.calls: list[tuple[str, str]] = []
        self.deleted_after = deleted_after
        self.delete_status = delete_status
        self.now = 0.0
        self.polls: dict[str, int] = {}

    def __call__(self, method: str, url: str, headers: dict[str, str], timeout_s: float) -> int:
        path = url.removeprefix("http://lf:3000")
        self.calls.append((method, path))
        if method == "DELETE":
            return self.delete_status
        self.polls[path] = self.polls.get(path, 0) + 1
        return 404 if self.polls[path] > self.deleted_after else 200

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now


def _retirer(requests: Requests) -> LangfuseScores:
    return LangfuseScores(
        "http://lf:3000",
        "pk-lf-pub",
        "sk-lf-secret",
        product="apostrophe",
        requester=requests,
        sleep=requests.sleep,
        clock=requests.clock,
    )


def test_retire_deletes_each_id_then_polls_until_the_score_is_gone() -> None:
    requests = Requests(deleted_after=2)
    assert _retirer(requests).retire(["a1", "b2"]) is True
    path = "/api/public/scores/"
    assert requests.calls == [
        ("DELETE", path + "a1"),
        *[("GET", path + "a1")] * 3,
        ("DELETE", path + "b2"),
        *[("GET", path + "b2")] * 3,
    ]


def test_retire_counts_a_404_on_the_delete_as_done_and_does_not_poll() -> None:
    requests = Requests(delete_status=404)
    assert _retirer(requests).retire(["a1"]) is True
    assert requests.calls == [("DELETE", "/api/public/scores/a1")]


def test_retire_gives_up_after_sixty_seconds_in_total_and_touches_no_later_id() -> None:
    requests = Requests(deleted_after=10**6)
    assert _retirer(requests).retire(["a1", "b2"]) is False
    assert requests.now >= 60.0 and requests.now < 70.0
    assert all(path.endswith("a1") for _, path in requests.calls)


def test_retire_sends_the_basic_authorization_and_refuses_a_rejected_delete(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[dict[str, str]] = []

    def requester(method: str, url: str, headers: dict[str, str], timeout_s: float) -> int:
        seen.append(headers)
        return 500

    writer = LangfuseScores(
        "http://lf:3000", "pk", "sk-secret-value", product="apostrophe", requester=requester
    )
    assert writer.retire(["a1"]) is False
    assert seen[0]["Authorization"] == "Basic " + base64.b64encode(b"pk:sk-secret-value").decode()
    out = capsys.readouterr().out
    assert "sk-secret-value" not in out and "a1" in out


def test_retire_with_no_ids_does_nothing() -> None:
    requests = Requests()
    assert _retirer(requests).retire([]) is True
    assert requests.calls == []


def test_a_retire_that_runs_out_on_a_later_id_keeps_the_earlier_gone_and_a_rerun_finishes() -> None:
    gone = {"a1"}
    calls: list[tuple[str, str]] = []

    def requester(method: str, url: str, headers: dict[str, str], timeout_s: float) -> int:
        score_id = url.rsplit("/", 1)[1]
        calls.append((method, score_id))
        if method == "DELETE":
            return 404 if score_id in gone else 200
        return 404 if score_id in gone else 200  # b2 never goes away in the first run

    clock = Requests()
    writer = LangfuseScores(
        "http://lf:3000",
        "pk",
        "sk",
        product="apostrophe",
        requester=requester,
        sleep=clock.sleep,
        clock=clock.clock,
    )
    assert writer.retire(["a1", "b2"]) is False
    assert ("DELETE", "a1") in calls and ("DELETE", "b2") in calls
    gone.add("b2")
    calls.clear()
    assert writer.retire(["a1", "b2"]) is True
    assert calls == [("DELETE", "a1"), ("DELETE", "b2")]
