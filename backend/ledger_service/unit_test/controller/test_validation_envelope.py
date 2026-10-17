"""A request FastAPI refuses is answered in the ledger's own envelope. #117

Before this, a 422 from FastAPI's request validation carried
`{"detail": [...]}` while every 422 the ledger raised itself carried
`{"error": {...}}`, so one route answered in two shapes and the trading panel
(#49) had to branch on which one it got.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.roles import UserRole
from unit_test.conftest import bearer

_SOME_ID = uuid.uuid4()

# Each case is one ledger route that takes a parameter, with one parameter
# wrong, and where pydantic reports it.
_MALFORMED_REQUESTS = [
    pytest.param(
        "GET",
        f"/ledger/markets/{_SOME_ID}/preview",
        {"params": {"outcome_id": str(_SOME_ID), "side": "buy", "quantity": "0"}},
        ["query", "quantity"],
        # `gt=0` puts `Decimal("0")` in the error's `ctx`, which plain JSON
        # cannot encode: copying `ctx` would turn this 422 into a 500.
        id="preview-quantity-zero",
    ),
    pytest.param(
        "GET",
        f"/ledger/markets/{_SOME_ID}/preview",
        {"params": {"outcome_id": str(_SOME_ID), "side": "sideways", "quantity": "1"}},
        ["query", "side"],
        id="preview-unknown-side",
    ),
    pytest.param(
        "GET",
        "/ledger/markets/not-a-uuid/snapshot",
        {},
        ["path", "market_id"],
        id="snapshot-malformed-path-id",
    ),
    pytest.param(
        "GET",
        "/ledger/users/not-a-uuid/balance",
        {},
        ["path", "user_id"],
        id="admin-balance-malformed-path-id",
    ),
    pytest.param(
        "GET",
        "/ledger/entries/me",
        {"params": {"limit": "0"}},
        ["query", "limit"],
        id="entries-limit-zero",
    ),
    pytest.param(
        "POST",
        f"/ledger/markets/{_SOME_ID}/trades",
        {
            "json": {
                "outcome_id": str(_SOME_ID),
                "side": "buy",
                "quantity": "10",
                "state_version": 0,
                "idempotency_key": "k",
                "amount": "1000000",
            }
        },
        ["body", "amount"],
        id="trade-extra-field",
    ),
]


@pytest.fixture
async def any_route_client(app_under_test):
    """A client on which every ledger route, the trade route included, can run.

    `ASGITransport` runs no lifespan. `app_under_test` supplies the
    market_service client, and the trade route's Redis dependency is
    overridden here, because FastAPI resolves dependencies before it
    validates parameters: without both, the preview, snapshot and trade cases
    would fail on a missing client before validation ran. Nothing here uses
    Redis.
    """
    from controller.dependencies import get_redis  # noqa: PLC0415

    app_under_test.dependency_overrides[get_redis] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=app_under_test), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(("method", "path", "request_kwargs", "loc"), _MALFORMED_REQUESTS)
async def test_a_malformed_request_is_answered_in_the_envelope(
    any_route_client: AsyncClient,
    method: str,
    path: str,
    request_kwargs: dict,
    loc: list[str],
) -> None:
    """One shape for every 422, with the field that was wrong kept in
    `error.details`, and never a top-level `detail`."""
    response = await any_route_client.request(
        method,
        path,
        headers=bearer(uuid.uuid4(), UserRole.ADMIN),
        **request_kwargs,
    )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}, "a top-level `detail` is FastAPI's shape, not ours"
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["message"]

    details = body["error"]["details"]
    assert loc in [item["loc"] for item in details]
    for item in details:
        assert set(item) == {"loc", "msg", "type"}


async def test_every_documented_422_is_the_envelope(
    any_route_client: AsyncClient,
) -> None:
    """`/docs` is the contract: no ledger route may still describe its 422 as
    FastAPI's `HTTPValidationError`, or with no body at all."""
    schema = (await any_route_client.get("/openapi.json")).json()

    documented_422s = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if "422" in operation["responses"]:
                documented_422s.append((method, path, operation["responses"]["422"]))

    assert documented_422s, "no route documents a 422 at all"
    for method, path, response in documented_422s:
        body_schema = response["content"]["application/json"]["schema"]
        assert body_schema == {"$ref": "#/components/schemas/ValidationErrorOut"}, (
            f"{method.upper()} {path}"
        )
    assert "HTTPValidationError" not in schema["components"]["schemas"]
