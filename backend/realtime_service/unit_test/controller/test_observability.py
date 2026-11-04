"""The request-ID, metrics and tracing middleware, and /metrics, are on the app.
#599

What the ID is, what each metric means and what a span holds is `shared/`'s to
test; this holds only that this service's app carries them, in the right order.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core import observability
from unit_test.conftest import bearer

_SECRET = "secret-7f3a"
_ALLOWED_ORIGIN = "http://localhost:5173"


class _ArmedSpanExporter(InMemorySpanExporter):
    """Keeps a finished span only while a test has it armed.

    Its processor stays on the provider after this module, so unarmed it drops
    the spans of every later test rather than keeping them for the whole run.
    Its twin is in ledger_service/unit_test/conftest.py.
    """

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not self.armed:
            return SpanExportResult.SUCCESS
        return super().export(spans)


@pytest.fixture(scope="module")
def _span_recorder() -> _ArmedSpanExporter:
    """On the app's own provider: the instrumentation was given that one."""
    exporter = _ArmedSpanExporter()
    observability.tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def span_exporter(_span_recorder: _ArmedSpanExporter) -> Iterator[InMemorySpanExporter]:
    _span_recorder.clear()
    _span_recorder.armed = True
    yield _span_recorder
    _span_recorder.armed = False
    _span_recorder.clear()


def test_a_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_metrics_serves_this_service_s_traffic_and_readiness(client: TestClient) -> None:
    # This service's only HTTP routes are probes, which get no metric, so the
    # one request that is counted is one with no route template: FastAPI's docs
    # page is a Starlette route, so it is labelled unmatched.
    client.get("/docs")

    body = client.get("/metrics").text

    assert 'http_requests_total{method="GET",route="unmatched",status="200"}' in body
    assert 'service_dependency_ready{dependency="redis"} 1.0' in body


def test_a_cors_preflight_is_counted_as_unmatched(client: TestClient) -> None:
    # Metrics must wrap CORS: a preflight is answered by CORSMiddleware before
    # routing, so a metrics layer inside it would never see the request. Not
    # /health: probe paths get no HTTP metric at all.
    client.options(
        "/docs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    body = client.get("/metrics").text

    assert 'http_requests_total{method="OPTIONS",route="unmatched"' in body


def test_a_socket_is_one_span_that_holds_no_query_string(
    client: TestClient, span_exporter: InMemorySpanExporter
) -> None:
    headers = {**bearer(), "origin": _ALLOWED_ORIGIN}

    with client.websocket_connect(f"/ws/prices?q={_SECRET}", headers=headers):
        pass

    # The app runs in another thread; its span ends when the handler returns.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not span_exporter.get_finished_spans():
        time.sleep(0.01)
    [span] = span_exporter.get_finished_spans()
    assert span.attributes["http.target"] == "/ws/prices"
    assert all(_SECRET not in str(value) for value in span.attributes.values())
