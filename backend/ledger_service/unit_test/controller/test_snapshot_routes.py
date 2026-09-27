"""Status codes, authorisation and the shape on the wire. [F-9] #112

What the controller owns: who may call the route, each refusal's status and
code, and the JSON. The body must be the `price` frame without its `type`
(`docs/api/realtime-service.md`), and nothing generated checks that, so the
field-set assertions here do. The cold path is stubbed at
`market_terms.fetch`.
"""

from __future__ import annotations

import json
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
from unit_test.conftest import bearer, mint_token, strip_outcomes


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

# Every field in the response, and nothing else: the matching socket frame
# forbids extra fields.
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
    """A stub for `market_terms.fetch`, patched onto the module, which
    `books.py` resolves at call time."""

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
    """Any valid access token, any role (D-018)."""
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
    """401 `invalid_token`, reusing the preview's code."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(_path(market.market_id))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_an_expired_token_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """An expired token gets a 401, not a stale price."""
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
    """ADR 0002: the browser sends a cookie, through `AccessToken` too; the
    realtime path is cookie-only."""
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
    """The criterion: exactly `market_id`, `state_version`, `prices`,
    `occurred_at`. `type` belongs to the socket envelope."""
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert set(body) == _FIELDS
    assert "type" not in body


async def test_each_price_carries_its_outcome_and_position(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The nested shape, which the check above cannot see."""
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
    """Prices are JSON strings at scale 4, like the socket frame's, checked by
    type on the wire."""
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    for entry in body["prices"]:
        assert isinstance(entry["price"], str), entry
        assert Decimal(entry["price"]).as_tuple().exponent == -4, entry


async def test_state_version_is_a_json_number(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Deliberately not a string, matching `PriceEvent`: a count compared with
    `>`."""
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert isinstance(body["state_version"], int)
    assert not isinstance(body["state_version"], bool)


async def test_occurred_at_reaches_the_wire_with_an_offset(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Timezone-aware, and spelled exactly as a `PriceEvent` built from the
    same instant spells it: a `Z` and a `+00:00` both parse but differ.
    """
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    parsed = datetime.fromisoformat(body["occurred_at"])
    assert parsed.tzinfo is not None, body

    from model.schemas import OutcomePrice, PriceEvent  # noqa: PLC0415

    frame = PriceEvent(
        market_id=market.market_id,
        state_version=body["state_version"],
        occurred_at=parsed,
        prices=[
            OutcomePrice(
                outcome_id=p["outcome_id"],
                position=p["position"],
                price=Decimal(p["price"]),
            )
            for p in body["prices"]
        ],
    )
    assert json.loads(frame.model_dump_json())["occurred_at"] == body["occurred_at"], (
        "the snapshot and the price frame spell the same instant differently"
    )


async def test_the_market_id_is_echoed(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """So a client holding several open markets can route the body."""
    market = _Market()
    await _warm(session, market)

    body = (await client.get(_path(market.market_id), headers=trader_headers)).json()

    assert body["market_id"] == str(market.market_id)


async def test_a_malformed_market_id_is_422(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """FastAPI's own path validation, before anything below runs."""
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
    """409 `market_not_published`, as the handler maps it."""
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
    """503 `market_terms_unavailable`, only on a first touch."""
    terms = _Terms(raises=_errors().MarketTermsUnavailable()).install(monkeypatch)

    response = await client.get(_path(terms.market_id), headers=trader_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "market_terms_unavailable"


async def test_the_error_envelope_is_the_one_every_service_uses(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`{"error": {"code", "message"}}`, the envelope every service uses."""
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
    """A closed market "returns a number" at the route too, where a gate
    added here would pass every service test. ADR 0017.
    """
    market = _Market(status="closed")
    await _warm(session, market)

    response = await client.get(_path(market.market_id), headers=trader_headers)

    assert response.status_code == 200, response.json()
    assert Decimal(response.json()["prices"][0]["price"]) > 0


async def test_a_book_with_no_outcome_rows_is_a_500_in_the_envelope(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """A 500 in the envelope, with a code a log search can find."""
    from sqlalchemy import delete  # noqa: PLC0415

    market = _Market()
    await _warm(session, market)
    await strip_outcomes(session, market.market_id)

    response = await client.get(_path(market.market_id), headers=trader_headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "market_book_incomplete"
