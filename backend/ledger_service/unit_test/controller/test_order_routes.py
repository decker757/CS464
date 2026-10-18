"""Limit orders on the wire. [T-6] #26, [T-8] #28, ADR 0024.

What the controller owns for `POST /ledger/markets/{market_id}/orders`,
`GET /ledger/orders/me` and `POST /ledger/orders/{order_id}/cancel`: who may
call them, what the body may contain, the status code and `error.details` of
each refusal, and the shape [FE][T-6] #61 builds against, which is
`docs/api/ledger-service.md`'s "Limit orders" section. Business rules are
`unit_test/service/test_place_order*.py`'s and `test_cancel_order*.py`'s.

Built on `test_trade_routes.py`'s client, market stub and terms stub. A book
the contract's example needs, `b = 100` at `q = [20, 0]`, is opened by the
trade tests' `_warm` and given its `b` with `set_b`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import auth_client_over, bearer, set_b
from unit_test.controller.test_sell_routes import _doc, _section, _table_rows
from unit_test.controller.test_trade_routes import (  # noqa: F401 - fixture
    _Market,
    _Terms,
    _warm,
    trade_client,
)
from unit_test.placement_fixtures import record_result

_ORDER_HEADING = "### POST /ledger/markets/{market_id}/orders"
_CANCEL_HEADING = "### POST /ledger/orders/{order_id}/cancel"
_MY_ORDERS = "/ledger/orders/me"

# The order object's fields, and nothing else: a gained field is a contract
# change #61 has to hear about.
_ORDER_FIELDS = {
    "id",
    "market_id",
    "outcome_id",
    "outcome_position",
    "side",
    "limit_price",
    "quantity",
    "filled_quantity",
    "average_fill_price",
    "reserved_credits",
    "reserved_shares",
    "status",
    "created_at",
    "cancelled_at",
}

# The contract's error table for a placement, by status.
_CONTRACT_CODES = {
    "401": ["invalid_token"],
    "403": ["account_suspended"],
    "404": ["market_not_found"],
    "409": [
        "market_closed",
        "market_not_binary",
        "order_crosses_book",
        "insufficient_funds",
        "insufficient_shares_held",
        "idempotency_key_reused",
    ],
    "422": ["unknown_outcome", "quantity_too_large"],
    "500": ["market_book_incomplete"],
    "503": ["market_terms_unavailable", "auth_unavailable"],
}

# The contract's error table for a cancel, by status.
_CANCEL_CODES = {
    "401": ["invalid_token"],
    "404": ["order_not_found"],
    "409": ["order_already_filled", "order_already_cancelled", "order_released"],
}

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/orders"


def _body(market: _Market, **overrides) -> dict:
    body = {
        "outcome_id": str(market.outcomes[0]),
        "side": "buy",
        "limit_price": "0.6000",
        "quantity": "100.0000",
        "idempotency_key": "order-key-1",
    }
    body.update(overrides)
    return body


async def _market(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    q: Sequence[Decimal] = (Decimal("20.0000"), Decimal("0.0000")),
) -> _Market:
    """A market whose book is open at `b = 100` and `q`, with the gate open."""
    market = _Market()
    await _warm(session, market, q)
    await set_b(session, market.market_id, Decimal("100.0000"))
    _Terms().install(monkeypatch, market)
    return market


# =========================================================================
# Who may call
# =========================================================================
async def test_a_placement_needs_a_token(trade_client, session, monkeypatch) -> None:
    client, _ = trade_client
    market = await _market(session, monkeypatch)

    response = await client.post(_path(market.market_id), json=_body(market))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_my_orders_needs_a_token(trade_client) -> None:
    client, _ = trade_client

    response = await client.get(_MY_ORDERS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# =========================================================================
# The response
# =========================================================================
async def test_a_placement_is_201_with_the_contracts_example(
    trade_client, session, monkeypatch
) -> None:
    """`docs/api/ledger-service.md`'s example, as the frontend will read it:
    every amount a decimal string at scale 4, the versions and the position
    JSON numbers, the two absent figures null."""
    client, recorder = trade_client
    market = await _market(session, monkeypatch)
    user_id = uuid.uuid4()

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(user_id)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"order", "fills", "book_state_version"}
    order = body["order"]
    assert set(order) == _ORDER_FIELDS
    assert {key: order[key] for key in _ORDER_FIELDS - {"id", "created_at"}} == {
        "market_id": str(market.market_id),
        "outcome_id": str(market.outcomes[0]),
        "outcome_position": 0,
        "side": "buy",
        "limit_price": "0.6000",
        "quantity": "100.0000",
        "filled_quantity": "20.5465",
        "average_fill_price": "0.5750",
        "reserved_credits": "48.1848",
        "reserved_shares": None,
        "status": "open",
        "cancelled_at": None,
    }
    (fill,) = body["fills"]
    assert set(fill) == {"transaction_id", "quantity", "total", "state_version"}
    assert (fill["quantity"], fill["total"], fill["state_version"]) == (
        "20.5465",
        "-11.8152",
        1,
    )
    assert body["book_state_version"] == 1
    assert len(recorder.payloads_on("market.price")) == 1


async def test_a_retry_is_201_with_the_same_body(trade_client, session, monkeypatch) -> None:
    """The contract: "A retry with the same key returns `201`", the order as
    it stands now. Nothing has filled it since, so the body is the same."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)
    headers = bearer(uuid.uuid4())

    first = await client.post(_path(market.market_id), json=_body(market), headers=headers)
    retry = await client.post(
        _path(market.market_id), json=_body(market, quantity="100"), headers=headers
    )

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()


