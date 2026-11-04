"""Readiness for this service, bound to the shared checks. #599

Logs, metrics and traces join here in later steps. The checks and the route are
`shared/readiness.py`'s; this file only names the service. ADR 0012, ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI

from shared.readiness import Check, add_ready_route, is_database_reachable

__all__ = ["SERVICE_NAME", "Check", "install", "is_database_reachable"]

SERVICE_NAME = "ledger"


def install(app: FastAPI, *, ready_checks: Mapping[str, Check]) -> None:
    """Add the observability routes to `app`. Call it last in `create_app`."""
    add_ready_route(app, ready_checks)
