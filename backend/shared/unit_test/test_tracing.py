"""Tracing, tested once, where it lives. #599

Five services build their tracer provider and instrument their app, clients
and engine through `shared/tracing.py`, so its promises are asserted here: a
call out joins the trace of the request that made it, the server span carries
the request ID, an inbound `traceparent` is joined, probes make no spans, a
readiness check makes none either, sampling follows the setting, no endpoint
builds no exporter, and no span carries a query string, a cookie or a token.
Each test records into its own provider; no global one is set.
"""

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http import trace_exporter
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import create_async_engine

from shared.readiness import is_database_reachable, readiness_of
from shared.request_ids import REQUEST_ID_HEADER, RequestContextMiddleware
from shared.tracing import (
    build_tracer_provider,
    instrument_app,
    instrument_client,
    instrument_engine,
)

SECRET = "secret-7f3a"
# Header capture is off by default in 0.66b2; these keep it that way.
SECRET_HEADERS = {
    "Authorization": f"Bearer token-{SECRET}",
    "Cookie": f"access_token=cookie-{SECRET}",
}
UNREACHABLE_DATABASE = "postgresql+asyncpg://127.0.0.1:1/none"


@pytest.fixture
def exporter():
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter):
    provider = build_tracer_provider(service="test", endpoint="", sample_ratio=1.0)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def _recording_client(provider, recorded: dict) -> httpx.AsyncClient:
    def record(request: httpx.Request) -> httpx.Response:
        recorded.update(request.headers)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    instrument_client(client, provider)
    return client


def _build_app(provider, recorded: dict) -> FastAPI:
    """Built the way a service builds one: middleware first, instrumentation last."""
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: int):
        async with _recording_client(provider, recorded) as upstream:
            await upstream.get("http://up/x")
        return {"id": item_id}

    for probe in ("/health", "/ready", "/metrics"):
        app.add_api_route(probe, lambda: {"status": "ok"})

    app.add_middleware(RequestContextMiddleware)
    instrument_app(app, provider)
    return app


async def _get(app: FastAPI, path: str, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


def _attribute_values(exporter) -> list[str]:
    return [
        str(value)
        for span in exporter.get_finished_spans()
        for value in span.attributes.values()
    ]


def _server_span(exporter):
    return next(s for s in exporter.get_finished_spans() if s.name == "GET /items/{item_id}")


async def test_a_call_out_carries_the_inbound_request_s_trace(provider, exporter):
    recorded = {}
    await _get(_build_app(provider, recorded), "/items/1")
    server = _server_span(exporter)
    assert recorded["traceparent"].split("-")[1] == format(server.context.trace_id, "032x")


async def test_probe_paths_make_no_spans(provider, exporter):
    app = _build_app(provider, {})
    for probe in ("/health", "/ready", "/metrics"):
        assert (await _get(app, probe)).status_code == 200
    assert exporter.get_finished_spans() == ()


async def test_the_server_span_carries_the_request_id(provider, exporter):
    await _get(_build_app(provider, {}), "/items/1", {REQUEST_ID_HEADER: "span-req-0001"})
    assert _server_span(exporter).attributes["request_id"] == "span-req-0001"


async def test_a_server_span_carries_no_query_string_cookie_or_token(provider, exporter):
    await _get(_build_app(provider, {}), f"/items/1?q={SECRET}", SECRET_HEADERS)
    values = _attribute_values(exporter)
    assert values and not any(SECRET in value for value in values)


async def test_a_call_out_span_carries_no_query_string_cookie_or_token(provider, exporter):
    async with _recording_client(provider, {}) as client:
        await client.get(f"http://up/x?q={SECRET}", headers=SECRET_HEADERS)
    values = _attribute_values(exporter)
    assert values and not any(SECRET in value for value in values)


async def test_an_inbound_traceparent_is_joined(provider, exporter):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_id = "00f067aa0ba902b7"
    traceparent = f"00-{trace_id}-{parent_id}-01"

    await _get(_build_app(provider, {}), "/items/1", {"traceparent": traceparent})

    server = _server_span(exporter)
    assert format(server.context.trace_id, "032x") == trace_id
    assert format(server.parent.span_id, "016x") == parent_id


class _ExporterBuilt(Exception):
    pass


class _RefusingExporter:
    def __init__(self, **kwargs):
        raise _ExporterBuilt


def test_no_endpoint_builds_no_exporter(monkeypatch):
    # Grafana Cloud stays optional: with no endpoint nothing retries or logs.
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", _RefusingExporter)

    build_tracer_provider(service="test", endpoint="", sample_ratio=1.0)

    with pytest.raises(_ExporterBuilt):
        build_tracer_provider(
            service="test", endpoint="http://127.0.0.1:1/v1/traces", sample_ratio=1.0
        )


@pytest.mark.parametrize(("ratio", "sampled"), [(0.0, False), (1.0, True)])
def test_sampling_follows_the_setting(ratio, sampled):
    provider = build_tracer_provider(service="test", endpoint="", sample_ratio=ratio)
    with provider.get_tracer("test").start_as_current_span("root") as span:
        assert span.get_span_context().trace_flags.sampled is sampled


async def test_a_readiness_probe_makes_no_spans(provider, exporter):
    engine = create_async_engine(UNREACHABLE_DATABASE)
    instrument_engine(engine, provider)
    try:
        answers = await readiness_of({"database": lambda: is_database_reachable(engine)})
    finally:
        SQLAlchemyInstrumentor().uninstrument()
        await engine.dispose()
    assert answers == {"database": False}
    assert exporter.get_finished_spans() == ()
