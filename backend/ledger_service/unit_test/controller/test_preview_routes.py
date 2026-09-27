"""Status codes, authorisation and the shape on the wire. [T-1] #21

What the controller owns: who may call the route, what the query string may
say, each refusal's status and code, and the JSON shape. Business rules are
`test_preview.py`'s. The cold path is stubbed at `market_terms.fetch`, since a
route cannot be handed a transport.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from model.entities import Entry
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


_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")
_Q = [Decimal("137.5000"), Decimal("42.2500")]

# At `b = 100`, selling 100 of the second outcome pays 0.0000288, which floors
# to nothing; buying 100 costs 0.0000784, which ceils to a tick.
_SATURATED = [Decimal("1560.0000"), Decimal("100.0000")]

# Every field in the response, and nothing else.
_FIELDS = {
    "market_id",
    "state_version",
    "side",
    "outcome_id",
    "quantity",
    "total",
    "average_price",
    "prices",
    "post_trade_prices",
}

# The money fields, `quantity` included: it is echoed at the scale it arrived
# (D-038) and must not round-trip through a float either.
_DECIMAL_STRINGS = {"quantity", "total", "average_price"}


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/preview"


def _params(
    outcome_id: uuid.UUID, *, side: str = "buy", quantity: str = "10.0000"
) -> dict[str, str]:
    return {"outcome_id": str(outcome_id), "side": side, "quantity": quantity}


class _Market:
    """A published market with a book, outcomes and shares outstanding."""

    def __init__(self, outcomes: int = 2) -> None:
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": str(self.market_id),
                    "status": "open",
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
    """A stub for `market_terms.fetch`, recording what it was asked. Patched
    onto the module, which `books.py` resolves at call time."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4(), uuid.uuid4()]
        self._raises = raises

    def install(self, monkeypatch: pytest.MonkeyPatch, *, published: bool = True):
        from service import market_terms  # noqa: PLC0415

        async def fake(market_id, *, access_token, transport=None):  # noqa: ANN001
            self.calls += 1
            self.tokens.append(access_token)
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
async def test_a_trader_may_preview(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
) -> None:
    """The first criterion: any valid access token, any role (D-018, D-014)."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0]),
        headers=trader_headers,
    )

    assert response.status_code == 200


async def test_an_administrator_may_preview_too(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """"any role" runs both ways. There is no role this route refuses."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0]),
        headers=admin_headers,
    )

    assert response.status_code == 200


async def test_an_anonymous_caller_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """401 `invalid_token`, reusing [F-7] #96's code as the criteria require."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id), params=_params(market.outcomes[0])
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_an_expired_token_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The fifteen-minute window ADR 0001 records, on this route like every other."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0]),
        headers=bearer(uuid.uuid4(), UserRole.TRADER, expires_in=-1),
    )

    assert response.status_code == 401


async def test_the_cookie_is_accepted_too(
    client: AsyncClient, session: AsyncSession, user_id: uuid.UUID
) -> None:
    """ADR 0002: the browser sends a cookie, through `AccessToken` too."""
    market = _Market()
    await _warm(session, market)
    client.cookies.set("access_token", mint_token(user_id, UserRole.TRADER))

    response = await client.get(
        _path(market.market_id), params=_params(market.outcomes[0])
    )

    assert response.status_code == 200


# =========================================================================
# The wire
# =========================================================================
async def test_every_money_and_price_field_is_a_json_string(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Money and prices are JSON strings, checked by type on the wire, since a
    parsed float compares equal to the Decimal it lost precision from.
    """
    market = _Market()
    await _warm(session, market)

    body = (
        await client.get(
            _path(market.market_id),
            params=_params(market.outcomes[0]),
            headers=trader_headers,
        )
    ).json()

    for field in _DECIMAL_STRINGS:
        assert isinstance(body[field], str), f"{field} is {type(body[field])}"
        assert Decimal(body[field]).as_tuple().exponent == -4, body[field]

    for vector in ("prices", "post_trade_prices"):
        for entry in body[vector]:
            assert isinstance(entry["price"], str), entry
            assert Decimal(entry["price"]).as_tuple().exponent == -4, entry


