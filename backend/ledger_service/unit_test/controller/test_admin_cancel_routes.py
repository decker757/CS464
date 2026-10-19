"""An administrator's cancel on the wire. [T-8] #326, ADR 0024.

What the controller owns for `POST /ledger/admin/orders/{order_id}/cancel`:
who may call it, what the body must carry and how long it may be, the status
code of each refusal, and the contract in `docs/api/ledger-service.md`.
Which orders it cancels, the reason's floor and the audit entry are
`unit_test/service/test_admin_cancel_order.py`'s.

Built on `test_order_routes.py`'s market and placement helpers. The
contract's example order, a buy of 100 at 0.6000 on `b = 100` at
`q = [20, 0]`, fills 20.5465 on arrival and rests holding 48.1848.
"""

from __future__ import annotations

import uuid

import pytest

from core.roles import UserRole
from unit_test.conftest import bearer
from unit_test.controller.test_order_routes import (
    _cancel_path,
    _market,
    _placed,
)
from unit_test.controller.test_sell_routes import _doc, _section, _table_rows
from unit_test.controller.test_trade_routes import trade_client  # noqa: F401 - fixture

_HEADING = "### POST /ledger/admin/orders/{order_id}/cancel"
_REASON = "The owner is suspended; releasing what the order holds."

# The contract's error table, by status.
_CODES = {
    "401": ["invalid_token"],
    "403": ["not_an_administrator"],
    "404": ["order_not_found"],
    "409": ["order_already_filled", "order_already_cancelled", "order_released"],
    "422": ["invalid_request", "cancellation_incomplete"],
}


def _path(order_id: object) -> str:
    return f"/ledger/admin/orders/{order_id}/cancel"


def _admin() -> dict[str, str]:
    return bearer(uuid.uuid4(), UserRole.ADMIN)


async def _resting_example(client, session, monkeypatch) -> tuple[dict, dict[str, str]]:
    """The contract's example order and its owner's headers."""
    market = await _market(session, monkeypatch)
    owner = bearer(uuid.uuid4())
    return await _placed(client, market, owner), owner


async def _status_of(client, order_id: str, owner: dict[str, str]) -> str | None:
    """The order's status in its owner's live orders, None once it has left."""
    orders = (await client.get("/ledger/orders/me", headers=owner)).json()["orders"]
    return next((order["status"] for order in orders if order["id"] == order_id), None)


# =========================================================================
# Who may call
# =========================================================================
async def test_an_administrator_cancels_a_traders_order_with_the_owners_response(
    trade_client, session, monkeypatch
) -> None:
    """#326: "An administrator's token can cancel any order". The response is
    the owner's cancel's, and the owner's balance reserves nothing after it.
    No frame is published: a cancel sends none, whoever sends it."""
    client, recorder = trade_client
    order, owner = await _resting_example(client, session, monkeypatch)
    recorder.calls.clear()

    response = await client.post(_path(order["id"]), json={"reason": _REASON}, headers=_admin())

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"order", "released_credits", "released_shares"}
    assert (body["released_credits"], body["released_shares"]) == ("48.1848", None)
    assert (body["order"]["id"], body["order"]["status"]) == (order["id"], "cancelled")
    balance = (await client.get("/ledger/balances/me", headers=owner)).json()
    assert balance["reserved"] == "0.0000"
    assert recorder.calls == []


async def test_an_admin_cancel_needs_a_token(trade_client) -> None:
    client, _ = trade_client

    response = await client.post(_path(uuid.uuid4()), json={"reason": _REASON})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_is_403_not_an_administrator_even_for_their_own_order(
    trade_client, session, monkeypatch
) -> None:
    """The owner's own token, with a valid reason: only the role can be what
    refuses it, and the order stays live."""
    client, _ = trade_client
    order, owner = await _resting_example(client, session, monkeypatch)

    response = await client.post(_path(order["id"]), json={"reason": _REASON}, headers=owner)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"
    assert await _status_of(client, order["id"], owner) == "open"


# =========================================================================
# The body
# =========================================================================
@pytest.mark.parametrize(
    "body",
    [None, {}, {"reason": _REASON, "released_credits": "1.0000"}],
    ids=["no_body", "no_reason", "a_field_beside_the_reason"],
)
async def test_a_body_without_a_reason_or_with_another_field_is_422_invalid_request(
    trade_client, session, monkeypatch, body: dict | None
) -> None:
    """#326: "A reason is required". A field beside it is refused, not
    ignored, as on the owner's cancel."""
    client, _ = trade_client
    order, owner = await _resting_example(client, session, monkeypatch)

    response = await client.post(_path(order["id"]), json=body, headers=_admin())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert await _status_of(client, order["id"], owner) == "open"


async def test_a_blank_reason_is_422_cancellation_incomplete(
    trade_client, session, monkeypatch
) -> None:
    client, _ = trade_client
    order, owner = await _resting_example(client, session, monkeypatch)

    response = await client.post(_path(order["id"]), json={"reason": "   "}, headers=_admin())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cancellation_incomplete"
    assert await _status_of(client, order["id"], owner) == "open"


@pytest.mark.parametrize(
    ("length", "status"), [(500, 200), (501, 422)], ids=["500_accepted", "501_refused"]
)
async def test_a_reason_may_be_500_characters_and_no_more(
    trade_client, session, monkeypatch, length: int, status: int
) -> None:
    """The suspension's ceiling, both edges."""
    client, _ = trade_client
    order, _ = await _resting_example(client, session, monkeypatch)

    response = await client.post(
        _path(order["id"]), json={"reason": "r" * length}, headers=_admin()
    )

    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["error"]["code"] == "invalid_request"


async def test_an_order_the_owner_already_cancelled_is_409_order_already_cancelled(
    trade_client, session, monkeypatch
) -> None:
    """The owner's refusal, at the wire: the admin route maps it the same way."""
    client, _ = trade_client
    order, owner = await _resting_example(client, session, monkeypatch)
    first = await client.post(_cancel_path(order["id"]), headers=owner)
    assert first.status_code == 200, first.text

    response = await client.post(_path(order["id"]), json={"reason": _REASON}, headers=_admin())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "order_already_cancelled"


# =========================================================================
# The contract
# =========================================================================
async def test_the_admin_cancel_declares_the_contracts_responses(trade_client) -> None:
    client, _ = trade_client
    schema = (await client.get("/openapi.json")).json()
    responses = schema["paths"]["/ledger/admin/orders/{order_id}/cancel"]["post"]["responses"]

    assert set(responses) == {"200", *_CODES}
    for status, codes in _CODES.items():
        for code in codes:
            assert f"`{code}`" in responses[status]["description"], (status, code)


def test_the_docs_error_table_for_the_admin_cancel_is_what_the_route_answers() -> None:
    rows = _table_rows(_section(_doc(), _HEADING).split("\n### ", 1)[0])

    assert rows == {(status, code) for status, codes in _CODES.items() for code in codes}
