"""The request-ID and metrics middleware, and /metrics, are on the app. #599

What the ID is and what each metric means is `shared/`'s to test; this holds
only that this service's app carries them, in the right order.
"""

from __future__ import annotations

import re

from httpx import AsyncClient


async def test_a_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_metrics_serves_this_service_s_traffic_and_readiness(client: AsyncClient) -> None:
    await client.post("/auth/login", json={"username": "nobody", "password": "wrong-password-1"})

    body = (await client.get("/metrics")).text

    assert 'http_requests_total{method="POST",route="/auth/login"' in body
    assert 'service_dependency_ready{dependency="database"} 1.0' in body


async def test_a_cors_preflight_is_counted_as_unmatched(client: AsyncClient) -> None:
    # Metrics must wrap CORS: a preflight is answered by CORSMiddleware before
    # routing, so a metrics layer inside it would never see the request. Not
    # /health: probe paths get no HTTP metric at all.
    await client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    body = (await client.get("/metrics")).text

    assert 'http_requests_total{method="OPTIONS",route="unmatched"' in body
