"""A trade's request ID and trace follow it to market_service and auth. #599

The DoD item is that one ID finds the trade in every service's log, and the
tracing AC is that the trade is one trace across the three services. The
ledger makes both outgoing calls, so this is the only place that can show it.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from unit_test.conftest import (
    active_auth_transport,
    auth_client_over,
    bearer,
    terms_client_over,
)
from unit_test.trade_fixtures import QUANTITY, Recorder, Upstream, fund, warm

_REQUEST_ID = "trade-req-0001"
_TRADE_ROUTE = "POST /ledger/markets/{market_id}/trades"


def _recording(
    seen: list[dict[str, str]], delegate: httpx.AsyncBaseTransport
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return await delegate.handle_async_request(request)

    return httpx.MockTransport(handler)


async def _trade(
    app: FastAPI,
    session,
    headers: dict[str, str],
    *,
    market_seen: list[dict[str, str]],
    auth_seen: list[dict[str, str]],
) -> httpx.Response:
    """One buy on a warmed market by a funded trader, recording the headers
    of every call the ledger makes to market_service and to auth."""
    from controller.dependencies import (  # noqa: PLC0415
        get_auth_client,
        get_redis,
        get_terms_client,
    )

    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    async with (
        terms_client_over(_recording(market_seen, upstream.transport)) as terms_client,
        auth_client_over(_recording(auth_seen, active_auth_transport())) as auth_client,
    ):
        app.dependency_overrides[get_terms_client] = lambda: terms_client
        app.dependency_overrides[get_auth_client] = lambda: auth_client
        app.dependency_overrides[get_redis] = lambda: Recorder()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                f"/ledger/markets/{upstream.market_id}/trades",
                json={
                    "outcome_id": str(upstream.outcomes[0]),
                    "side": "buy",
                    "quantity": str(QUANTITY),
                    "state_version": 0,
                    "idempotency_key": "client-key-1",
                },
                headers={**bearer(user_id), **headers},
            )


async def test_a_trade_passes_its_request_id_to_market_and_auth(
    app_under_test, session
) -> None:
    market_seen: list[dict[str, str]] = []
    auth_seen: list[dict[str, str]] = []

    response = await _trade(
        app_under_test,
        session,
        {"X-Request-ID": _REQUEST_ID},
        market_seen=market_seen,
        auth_seen=auth_seen,
    )

    assert response.status_code == 201
    assert market_seen and auth_seen
    assert all(h["x-request-id"] == _REQUEST_ID for h in market_seen + auth_seen)
    assert response.headers["X-Request-ID"] == _REQUEST_ID


async def test_a_trade_is_one_trace_across_ledger_market_and_auth(
    app_under_test, session, span_exporter
) -> None:
    """The calls to market_service and auth carry the trade's trace, and the
    book lock is a span inside it."""
    market_seen: list[dict[str, str]] = []
    auth_seen: list[dict[str, str]] = []

    response = await _trade(
        app_under_test, session, {}, market_seen=market_seen, auth_seen=auth_seen
    )

    assert response.status_code == 201
    spans = span_exporter.get_finished_spans()
    [server] = [span for span in spans if span.name == _TRADE_ROUTE]
    trace_id = format(server.context.trace_id, "032x")
    assert market_seen and auth_seen
    for headers in market_seen + auth_seen:
        assert "traceparent" in headers, "a call left the ledger without the trace"
        assert headers["traceparent"].split("-")[1] == trace_id
    assert any(
        "FOR UPDATE" in span.attributes.get("db.statement", "")
        and span.context.trace_id == server.context.trace_id
        for span in spans
    ), "the book lock was not traced inside the trade's trace"
