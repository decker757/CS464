"""Status codes, the error envelope and the response shape. [BE][X] #62.

The trader-facing counterpart to `test_market_routes.py`. Business rules are
asserted in `unit_test/service/test_browsing.py` and pure shape in
`unit_test/model/test_public_schemas.py`; what belongs here is everything a
caller parses — the codes, the envelope, the query parameters, and the fields
surviving a round trip through Postgres.

That last one is not a duplicate of the model suite. `Numeric(18, 4)` hands
back `Decimal("100.0000")` where a hand-built stand-in has `Decimal("100")`,
and the whole point of the decimal-string rule is that the value the ledger
reads is the value the column holds. The model test proves the schema is
right; this proves the stack is.

**There are two callers of these routes**, which is unusual for this
repository and shapes several tests below. Michelle's browse and detail pages
are one. The ledger is the other: ADR 0005 says `b` and the subsidy cross to
the trading side once, as an immutable snapshot, and this endpoint is where
they cross. A field that is merely nice for a page is load-bearing for a price.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import MarketStatus
from unit_test.conftest import (
    SOURCE_URL,
    actor,
    closed_market,
    draft_request,
    published_market,
)
from service import market_service

_LIST = "/public/markets"


def _detail(market_id: object) -> str:
    return f"/public/markets/{market_id}"


async def _published(session: AsyncSession, **overrides: object):
    """A market a trader can see, with `liquidity_b` pinned.

    `market_terms` deliberately omits `liquidity_b` so that every other suite
    exercises the configured default. Here the value is the subject, so it is
    stated rather than inherited from `core/config.py`, where an operator
    changing a default would otherwise turn these assertions red.
    """
    return await published_market(
        session, actor(), liquidity_b=Decimal("100"), **overrides
    )


# --- who may call these ---------------------------------------------------
async def test_a_trader_may_browse(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-1] #34 is a trader story, and every existing `/markets` route answers
    a trader with `403 not_an_administrator`.

    So this is the first read in the service that a non-administrator is
    allowed to make, and it is the whole reason #62 is a separate router
    rather than a widened guard on the existing one.
    """
    await _published(session)

    response = await client.get(_LIST, headers=trader_headers)

    assert response.status_code == 200


