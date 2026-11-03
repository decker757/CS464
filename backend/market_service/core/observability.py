"""Readiness, metrics, logs, request IDs and traces for this service. #599

Logs, metrics, request IDs and traces are `shared/`'s, bound here to this
service's name and settings. The checks and the route are
`shared/readiness.py`'s; this file only names the service. ADR 0012, ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from shared.http_metrics import HttpMetricsMiddleware, add_metrics_route
from shared.logs import configure_logging as _configure_logging
from shared.readiness import Check, add_ready_route, is_database_reachable
from shared.request_ids import RequestContextMiddleware, background_run
from shared.tracing import build_tracer_provider, instrument_app
from shared.tracing import instrument_engine as _instrument_engine

__all__ = [
    "Check",
    "SERVICE_NAME",
    "background_run",
    "configure_logging",
    "install",
    "is_database_reachable",
]

SERVICE_NAME = "market"


@lru_cache
def tracer_provider() -> TracerProvider:
    """This process's one tracer provider, built from the settings on first use."""
    settings = get_settings()
    return build_tracer_provider(
        service=SERVICE_NAME,
        endpoint=settings.tracing_endpoint,
        sample_ratio=settings.tracing_sample_ratio,
    )


def instrument_engine(engine: AsyncEngine) -> None:
    """A span per SQL statement on `engine`, the statement without its values."""
    _instrument_engine(engine, tracer_provider())


def configure_logging() -> None:
    """JSON log lines on stdout, tagged with this service's name."""
    _configure_logging(service=SERVICE_NAME)


def install(app: FastAPI, *, ready_checks: Mapping[str, Check]) -> None:
    """Add the observability routes and middleware to `app`.

    Call it last in `create_app`, after CORS. Each added middleware wraps the
    ones before it, so the order outermost first is request context, then
    metrics, then CORS: metrics must wrap CORS or a preflight, which CORS
    answers by itself, would never be counted. The tracing middleware goes
    outside all of them, so the server span is the request's first and last.
    """
    add_ready_route(app, ready_checks)
    add_metrics_route(app, ready_checks)
    app.add_middleware(HttpMetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    instrument_app(app, tracer_provider())
