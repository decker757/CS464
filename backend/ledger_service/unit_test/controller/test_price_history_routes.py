"""Who may read a market's price history, and its shape on the wire. [BE][2.4] #63

`GET /ledger/markets/{market_id}/price-history`, docs/api/ledger-service.md.
What the points are, the window and the result are
`service/test_market_price_history.py`'s and `core/test_price_history.py`'s.
What this file owns: the guard (DECISIONS.md, "Price history takes any valid
token; the trade feed takes an administrator's"), the query's validation,
each refusal's status and code, the points agreeing with the snapshot and the
trade feed on the wire, the cold market's empty `200`, and a read that writes
nothing.

The terms client is the production one (`app_under_test`), so a call to
market_service from this read fails the request instead of passing silently.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer, drop_outcome_row, set_b, strip_outcomes
from unit_test.price_history_fixtures import gapped, result_row_of, traded
from unit_test.sell_fixtures import footprint, open_at_zero
from unit_test.trade_feed_fixtures import settled_feed
from unit_test.trade_fixtures import (
    Upstream,
    accounts_module,
    capture_sql,
    entities,
    grants,
    ledger_counts,
    session_factory,
    transaction_count,
    writes,
)

_SCALE_FOUR = re.compile(r"^\d+\.\d{4}$")
_BODY_FIELDS = {"market_id", "points", "result", "winning_outcome_id", "result_recorded_at"}
_POINT_FIELDS = {"state_version", "occurred_at", "prices"}
_PRICE_FIELDS = {"outcome_id", "position", "price"}


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/price-history"


async def _history(
    client: AsyncClient,
    market_id: uuid.UUID,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
) -> dict:
    response = await client.get(_path(market_id), headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _locs(response) -> list[list[str]]:
    return [item["loc"] for item in response.json()["error"]["details"]]


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


async def _break_book(session: AsyncSession, market_id: uuid.UUID, how: str) -> None:
    """The three books `book_prices.refuse_unpriceable` refuses. No outcome
    rows is the snapshot's own case (`strip_outcomes`); one row and a `b` of
    zero come from conftest.py's `drop_outcome_row` and `set_b`. No CHECK on
    `liquidity_b` refuses the zero. Committed."""
    if how == "no_outcomes":
        await strip_outcomes(session, market_id)
    elif how == "one_outcome":
        await drop_outcome_row(session, market_id, 1)
    else:
        await set_b(session, market_id, Decimal(how))


# =========================================================================
# The guard
# =========================================================================
async def test_a_trader_may_read_the_price_history(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """"Any valid access token, any role." Catches: `CurrentAdmin` in place of
    `CurrentUser`."""
    response = await client.get(_path(uuid.uuid4()), headers=trader_headers)

    assert response.status_code == 200, response.text


async def test_an_anonymous_caller_is_refused(client: AsyncClient) -> None:
    """Catches: no auth dependency on the route."""
    response = await client.get(_path(uuid.uuid4()))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# =========================================================================
# The request
# =========================================================================
@pytest.mark.parametrize("field", ["from", "to"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2026-10-09T00:00:00", id="no-offset"),
        pytest.param("2026-10-09", id="date-only"),
        pytest.param("not-a-time", id="not-a-time"),
    ],
)
async def test_a_time_without_an_offset_is_invalid_request_naming_the_field(
    client: AsyncClient, trader_headers: dict[str, str], field: str, value: str
) -> None:
    """One parameter alone, so nothing else can refuse. Catches: `datetime`
    in place of an aware datetime, accepting a naive time as some zone's."""
    response = await client.get(
        _path(uuid.uuid4()), headers=trader_headers, params={field: value}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert ["query", field] in _locs(response)


@pytest.mark.parametrize("field", ["from", "to"])
async def test_a_raw_plus_in_the_offset_is_invalid_request(
    client: AsyncClient, trader_headers: dict[str, str], field: str
) -> None:
    """"A raw `+` decodes to a space and the time is refused." Sent as a
    literal URL, and the first assertion proves the `+` left the client
    unencoded, so the case is the one the docs describe. Catches: a parser
    that reads the space as an offset separator."""
    url = f"{_path(uuid.uuid4())}?{field}=2026-10-09T00:00:00+08:00"

    response = await client.get(url, headers=trader_headers)

    assert b"+08:00" in response.request.url.query
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert ["query", field] in _locs(response)


async def test_a_market_id_that_is_not_a_uuid_is_invalid_request(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """Catches: `market_id` typed `str`."""
    response = await client.get(
        "/ledger/markets/not-a-uuid/price-history", headers=trader_headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert ["path", "market_id"] in _locs(response)


async def test_from_later_than_to_is_invalid_request_naming_both(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """Both times valid on their own, so only their order can refuse them.
    Catches: no ordering check."""
    response = await client.get(
        _path(uuid.uuid4()),
        headers=trader_headers,
        params={"from": "2026-10-09T00:00:01Z", "to": "2026-10-09T00:00:00Z"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    locs = _locs(response)
    assert ["query", "from"] in locs
    assert ["query", "to"] in locs


async def test_the_same_instant_in_two_offsets_is_accepted(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """`from` equal to `to` is not later than it. `from` goes through
    `params=`, so it arrives as `%2B08:00`: the one route-level proof that an
    offset other than `Z` is accepted. Catches: the ordering check written
    `>=`; an offset compared by wall clock rather than by instant."""
    response = await client.get(
        _path(uuid.uuid4()),
        headers=trader_headers,
        params={"from": "2026-10-09T08:00:00+08:00", "to": "2026-10-09T00:00:00Z"},
    )

    assert b"%2B08" in response.request.url.query
    assert response.status_code == 200, response.text


# =========================================================================
# The points on the wire
# =========================================================================
async def test_the_last_point_is_the_snapshot_route_s_prices_and_state_version(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The snapshot prices the stored `q`; the history replays the trades.
    Buys on both outcomes and a sell. Not `occurred_at`: the docs say the two
    can differ. Catches: a trade's side taken from the pool leg's sign, which
    is the trader's inverted, or every trade replayed as a buy."""
    scenario = await traded(session)

    history = await _history(client, scenario.market_id, trader_headers)
    snapshot = await client.get(
        f"/ledger/markets/{scenario.market_id}/snapshot", headers=trader_headers
    )

    assert snapshot.status_code == 200, snapshot.text
    last = history["points"][-1]
    assert last["state_version"] == snapshot.json()["state_version"] == 3
    assert last["prices"] == snapshot.json()["prices"]


async def test_each_trade_feed_row_s_created_at_is_its_point_s_occurred_at(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """docs/api/ledger-service.md: a feed row's `created_at` "equals the
    matching price-history point's `occurred_at`". Matched on
    `state_version`, compared as instants. Catches: points stamped with the
    book's `state_changed_at` or the read's clock rather than the entry's
    `created_at`."""
    scenario = await traded(session)

    history = await _history(client, scenario.market_id, trader_headers)
    feed = await client.get(
        f"/ledger/markets/{scenario.market_id}/trades", headers=admin_headers
    )

    assert feed.status_code == 200, feed.text
    created_at = {
        row["state_version"]: datetime.fromisoformat(row["created_at"])
        for row in feed.json()["trades"]
    }
    occurred_at = {
        point["state_version"]: datetime.fromisoformat(point["occurred_at"])
        for point in history["points"]
    }
    assert sorted(created_at) == [1, 2, 3]
    for state_version, when in created_at.items():
        assert occurred_at[state_version] == when, state_version


async def test_every_price_names_its_outcome_and_position_in_position_order(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The market's outcome ids run high to low by position, so an order by
    id is the reverse of the order by position. Catches: outcome rows read
    without `ORDER BY position`."""
    scenario = await traded(session)

    history = await _history(client, scenario.market_id, trader_headers)

    expected_ids = [str(outcome_id) for outcome_id in scenario.upstream.outcomes]
    assert len(history["points"]) == 4
    for point in history["points"]:
        assert [price["position"] for price in point["prices"]] == [0, 1]
        assert [price["outcome_id"] for price in point["prices"]] == expected_ids
        for price in point["prices"]:
            assert type(price["position"]) is int
            assert type(price["price"]) is str
            assert _SCALE_FOUR.match(price["price"]), price["price"]


async def test_a_point_is_a_price_frame_without_type_and_market_id(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """"A point is a `price` socket frame without `type` and `market_id`."
    Nothing generated checks that. Catches: a point carrying `market_id`,
    `type` or a bucket's fields."""
    scenario = await traded(session)

    body = await _history(client, scenario.market_id, trader_headers)

    assert set(body) == _BODY_FIELDS
    for point in body["points"]:
        assert set(point) == _POINT_FIELDS
        assert type(point["state_version"]) is int
        assert point["occurred_at"].endswith(("Z", "+00:00"))
        for price in point["prices"]:
            assert set(price) == _PRICE_FIELDS


async def test_a_settled_market_s_result_is_on_the_wire_beside_its_points(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The result's three fields as JSON: `result` the string `settled`, the
    winner an id string, the time an aware instant, each equal to
    `ledger.market_results`' own row read from a fresh session. The series
    still ends at the last trade. Catches: the route building the response
    without the result fields."""
    scenario = await settled_feed(session)
    market_id = scenario.upstream.market_id
    async with session_factory()() as fresh:
        winner, recorded_at = await result_row_of(fresh, market_id)

    body = await _history(client, market_id, trader_headers)

    assert type(body["result"]) is str
    assert body["result"] == "settled"
    assert type(body["winning_outcome_id"]) is str
    assert body["winning_outcome_id"] == str(winner)
    result_recorded_at = datetime.fromisoformat(body["result_recorded_at"])
    assert result_recorded_at.tzinfo is not None
    assert result_recorded_at == recorded_at
    assert body["points"][-1]["state_version"] == scenario.loser_buy.state_version


# =========================================================================
# Refusals
# =========================================================================
@pytest.mark.parametrize("how", ["no_outcomes", "one_outcome", "0.0000"])
async def test_a_book_the_engine_cannot_price_is_500_market_book_incomplete(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    how: str,
) -> None:
    """"The same guard the snapshot uses." On an untraded book, so no trade
    read can fail first. Catches: `book_prices.refuse_unpriceable` never
    called, so no outcome rows read as no book (`200`), one row reaches the
    engine's `ValueError`, and a zero `b` divides by zero."""
    upstream = Upstream()
    await open_at_zero(session, upstream)
    await _break_book(session, upstream.market_id, how)

    response = await client.get(_path(upstream.market_id), headers=trader_headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "market_book_incomplete"


async def test_a_gap_in_the_trades_is_500_price_history_inconsistent(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The pool feed runs 1, 3 (`price_history_fixtures.gapped`). The handler
    already logs every 500 `LedgerError` at ERROR. Catches: the replay's
    `ValueError` reaching the client as a bare 500 outside the envelope."""
    upstream = await gapped(session)

    response = await client.get(_path(upstream.market_id), headers=trader_headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "price_history_inconsistent"


# =========================================================================
# Reads that write nothing
# =========================================================================
async def test_an_unknown_market_is_an_empty_200_and_opens_no_book(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """"This route does not open one: it writes nothing and never calls
    market_service." A call to market_service would fail the request.
    Catches: the snapshot's `book_prices.read_or_open` on this read."""
    market_id = uuid.uuid4()

    body = await _history(client, market_id, trader_headers)

    assert body == {
        "market_id": str(market_id),
        "points": [],
        "result": None,
        "winning_outcome_id": None,
        "result_recorded_at": None,
    }
    assert await _book_count(market_id) == 0
    async with session_factory()() as fresh:
        assert await ledger_counts(fresh) == (0, 0)


async def test_the_read_writes_nothing_and_grants_the_reader_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A trader nobody has seen reads a traded market. Rows are counted from
    fresh sessions, and the statements are captured too, because the
    request's session rolls back at the end and an uncommitted write would
    leave no row. Catches: `ensure_granted` for the reader, as `/balance`
    does."""
    scenario = await traded(session)
    reader_id = uuid.uuid4()
    async with session_factory()() as fresh:
        before = await footprint(fresh, scenario.market_id)
    accounts_before = await _account_count()

    with capture_sql() as statements:
        body = await _history(
            client, scenario.market_id, bearer(reader_id, UserRole.TRADER)
        )

    assert len(body["points"]) == 4
    assert writes(statements) == []
    async with session_factory()() as fresh:
        assert await footprint(fresh, scenario.market_id) == before
        assert await transaction_count(fresh, key=grants().grant_key(reader_id)) == 0
        assert (
            await accounts_module().find(fresh, entities().AccountKind.USER, reader_id)
            is None
        )
    assert await _account_count() == accounts_before
