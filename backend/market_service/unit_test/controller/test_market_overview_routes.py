"""Status codes and the response shape of the admin market overview. [2.1] #5.

`GET /markets/overview`. The rows and counts themselves are
`unit_test/service/test_market_overview.py`'s; this suite pins the guard, the
route's place before `/markets/{market_id}`, the filter's accepted values,
what reaches the wire, and that the two older lists did not move.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import MarketStatus
from service.audit import Actor
from unit_test.conftest import overdue_market, published_market_closing_at

# The one place the path is written.
_OVERVIEW = "/markets/overview"


@pytest.fixture
def route_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze the admin routes' clock ten days ahead; returns the frozen instant.

    Assumes `controller/routes.py` reads the clock as `datetime.now(UTC)`, as
    `public_routes.py` does. `raising=False` because the module does not
    import `datetime` until the route exists.
    """
    import controller.routes as routes  # noqa: PLC0415

    frozen = datetime.now(UTC) + timedelta(days=10)

    class _Frozen:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return frozen

    monkeypatch.setattr(routes, "datetime", _Frozen, raising=False)
    return frozen


def _admin(admin_id: uuid.UUID) -> Actor:
    """The token's administrator, for building markets through the service layer."""
    return Actor(id=admin_id, username="ernest_t", role="admin")


async def _open_until(session: AsyncSession, creator: Actor, closes: datetime) -> str:
    return str((await published_market_closing_at(session, creator, closes)).id)


def _status_error_is_on_the_query(response_json: dict) -> bool:
    """The 422 names `?status`, not a path id that swallowed `overview`."""
    return any(error["loc"] == ["query", "status"] for error in response_json["detail"])


# --- the guard and the route's place ---------------------------------------
async def test_a_trader_is_refused_with_403_not_an_administrator(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """[2.1] #5: "A trader is refused with 403"."""
    response = await client.get(_OVERVIEW, headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_overview_is_not_captured_by_the_market_id_route(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Declared after `/{market_id}`, FastAPI parses `overview` as a UUID and
    answers 422. "The admin market overview is its own route"."""
    response = await client.get(_OVERVIEW, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["markets"] == []


# --- the status filter -----------------------------------------------------
@pytest.mark.parametrize("status", [member.value for member in MarketStatus])
async def test_every_market_status_is_an_accepted_filter(
    client: AsyncClient, admin_headers: dict[str, str], status: str
) -> None:
    """[2.1] #5: "Filters: draft, submitted, open, closed, pending resolution,
    approved". `draft` and `submitted` too, which the trader filter refuses."""
    response = await client.get(
        _OVERVIEW, params={"status": status}, headers=admin_headers
    )

    assert response.status_code == 200


async def test_an_unknown_status_filter_is_422(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """A closed set; untyped, this is a silent empty list or a 500."""
    response = await client.get(
        _OVERVIEW, params={"status": "nonsense"}, headers=admin_headers
    )

    assert response.status_code == 422
    assert _status_error_is_on_the_query(response.json())


async def test_settled_is_422_until_3_4_12_adds_it__delete_when_12_lands(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """"Settled is #12's filter and count, not #5's". Delete this test when
    [3.4] #12 adds SETTLED to `MarketStatus`; it will then be a 200."""
    response = await client.get(
        _OVERVIEW, params={"status": "settled"}, headers=admin_headers
    )

    assert response.status_code == 422
    assert _status_error_is_on_the_query(response.json())


# --- what reaches the wire -------------------------------------------------
async def test_each_row_carries_its_creator_id_and_derived_status(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
    other_admin_id: uuid.UUID,
    route_now: datetime,
) -> None:
    """[2.1] #5: "Each market carries its creator's id, so the caller can tell
    their own", and a market past its close time reads `closed` on the wire,
    surviving FastAPI's re-validation of the response."""
    mine = await _open_until(session, _admin(admin_id), route_now - timedelta(hours=1))
    theirs = await _open_until(
        session, _admin(other_admin_id), route_now + timedelta(days=1)
    )

    payload = (await client.get(_OVERVIEW, headers=admin_headers)).json()
    rows = {row["id"]: row for row in payload["markets"]}

    assert rows[mine]["creator_id"] == str(admin_id)
    assert rows[theirs]["creator_id"] == str(other_admin_id)
    assert rows[mine]["status"] == MarketStatus.CLOSED.value
    assert rows[theirs]["status"] == MarketStatus.OPEN.value


async def test_counts_are_json_integers_keyed_by_every_status(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
    route_now: datetime,
) -> None:
    """[2.1] #5: counts "for every status, zero when none", as numbers the
    frontend can add, not strings."""
    await _open_until(session, _admin(admin_id), route_now + timedelta(days=1))

    counts = (await client.get(_OVERVIEW, headers=admin_headers)).json()["counts"]

    assert set(counts) == {member.value for member in MarketStatus}
    for value in counts.values():
        assert type(value) is int
    assert counts[MarketStatus.OPEN.value] == 1


async def test_the_controllers_clock_reaches_both_the_list_and_the_counts(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
    route_now: datetime,
) -> None:
    """"The overview's list and counts share one clock": read once, at the
    controller, and handed to both halves.

    Closing exactly at the frozen instant, so closed. Fails if the controller
    does not pass its clock, or reads it twice: the service's own clock is
    ten days earlier and would call it open.
    """
    await _open_until(session, _admin(admin_id), route_now)

    payload = (await client.get(_OVERVIEW, headers=admin_headers)).json()

    assert [row["status"] for row in payload["markets"]] == [MarketStatus.CLOSED.value]
    assert payload["counts"][MarketStatus.CLOSED.value] == 1
    assert payload["counts"][MarketStatus.OPEN.value] == 0


# --- the two older lists did not move --------------------------------------
# Also pinned already: GET /markets's scope and shape by
# `test_the_list_shows_only_my_markets` and
# `test_the_list_is_a_summary_not_the_whole_market`; the trader browse's
# order by `test_the_default_view_shows_open_markets_ordered_by_soonest_close`
# and `test_the_default_view_sorts_a_market_past_its_close_time_behind_the_open_ones`.
async def test_get_markets_still_reports_the_stored_column(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """"The admin overview derives status…": `MarketOut` keeps the column,
    and is where the sweeper-health signal is now read. Fails if the
    overview's derivation is shared into `GET /markets`."""
    await overdue_market(session, _admin(admin_id))

    rows = (await client.get("/markets", headers=admin_headers)).json()["markets"]

    assert [row["status"] for row in rows] == [MarketStatus.OPEN.value]


async def test_the_public_browse_still_refuses_a_draft_filter(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """[1.1] #1: `draft` is not a trader's filter. Fails if the overview's
    wider filter type is shared into `GET /public/markets`."""
    response = await client.get(
        "/public/markets", params={"status": "draft"}, headers=trader_headers
    )

    assert response.status_code == 422
