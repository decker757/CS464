"""Status codes, authorisation and the shape on the wire. [T-1] #21

Business rules are tested in `unit_test/service/test_preview.py` without HTTP.
What is asserted here is what the controller owns: who may call the route, what
the query string is allowed to say, which status code each refusal carries, and
what the JSON looks like — including which fields are strings, which is a money
decision rather than a formatting one.

**The cold path is stubbed at `market_terms.fetch` rather than at a transport.**
A route cannot be handed an `httpx.MockTransport`; the service-layer tests
exercise the real request building, the real headers and the real parsing
through one, and `test_market_terms.py` owns the upstream-to-error mapping. What
is left for this layer is whether a domain error raised below it becomes the
right status and the right code, and a stub that raises the domain error
directly is the most honest way to ask that question.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

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

# Every field in the response, and nothing else. Asserted as a set so that an
# extra one is a failure rather than an unnoticed addition — see
# `test_the_response_is_the_quote_and_no_second_reference`.
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

# The money and price fields. `quantity` is in here with the rest: it is a
# Decimal at scale 4, `total` was computed from it, and it is the one field a
# client could otherwise round-trip through an IEEE double and hand back to a
# confirm step as a different number.
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


async def _warm(session: AsyncSession, market: _Market) -> None:
    """Open the book and write `q`, committed, so the route's own session sees it."""
    await _books().ensure_open(
        session,
        market.market_id,
        access_token=mint_token(uuid.uuid4()),
        transport=market.transport,
    )
    outcome = _entities().MarketOutcome
    for position, value in enumerate(_Q):
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
    """A stub for `market_terms.fetch`, recording what it was asked.

    Patched onto the module rather than onto `books`, because `books.py` holds
    the module and resolves `market_terms.fetch` at call time — so this is the
    same function the real path calls, reached the same way.
    """

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
    """The first criterion: any valid access token, any role (D-018).

    A preview mints nothing and reveals nothing beyond what the public market
    read and the realtime snapshot already show, so the trader's own token is
    the right credential and there is no admin gate. D-014's Notes say this
    explicitly, and say why it does not reopen ADR 0009's deferred
    service-auth question: that one is about a *write* route.
    """
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
    """ADR 0002: the browser sends a cookie, a service sends a header.

    Worth asserting on this route specifically. It is the one the frontend
    calls on every keystroke, and it is also the one that forwards the
    credential upstream — so the cookie has to survive being read by
    `extract_access_token` and handed on as a bearer token.
    """
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
    """The third criterion's wire half, and the ledger's standing rule.

    A JSON number is an IEEE double by the time a browser has parsed it, and
    `0.1 + 0.2` is famously not `0.3`. The ledger is careful to keep its
    arithmetic exact all the way from `q` to the quantized total, and handing
    the result through a double on the last hop throws that away. `BalanceOut`
    and `OutcomePrice` both already serialise with `str()` for this reason.

    Checked with `is` against `str` rather than by parsing, because
    `json.loads` turns `1000.0` into a float that compares equal to
    `Decimal("1000")` — the assertion has to be about the type on the wire.
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
    """The one field that is deliberately not a string.

    It is a counter, not money: nothing is lost by putting an integer through a
    double, and a client compares it with `>` against the version it last
    rendered. `PriceEvent.state_version` is an `int` and the snapshot reports
    the same, so a string here would make the frontend coerce one of the three
    before comparing them — which is the bug [X-4] #37's ordering rule exists
    to avoid.
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

    assert isinstance(body["state_version"], int)
    assert not isinstance(body["state_version"], bool)


