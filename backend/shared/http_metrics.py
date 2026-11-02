"""HTTP metrics, and the `/metrics` route Prometheus scrapes. #599

Every label is a route template or `unmatched`, never a raw path, an address,
a username or a request ID: a label with an unbounded set of values would grow
the time-series database without limit, and a scanner inventing paths could
make it do so on purpose. The probe paths in `QUIET_PATHS` get no HTTP metric
at all, or a health check every few seconds would drown the traffic.

`/metrics` is never published by the proxy (the design's "/metrics stays
private"): Alloy scrapes it on the private network.

The default registry is used because every container runs one uvicorn worker.
A pure ASGI middleware, like `request_ids.py`, and for the same reason: a
`BaseHTTPMiddleware` would break the websocket handshake.
"""

import time
from collections.abc import Mapping

import prometheus_client
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shared.readiness import Check, readiness_of
from shared.request_ids import QUIET_PATHS, route_template_of

# Without these, every metric also exports a `_created` timestamp series.
prometheus_client.disable_created_metrics()

HTTP_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

# Exported as `http_requests_total`: the client appends the suffix itself.
REQUESTS = Counter(
    "http_requests", "HTTP requests answered", ["method", "route", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time to answer an HTTP request",
    ["method", "route"],
    buckets=HTTP_BUCKETS,
)
DEPENDENCY_READY = Gauge(
    "service_dependency_ready",
    "1 if the last readiness check of this dependency passed",
    ["dependency"],
)


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in QUIET_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500  # what an exception no handler caught will have become

        async def send_and_note_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_note_status)
        finally:
            # The router has put the matched route in `scope` by now.
            route = route_template_of(scope)
            REQUESTS.labels(scope["method"], route, str(status)).inc()
            LATENCY.labels(scope["method"], route).observe(
                time.perf_counter() - started
            )


def add_metrics_route(app: FastAPI, checks: Mapping[str, Check]) -> None:
    """Add `GET /metrics`, measuring each dependency's readiness per scrape.

    The checks are the ones `/ready` uses, each under its own timeout, so a
    hung database makes the gauge 0 instead of hanging the scrape.
    """

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        for dependency, up in (await readiness_of(checks)).items():
            DEPENDENCY_READY.labels(dependency).set(1 if up else 0)
        return Response(
            prometheus_client.generate_latest(), media_type=CONTENT_TYPE_LATEST
        )
