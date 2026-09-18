import pytest
from opentelemetry.sdk.metrics.export import MetricReader, MetricsData


@pytest.fixture
def anyio_backend():
    return "asyncio"


class BatchReader(MetricReader):
    """Keeps every export as its own batch: a `finish` flushes, so one event is one
    batch. Shared by the adapter tests (`core/measurement/adapters/otel`) and the
    end-to-end graph test, which both need to tell one role's samples from another's."""

    def __init__(self):
        super().__init__()
        self.batches: list[MetricsData] = []

    def _receive_metrics(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kw):
        self.batches.append(metrics_data)

    def shutdown(self, timeout_millis: float = 30_000, **kw):
        pass


def points(reader: BatchReader) -> dict[str, list]:
    """{metric name: [data points]} of everything exported so far."""
    out: dict[str, list] = {}
    for data in reader.batches:
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No AGENTIC_* from the shell or `.env` leaks into a test."""
    import os

    for name in list(os.environ):
        if name.startswith("AGENTIC_"):
            monkeypatch.delenv(name)
    # Nor the collector's endpoint: a test never exports telemetry.
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("agentic.cli.load_dotenv", lambda: None)
