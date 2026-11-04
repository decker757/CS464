"""OpenTelemetry tracing: one trace across every service a request touches. #599

The provider and the instrumentations are always on, so `traceparent` always
propagates and every log line carries a `trace_id`. Only export depends on the
setting: with no endpoint there is no exporter, so nothing retries or logs
errors. An inbound `traceparent` is accepted as the parent, and nginx passes it
through untouched.

It clears ADR 0012's bar because a trace crosses services: every one of them
must propagate the same `traceparent`, skip the same probe URLs and keep the
same no-query-string rule, or a trace breaks at the service that differs. A
divergence would be a bug, not a design choice.

No span carries a query string. The instrumentations record the full URL by
default, and a query string can hold anything a caller typed, so the URL
attributes are cut back to the path as each span starts. Request and response
headers, cookies and Authorization included, are not captured in 0.66b2 unless
asked for, and nothing here asks. The query-string hook is the only attribute
rewrite: a server span keeps the client address and user agent on purpose, a
decision for chasing abuse. Logs still carry no IP.

Only the API, the SDK and the FastAPI instrumentation are imported at the top,
because all five services install them. The exporter and the httpx, SQLAlchemy
and Redis instrumentations are imported inside the function that needs them.
The SQLAlchemy and Redis instrumentors are process singletons: `instrument()`
on one already instrumented is a no-op. `instrument_engine` uninstruments first,
so the last engine instrumented is the one traced; `instrument_redis` does not,
so Redis keeps the first provider it was given.
"""

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span

from shared.request_ids import QUIET_PATHS

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncEngine

# The probe paths, as the instrumentation's comma-separated regexes.
UNTRACED_URLS = ",".join(f"{path}$" for path in sorted(QUIET_PATHS))

EXPORT_TIMEOUT_SECONDS = 5

# Where 0.66b2 records the URL: `http.url` under the default HTTP semantic
# conventions, `url.full` and `url.query` under OTEL_SEMCONV_STABILITY_OPT_IN.
_URL_ATTRIBUTES = ("http.url", "url.full")
_QUERY_ATTRIBUTE = "url.query"


def _path_only(url: str) -> str:
    return urlsplit(url)._replace(query="", fragment="").geturl()


def _drop_query_strings(span: Span) -> None:
    if not span.is_recording():
        return
    # The SDK span's attributes, as the instrumentation set them at the start.
    recorded = span.attributes
    for name in _URL_ATTRIBUTES:
        if name in recorded:
            span.set_attribute(name, _path_only(recorded[name]))
    if _QUERY_ATTRIBUTE in recorded:
        span.set_attribute(_QUERY_ATTRIBUTE, "")


def _drop_query_strings_of_request(span: Span, scope: dict[str, Any]) -> None:
    _drop_query_strings(span)


async def _drop_query_strings_of_call(span: Span, request: Any) -> None:
    _drop_query_strings(span)


def build_tracer_provider(*, service: str, endpoint: str, sample_ratio: float) -> TracerProvider:
    """A provider naming `service`, sampling roots at `sample_ratio`.

    A sampled or unsampled parent decides for its children. Spans are exported
    over OTLP/HTTP to `endpoint` only when it is set.
    """
    provider = TracerProvider(
        resource=Resource({SERVICE_NAME: service}),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=EXPORT_TIMEOUT_SECONDS)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def instrument_app(app: FastAPI, provider: TracerProvider) -> None:
    """One server span per request, outside every middleware; probes get none.

    `send` and `receive` spans are left out, so a price socket is one span for
    the connection rather than one per frame.
    """
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=UNTRACED_URLS,
        exclude_spans=["receive", "send"],
        server_request_hook=_drop_query_strings_of_request,
    )


def instrument_client(client: "httpx.AsyncClient", provider: TracerProvider) -> None:
    """One client span per call, and `traceparent` sent with it."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: PLC0415

    HTTPXClientInstrumentor.instrument_client(
        client, tracer_provider=provider, request_hook=_drop_query_strings_of_call
    )


def instrument_engine(engine: "AsyncEngine", provider: TracerProvider) -> None:
    """One span per statement on `engine`, replacing any engine traced before."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # noqa: PLC0415

    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    instrumentor.instrument(
        engine=engine.sync_engine, tracer_provider=provider, enable_commenter=False
    )


def instrument_redis(provider: TracerProvider) -> None:
    """One span per Redis command, process-wide. Only the first call counts."""
    from opentelemetry.instrumentation.redis import RedisInstrumentor  # noqa: PLC0415

    instrumentor = RedisInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(tracer_provider=provider)


def current_trace_id() -> str | None:
    """The current span's trace ID as 32 hex digits, or None outside any span."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return trace.format_trace_id(context.trace_id)
