"""The request-ID and metrics middleware, and /metrics, are on the app. #599

What the ID is and what each metric means is `shared/`'s to test; this holds
only that this service's app carries them, in the right order.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient


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
