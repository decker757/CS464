"""A trade's request ID follows it to market_service and auth. #599

The DoD item is that one ID finds the trade in every service's log. The ledger
makes both outgoing calls, so this is the only place that can show it.
"""

from __future__ import annotations

import uuid

import httpx
from httpx import ASGITransport, AsyncClient

from unit_test.conftest import (
    active_auth_transport,
    auth_client_over,
    bearer,
    terms_client_over,
)
from unit_test.trade_fixtures import QUANTITY, Recorder, Upstream, fund, warm

_REQUEST_ID = "trade-req-0001"


def _recording(
    seen: list[dict[str, str]], delegate: httpx.AsyncBaseTransport
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return await delegate.handle_async_request(request)

    return httpx.MockTransport(handler)


async def test_a_trade_passes_its_request_id_to_market_and_auth(
    app_under_test, session
) -> None:
    from controller.dependencies import (  # noqa: PLC0415
        get_auth_client,
        get_redis,
        get_terms_client,
    )

    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    market_seen: list[dict[str, str]] = []
    auth_seen: list[dict[str, str]] = []

    async with (
        terms_client_over(_recording(market_seen, upstream.transport)) as terms_client,
        auth_client_over(_recording(auth_seen, active_auth_transport())) as auth_client,
    ):
        app_under_test.dependency_overrides[get_terms_client] = lambda: terms_client
        app_under_test.dependency_overrides[get_auth_client] = lambda: auth_client
        app_under_test.dependency_overrides[get_redis] = lambda: Recorder()
        async with AsyncClient(
            transport=ASGITransport(app=app_under_test), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/ledger/markets/{upstream.market_id}/trades",
                json={
                    "outcome_id": str(upstream.outcomes[0]),
                    "side": "buy",
                    "quantity": str(QUANTITY),
                    "state_version": 0,
                    "idempotency_key": "client-key-1",
                },
                headers={**bearer(user_id), "X-Request-ID": _REQUEST_ID},
            )

    assert response.status_code == 201
    assert market_seen and auth_seen
    assert all(h["x-request-id"] == _REQUEST_ID for h in market_seen + auth_seen)
    assert response.headers["X-Request-ID"] == _REQUEST_ID
