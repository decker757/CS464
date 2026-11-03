"""HTTP metrics and `/metrics`, tested once, where they live. #599

The metric names and labels are a contract with the dashboard and the alert
rules. The registry is process-wide, so every test asserts on a change from a
reading taken before the request, never on an absolute value.
"""

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from prometheus_client import REGISTRY
from starlette.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from shared.http_metrics import HttpMetricsMiddleware, add_metrics_route


def _reading(name: str, **labels: str) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


def _every_request_count() -> float:
    return sum(
        sample.value
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name == "http_requests_total"
    )


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict:
        return {"id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("nobody handles this")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return app


def _wrapped(app: FastAPI):
    cors = CORSMiddleware(app, allow_origins=["http://a.test"], allow_methods=["GET"])
    return HttpMetricsMiddleware(cors)


async def _get(app, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(kwargs.pop("method", "GET"), path, **kwargs)


async def test_a_request_is_counted_under_its_route_template():
    labels = {"method": "GET", "route": "/items/{item_id}", "status": "200"}
    before = _reading("http_requests_total", **labels)

    await _get(_wrapped(_app()), "/items/42")

    assert _reading("http_requests_total", **labels) - before == 1
    raw_path = {"method": "GET", "route": "/items/42", "status": "200"}
    assert REGISTRY.get_sample_value("http_requests_total", raw_path) is None


async def test_paths_with_no_route_and_preflights_share_one_label():
    not_found = {"method": "GET", "route": "unmatched", "status": "404"}
    preflight = {"method": "OPTIONS", "route": "unmatched", "status": "200"}
    not_found_before = _reading("http_requests_total", **not_found)
    preflight_before = _reading("http_requests_total", **preflight)
    app = _wrapped(_app())

    await _get(app, "/wp-login.php")
    await _get(app, "/items/a/b/c")
    await _get(
        app,
        "/items/1",
        method="OPTIONS",
        headers={
            "Origin": "http://a.test",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert _reading("http_requests_total", **not_found) - not_found_before == 2
    assert _reading("http_requests_total", **preflight) - preflight_before == 1


async def test_probes_are_not_counted():
    app = _wrapped(_app())
    add_metrics_route(app.app.app, {})
    before = _every_request_count()
    latency_before = _reading(
        "http_request_duration_seconds_count", method="GET", route="/health"
    )

    for path in ("/health", "/ready", "/metrics"):
        await _get(app, path)

    assert _every_request_count() == before
    assert (
        _reading("http_request_duration_seconds_count", method="GET", route="/health")
        == latency_before
    )


async def test_latency_is_observed_per_route():
    labels = {"method": "GET", "route": "/items/{item_id}"}
    before = _reading("http_request_duration_seconds_count", **labels)

    await _get(_wrapped(_app()), "/items/7")

    assert _reading("http_request_duration_seconds_count", **labels) - before == 1


async def test_an_unhandled_error_counts_as_a_500():
    labels = {"method": "GET", "route": "/boom", "status": "500"}
    before = _reading("http_requests_total", **labels)

    with pytest.raises(RuntimeError):
        transport = httpx.ASGITransport(app=_wrapped(_app()))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await c.get("/boom")

    assert _reading("http_requests_total", **labels) - before == 1


async def _metrics_body(checks) -> str:
    app = FastAPI()
    add_metrics_route(app, checks)
    response = await _get(app, "/metrics")
    assert response.status_code == 200
    return response.text


def _answers(value: bool):
    async def check() -> bool:
        return value

    return check


async def test_metrics_reports_each_dependency_s_readiness():
    line = 'service_dependency_ready{dependency="database"}'
    assert f"{line} 0.0" in await _metrics_body({"database": _answers(False)})
    assert f"{line} 1.0" in await _metrics_body({"database": _answers(True)})


def test_a_websocket_is_never_counted():
    before = _every_request_count()

    with TestClient(_wrapped(_app())) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_text() == "hello"

    assert _every_request_count() == before
    assert REGISTRY.get_sample_value(
        "http_requests_total", {"method": "GET", "route": "/ws", "status": "200"}
    ) is None


async def test_a_non_standard_method_is_counted_as_other():
    labels = {"method": "other", "route": "/items/{item_id}", "status": "405"}
    before = _reading("http_requests_total", **labels)

    await _get(_wrapped(_app()), "/items/42", method="PROPFIND")

    assert _reading("http_requests_total", **labels) - before == 1
    raw_method = {"method": "PROPFIND", "route": "/items/{item_id}", "status": "405"}
    assert REGISTRY.get_sample_value("http_requests_total", raw_method) is None
