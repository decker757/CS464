"""Status codes, authorisation and the shape on the wire. [F-9] #112

Business rules are tested in `unit_test/service/test_snapshot.py` without HTTP.
What is asserted here is what the controller owns: who may call the route,
which status code each refusal carries, and what the JSON looks like —
including which fields are strings, which is an exactness decision rather than
a formatting one.

**The shape is the point of this file.** `docs/api/realtime-service.md` pins
the snapshot body as the `price` frame without its `type`, "identical on
purpose. A client that renders a snapshot and a client that renders an event
should be running the same function." Nothing generated enforces that —
OpenAPI has no vocabulary for a WebSocket, so there is no schema check that
will catch the two drifting. The field-set assertions below are the
enforcement.

**The cold path is stubbed at `market_terms.fetch` rather than at a
transport**, the same way `test_preview_routes.py` does it: a route cannot be
handed an `httpx.MockTransport`. The service-layer tests exercise the real
request building through one, and `test_market_terms.py` owns the
upstream-to-error mapping. What is left here is whether a domain error raised
below the controller becomes the right status and the right code, and a stub
that raises the domain error directly is the most honest way to ask that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer, mint_token


def _books():
    """Imported inside each helper rather than at module scope, so a missing
    name fails the test that needs it instead of collecting the whole file
    (D-007)."""
    from service import books  # noqa: PLC0415

    return books


def _entities():
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    from core import errors  # noqa: PLC0415

    return errors


_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")
_Q = [Decimal("137.5000"), Decimal("42.2500")]

# Every field in the response, and nothing else. Asserted as a set so that a
# gained field is a failure rather than an unnoticed addition — the consumer of
# the matching socket frame validates with `extra="forbid"`, so an extra field
# there is a dropped event, and a client written against one shape and handed
# the other is the bug this pins.
_FIELDS = {"market_id", "state_version", "prices", "occurred_at"}
_PRICE_FIELDS = {"outcome_id", "position", "price"}


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/snapshot"


class _Market:
    """A published market with a book and outcomes."""

    def __init__(self, *, status: str = "open", outcomes: int = 2) -> None:
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self._status = status

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": str(self.market_id),
                    "status": self._status,
                    "close_time": "2027-01-05T12:00:00Z",
                    "resolution_time": "2027-01-20T12:00:00Z",
                    "liquidity_b": str(_B),
                    "seed_subsidy": str(_SUBSIDY),
                    "published_at": "2026-09-01T09:00:00Z",
                    "outcomes": [
                        {"id": str(o), "position": i, "label": f"Outcome {i}"}
                        for i, o in enumerate(self.outcomes)
                    ],
                },
            )

        return httpx.MockTransport(handler)


async def _warm(
    session: AsyncSession, market: _Market, q: Sequence[Decimal] = tuple(_Q)
) -> None:
    """Open the book and write `q`, committed, so the route's own session sees it."""
    await _books().ensure_open(
        session,
        market.market_id,
        access_token=mint_token(uuid.uuid4()),
        transport=market.transport,
    )
    outcome = _entities().MarketOutcome
    for position, value in enumerate(q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == market.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


class _Terms:
    """A stub for `market_terms.fetch`, patched onto the module.

    Patched there rather than onto `books`, because `books.py` holds the module
    and resolves `market_terms.fetch` at call time — so this is the same
    function the real path calls, reached the same way.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4(), uuid.uuid4()]
        self._raises = raises

    def install(self, monkeypatch: pytest.MonkeyPatch, *, published: bool = True):
        from service import market_terms  # noqa: PLC0415

        async def fake(market_id, *, access_token, transport=None):  # noqa: ANN001
            self.calls += 1
            if self._raises is not None:
                raise self._raises
            return market_terms.MarketTerms(
                market_id=market_id,
                # Explicit: `MarketTerms.status` has no default, because a
                # default of "open" is a fail-open on the trade gate.
                status="open",
                liquidity_b=_B,
                seed_subsidy=_SUBSIDY,
                published_at=(
                    datetime(2026, 9, 1, 9, 0, tzinfo=UTC) if published else None
                ),
                outcomes=[
                    market_terms.OutcomeTerms(outcome_id=o, position=i)
                    for i, o in enumerate(self.outcomes)
                ],
            )

        monkeypatch.setattr(market_terms, "fetch", fake)
        return self


# =========================================================================
# Authentication and authorisation
# =========================================================================
async def test_a_trader_may_read_a_snapshot(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Any valid access token, any role (D-018), like the preview beside it.

    A snapshot mints nothing and reveals nothing beyond what the public market
    read already shows. It is also the read every socket client makes before it
    can render anything, so an admin gate here would mean no trader could open
    a market page at all.
    """
    market = _Market()
    await _warm(session, market)

    response = await client.get(_path(market.market_id), headers=trader_headers)

    assert response.status_code == 200


async def test_an_administrator_may_read_a_snapshot_too(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """"Any role" runs both ways. There is no role this route refuses."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(_path(market.market_id), headers=admin_headers)

    assert response.status_code == 200


async def test_an_anonymous_caller_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """401 `invalid_token`, reusing the preview's code as the criteria require.

    Every read in this backend authenticates a person from a signed token, and
    this is no exception — even though the socket that follows it will
    authenticate the same person again at the handshake.
    """
    market = _Market()
    await _warm(session, market)

    response = await client.get(_path(market.market_id))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_an_expired_token_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The fifteen-minute window, on this route like every other.

    Worth pinning here specifically: `docs/api/realtime-service.md`'s reconnect
    sequence has a client fetch this after its socket closed with `4408` —
    token expired while connected — so an expired token arriving here is the
    *expected* shape of a client that has not refreshed yet, and it must get a
    401 rather than a stale price.
    """
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        headers=bearer(uuid.uuid4(), UserRole.TRADER, expires_in=-1),
    )

    assert response.status_code == 401


async def test_the_cookie_is_accepted_too(
    client: AsyncClient, session: AsyncSession, user_id: uuid.UUID
) -> None:
    """ADR 0002: the browser sends a cookie, a service sends a header.

    Load-bearing on this route for the reason it is on the preview — the
    credential is forwarded upstream on a market's first touch, so the cookie
    has to survive being read by `extract_access_token` and handed on as a
    bearer token — and for one more: the browser opening the socket next has no
    way to send a header either, so cookie-only is the whole realtime path.
    """
    market = _Market()
    await _warm(session, market)
    client.cookies.set("access_token", mint_token(user_id, UserRole.TRADER))

    response = await client.get(_path(market.market_id))

    assert response.status_code == 200


# =========================================================================
# The wire
# =========================================================================
async def test_the_body_is_a_price_frame_without_its_type(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The criterion: `market_id`, `state_version`, `prices`, `occurred_at`.

    Asserted as an exact set in both directions. A missing field is a client
    that cannot resume its version check; an extra one is the two shapes
    diverging, which is the thing `docs/api/realtime-service.md` promises will
    not happen — "a client that renders a snapshot and a client that renders an
    event should be running the same function".

    `type` is absent because it belongs to the socket envelope, not to the
    event: `realtime_service`'s `PriceEvent.frame()` adds it on the way out.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert set(body) == _FIELDS
    assert "type" not in body


async def test_each_price_carries_its_outcome_and_position(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The nested shape, which the check above cannot see.

    `position` is what lets a categorical market render in the order the
    administrator arranged without a second lookup, and it is the field most
    likely to be dropped as redundant because the list is already ordered.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert len(body["prices"]) == 2
    for entry in body["prices"]:
        assert set(entry) == _PRICE_FIELDS
    assert [e["position"] for e in body["prices"]] == [0, 1]
    assert [e["outcome_id"] for e in body["prices"]] == [str(o) for o in market.outcomes]


async def test_every_price_is_a_json_string(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The ledger's standing rule, on the last hop before a browser parses it.

    A JSON number is an IEEE double by the time `JSON.parse` has run, and this
    service is careful to keep its arithmetic exact all the way from `q`
    through `core/lmsr.py`'s fifty digits to the quantized price. Handing the
    result through a double at the edge throws that away, and the socket frame
    beside it already sends a string — so a number here would make the two
    shapes differ on the one property the page insists they share.

    Checked with `isinstance` against `str` rather than by parsing, because
    `json.loads` turns `0.6234` into a float that compares equal to
    `Decimal("0.6234")`; the assertion has to be about the type on the wire.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    for entry in body["prices"]:
        assert isinstance(entry["price"], str), entry
        assert Decimal(entry["price"]).as_tuple().exponent == -4, entry


async def test_state_version_is_a_json_number(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The one field deliberately not a string, matching `PriceEvent`.

    It is a count, not money. A client compares it with `>` against the version
    it last rendered, and the first consumer that forgot to parse a string
    would order `"9"` after `"10"` — silently rendering a stale price and
    discarding the correction.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert isinstance(body["state_version"], int)
    assert not isinstance(body["state_version"], bool)


async def test_occurred_at_reaches_the_wire_with_an_offset(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Timezone-aware, always UTC, as the contract's field documents.

    A naive value serialises without the offset and the client parses it as
    local time. The other three services carry the same guard for the same
    reason, and CLAUDE.md records that this has already caused bugs here.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert datetime.fromisoformat(body["occurred_at"]).tzinfo is not None, body


async def test_the_market_id_is_echoed(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """So a client holding several open markets can route the body without
    remembering which request it belongs to — the same reason the socket frame
    carries it rather than relying on the subscription."""
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert body["market_id"] == str(market.market_id)


async def test_a_malformed_market_id_is_422(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """FastAPI's own path validation, before anything below the controller runs.

    Asserted so that a market id that is not a UUID cannot reach
    `books.ensure_open` and become a 503 about a dependency that was never
    asked anything.
    """
    response = await client.get("/ledger/markets/not-a-uuid/snapshot", headers=trader_headers)

    assert response.status_code == 422


# =========================================================================
# Failures, reusing the preview's codes
# =========================================================================
async def test_a_market_that_does_not_exist_is_404(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terms = _Terms(raises=_errors().MarketNotFound()).install(monkeypatch)

    response = await client.get(_path(terms.market_id), headers=trader_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_an_unpublished_market_is_409(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409 `market_not_published`. A draft has no terms to open a book from.

    Not 404: the market exists, and telling a caller it does not would be a
    different bug to chase.
    """
    terms = _Terms().install(monkeypatch, published=False)

    response = await client.get(_path(terms.market_id), headers=trader_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_published"


async def test_an_unreachable_market_service_is_503(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 `market_terms_unavailable`, and only on a first touch.

    The request was fine and the dependency was not, which is what makes it
    worth retrying — and after one success it can never fire for this market
    again.
    """
    terms = _Terms(raises=_errors().MarketTermsUnavailable()).install(monkeypatch)

    response = await client.get(_path(terms.market_id), headers=trader_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "market_terms_unavailable"


async def test_the_error_envelope_is_the_one_every_service_uses(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`{"error": {"code", "message"}}`, so the frontend parses one shape across
    the whole backend — including the socket, whose `error` frame wraps the
    same envelope."""
    terms = _Terms(raises=_errors().MarketNotFound()).install(monkeypatch)

    body = (await client.get(_path(terms.market_id), headers=trader_headers)).json()

    assert set(body) == {"error"}
    assert {"code", "message"} <= set(body["error"])


# =========================================================================
# It never gates on status
# =========================================================================
async def test_a_closed_market_returns_prices_rather_than_an_error(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The criterion's own wording: a snapshot on a closed market "returns a
    number".

    Asserted at this layer as well as at the service layer, because the two
    fail differently. A gate added below shows up in
    `test_snapshot.py::test_a_closed_market_makes_no_status_hop`; a gate added
    *here*, as a route-level check before the service is called, would pass
    every service-layer test in the suite.

    `market_closed` is 409 in `core/errors.py` and belongs to the trade path
    alone (ADR 0017). It must never come out of this route.
    """
    market = _Market(status="closed")
    await _warm(session, market)

    response = await client.get(_path(market.market_id), headers=trader_headers)

    assert response.status_code == 200, response.json()
    assert Decimal(response.json()["prices"][0]["price"]) > 0


async def test_a_book_with_no_outcome_rows_is_a_500_in_the_envelope(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Still a 500, now with the envelope and a code a log search can find.

    Before the read was shared this was an `IndexError` out of `rows[0]`,
    which no handler maps: a bare 500 with no body the frontend could parse.
    The state takes a hand-run repair to reach, and `test_snapshot.py`
    explains how. This holds only the HTTP shape.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    market = _Market()
    await _warm(session, market)
    outcome = _entities().MarketOutcome
    await session.execute(delete(outcome).where(outcome.market_id == market.market_id))
    await session.commit()

    response = await client.get(_path(market.market_id), headers=trader_headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "market_book_incomplete"
