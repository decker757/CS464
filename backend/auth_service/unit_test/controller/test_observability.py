"""The request-ID, metrics and tracing middleware, and /metrics, are on the app.
#599

What the ID is, what each metric means and what a span holds is `shared/`'s to
test; this holds only that this service's app carries them, in the right order,
and that its Redis client is traced.
"""

from __future__ import annotations

import re

from httpx import ASGITransport, AsyncClient

from unit_test.conftest import TEST_IP

_USERNAME = "trace_probe_user"


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


async def test_a_login_s_trace_has_a_redis_span_that_holds_no_arguments(
    clean_database, app, span_exporter
) -> None:
    # The attempt limiter's keys hold the identifier typed and the caller's
    # address; a Redis span records the command with every argument as "?".
    # The server span keeps the address on purpose, so only Redis's are read.
    transport = ASGITransport(app=app, client=(TEST_IP, 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login", json={"identifier": _USERNAME, "password": "wrong-password-1"}
        )
    assert response.status_code == 401

    spans = span_exporter.get_finished_spans()
    [server] = [span for span in spans if span.name == "POST /auth/login"]
    redis_spans = [span for span in spans if span.attributes.get("db.system") == "redis"]
    assert redis_spans, "the login made no Redis span, so Redis is not traced"
    for span in redis_spans:
        assert span.context.trace_id == server.context.trace_id
        recorded = " ".join(str(value) for value in span.attributes.values())
        assert _USERNAME not in recorded, f"Redis span {span.name!r} holds the username"
        assert TEST_IP not in recorded, f"Redis span {span.name!r} holds the address"
