"""Readiness, metrics, logs and request IDs for this service. #599

Logs, metrics and request IDs are `shared/`'s, bound here to this service's
name; traces join in a later step. The checks and the route are
`shared/readiness.py`'s; this file only names the service. ADR 0012, ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI

from shared.http_metrics import HttpMetricsMiddleware, add_metrics_route
from shared.logs import configure_logging as _configure_logging
from shared.readiness import Check, add_ready_route, is_database_reachable
from shared.request_ids import RequestContextMiddleware, background_run

__all__ = [
    "Check",
    "SERVICE_NAME",
    "background_run",
    "configure_logging",
    "install",
    "is_database_reachable",
]

SERVICE_NAME = "market"


def configure_logging() -> None:
    """JSON log lines on stdout, tagged with this service's name."""
    _configure_logging(service=SERVICE_NAME)


def install(app: FastAPI, *, ready_checks: Mapping[str, Check]) -> None:
    """Add the observability routes and middleware to `app`.

    Call it last in `create_app`, after CORS. Each added middleware wraps the
    ones before it, so the order outermost first is request context, then
    metrics, then CORS: metrics must wrap CORS or a preflight, which CORS
    answers by itself, would never be counted.
    """
    add_ready_route(app, ready_checks)
    add_metrics_route(app, ready_checks)
    app.add_middleware(HttpMetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