@pytest.mark.parametrize(
    "extra",
    [
        {"account_id": str(uuid.uuid4())},
        {"budget": "1.0000"},
        {"total": "-1.0000"},
        {"state_version": 0},
    ],
    ids=["account_id", "budget", "total", "state_version"],
)
async def test_a_body_naming_an_account_an_amount_or_a_quote_is_refused(
    trade_client, session, monkeypatch, extra: dict
) -> None:
    """`extra="forbid"` over exactly five fields: the body names no account and
    no amount, and there is no `state_version` (ADR 0024, "A placement takes
    no `state_version`")."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)

    response = await client.post(
        _path(market.market_id), json=_body(market, **extra), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("limit_price", "status"),
    [
        ("0", 422),
        ("0.0001", 201),
        ("0.9999", 201),
        ("1", 422),
        ("0.00005", 422),
        ("-0.5000", 422),
    ],
    ids=["zero", "a_tick", "a_tick_below_one", "one", "five_decimals", "negative"],
)
async def test_a_limit_price_is_strictly_between_0_and_1_at_four_decimals(
    trade_client, session, monkeypatch, limit_price: str, status: int
) -> None:
    """Both edges of both ends: `> 0` and `< 1`, at most four decimal places,
    a fifth refused rather than rounded."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, limit_price=limit_price, quantity="1.0000"),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == status, response.text


# =========================================================================
# Refusals
# =========================================================================
async def test_order_crosses_book_is_409_with_the_side_and_its_bound(
    trade_client, session, monkeypatch
) -> None:
    """The contract: `error.details` carries `side` and the bound,
    `max_limit_price` for a buy and `min_limit_price` for a sell. One trader
    rests a buy of outcome 1 at 0.4000, which pushes outcome 0 down from 0.6000
    up, and a buy of outcome 0 at 0.3000, which pushes it up from 0.3000 down.
    A buy of outcome 0 must then be below 0.6000, and a sell of outcome 0
    above 0.3000. The crossing check comes before the shares check, so the
    sell is refused for the book, not for holding nothing."""
    client, _ = trade_client
    market = await _market(session, monkeypatch, ZERO_Q)
    resting = bearer(uuid.uuid4())
    for outcome, limit in ((1, "0.4000"), (0, "0.3000")):
        placed = await client.post(
            _path(market.market_id),
            json=_body(
                market,
                outcome_id=str(market.outcomes[outcome]),
                limit_price=limit,
                quantity="10.0000",
                idempotency_key=f"rest-{outcome}",
            ),
            headers=resting,
        )
        assert placed.status_code == 201, placed.text
        assert placed.json()["fills"] == []

    newcomer = bearer(uuid.uuid4())
    buy = await client.post(
        _path(market.market_id),
        json=_body(market, limit_price="0.6000", idempotency_key="cross-buy"),
        headers=newcomer,
    )
    sell = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell", limit_price="0.3000", idempotency_key="cross-sell"),
        headers=newcomer,
    )

    for response in (buy, sell):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "order_crosses_book"
    assert buy.json()["error"]["details"] == {"side": "buy", "max_limit_price": "0.6000"}
    assert sell.json()["error"]["details"] == {"side": "sell", "min_limit_price": "0.3000"}


