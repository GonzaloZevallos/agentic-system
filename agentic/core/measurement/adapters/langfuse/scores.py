"""The scores of one finished task, written to Langfuse (GH-273, ADR-0037 addendum).

Langfuse widgets aggregate score values only, so a task's numbers go out as scores on a
*carrier trace* of their own: one span, then one `score-create` batch. Two HTTP calls, in
that order, no retry. The span is built by a `TracerProvider` that never exports (its
processor only captures), encoded with the SDK's own `encode_spans` and posted once as
protobuf; there is no `OTLPSpanExporter` on this path.

A write is a side channel and never a result: every exception is caught, one line names
the call and the task, and `write` returns `False`. The line carries the exception's
class and nothing of its message, because a transport's message may hold the key pair or
an auth header; no response body is ever read into a line either. Every id comes from
`task_scores.identity`, and every timestamp from the task's own recorded times.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.id_generator import IdGenerator

from ...domain import task_scores
from ...domain.task_scores import Dimensions, Score, TaskTime

# Declared, not measured: a healthy post takes well under a second. A write point makes
# two, so it costs at most twice this; one constant to change if an incident shows it long.
LANGFUSE_TIMEOUT_S: float = 10

SPAN_PATH = "/api/public/otel/v1/traces"
INGESTION_PATH = "/api/public/ingestion"

# `(url, body, headers, timeout_s)` in, `(status, body)` out; raises on anything else.
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SERVICE = "agentic-system"


class Rejected(Exception):
    """A post Langfuse answered and did not accept. Carries a status, never a body."""


def urllib_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> tuple[int, bytes]:
    """The default transport: a stdlib POST bounded by `timeout_s`. An HTTP error status
    is an answer (its body is not read), not an exception."""
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


class _Derived(IdGenerator):
    """Ids fixed by `task_scores.identity`, never random."""

    def __init__(self, ids: task_scores.Identity) -> None:
        self._trace_id = int(ids.trace_id, 16)
        self._span_id = int(ids.span_id, 16)

    def generate_span_id(self) -> int:
        return self._span_id

    def generate_trace_id(self) -> int:
        return self._trace_id


class _Capture(SpanProcessor):
    """Keeps the finished spans; exports nothing."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        self.spans.append(span)


def _ns(moment: datetime) -> int:
    return ((moment - _EPOCH) // timedelta(microseconds=1)) * 1000


def _iso(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


class LangfuseScores:
    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        *,
        product: str,
        transport: Transport | None = None,
        timeout_s: float = LANGFUSE_TIMEOUT_S,
    ) -> None:
        self._host = host.rstrip("/")
        self._product = product
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._authorization = f"Basic {token}"
        self._transport: Transport = transport or urllib_transport
        self._timeout_s = timeout_s

    def write(
        self,
        product: str,
        task_id: str,
        document: Mapping[str, Any],
        scores: Sequence[Score],
        task_time: TaskTime,
        dimensions: Dimensions,
    ) -> bool:
        """Sends the carrier span, then the scores. `True` only when both posts were
        accepted; on any failure one line is printed and the result is `False`.

        The writer is bound to one product (GH-283): any other `product` is refused before
        a post, so the carrier span's environment and every score's can only be the
        declared one."""
        if product != self._product:
            print(
                f"Langfuse write refused for {task_id}: product {product} is not {self._product}",
                flush=True,
            )
            return False
        call = "span"
        try:
            ids = task_scores.identity(product, task_id, task_time.start)
            self._post(
                SPAN_PATH,
                self._span(product, task_id, ids, task_time, dimensions),
                "application/x-protobuf",
            )
            call = "scores"
            if scores:
                body = self._batch(product, ids, scores, task_time, dimensions)
                self._check_batch(self._post(INGESTION_PATH, body, "application/json"))
        except Exception as error:
            detail = f" ({error})" if isinstance(error, Rejected) else ""
            print(
                f"Langfuse {call} write failed for {task_id}: {type(error).__name__}{detail}",
                flush=True,
            )
            return False
        return True

    def _span(
        self,
        product: str,
        task_id: str,
        ids: task_scores.Identity,
        task_time: TaskTime,
        dimensions: Dimensions,
    ) -> bytes:
        capture = _Capture()
        provider = TracerProvider(
            resource=Resource.create({"service.name": _SERVICE}), id_generator=_Derived(ids)
        )
        provider.add_span_processor(capture)
        span = provider.get_tracer("agentic.task.scores").start_span(
            f"agentic.task.scores {task_id}",
            start_time=_ns(task_time.start),
            attributes={
                "langfuse.release": dimensions.release,
                "langfuse.environment": product,
                "agentic.task.id": task_id,
                "agentic.task.arm": dimensions.arm,
                "agentic.task.baseline": dimensions.baseline,
            },
        )
        span.end(end_time=_ns(task_time.end))
        encoded: bytes = encode_spans(capture.spans).SerializeToString()
        return encoded

    def _batch(
        self,
        product: str,
        ids: task_scores.Identity,
        scores: Sequence[Score],
        task_time: TaskTime,
        dimensions: Dimensions,
    ) -> bytes:
        stamp = _iso(task_time.end)
        metadata = {
            "release": dimensions.release,
            "arm": dimensions.arm,
            "baseline": dimensions.baseline,
        }
        events = []
        for score in scores:
            score_id = ids.score_id(score.name)
            value = int(score.value) if score.data_type == "BOOLEAN" else score.value
            events.append(
                {
                    "id": ids.event_id(score_id),
                    "type": "score-create",
                    "timestamp": stamp,
                    "body": {
                        "id": score_id,
                        "traceId": ids.trace_id,
                        "name": score.name,
                        "value": value,
                        "dataType": score.data_type,
                        "environment": product,
                        "timestamp": stamp,
                        "metadata": metadata,
                    },
                }
            )
        return json.dumps({"batch": events}).encode("utf-8")

    def _post(self, path: str, body: bytes, content_type: str) -> tuple[int, bytes]:
        """One POST, bounded as a whole: the transport runs on a daemon thread the caller
        stops waiting for after `timeout_s`, so a peer that drips bytes cannot stretch the
        socket timeout into a longer wait."""
        headers = {"Content-Type": content_type, "Authorization": self._authorization}
        outcome: list[tuple[int, bytes] | Exception] = []

        def send() -> None:
            try:
                outcome.append(self._transport(self._host + path, body, headers, self._timeout_s))
            except Exception as error:
                outcome.append(error)

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        thread.join(self._timeout_s)
        if not outcome:
            raise TimeoutError
        answer = outcome[0]
        if isinstance(answer, Exception):
            raise answer
        status, _ = answer
        if not 200 <= status < 300:
            raise Rejected(f"HTTP {status}")
        return answer

    @staticmethod
    def _check_batch(answer: tuple[int, bytes]) -> None:
        """A `207` batch can carry per-event errors under a successful status."""
        try:
            errors = json.loads(answer[1]).get("errors")
        except (ValueError, AttributeError):
            return
        if errors:
            raise Rejected(f"{len(errors)} score(s) refused")
