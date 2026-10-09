"""Status codes, the error envelope and the response shape. [BE][X] #62.

Everything a caller parses, through the real stack including Postgres. Two
callers read these routes: the browse and detail pages, and the ledger, which
prices from `b` and the subsidy here (D-008, D-016).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from model.entities import Market, MarketStatus
from unit_test.conftest import (
    SOURCE_URL,
    actor,
    approved_market,
    closed_market,
    draft_request,
    overdue_market,
    published_market,
    settled_market,
)
from service import market_service

_LIST = "/public/markets"


def _detail(market_id: object) -> str:
    return f"/public/markets/{market_id}"


async def _published(session: AsyncSession, **overrides: object):
    """A market a trader can see, with `liquidity_b` pinned rather than defaulted."""
    return await published_market(
        session, actor(), liquidity_b=Decimal("100"), **overrides
    )


# --- who may call these ---------------------------------------------------
async def test_a_trader_may_browse(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-1] #34 is a trader story; every `/markets` route refuses a trader. D-018."""
    await _published(session)

    response = await client.get(_LIST, headers=trader_headers)

    assert response.status_code == 200


async def test_a_trader_may_read_one_market_by_its_id(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-3] #36: unlike the admin detail route, this one serves a stranger."""
    market = await _published(session)

    response = await client.get(_detail(market.id), headers=trader_headers)

    assert response.status_code == 200
    assert response.json()["id"] == str(market.id)