async def test_state_version_is_a_json_number(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """Deliberately not a string: a counter a client compares with `>`, an
    `int` in the event and the snapshot too."""
    market = _Market()
    await _warm(session, market)

    body = (
        await client.get(
            _path(market.market_id),
            params=_params(market.outcomes[0]),
            headers=trader_headers,
        )
    ).json()

    assert isinstance(body["state_version"], int)
    assert not isinstance(body["state_version"], bool)


async def test_the_response_is_the_quote_and_no_second_reference(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The fifth criterion: `state_version` is the only quote reference, so the
    field set is exact."""
    market = _Market()
    await _warm(session, market)

    body = (
        await client.get(
            _path(market.market_id),
            params=_params(market.outcomes[0]),
            headers=trader_headers,
        )
    ).json()

    assert set(body) == _FIELDS


async def test_prices_match_the_price_event_shape(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """`prices` is `PriceEvent.prices`, field for field, so one client function
    renders the snapshot, the frame and the preview."""
    market = _Market()
    await _warm(session, market)

    body = (
        await client.get(
            _path(market.market_id),
            params=_params(market.outcomes[0]),
            headers=trader_headers,
        )
    ).json()

    for vector in ("prices", "post_trade_prices"):
        assert len(body[vector]) == len(market.outcomes)
        for i, entry in enumerate(body[vector]):
            assert set(entry) == {"outcome_id", "position", "price"}
            assert entry["position"] == i
            assert entry["outcome_id"] == str(market.outcomes[i])
            assert isinstance(entry["position"], int)


async def test_the_side_and_ids_are_echoed_back(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """What was asked comes back with the answer, so a client can match
    out-of-order responses to keystrokes."""
    market = _Market()
    await _warm(session, market)

    body = (
        await client.get(
            _path(market.market_id),
            params=_params(market.outcomes[1], side="sell", quantity="1.5000"),
            headers=trader_headers,
        )
    ).json()

    assert body["market_id"] == str(market.market_id)
    assert body["outcome_id"] == str(market.outcomes[1])
    assert body["side"] == "sell"
    assert body["quantity"] == "1.5000"


# =========================================================================
# Validation
# =========================================================================
@pytest.mark.parametrize("quantity", ["10.00005", "0.00001", "1.123456"])
async def test_a_quantity_with_five_decimal_places_is_422(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    quantity: str,
) -> None:
    """Refused rather than rounded (D-038): rounding quotes a quantity the
    trader did not type."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], quantity=quantity),
        headers=trader_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("quantity", ["0", "0.0000", "-1.0000", "abc", ""])
async def test_a_quantity_that_is_not_a_positive_decimal_is_422(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    quantity: str,
) -> None:
    """`gt 0`: `side` carries the direction, so a negative quantity would be a
    second place for it."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], quantity=quantity),
        headers=trader_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("side", ["BUY", "long", "", "buy ", "sell,buy"])
async def test_an_unrecognised_side_is_422(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    side: str,
) -> None:
    """Two values, parsed once at the boundary, and not case-folded."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], side=side),
        headers=trader_headers,
    )

    assert response.status_code == 422


async def test_a_missing_parameter_is_422(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """All three parameters are required."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params={"outcome_id": str(market.outcomes[0])},
        headers=trader_headers,
    )

    assert response.status_code == 422


async def test_a_malformed_market_id_is_422(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The market id is validated as a UUID before anything reads or writes."""
    response = await client.get(
        "/ledger/markets/not-a-uuid/preview",
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 422


async def test_an_unknown_outcome_id_is_422_and_not_404(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The tenth criterion: 422, because the market was found and the
    parameter is wrong."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_outcome"


async def test_side_and_quantity_are_validated_before_any_http_call_or_write(
    client: AsyncClient,
    session: AsyncSession,
    trader_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed query string on a cold market reaches no market_service,
    opens no book and funds no pool, not just a 422.
    """
    terms = _Terms().install(monkeypatch)

    response = await client.get(
        _path(terms.market_id),
        params=_params(uuid.uuid4(), side="sideways", quantity="-1"),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert terms.calls == 0, "a bad query string must not reach market_service"

    book = _entities().MarketBook
    assert (
        await session.execute(
            select(func.count())
            .select_from(book)
            .where(book.market_id == terms.market_id)
        )
    ).scalar_one() == 0
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == 0


# =========================================================================
# Refusals that come from below
# =========================================================================
async def test_a_sell_larger_than_the_outcome_s_q_is_409(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The seventh criterion: 409, because the book's state refuses it."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[1], side="sell", quantity="43.0000"),
        headers=trader_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_shares_outstanding"


async def test_an_unreachable_market_service_is_503(
    client: AsyncClient, trader_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-030's 503, reaching the wire unchanged through the preview."""
    from core.errors import MarketTermsUnavailable  # noqa: PLC0415

    terms = _Terms(raises=MarketTermsUnavailable()).install(monkeypatch)

    response = await client.get(
        _path(terms.market_id),
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "market_terms_unavailable"


async def test_an_unknown_market_is_404(
    client: AsyncClient, trader_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 `market_not_found`, not distinguished from a draft."""
    from core.errors import MarketNotFound  # noqa: PLC0415

    terms = _Terms(raises=MarketNotFound()).install(monkeypatch)

    response = await client.get(
        _path(terms.market_id),
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_an_unpublished_market_is_409(
    client: AsyncClient, trader_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 `market_not_published`, as the handler maps it."""
    terms = _Terms().install(monkeypatch, published=False)

    response = await client.get(
        _path(terms.market_id),
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_published"


async def test_an_upstream_401_is_401(
    client: AsyncClient, trader_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-030: a forwarded token market_service refused is the caller's 401,
    not a 503."""
    from core.errors import NotAuthenticated  # noqa: PLC0415

    terms = _Terms(raises=NotAuthenticated()).install(monkeypatch)

    response = await client.get(
        _path(terms.market_id),
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# =========================================================================
# The forwarded credential
# =========================================================================
async def test_the_route_forwards_the_caller_s_own_token_upstream(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AccessToken` forwards the caller's own raw token upstream (D-018)."""
    terms = _Terms().install(monkeypatch)
    token = mint_token(uuid.uuid4(), UserRole.TRADER)

    await client.get(
        _path(terms.market_id),
        params=_params(terms.outcomes[0]),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert terms.tokens == [token]


async def test_a_cookie_session_forwards_its_own_token_too(
    client: AsyncClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser sends a cookie, never a header, and that token is the one
    forwarded (ADR 0002)."""
    terms = _Terms().install(monkeypatch)
    token = mint_token(user_id, UserRole.TRADER)
    client.cookies.set("access_token", token)

    await client.get(_path(terms.market_id), params=_params(terms.outcomes[0]))

    assert terms.tokens == [token]


async def test_a_quantity_is_echoed_at_the_scale_it_arrived(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """D-038's echo rule, at scales where echoing and quantizing to 4 differ."""
    market = _Market()
    await _warm(session, market)

    for sent in ("10", "1.5", "10.0000", "0.5000"):
        body = (
            await client.get(
                _path(market.market_id),
                params=_params(market.outcomes[0], quantity=sent),
                headers=trader_headers,
            )
        ).json()
        assert body["quantity"] == sent


async def test_a_quantity_that_prices_above_the_column_is_422(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """D-040. 422 with a code of its own, not a 500 from the confirm step."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], quantity="99999999999999.9999"),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quantity_too_large"


async def test_a_sell_whose_proceeds_quantize_to_zero_is_422(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """D-041 at the wire: 422 `proceeds_below_tick`, apart from
    `quantity_too_large` because the fix is the opposite quantity."""
    market = _Market()
    await _warm(session, market, _SATURATED)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[1], side="sell", quantity="100.0000"),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "proceeds_below_tick"


async def test_the_same_sub_tick_quantity_is_still_priced_on_a_buy(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The refusal is about proceeds: the same sub-tick buy is charged a tick."""
    market = _Market()
    await _warm(session, market, _SATURATED)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[1], side="buy", quantity="100.0000"),
        headers=trader_headers,
    )

    assert response.status_code == 200
    assert response.json()["total"] == "-0.0001"


# =========================================================================
# Precision and zero totals at the wire
# =========================================================================
async def test_a_quantity_with_more_digits_than_the_column_is_422_not_500(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """`1E+25` has no fifth decimal place and is still not a quantity."""
    market = _Market()
    await _warm(session, market)

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], quantity="1E+25"),
        headers=trader_headers,
    )

    assert response.status_code == 422


async def test_a_buy_priced_at_exactly_zero_is_422_cost_below_tick(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    market = _Market()
    await _warm(session, market, [Decimal("12000"), Decimal("1")])

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[1], side="buy", quantity="100"),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cost_below_tick"


async def test_a_buy_whose_resulting_q_overflows_is_422_quantity_too_large(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    market = _Market()
    await _warm(session, market, [Decimal("99999999999999.9999"), Decimal("0")])

    response = await client.get(
        _path(market.market_id),
        params=_params(market.outcomes[0], side="buy", quantity="0.0001"),
        headers=trader_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quantity_too_large"


# =========================================================================
# The documented contract
# =========================================================================
async def test_a_quantity_echo_preserves_scale_not_the_literal_string(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The scale a client sent comes back, not its spelling: `.5` is `0.5`."""
    market = _Market()
    await _warm(session, market)

    for sent, echoed in ((".5", "0.5"), ("10.", "10")):
        body = (
            await client.get(
                _path(market.market_id),
                params=_params(market.outcomes[0], quantity=sent),
                headers=trader_headers,
            )
        ).json()
        assert body["quantity"] == echoed
        assert Decimal(body["quantity"]) == Decimal(sent)


async def test_the_preview_contract_does_not_offer_market_not_published(
    client: AsyncClient,
) -> None:
    """A 409 for an unpublished market cannot reach a caller of this route
    (market_service answers 404 first), so the contract must not offer it.
    Asserted on the error code, not on prose.
    """
    operation = (await client.get("/openapi.json")).json()["paths"][
        "/ledger/markets/{market_id}/preview"
    ]["get"]
    documented = json.dumps(operation).lower()

    assert "market_not_published" not in documented

    doc = (Path(__file__).resolve().parents[4] / "docs/api/ledger-service.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("## GET /ledger/markets/{market_id}/preview", 1)[1]
    section = section.split("\n## ", 1)[0]
    assert "market_not_published" not in section