async def test_a_trader_may_read_one_market_by_its_id(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-3] #36. The admin detail route answers a stranger `404` by design;
    this one is the read that does not."""
    market = await _published(session)

    response = await client.get(_detail(market.id), headers=trader_headers)

    assert response.status_code == 200
    assert response.json()["id"] == str(market.id)


async def test_an_administrator_may_also_browse(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """An administrator is a user. Guarding this on the trader role rather than
    on "any valid token" would give the three people running the platform a
    worse view of it than everybody else, and would make #62's response
    untestable from the admin console."""
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
    """The ledger reads `b` from here before it can price anything.

    A JSON number is an IEEE double by the time any client has parsed it, and
    `b` is the denominator of `C(q) = b·ln(Σ e^(q_i/b))`. Shipping it as one
    puts a rounding error under every cost preview ([T-1] #21), every trade
    ([T-2] #22) and every price on the websocket.

    Asserted through Postgres rather than only on the model because the column
    is `Numeric(18, 4)`: the driver returns `Decimal("100.0000")` where the
    request sent `Decimal("100")`, and it is the stored value the ledger has
    to be able to reconstruct exactly.

    `MarketOut` still sends both as floats for Michelle's create form, and
    `test_the_administrators_market_out_still_serialises_them_as_floats`
    holds it there. Two consumers, two schemas.
    """
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
    """The other half of what the ledger needs to open a book.

    `q` is one quantity per outcome, and the `price` frame in
    `docs/api/realtime-service.md` names each one by `outcome_id` and orders
    them by `position` — so both have to be snapshotted when the ledger first
    prices this market, and this is the only place it can read them. Every
    outcome, not only the two a binary market happens to have: the schema
    allows up to ten.
    """
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
    """[X-3] #36: "The page shows the market question, outcomes, current
    YES/NO prices, status, closing time, and named resolution source."

    Every part of that except the prices, which are not this service's to
    serve — `q` lives with the ledger (ADR 0005) and the authoritative read is
    the snapshot endpoint specified in `docs/api/realtime-service.md`, landing
    with [F-3] #43 and [T-2] #22.
    """
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
    """[X-1] #34's first criterion, minus the prices, for the list projection.

    Separate from the detail because the list is a narrower shape on purpose —
    `MarketSummaryOut` already establishes that pattern for the administrator's
    list — and a card that had to fetch the detail of every row to render a
    closing time would make the browse page N+1 requests.
    """
    await _published(session)

    payload = (await client.get(_LIST, headers=trader_headers)).json()

    card = payload["markets"][0]
    assert card["question"]
    assert card["status"] == MarketStatus.OPEN.value
    assert card["close_time"]
    assert card["id"]


async def test_timestamps_come_back_with_an_offset(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The guard every other response in this service already has.

    [X-1] #34 renders a countdown from `close_time`. A naive timestamp parsed
    in a Singapore browser is eight hours of countdown, on the field that
    decides whether trading is still open.
    """
    market = await _published(session)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert datetime.fromisoformat(payload["close_time"]).tzinfo is not None
    assert datetime.fromisoformat(payload["published_at"]).tzinfo is not None


# --- refusals -------------------------------------------------------------
async def test_an_unpublished_market_is_404_with_the_service_envelope(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[1.1] #1: a draft is visible to nobody but its creator.

    The envelope is asserted, not only the status. A route that does not exist
    is also a 404, so `assert response.status_code == 404` alone is a test that
    passes before the endpoint is written and proves nothing afterwards.
    `{"error": {"code": ...}}` is this service's shape; FastAPI's own 404 is
    `{"detail": "Not Found"}`.
    """
    market, _, _ = await market_service.save(session, actor(), draft_request())

    response = await client.get(_detail(market.id), headers=trader_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_an_unknown_id_is_404_with_the_service_envelope(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The same answer as an unpublished market, so the two cannot be told
    apart from outside — which is what makes the rule above hold."""
    response = await client.get(_detail(uuid.uuid4()), headers=trader_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_a_malformed_id_is_422_not_500(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """A path parameter typed as `uuid.UUID` is FastAPI's 422 before the
    service sees it. Without the annotation it reaches the driver and comes
    back as a 500 on a link somebody mistyped."""
    response = await client.get(_detail("not-a-uuid"), headers=trader_headers)

    assert response.status_code == 422


async def test_an_unknown_status_filter_is_422_not_500(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """[X-2] #35's filter is a closed set, and typing the parameter is what
    makes /docs list the values Michelle may send.

    Untyped, `?status=nonsense` is either a silent empty list — which looks
    like "no markets match" and is indistinguishable from a working filter —
    or a driver error surfacing as a 500.
    """
    response = await client.get(
        _LIST, params={"status": "nonsense"}, headers=trader_headers
    )

    assert response.status_code == 422


# --- the query parameters -------------------------------------------------
async def test_the_status_filter_is_a_query_parameter(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """[X-2] #35: "Users can filter markets by status."

    The wiring only. That the filter selects the right rows is
    `test_browsing.py`'s job; what is asserted here is that the parameter
    exists, is named `status`, and reaches the service — which a route that
    accepted and ignored it would fail.
    """
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
    """[X-1] #34: "An appropriate empty state is shown when no markets match
    the selected view."

    A 404 here would have the browse page render an error where it should
    render "nothing matches", and a frontend cannot tell that 404 from the one
    the detail route returns for a market that does not exist.
    """
    await _published(session)

    response = await client.get(
        _LIST, params={"q": "nothing matches this"}, headers=trader_headers
    )

    assert response.status_code == 200
    assert response.json()["markets"] == []


# --- ADR 0011, as the wire reports it -------------------------------------
async def test_a_market_past_its_close_time_is_reported_closed(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """ADR 0011, end to end: the column still says `open` and the response must
    not.

    The sweeper is deliberately not run here. This is the state every market
    passes through for a few seconds, and on a browse page it is the difference
    between a buy button and a closed badge. The argument for deriving it in
    the response rather than leaving it to the frontend is in
    `test_browsing.py`; what this asserts is that the derivation survives the
    route.
    """
    market = await _published(session)
    market.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

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
    """Asserted at the route as well as on the schema, because this is the one
    that fails if somebody points the public router at `MarketOut`.

    That is a plausible shortcut — `MarketOut` already carries every field
    these pages need — and it would ship `creator_id` and `draft_key` to every
    trader without any model test noticing.
    """
    market = await _published(session)

    payload = (await client.get(_detail(market.id), headers=trader_headers)).json()

    assert field not in payload