async def test_an_administrator_may_also_browse(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Guard: any valid token, not the trader role. D-018 and ADR 0011's amendment cite it."""
    await _published(session)

    assert (await client.get(_LIST, headers=admin_headers)).status_code == 200


# --- the ledger's two requirements ----------------------------------------
@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
async def test_the_pricing_numbers_are_decimal_strings_through_the_real_stack(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    field: str,
) -> None:
    """The ledger prices from these, so they are exact strings (D-016), checked
    through Postgres because the stored `Numeric(18, 4)` is what must survive."""
    market = await _published(session)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert isinstance(payload[field], str), (
        f"{field} came back as {type(payload[field])}; a JSON number here is "
        "an IEEE double at the root of every price in the system"
    )
    assert Decimal(payload[field]) == Decimal(str(getattr(market, field)))


async def test_the_detail_carries_every_outcome_with_its_id_and_position(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The ledger opens its book from these: every outcome's id and position."""
    market = await _published(session)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert len(payload["outcomes"]) == len(market.outcomes)
    assert [o["position"] for o in payload["outcomes"]] == [0, 1]
    assert {o["id"] for o in payload["outcomes"]} == {
        str(o.id) for o in market.outcomes
    }


# --- what the pages render ------------------------------------------------
async def test_the_detail_carries_the_question_outcomes_status_and_close_time(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-3] #36's detail page, less the prices, which are the ledger's. ADR 0005."""
    market = await _published(
        session,
        resolution_sources=[{"url": SOURCE_URL, "label": "MAS official statistics"}],
    )

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert payload["question"] == market.question
    assert payload["status"] == MarketStatus.OPEN.value
    assert payload["close_time"]
    assert [o["label"] for o in payload["outcomes"]] == ["Yes", "No"]
    assert payload["resolution_sources"][0]["label"] == "MAS official statistics"
    assert payload["resolution_criteria"]


async def test_a_market_card_carries_what_the_browse_page_renders(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-1] #34's card, less the prices, without a detail fetch per row."""
    await _published(session)

    payload = (await client.get(_LIST, headers=trader_headers)).json()

    card = payload["markets"][0]
    assert card["question"]
    assert card["status"] == MarketStatus.OPEN.value
    assert card["close_time"]
    assert card["id"]


async def test_a_market_card_names_its_outcomes_as_the_detail_read_does(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """#214: the card's outcomes are the detail's `{id, position, label}`, no more.

    Without them the browse page guessed "Yes" and "No" for every market.
    """
    market = await _published(session)

    card = (await client.get(_LIST, headers=trader_headers)).json()["markets"][0]
    detail = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert [outcome["label"] for outcome in card["outcomes"]] == ["Yes", "No"]
    assert card["outcomes"] == detail["outcomes"]


# --- refusals -------------------------------------------------------------
async def test_an_unpublished_market_is_404_with_the_service_envelope(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[1.1] #1. The envelope is asserted, because a missing route is a 404 too."""
    market, _, _ = await market_service.save(session, actor(), draft_request())

    response = await client.get(_detail(market.id), headers=trader_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_an_unknown_id_is_404_with_the_service_envelope(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The same answer as an unpublished market, so the two look alike."""
    response = await client.get(_detail(uuid.uuid4()), headers=trader_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_a_malformed_id_is_422_not_500(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """Untyped, a mistyped link would reach the driver and 500."""
    response = await client.get(_detail("not-a-uuid"), headers=trader_headers)

    assert response.status_code == 422


@pytest.mark.parametrize("q", ["\x00", "inflation\x00", "\x00inflation"])
async def test_a_nul_in_the_search_term_is_422_not_500(
    client: AsyncClient, trader_headers: dict[str, str], q: str
) -> None:
    """A NUL reaching asyncpg is an unhandled DBAPIError and a 500. All three
    positions, so a prefix- or suffix-only guard fails."""
    response = await client.get(_LIST, params={"q": q}, headers=trader_headers)

    assert response.status_code == 422, (
        f"q={q!r} came back {response.status_code}; a NUL reaching asyncpg is "
        "an unhandled DBAPIError and a 500"
    )


async def test_an_ordinary_search_term_is_not_caught_by_the_nul_guard(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Fails if the NUL pattern is over-tightened: the rule is "not a NUL"."""
    wanted = await _published(
        session, question="Will Singapore core inflation be below 2% in December 2026?"
    )

    payload = (
        await client.get(
            _LIST, params={"q": "inflation be below 2%"}, headers=trader_headers
        )
    ).json()

    assert [m["id"] for m in payload["markets"]] == [str(wanted.id)]


async def test_an_unknown_status_filter_is_422_not_500(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """[X-2] #35's filter is a closed set; untyped, this is a silent `[]` or a 500."""
    response = await client.get(
        _LIST, params={"status": "nonsense"}, headers=trader_headers
    )

    assert response.status_code == 422


# --- the query parameters -------------------------------------------------
async def test_the_status_filter_is_a_query_parameter(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-2] #35: the wiring only; the rows are `test_browsing.py`'s job."""
    await _published(session)
    closed = await closed_market(session, actor())

    payload = (
        await client.get(_LIST, params={"status": "closed"}, headers=trader_headers)
    ).json()

    assert [m["id"] for m in payload["markets"]] == [str(closed.id)]


async def test_the_search_term_is_a_query_parameter(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-2] #35: "Users can search markets using words from the market
    question." The wiring, for the same reason as above."""
    wanted = await _published(
        session,
        question="Will Singapore core inflation be below 2% in December 2026?",
    )
    await _published(
        session, question="Will the MRT Cross Island Line open before June 2027?"
    )

    payload = (
        await client.get(_LIST, params={"q": "inflation"}, headers=trader_headers)
    ).json()

    assert [m["id"] for m in payload["markets"]] == [str(wanted.id)]


async def test_an_empty_result_is_200_with_an_empty_list(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-1] #34's empty state needs a 200, not a 404."""
    await _published(session)

    response = await client.get(
        _LIST, params={"q": "nothing matches this"}, headers=trader_headers
    )

    assert response.status_code == 200
    assert response.json()["markets"] == []
    assert response.json()["next_cursor"] is None


# --- paging, #104 ---------------------------------------------------------
async def test_next_cursor_continues_the_list_and_is_null_on_the_last_page(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """#104's contract: send `next_cursor` back as `cursor`; null is the end.
    The wiring only; the order and the rows are `test_browsing.py`'s job."""
    created = set()
    for _ in range(3):
        created.add(str((await _published(session)).id))

    first = (
        await client.get(_LIST, params={"limit": 2}, headers=trader_headers)
    ).json()
    second = (
        await client.get(
            _LIST,
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=trader_headers,
        )
    ).json()

    assert len(first["markets"]) == 2
    assert first["next_cursor"]
    assert len(second["markets"]) == 1
    assert second["next_cursor"] is None
    assert {m["id"] for m in first["markets"] + second["markets"]} == created


async def test_a_cursor_this_service_did_not_issue_is_400_with_the_service_envelope(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """#104: one error for every bad cursor, the code the other services use."""
    response = await client.get(
        _LIST, params={"cursor": "made-this-up"}, headers=trader_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_cursor"


async def test_with_no_limit_a_page_is_the_configured_default(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#104: "default 50", from settings. Shrunk to 2 so three markets show it."""
    for _ in range(3):
        await _published(session)
    get_settings.cache_clear()
    monkeypatch.setenv("DEFAULT_PAGE_SIZE", "2")
    try:
        payload = (await client.get(_LIST, headers=trader_headers)).json()
    finally:
        # Settings cached under the patched environment must not leak.
        get_settings.cache_clear()

    assert len(payload["markets"]) == 2
    assert payload["next_cursor"]


async def test_a_limit_above_the_ceiling_is_clamped_rather_than_refused(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#104: "oversized clamped, not refused" — a caller asking for more than
    the ceiling wants as much as it can get."""
    for _ in range(3):
        await _published(session)
    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAGE_SIZE", "2")
    try:
        response = await client.get(
            _LIST, params={"limit": 1000}, headers=trader_headers
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert len(response.json()["markets"]) == 2


@pytest.mark.parametrize("limit", [0, -1])
async def test_a_limit_below_one_is_422(
    client: AsyncClient, trader_headers: dict[str, str], limit: int
) -> None:
    """#104: a page of nothing makes no progress, so it is refused, not clamped."""
    response = await client.get(
        _LIST, params={"limit": limit}, headers=trader_headers
    )

    assert response.status_code == 422


# --- ADR 0011, as the wire reports it -------------------------------------
async def test_a_market_past_its_close_time_is_reported_closed(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """ADR 0011 end to end: the column says `open`, the response must not."""
    market = await overdue_market(session, actor(), liquidity_b=Decimal("100"))

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert payload["status"] == MarketStatus.CLOSED.value


# --- what a trader must not be handed -------------------------------------
@pytest.mark.parametrize("field", ["creator_id", "draft_key"])
async def test_the_response_does_not_carry_internal_fields(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    field: str,
) -> None:
    """Fails if the public router is pointed at `MarketOut`, a plausible shortcut. D-019."""
    market = await _published(session)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert field not in payload


# --- the derived status has to survive the response model -----------------
async def test_the_derived_status_reaches_the_wire_not_just_the_route(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-025's one clock must reach the client, surviving FastAPI's re-validation.

    With the clock two hours back, the market was still trading, so `open`.
    Asserted on `response.json()`: the returned object hid the bug. D-027.
    """
    import controller.public_routes as routes  # noqa: PLC0415

    market = await overdue_market(
        session, actor(), overdue_by=timedelta(hours=1), liquidity_b=Decimal("100")
    )

    class _TwoHoursAgo:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return datetime.now(UTC) - timedelta(hours=2)

    monkeypatch.setattr(routes, "datetime", _TwoHoursAgo)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert payload["status"] == MarketStatus.OPEN.value


async def test_the_browse_list_derived_status_reaches_the_wire_too(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list half, where wrapping in the list response alone re-validates."""
    import controller.public_routes as routes  # noqa: PLC0415

    await overdue_market(
        session, actor(), overdue_by=timedelta(hours=1), liquidity_b=Decimal("100")
    )

    class _TwoHoursAgo:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return datetime.now(UTC) - timedelta(hours=2)

    monkeypatch.setattr(routes, "datetime", _TwoHoursAgo)

    payload = (await client.get(_LIST, headers=trader_headers)).json()

    assert [m["status"] for m in payload["markets"]] == [MarketStatus.OPEN.value]


# --- [3.4] #12: settled, and settleable ------------------------------------
async def test_the_browse_accepts_a_settled_filter(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[3.4] #12: `settled` "is an accepted status on the trader browse".

    The literal string, so it fails while `PublicMarketStatus` lacks it. The
    approved decoy fails a filter that matches every decided market.
    """
    await _published(session)
    await approved_market(session, actor())
    settled_id = (await settled_market(session, actor())).id

    response = await client.get(
        _LIST, params={"status": "settled"}, headers=trader_headers
    )

    assert response.status_code == 200
    assert [m["id"] for m in response.json()["markets"]] == [str(settled_id)]
    assert response.json()["markets"][0]["status"] == "settled"


async def test_settleable_on_the_wire_follows_the_routes_clock(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"`settleable` reads the request's one Python clock": the controller's
    `now` must reach the flag and survive FastAPI's re-validation. D-027.

    Approved a moment ago, so the real clock reads false. At the edge the
    route's clock reads true, one second earlier false. Fails if the service
    reads `datetime.now(UTC)` instead of the `now` it is handed: both sides
    then read false.
    """
    import controller.public_routes as routes  # noqa: PLC0415

    market_id = (await approved_market(session, actor())).id
    approved_at = await session.scalar(
        select(Market.approved_at).where(Market.id == market_id)
    )
    window = timedelta(seconds=get_settings().dispute_window_seconds)
    edge = approved_at + window + timedelta(minutes=5)

    route_clock = {"now": edge}

    class _RouteClock:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return route_clock["now"]

    monkeypatch.setattr(routes, "datetime", _RouteClock)

    at_the_edge = (await client.get(_detail(market_id), headers=trader_headers)).json()
    route_clock["now"] = edge - timedelta(seconds=1)
    just_before = (await client.get(_detail(market_id), headers=trader_headers)).json()

    assert at_the_edge["settleable"] is True
    assert just_before["settleable"] is False
