"""Who may read a market's trade feed, and its shape on the wire. [BE][2.4] #63

`GET /ledger/markets/{market_id}/trades`, docs/api/ledger-service.md. Which
entries are trades, their order and the keyset paging are
`service/test_trade_feed.py`'s. What this file owns: the admin guard
(DECISIONS.md, "Price history takes any valid token; the trade feed takes an
administrator's"), the page size and cursor reaching the service, every
field as the trader's own history and the trade result give it, the cold
market's empty `200`, and a read that writes nothing.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.roles import UserRole
from unit_test.conftest import bearer
from unit_test.history_fixtures import BUY_AVERAGE, SELL_AVERAGE
from unit_test.sell_fixtures import PROCEEDS, footprint
from unit_test.trade_feed_fixtures import feed
from unit_test.trade_fixtures import (
    QUANTUM,
    accounts_module,
    entities,
    grants,
    ledger_counts,
    session_factory,
    transaction_count,
)

_SCALE_FOUR = re.compile(r"^-?\d+\.\d{4}$")
_PAGE_FIELDS = {"trades", "next_cursor", "has_more"}
_ROW_FIELDS = {
    "transaction_id",
    "created_at",
    "user_id",
    "outcome_id",
    "side",
    "quantity",
    "average_price",
    "total",
    "state_version",
}
_MONEY_FIELDS = ("quantity", "average_price", "total")


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/trades"


async def _trades(
    client: AsyncClient, market_id: uuid.UUID, headers: dict[str, str], query: str = ""
) -> dict:
    response = await client.get(f"{_path(market_id)}{query}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _own_rows_by_transaction(client: AsyncClient, user_id: uuid.UUID) -> dict:
    response = await client.get(
        "/ledger/entries/me", headers=bearer(user_id, UserRole.TRADER)
    )
    assert response.status_code == 200, response.text
    return {row["transaction_id"]: row for row in response.json()["entries"]}


async def _account_count() -> int:
    async with session_factory()() as fresh:
        return (
            await fresh.execute(select(func.count()).select_from(entities().Account))
        ).scalar_one()


async def _book_count(market_id: uuid.UUID) -> int:
    book = entities().MarketBook
    async with session_factory()() as fresh:
        return (
            await fresh.execute(
                select(func.count()).select_from(book).where(book.market_id == market_id)
            )
        ).scalar_one()


# =========================================================================
# The guard
# =========================================================================
async def test_an_anonymous_caller_is_refused(client: AsyncClient) -> None:
    """Catches: no auth dependency on the route."""
    response = await client.get(_path(uuid.uuid4()))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_an_expired_token_is_refused(client: AsyncClient) -> None:
    """An admin's token, so only its expiry can be what refuses it. Catches:
    expiry not verified."""
    response = await client.get(
        _path(uuid.uuid4()),
        headers=bearer(uuid.uuid4(), UserRole.ADMIN, expires_in=-1),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_is_refused_with_not_an_administrator(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """A valid trader token, so a 401 cannot be what answers. Catches:
    `CurrentUser` instead of `CurrentAdmin`."""
    response = await client.get(_path(uuid.uuid4()), headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


# =========================================================================
# The request
# =========================================================================
async def test_a_malformed_market_id_is_invalid_request_naming_the_field(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Catches: `market_id` typed `str`."""
    response = await client.get(
        "/ledger/markets/not-a-uuid/trades", headers=admin_headers
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert ["path", "market_id"] in [item["loc"] for item in error["details"]]


async def test_a_zero_limit_is_refused(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Catches: `limit` without `ge=1`, serving a page of nothing."""
    response = await client.get(f"{_path(uuid.uuid4())}?limit=0", headers=admin_headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert ["query", "limit"] in [item["loc"] for item in error["details"]]


async def test_a_cursor_we_did_not_issue_is_a_400(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """On a market with trades, so the answer cannot come from the cold
    path. Catches: the cursor not decoded through `core.paging`."""
    scenario = await feed(session)

    response = await client.get(
        f"{_path(scenario.m1.market_id)}?cursor=nonsense", headers=admin_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_cursor"


async def test_an_oversized_limit_is_clamped_not_refused(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """"Clamped at 200. Asking for more is not an error." Catches: a `le=`
    bound on `limit`, which answers 422."""
    response = await client.get(
        f"{_path(uuid.uuid4())}?limit=100000", headers=admin_headers
    )

    assert response.status_code == 200


async def test_the_page_size_defaults_and_is_clamped_by_the_server_settings(
    client: AsyncClient,
    session: AsyncSession,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settings `/entries/me` pages by, made small enough that M1's
    three trades tell a default and a ceiling from no limit at all.
    Catches: the route bypassing `_page_size`, so no `limit` serves every
    row or an oversized one serves more than the ceiling."""
    scenario = await feed(session)
    monkeypatch.setattr(get_settings(), "default_page_size", 1)
    monkeypatch.setattr(get_settings(), "max_page_size", 2)

    by_default = await _trades(client, scenario.m1.market_id, admin_headers)
    oversized = await _trades(
        client, scenario.m1.market_id, admin_headers, "?limit=100000"
    )

    assert len(by_default["trades"]) == 1
    assert by_default["has_more"] is True
    assert len(oversized["trades"]) == 2
    assert oversized["has_more"] is True


async def test_page_two_continues_from_page_one_s_cursor(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Catches: a controller that drops `cursor`, so page 2 is page 1 again."""
    scenario = await feed(session)
    market_id = scenario.m1.market_id

    page_one = await _trades(client, market_id, admin_headers, "?limit=1")
    page_two = await _trades(
        client,
        market_id,
        admin_headers,
        f"?limit=1&cursor={page_one['next_cursor']}",
    )

    assert page_one["next_cursor"] is not None
    first = [row["transaction_id"] for row in page_one["trades"]]
    second = [row["transaction_id"] for row in page_two["trades"]]
    assert first == [str(scenario.b_buy.transaction_id)]
    assert second == [str(scenario.a_sell.transaction_id)]


# =========================================================================
# The rows
# =========================================================================
async def test_every_field_is_the_trader_s_own_row_and_the_trade_result_s(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Expected values come from the trade results and the trader's own
    `/entries/me` row, never from the code under test. A's buy and sell are
    #23's worked example, so their averages are pinned too, and the sell,
    sent as `"10"`, shows `"10.0000"`. Catches: a second row builder;
    an unquantized quantity; `total` with the pool's sign; `state_version`
    read from the book rather than the trade; `user_id` from the pool or
    the reader."""
    scenario = await feed(session)

    trades = (await _trades(client, scenario.m1.market_id, admin_headers))["trades"]
    feed_rows = {row["transaction_id"]: row for row in trades}
    own_rows = await _own_rows_by_transaction(client, scenario.trader_a)
    own_rows |= await _own_rows_by_transaction(client, scenario.trader_b)

    assert len(trades) == 3
    for result in scenario.m1_newest_first:
        transaction_id = str(result.transaction_id)
        row = feed_rows[transaction_id]
        own = own_rows[transaction_id]
        assert row["user_id"] == str(result.user_id)
        assert row["outcome_id"] == str(result.outcome_id)
        assert row["total"] == str(result.total.quantize(QUANTUM))
        assert row["state_version"] == result.state_version
        for field in ("created_at", "side", "quantity", "average_price"):
            assert row[field] == own[field], (transaction_id, field)

    a_buy = feed_rows[str(scenario.a_buy.transaction_id)]
    a_sell = feed_rows[str(scenario.a_sell.transaction_id)]
    b_buy = feed_rows[str(scenario.b_buy.transaction_id)]
    assert (a_buy["side"], a_buy["quantity"], a_buy["average_price"]) == (
        "buy",
        "54.3333",
        str(BUY_AVERAGE),
    )
    assert (a_sell["side"], a_sell["quantity"], a_sell["average_price"]) == (
        "sell",
        "10.0000",
        str(SELL_AVERAGE),
    )
    assert a_sell["total"] == str(PROCEEDS)
    assert Decimal(a_buy["total"]) < 0
    assert Decimal(b_buy["total"]) < 0


async def test_the_page_is_exactly_the_documented_fields_as_strings_and_an_int(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Catches: a Decimal or float on the wire; `balance_after` or another
    history field leaking into a row; a dropped field."""
    scenario = await feed(session)

    body = await _trades(client, scenario.m1.market_id, admin_headers)

    assert set(body) == _PAGE_FIELDS
    assert len(body["trades"]) == 3
    for row in body["trades"]:
        assert set(row) == _ROW_FIELDS
        for field in ("side", "created_at", *_MONEY_FIELDS):
            assert type(row[field]) is str, field
        for field in _MONEY_FIELDS:
            assert _SCALE_FOUR.match(row[field]), f"{field} = {row[field]!r}"
        assert row["side"] in {"buy", "sell"}
        assert row["created_at"].endswith(("Z", "+00:00"))
        assert type(row["state_version"]) is int


# =========================================================================
# Reads that write nothing
# =========================================================================
async def test_an_unknown_market_is_an_empty_200_and_opens_no_book(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """"A market with no book is an empty `200`… An id that names no market
    reads the same way." The fixture's terms client is the real one, so a
    call to market_service would fail the request. Catches: a 404; opening a
    book or calling market_service on the read."""
    market_id = uuid.uuid4()

    body = await _trades(client, market_id, admin_headers)

    assert body == {"trades": [], "next_cursor": None, "has_more": False}
    assert await _book_count(market_id) == 0
    async with session_factory()() as fresh:
        assert await ledger_counts(fresh) == (0, 0)


async def test_the_read_writes_nothing_and_grants_the_admin_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    """DECISIONS.md, "Price history takes any valid token…": neither route
    can mint a grant or open a book. Catches: `ensure_granted` or
    `ensure_open` on this path."""
    scenario = await feed(session)
    market_id = scenario.m1.market_id
    admin_id = uuid.uuid4()
    async with session_factory()() as fresh:
        before = await footprint(fresh, market_id)
    accounts_before = await _account_count()

    body = await _trades(client, market_id, bearer(admin_id, UserRole.ADMIN))

    assert len(body["trades"]) == 3
    async with session_factory()() as fresh:
        assert await footprint(fresh, market_id) == before
        assert await transaction_count(fresh, key=grants().grant_key(admin_id)) == 0
        assert (
            await accounts_module().find(fresh, entities().AccountKind.USER, admin_id)
            is None
        )
    assert await _account_count() == accounts_before
