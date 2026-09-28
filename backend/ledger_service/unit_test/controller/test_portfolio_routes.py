"""The portfolio on the wire. [T-4] #24

What the controller owns: who may call `GET /ledger/portfolio/me`, the exact
keys it returns, every amount, quantity and price as a decimal string at
scale 4, and that the body is the service's result and nothing else. The
figures are `unit_test/service/test_portfolio.py`'s.

Holdings are made by real buys through the service on a book opened at zero,
then read through the route: ADR 0018's 500 YES at b = 100.
"""

from __future__ import annotations

import re
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer
from unit_test.controller.test_sell_routes import _doc, _prose, _section
from unit_test.portfolio_fixtures import (
    ADR_AVERAGE,
    ADR_B,
    ADR_BASIS,
    ADR_PNL,
    ADR_PRICE,
    ADR_QUANTITY,
    ADR_VALUE,
    PORTFOLIO_FIELDS,
    POSITION_FIELDS,
    funded,
    market_at,
    read_portfolio,
)
from unit_test.sell_fixtures import hold

PATH = "/ledger/portfolio/me"
_HEADING = "## GET /ledger/portfolio/me"
_SCALE_FOUR = re.compile(r"^-?\d+\.\d{4}$")
_AMOUNTS = ("balance", "positions_value", "net_worth")
_POSITION_AMOUNTS = (
    "quantity",
    "cost_basis",
    "average_entry_price",
    "price",
    "value",
    "unrealized_pnl",
)


async def _adr_holder(session: AsyncSession):
    upstream = await market_at(session, ADR_B)
    user_id, credits = await funded(session)
    await hold(session, upstream, user_id=user_id, quantity=ADR_QUANTITY)
    return upstream, user_id, credits


async def test_an_anonymous_caller_is_refused(client: AsyncClient) -> None:
    response = await client.get(PATH)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_the_route_returns_the_figures_as_scale_four_strings(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Exactly the agreed keys at both levels, so a label, status or name
    field is a failure. Every amount, quantity and price a string at scale 4;
    `outcome_position` and `state_version` integers."""
    upstream, user_id, credits = await _adr_holder(session)

    response = await client.get(PATH, headers=bearer(user_id, UserRole.TRADER))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == PORTFOLIO_FIELDS
    assert body["user_id"] == str(user_id)
    balance = credits - ADR_BASIS
    assert body["balance"] == str(balance)
    assert body["positions_value"] == str(ADR_VALUE)
    assert body["net_worth"] == str(balance + ADR_VALUE)
    for key in _AMOUNTS:
        assert _SCALE_FOUR.match(body[key]), f"{key} = {body[key]!r}"

    assert len(body["positions"]) == 1
    position = body["positions"][0]
    assert set(position) == POSITION_FIELDS
    assert position == {
        "market_id": str(upstream.market_id),
        "outcome_id": str(upstream.outcomes[0]),
        "outcome_position": 0,
        "quantity": "500.0000",
        "cost_basis": str(ADR_BASIS),
        "average_entry_price": str(ADR_AVERAGE),
        "price": str(ADR_PRICE),
        "value": str(ADR_VALUE),
        "unrealized_pnl": str(ADR_PNL),
        "state_version": 1,
    }
    for key in _POSITION_AMOUNTS:
        assert _SCALE_FOUR.match(position[key]), f"{key} = {position[key]!r}"


async def test_the_response_is_the_service_result(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Two markets and two outcomes, so the route cannot pass by rebuilding
    one row. The service is read straight after, against the same state."""
    upstream, user_id, _ = await _adr_holder(session)
    await hold(session, upstream, user_id=user_id, quantity=Decimal("41.0000"), outcome=1)
    second = await market_at(session)
    await hold(session, second, user_id=user_id)

    response = await client.get(PATH, headers=bearer(user_id, UserRole.TRADER))
    result = await read_portfolio(session, user_id)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user_id": str(result.user_id),
        "account_id": str(result.account_id),
        "balance": str(result.balance),
        "positions_value": str(result.positions_value),
        "net_worth": str(result.net_worth),
        "positions": [
            {
                "market_id": str(p.market_id),
                "outcome_id": str(p.outcome_id),
                "outcome_position": p.outcome_position,
                "quantity": str(p.quantity),
                "cost_basis": str(p.cost_basis),
                "average_entry_price": str(p.average_entry_price),
                "price": str(p.price),
                "value": str(p.value),
                "unrealized_pnl": str(p.unrealized_pnl),
                "state_version": p.state_version,
            }
            for p in result.positions
        ],
    }
    assert len(result.positions) == 3


async def test_each_caller_reads_only_their_own_portfolio(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The route takes no user id: the token's `sub` is the user. Two holders
    in two markets each see their own market and not the other's."""
    first, first_id, _ = await _adr_holder(session)
    second = await market_at(session)
    second_id, _ = await funded(session)
    await hold(session, second, user_id=second_id)

    for user_id, market in ((first_id, first), (second_id, second)):
        response = await client.get(PATH, headers=bearer(user_id, UserRole.TRADER))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_id"] == str(user_id)
        assert [p["market_id"] for p in body["positions"]] == [str(market.market_id)]


def test_the_docs_document_the_route_and_say_value_is_not_quantity_times_price() -> None:
    doc = _doc()
    assert _HEADING in doc, f"`docs/api/ledger-service.md` has no `{_HEADING}`"
    section = _section(doc, _HEADING)

    says_it = [
        p
        for p in _prose(section)
        if "quantity × price" in p and re.search(r"\bnot\b", p)
    ]
    assert says_it, "the section never says that `value` is not `quantity × price`"
    assert "market_book_incomplete" in section