async def test_insufficient_funds_on_a_placement_is_409_with_shortfall_and_no_required(
    trade_client, session, monkeypatch, starting_credits: Decimal
) -> None:
    """The contract's row for a placement: `balance`, `reserved`, `available`
    and `shortfall`, and no `required`. A first order holds all but 3.0000 of
    the balance; a second whose budget is 5.7614 is 2.7614 short."""
    client, recorder = trade_client
    market = await _market(session, monkeypatch, ZERO_Q)
    headers = bearer(uuid.uuid4())
    held = starting_credits - Decimal("3.0000")
    first = await client.post(
        _path(market.market_id),
        json=_body(
            market, limit_price="0.5000", quantity=str(held * 2), idempotency_key="most"
        ),
        headers=headers,
    )
    assert first.status_code == 201, first.text

    response = await client.post(
        _path(market.market_id),
        json=_body(
            market, limit_price="0.4321", quantity="13.3333", idempotency_key="too-much"
        ),
        headers=headers,
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_funds"
    assert error["details"] == {
        "balance": str(starting_credits.quantize(Decimal("0.0001"))),
        "reserved": str(held.quantize(Decimal("0.0001"))),
        "available": "3.0000",
        "shortfall": "2.7614",
    }
    assert recorder.calls == []


async def test_a_market_with_three_outcomes_is_409_market_not_binary(
    trade_client, session, monkeypatch
) -> None:
    client, _ = trade_client
    market = _Market(outcomes=3)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_binary"


# =========================================================================
# My orders
# =========================================================================
async def test_my_orders_is_200_with_the_callers_live_orders_newest_first(
    trade_client, session, monkeypatch
) -> None:
    """The contract: `{"orders": [...]}`, each in the order object's shape, the
    caller's own and nobody else's."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)
    headers = bearer(uuid.uuid4())
    placed = []
    for key, limit in (("older", "0.4321"), ("newer", "0.5000")):
        response = await client.post(
            _path(market.market_id),
            json=_body(market, limit_price=limit, quantity="13.3333", idempotency_key=key),
            headers=headers,
        )
        placed.append(response.json()["order"]["id"])

    mine = await client.get(_MY_ORDERS, headers=headers)
    theirs = await client.get(_MY_ORDERS, headers=bearer(uuid.uuid4()))

    assert mine.status_code == 200
    assert set(mine.json()) == {"orders"}
    assert [order["id"] for order in mine.json()["orders"]] == list(reversed(placed))
    assert all(set(order) == _ORDER_FIELDS for order in mine.json()["orders"])
    assert theirs.json() == {"orders": []}


# =========================================================================
# The contract at /docs
# =========================================================================
async def test_the_placement_declares_the_contracts_responses(trade_client) -> None:
    client, _ = trade_client
    schema = (await client.get("/openapi.json")).json()
    responses = schema["paths"]["/ledger/markets/{market_id}/orders"]["post"]["responses"]

    assert set(responses) == {"201", "401", "403", "404", "409", "422", "500", "503"}
    for status, codes in _CONTRACT_CODES.items():
        for code in codes:
            assert f"`{code}`" in responses[status]["description"], (status, code)
    serialised = json.dumps(responses).lower()
    for unreachable in ("quote_stale", "cost_below_tick", "proceeds_below_tick"):
        assert unreachable not in serialised


def test_the_docs_error_table_for_a_placement_is_what_the_route_answers() -> None:
    """Every row of the placement's table and no other; `invalid_request` is
    FastAPI's, declared through the envelope."""
    rows = _table_rows(_section(_doc(), _ORDER_HEADING).split("\n### ", 1)[0])

    expected = {(status, code) for status, codes in _CONTRACT_CODES.items() for code in codes}
    assert rows == expected | {("422", "invalid_request")}


# =========================================================================
# Cancelling
# =========================================================================
def _cancel_path(order_id: object) -> str:
    return f"/ledger/orders/{order_id}/cancel"


async def _placed(client, market: _Market, headers: dict[str, str], **overrides) -> dict:
    response = await client.post(
        _path(market.market_id), json=_body(market, **overrides), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


async def test_a_cancel_needs_a_token(trade_client) -> None:
    client, _ = trade_client

    response = await client.post(_cancel_path(uuid.uuid4()))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_cancel_is_200_with_the_order_and_what_it_released(
    trade_client, session, monkeypatch
) -> None:
    """The contract's response: the order object as cancelled, and what the
    cancel released as a decimal string, the other figure null. The
    contract's example order spent 11.8152 of 60.0000 on arrival."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)
    headers = bearer(uuid.uuid4())
    order = await _placed(client, market, headers)

    response = await client.post(_cancel_path(order["id"]), headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"order", "released_credits", "released_shares"}
    assert (body["released_credits"], body["released_shares"]) == ("48.1848", None)
    cancelled = body["order"]
    assert set(cancelled) == _ORDER_FIELDS
    assert (cancelled["id"], cancelled["status"], cancelled["reserved_credits"]) == (
        order["id"],
        "cancelled",
        "0.0000",
    )
    assert cancelled["filled_quantity"] == "20.5465"
    assert cancelled["cancelled_at"] is not None


async def test_a_cancel_of_another_traders_order_is_404_order_not_found(
    trade_client, session, monkeypatch
) -> None:
    client, _ = trade_client
    market = await _market(session, monkeypatch)
    order = await _placed(client, market, bearer(uuid.uuid4()))

    response = await client.post(_cancel_path(order["id"]), headers=bearer(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"


@pytest.mark.parametrize(
    ("order_id", "body"),
    [("not-a-uuid", None), (None, {"released_credits": "1.0000"})],
    ids=["order_id_not_a_uuid", "body_with_a_field"],
)
async def test_a_cancel_with_a_bad_id_or_a_body_is_422_invalid_request(
    trade_client, session, monkeypatch, order_id: str | None, body: dict | None
) -> None:
    """The contract: "`order_id` is not a UUID, or the body is not empty". A
    body naming an amount is refused, not ignored: a cancel always releases
    the whole remainder."""
    client, _ = trade_client
    market = await _market(session, monkeypatch, ZERO_Q)
    headers = bearer(uuid.uuid4())
    if order_id is None:
        resting = await _placed(client, market, headers, limit_price="0.4000", quantity="10.0000")
        order_id = resting["id"]

    response = await client.post(_cancel_path(order_id), json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "code", ["order_already_filled", "order_already_cancelled", "order_released"]
)
async def test_a_cancel_of_an_order_that_has_ended_is_409_with_its_code(
    trade_client, session, monkeypatch, code: str
) -> None:
    """The contract's three 409s. Five shares at 0.6000 fill completely on
    arrival; a retried cancel is `order_already_cancelled`; a market with a
    result has released the order already."""
    client, _ = trade_client
    market = await _market(session, monkeypatch)
    headers = bearer(uuid.uuid4())
    quantity = "5.0000" if code == "order_already_filled" else "100.0000"
    order = await _placed(client, market, headers, quantity=quantity)
    if code == "order_already_cancelled":
        first = await client.post(_cancel_path(order["id"]), headers=headers)
        assert first.status_code == 200, first.text
    if code == "order_released":
        await record_result(session, market)

    response = await client.post(_cancel_path(order["id"]), headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code


async def test_a_suspended_trader_can_cancel_on_a_closed_market(
    trade_client, app_under_test, session, monkeypatch
) -> None:
    """The contract: "No account check and no market gate: a suspended account
    can still cancel while its token is valid, and a closed market's orders
    can be cancelled." Placed while both were open; cancelled after auth
    says suspended and market_service says closed."""
    from controller.dependencies import get_auth_client  # noqa: PLC0415

    client, _ = trade_client
    market = await _market(session, monkeypatch, ZERO_Q)
    headers = bearer(uuid.uuid4())
    order = await _placed(client, market, headers, limit_price="0.4000", quantity="10.0000")
    _Terms(status="closed").install(monkeypatch, market)

    def suspended(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "account_suspended", "message": "Suspended."}}
        )

    async with auth_client_over(httpx.MockTransport(suspended)) as auth_client:
        app_under_test.dependency_overrides[get_auth_client] = lambda: auth_client
        response = await client.post(_cancel_path(order["id"]), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["order"]["status"] == "cancelled"


async def test_the_cancel_declares_the_contracts_responses(trade_client) -> None:
    client, _ = trade_client
    schema = (await client.get("/openapi.json")).json()
    responses = schema["paths"]["/ledger/orders/{order_id}/cancel"]["post"]["responses"]

    assert set(responses) == {"200", "401", "404", "409", "422"}
    for status, codes in _CANCEL_CODES.items():
        for code in codes:
            assert f"`{code}`" in responses[status]["description"], (status, code)


def test_the_docs_error_table_for_a_cancel_is_what_the_route_answers() -> None:
    rows = _table_rows(_section(_doc(), _CANCEL_HEADING).split("\n### ", 1)[0])

    expected = {(status, code) for status, codes in _CANCEL_CODES.items() for code in codes}
    assert rows == expected | {("422", "invalid_request")}
