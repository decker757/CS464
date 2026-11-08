"""A trade's request ID and trace follow it to market_service. #599

The DoD item is that one ID finds the trade in every service's log, and the
tracing AC is that the trade is one trace across the services it calls. A
trade answers its account check and its gate from the local copy (ADR 0030),
so the call it makes is a cold market's first touch: these tests trade on a
market with no book, and the ledger making that call is the only place that
can show it.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from unit_test.conftest import bearer, terms_client_over
from unit_test.trade_fixtures import QUANTITY, Recorder, Upstream, fund

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
) -> httpx.Response:
    """One buy on a market nobody has touched, by a funded trader, recording
    the headers of the call the ledger makes to market_service for its
    terms."""
    from controller.dependencies import (  # noqa: PLC0415
        get_redis,
        get_terms_client,
    )

    upstream = Upstream()
    user_id = uuid.uuid4()
    await fund(session, user_id)

    async with terms_client_over(
        _recording(market_seen, upstream.transport)
    ) as terms_client:
        app.dependency_overrides[get_terms_client] = lambda: terms_client
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


async def test_a_trade_passes_its_request_id_to_market_service(
    app_under_test, session
) -> None:
    market_seen: list[dict[str, str]] = []

    response = await _trade(
        app_under_test,
        session,
        {"X-Request-ID": _REQUEST_ID},
        market_seen=market_seen,
    )

    assert response.status_code == 201
    assert market_seen
    assert all(h["x-request-id"] == _REQUEST_ID for h in market_seen)
    assert response.headers["X-Request-ID"] == _REQUEST_ID


async def test_a_trade_is_one_trace_across_ledger_and_market_service(
    app_under_test, session, span_exporter
) -> None:
    """The call to market_service carries the trade's trace, and the book
    lock is a span inside it."""
    market_seen: list[dict[str, str]] = []

    response = await _trade(app_under_test, session, {}, market_seen=market_seen)

    assert response.status_code == 201
    spans = span_exporter.get_finished_spans()
    [server] = [span for span in spans if span.name == _TRADE_ROUTE]
    trace_id = format(server.context.trace_id, "032x")
    assert market_seen
    for headers in market_seen:
        assert "traceparent" in headers, "a call left the ledger without the trace"
        assert headers["traceparent"].split("-")[1] == trace_id
    assert any(
        "FOR UPDATE" in span.attributes.get("db.statement", "")
        and span.context.trace_id == server.context.trace_id
        for span in spans
    ), "the book lock was not traced inside the trade's trace"