async def test_the_response_is_the_quote_and_no_second_reference(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The fifth criterion's "not a second one", asserted as an absence.

    `state_version` is the quote reference and nothing else is. A `quote_id`,
    an `expires_at`, a `priced_at` — any of them would be a second answer to
    "has this market moved", and #22 checks exactly one number under its lock.
    An exact field set is the only way to catch one being added, because every
    other test in this suite passes happily beside an extra key.
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

    assert set(body) == _FIELDS


async def test_prices_match_the_price_event_shape(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """`prices` is `PriceEvent.prices`, field for field.

    Three keys per outcome — `outcome_id`, `position`, `price` — with `position`
    a number and `price` a string, exactly as `OutcomePrice` puts them on the
    socket. A client that renders a snapshot, a price frame and a preview
    should be running one function over all three; a fourth shape here would
    make that three functions.

    Read off `realtime_service/model/schemas.py` rather than off
    `docs/api/realtime-service.md`, because that page is generated from nothing
    and its own header says so.
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
    """What was asked comes back with the answer.

    The frontend debounces this route and fires it on every keystroke, so
    responses arrive out of order. Without the request echoed into the
    response, a client cannot tell which quantity a quote belongs to and will
    eventually render the cost of a number the trader has already deleted.
    """
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
    """Refused rather than rounded, and that is the whole point (D-036).

    Money is `Numeric(18, 4)` and a quantity is quoted at the same scale.
    Silently rounding `10.00005` to `10.0001` quotes a trade for a quantity the
    trader did not type, and [T-2] #22 would then charge for that one — so the
    preview would be accurate about a trade nobody asked for. A 422 puts the
    correction where the typing happened.
    """
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
    """`gt 0`. A trade of nothing has a cost of nothing and is not a question.

    A negative quantity is the one worth naming: with `side` already carrying
    the direction, a negative would be a second place for it, and a `-5` sell
    would price a buy while the response said "sell".
    """
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
    """Two values, parsed once, at the boundary.

    `"BUY"` is in the list deliberately. Case-folding it would be a kindness
    that costs a second rule the service layer does not know about, and the
    enum's values are the wire's.
    """
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
    """All three are required. None of them has a sensible default: a preview
    with no quantity is not a smaller question, it is no question."""
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
    """The market id is validated as a UUID before anything reads or writes.

    A path parameter that is not a UUID cannot name a market, so there is
    nothing to look up and nothing to be unavailable — 422 rather than 404 or
    503, and settled by the route signature rather than by a check anybody
    could forget.
    """
    response = await client.get(
        "/ledger/markets/not-a-uuid/preview",
        params=_params(uuid.uuid4()),
        headers=trader_headers,
    )

    assert response.status_code == 422


async def test_an_unknown_outcome_id_is_422_and_not_404(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """The tenth criterion, exactly as it is worded.

    422 because the market was found and the parameter is wrong. A 404 here
    would collide with `market_not_found`, and a client could not tell "I sent
    a bad outcome id" from "this market is gone" — two different bugs with two
    different fixes.
    """
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
    """The tenth criterion's first sentence, on a market with no book.

    This is the ordering that matters. A malformed query string on a cold
    market must not reach `market_service`, must not open a book and must not
    fund a pool — otherwise a typo in a debounced keystroke costs a network
    round trip and a ledger transaction, and a bot sending nonsense would open
    a book for every market id it could guess.

    Asserted as three facts, because the status code alone is satisfied by an
    implementation that does all of that and *then* returns 422.
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
    """The seventh criterion at the wire.

    409 rather than 422: nothing about the request is malformed and the same
    request would succeed on a market where more shares were outstanding. It is
    the state of the book that refuses it — the same distinction
    `InsufficientFunds` already draws one file over.
    """
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
    """D-028's 503, reaching the wire unchanged through the preview.

    A 503 says the market service is down and the request is worth retrying in
    a moment, which is the only thing a client can act on. Collapsing it into a
    500 would report a bug in the ledger for a condition that is neither a bug
    nor the caller's fault.
    """
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
    """404 `market_not_found`, and deliberately not distinguished from a draft.

    `browsing.get_published` on the other side makes "no such market", "a
    draft" and "submitted" indistinguishable on purpose, so this side inherits
    the ambiguity. All three are permanent, which is what separates this from
    the 503 above: retrying never helps.
    """
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
    """409 `market_not_published`. The market exists and has no book coming.

    `published_at is not None` is the condition, read off the response rather
    than inferred from a status the market service could change independently
    of this rule.
    """
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
    """D-028: a token the ledger forwarded and market_service refused.

    401 rather than 503, because this is the caller's session problem and is
    fixed by logging in again — not by the ledger claiming its dependency is
    unavailable and inviting a retry that will fail identically.
    """
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
    """The `AccessToken` dependency, and the reason it exists beside `CurrentUser`.

    `CurrentUser` hands the route decoded claims, and claims cannot be
    re-signed into a credential. The cold path needs the raw token, because
    D-018's Notes have the ledger forwarding the caller's own and minting
    nothing — a token minted here would be this service asserting an identity
    it was not given, on a call to another service.

    Both dependencies read `transport.extract_access_token`, so the header and
    the cookie reach the upstream call identically.
    """
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
    """The browser's half of ADR 0002, through the same dependency.

    The frontend sends a cookie, never a header, so this is the path that
    actually runs in production. If `AccessToken` read the `Authorization`
    header alone, every cold market would answer 401 from `market_service` for
    a trader whose session was perfectly valid — and it would do it only on
    markets nobody had previewed yet.
    """
    terms = _Terms().install(monkeypatch)
    token = mint_token(user_id, UserRole.TRADER)
    client.cookies.set("access_token", token)

    await client.get(_path(terms.market_id), params=_params(terms.outcomes[0]))

    assert terms.tokens == [token]
