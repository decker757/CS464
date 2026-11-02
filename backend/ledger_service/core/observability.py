"""Readiness, JSON logs and request IDs for this service, from shared/. #599

Logs and request IDs are `shared/`'s, bound here to this service's name;
metrics and traces join in later steps. The checks and the route are
`shared/readiness.py`'s; this file only names the service. ADR 0012, ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI

from shared.logs import configure_logging as _configure_logging
from shared.readiness import Check, add_ready_route, is_database_reachable
from shared.request_ids import RequestContextMiddleware, background_run, send_request_id

__all__ = [
    "Check",
    "SERVICE_NAME",
    "background_run",
    "configure_logging",
    "install",
    "is_database_reachable",
    "send_request_id",
]

SERVICE_NAME = "ledger"


def configure_logging() -> None:
    """JSON log lines on stdout, tagged with this service's name."""
    _configure_logging(service=SERVICE_NAME)


def install(app: FastAPI, *, ready_checks: Mapping[str, Check]) -> None:
    """Add the observability routes and the request-ID middleware to `app`.

    Call it last in `create_app`, so the middleware is the outermost layer.
    """
    add_ready_route(app, ready_checks)
    app.add_middleware(RequestContextMiddleware)
