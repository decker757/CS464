"""Request IDs and the access line, tested once, where they live. #599

Five services take, echo, log and pass on the ID through this one middleware,
so its promises are asserted here directly: a safe inbound ID is kept, an
unsafe one is replaced rather than cleaned, one access line names the route
template and never the raw path, probes write none, and the ID rides on an
outgoing call. Each service tests its own seam instead: that the middleware
is installed, and that the ID goes with each call it makes to another service
and each background run it starts.
"""

import asyncio
import logging
import re

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient

from shared.request_ids import (
    ACCESS_LOGGER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    background_run,
    current_request_id,
    send_request_id,
)

GENERATED_ID = r"[0-9a-f]{32}"


def _build_app(outbound_recorder: dict | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: int):
        if outbound_recorder is not None:

            def record(request: httpx.Request) -> httpx.Response:
                outbound_recorder.update(request.headers)
                return httpx.Response(200)

            async with httpx.AsyncClient(
                event_hooks={"request": [send_request_id]},
                transport=httpx.MockTransport(record),
            ) as upstream:
                await upstream.get("http://up/x")
        return {"id": item_id, "seen": current_request_id()}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.websocket("/ws")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(current_request_id() or "")
        await websocket.close()

    app.add_middleware(RequestContextMiddleware)
    return app


async def _get(path: str, headers: dict | None = None, app: FastAPI | None = None):
    transport = httpx.ASGITransport(app=app or _build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


def _access_records(caplog):
    return [record for record in caplog.records if record.name == ACCESS_LOGGER]


async def test_a_safe_incoming_id_is_kept_and_returned():
    response = await _get("/items/1", {REQUEST_ID_HEADER: "abc-123.X_y"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123.X_y"
    assert response.json()["seen"] == "abc-123.X_y"


async def test_a_missing_id_is_made_up_and_returned():
    response = await _get("/items/1")
    assert re.fullmatch(GENERATED_ID, response.headers[REQUEST_ID_HEADER])


@pytest.mark.parametrize("unsafe", ["a" * 65, "evil\r\nX-Injected: 1", "has space", "", "ünïcode"])
async def test_an_unsafe_incoming_id_is_replaced_not_echoed(unsafe):
    # httpx refuses to send raw CR/LF or non-ASCII str headers on some paths,
    # so send bytes.
    response = await _get("/items/1", {REQUEST_ID_HEADER.encode(): unsafe.encode()})
    assert response.headers[REQUEST_ID_HEADER] != unsafe
    assert re.fullmatch(GENERATED_ID, response.headers[REQUEST_ID_HEADER])


async def test_one_access_line_per_request_with_the_route_template(caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        await _get("/items/42")
    (record,) = _access_records(caplog)
    assert (record.method, record.route, record.status) == ("GET", "/items/{item_id}", 200)
    assert isinstance(record.duration_ms, float)
    assert "/items/42" not in caplog.text


async def test_probe_paths_write_no_access_line(caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        response = await _get("/health")
    assert response.status_code == 200
    assert _access_records(caplog) == []


async def test_an_unhandled_error_is_logged_once_as_a_500(caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        response = await _get("/boom")
    assert response.status_code == 500
    (record,) = _access_records(caplog)
    assert record.status == 500


async def test_the_id_rides_on_an_outgoing_call():
    recorded: dict = {}
    await _get(
        "/items/1",
        {REQUEST_ID_HEADER: "outbound-check-1"},
        app=_build_app(recorded),
    )
    assert recorded["x-request-id"] == "outbound-check-1"


async def test_an_error_that_escapes_keeps_the_id_for_the_servers_traceback():
    # What uvicorn's run_asgi does: await the app in a task of its own, and log
    # "Exception in ASGI application" from the except, through the JSON
    # formatter, which reads current_request_id() at that moment.
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/boom",
        "raw_path": b"/boom",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"x-request-id", b"trace-this-500")],
        "server": ("test", 80),
        "client": ("client", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    async def serve_like_uvicorn() -> str | None:
        try:
            await _build_app()(scope, receive, send)
        except RuntimeError:
            return current_request_id()
        pytest.fail("the route's error should reach the server")

    assert await asyncio.create_task(serve_like_uvicorn()) == "trace-this-500"
    assert current_request_id() is None


def test_each_background_run_gets_its_own_id():
    with background_run() as first:
        assert current_request_id() == first
    with background_run() as second:
        pass
    assert first != second
    assert current_request_id() is None


def test_a_websocket_gets_an_id_for_its_lines(caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER), TestClient(_build_app()) as client:
        with client.websocket_connect("/ws") as socket:
            received = socket.receive_text()
    assert received and re.fullmatch(GENERATED_ID, received)
    assert _access_records(caplog) == []
